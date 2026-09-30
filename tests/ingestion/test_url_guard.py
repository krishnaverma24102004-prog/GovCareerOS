"""
Outbound URL guard: every fetch (listing, robots.txt, document, each redirect hop) must stay on
the source's official domain, over https, and off private/loopback/link-local addresses.

Real inputs: the UPSC listing/homepage fixtures and the ministry vacancy table used in
test_multi_source_discovery. The injected links (127.0.0.1, 10.0.0.5, 169.254.169.254, an
off-domain redirect) are HAND-BUILT to reproduce the reported SSRF/redirect bugs; no real
government page contains them.
"""
import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from govcareeros.backend.models import GovernmentSource, RawDocument, RecruitmentNotice
from govcareeros.backend.services.document_ingestion import run_upsc_pipeline
from govcareeros.backend.services.extraction_drafts import draft_pending_notices
from govcareeros.backend.services.source_discovery import _probe_url, discover_all
from govcareeros.backend.sources import upsc
from govcareeros.backend.sources.generic_notices import PageFetcher, extract_notices
from govcareeros.backend.sources.registry import SourceSpec
from govcareeros.backend.sources.url_guard import GuardedTransport, UrlPolicy, UrlRefused

from test_multi_source_discovery import HOME, VACANCIES_HTML

UPSC = Path(__file__).parents[1] / "fixtures" / "upsc"
POLICY = UrlPolicy("ministry.example.gov.in")
# Hand-built hostile links (see module docstring)
INJECTED = """
<a href="http://127.0.0.1/x.pdf">Recruitment notice</a>
<a href="http://169.254.169.254/latest/recruitment.pdf">Vacancy</a>
<a href="http://10.0.0.5/AdvtNo-1-Engl-2025.pdf">Advertisement</a>
<a href="https://elsewhere.example.org/recruitment.pdf">Recruitment</a>
<a href="http://ministry.example.gov.in/files/recruitment-plain-http.pdf">Recruitment</a>
"""

@pytest.mark.parametrize("url", [
    "http://127.0.0.1/x.pdf", "http://10.0.0.5/AdvtNo-1-Engl-2025.pdf", "https://127.0.0.1/x.pdf",
    "https://169.254.169.254/latest/recruitment.pdf", "https://[::1]/x.pdf", "https://localhost/x.pdf",
    "http://ministry.example.gov.in/files/a.pdf", "https://elsewhere.example.org/a.pdf",
    "https://ministry.example.gov.in.evil.org/a.pdf", "ftp://ministry.example.gov.in/a.pdf",
])
def test_policy_refuses(url):
    assert not POLICY.allows(url)

def test_policy_allows_domain_and_subdomains_over_https_and_http_only_when_configured():
    assert POLICY.allows(HOME + "files/a.pdf") and POLICY.allows("https://cdn.ministry.example.gov.in/a.pdf")
    plain = UrlPolicy.for_source("ministry.example.gov.in", "http://ministry.example.gov.in/vacancies")
    assert plain.allow_http and plain.allows("http://ministry.example.gov.in/a.pdf")
    assert not plain.allows("http://10.0.0.5/AdvtNo-1-Engl-2025.pdf")

def test_extract_notices_drops_offdomain_private_and_plain_http_documents():
    notices = extract_notices(VACANCIES_HTML + INJECTED, HOME + "vacancies", POLICY)
    assert [n.url for n in notices] == [
        "https://ministry.example.gov.in/files/Dy%20Dock%20Master%20by%20Deputation.pdf",
        "https://ministry.example.gov.in/whats-new/77893",
    ]

def test_upsc_parsers_accept_only_upsc_gov_in_links():
    listing = (UPSC / "recruitment-advertisement.html").read_text(encoding="utf-8")
    real = [l.url for l in upsc.parse_listing(listing, upsc.LISTING_URL)]
    assert real and all(u.startswith("https://www.upsc.gov.in/") for u in real)
    assert [l.url for l in upsc.parse_listing(listing + INJECTED, upsc.LISTING_URL)] == real
    assert upsc.parse_listing('<a href="http://10.0.0.5/AdvtNo-1-Engl-2025.pdf">x</a>', upsc.LISTING_URL) == []
    home = (UPSC / "home.html").read_text(encoding="utf-8")
    assert upsc.parse_notice_links(home + INJECTED, upsc.HOME_URL) == upsc.parse_notice_links(home, upsc.HOME_URL)

def _served(routes, seen):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        body = routes.get(str(request.url))
        if body is None:
            return httpx.Response(404)
        if isinstance(body, tuple):  # (status, location)
            return httpx.Response(body[0], headers={"location": body[1]})
        if isinstance(body, bytes):
            return httpx.Response(200, content=body, headers={"content-type": "application/pdf"})
        ctype = "text/plain" if request.url.path == "/robots.txt" else "text/html"
        return httpx.Response(200, text=body, headers={"content-type": ctype})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)

@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["http://127.0.0.1/x.pdf", "http://10.0.0.5/AdvtNo-1-Engl-2025.pdf"])
async def test_fetcher_never_sends_refused_requests(url):
    seen = []
    async with PageFetcher(allow_browser=False, client=_served({}, seen)) as f:
        with pytest.raises(UrlRefused):
            await f.get(url, POLICY)
        assert (await f.fetch_http(url)).error.status == "URL_REFUSED"  # own-host policy still needs https + public IP
    assert seen == []

@pytest.mark.asyncio
async def test_offdomain_redirect_is_refused_and_never_becomes_resolved_url(db_session):
    evil = "https://recruitment.evil.example.org/vacancies"
    seen = []
    specs = [SourceSpec("TEST-MIN", "Test Ministry", "MINISTRY", HOME + "vacancies")]
    client = _served({HOME + "vacancies": (302, evil), evil: VACANCIES_HTML}, seen)
    async with PageFetcher(allow_browser=False, client=client) as f:
        [summary] = await discover_all(db_session, f, specs)
    assert summary.status == "URL_REFUSED" and summary.notices_seen == 0
    assert evil not in seen
    source = (await db_session.execute(select(GovernmentSource))).scalar_one()
    assert "resolved_url" not in json.loads(source.configuration)
    assert (await db_session.execute(select(RecruitmentNotice))).first() is None

    # A resolved_url stored by an earlier (unguarded) run is not reused
    source.configuration = json.dumps({"resolved_url": evil})
    assert _probe_url(source) == HOME + "vacancies"

@pytest.mark.asyncio
async def test_draft_fetch_refuses_redirect_to_private_address(db_session):
    pdf = HOME + "files/Dy%20Dock%20Master%20by%20Deputation.pdf"
    seen = []
    specs = [SourceSpec("TEST-MIN", "Test Ministry", "MINISTRY", HOME + "vacancies")]
    client = _served({HOME + "vacancies": VACANCIES_HTML, pdf: (302, "http://10.0.0.5/AdvtNo-1-Engl-2025.pdf")}, seen)
    async with PageFetcher(allow_browser=False, client=client) as f:
        await discover_all(db_session, f, specs)
        drafts = await draft_pending_notices(db_session, f, provider=None)
    assert drafts == []
    notice = (await db_session.execute(select(RecruitmentNotice).where(RecruitmentNotice.url == pdf))).scalar_one()
    assert notice.review_status == "URL_REFUSED"
    assert not any("10.0.0.5" in u for u in seen)
    assert (await db_session.execute(select(RawDocument))).first() is None

@pytest.mark.asyncio
async def test_guarded_transport_refuses_names_resolving_to_private_addresses():
    # Hand-built resolver standing in for DNS that points an official name at a private address
    async def resolve(host, port):
        return ["10.0.0.5"]
    async with httpx.AsyncClient(transport=GuardedTransport(resolve=resolve)) as client:
        with pytest.raises(UrlRefused, match="non-public 10.0.0.5"):
            await client.get(HOME + "vacancies")

def _upsc_portal(extra, seen):
    routes = {
        upsc.LISTING_URL: (UPSC / "recruitment-advertisement.html").read_text(encoding="utf-8"),
        upsc.HOME_URL: (UPSC / "home.html").read_text(encoding="utf-8"),
    }
    routes.update(extra)
    return _served(routes, seen)

@pytest.mark.asyncio
async def test_upsc_honours_robots_txt(db_session):
    seen = []
    robots = "User-agent: *\nDisallow: /recruitment/"
    async with _upsc_portal({"https://www.upsc.gov.in/robots.txt": robots}, seen) as client:
        run = await run_upsc_pipeline(db_session, client)
    assert run.source_health == "ROBOTS_DISALLOWED"
    assert seen == ["https://www.upsc.gov.in/robots.txt"]

@pytest.mark.asyncio
async def test_upsc_pdf_redirected_offdomain_is_not_stored(db_session):
    seen = []
    advt = "https://www.upsc.gov.in/sites/default/files/AdvtNo-12-2026-Engl-250926_0.pdf"
    async with _upsc_portal({advt: (302, "http://127.0.0.1/x.pdf")}, seen) as client:
        run = await run_upsc_pipeline(db_session, client)
    assert any(advt in e and "UrlRefused" in e for e in run.errors)
    assert "http://127.0.0.1/x.pdf" not in seen
    raws = (await db_session.execute(select(RawDocument.url))).scalars().all()
    assert all(u.startswith("https://www.upsc.gov.in/") for u in raws) and advt not in raws

def test_official_aggregators_may_link_other_government_domains():
    from govcareeros.backend.sources.url_guard import UrlPolicy
    ncs = UrlPolicy.for_source("ncs.gov.in", "https://www.ncs.gov.in/", "OFFICIAL_AGGREGATOR")
    # NCS lists DRDO vacancy PDFs (seen live on 29-09-2026)
    assert ncs.allows("https://drdo.gov.in/drdo/sites/default/files/vacancy/advtLRDE07092026.pdf")
    assert ncs.allows("https://recruitment.itbpolice.nic.in/noticeboards/downloadpdf/359.pdf")
    assert not ncs.allows("https://example.com/job.pdf")
    assert not ncs.allows("http://drdo.gov.in/x.pdf")          # still https only
    assert not ncs.allows("https://127.0.0.1/x.pdf")           # still never non-public
    ministry = UrlPolicy.for_source("mha.gov.in", "https://www.mha.gov.in/en/notifications/vacancies", "MINISTRY")
    assert not ministry.allows("https://drdo.gov.in/x.pdf")    # other sources stay on their own domain
