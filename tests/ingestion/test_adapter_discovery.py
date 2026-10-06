"""
Listing adapters wired into source discovery, on the official pages captured on 2026-10-01
(tests/fixtures/adapters/<source>/ and tests/fixtures/sources/<source>/; each meta.json names the URL every
file was fetched from). The fetcher is the real PageFetcher (url_guard + robots.txt) over an httpx mock
transport that serves the captured bytes at their own URLs, and 404 for anything that was not captured.
"""
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx
import pytest
from sqlalchemy import select

from govcareeros.backend.models import GovernmentSource, NoticeTriage, RecruitmentNotice
from govcareeros.backend.services import triage
from govcareeros.backend.services.review_console import review_queue
from govcareeros.backend.services.source_discovery import KIND_HINT_KINDS, discover_all, reclassify_notices, source_policy
from govcareeros.backend.sources.adapters import all_adapters
from govcareeros.backend.sources.generic_notices import FetchResult, PageFetcher, extract_notices
from govcareeros.backend.sources.notice_classifier import classify_notice
from govcareeros.backend.sources.registry import SOURCES

FIXTURES = Path(__file__).parents[1] / "fixtures"
SPECS = {s.name: s for s in SOURCES}
ADAPTERS = all_adapters()

def _captures(o, out: Dict[str, Tuple[Path, str]], folder: Path, key: Optional[str] = None) -> None:
    """{url: (file, final_url)} from a meta.json, whichever of the capture layouts it uses. Browser renders
    are left out: the mock serves what an HTTP fetch got."""
    if isinstance(o, dict):
        name = o.get("file") or (key if key and key.endswith(".html") else None)
        if name and o.get("url") and o.get("fetch_mode") != "BROWSER":
            out[o["url"]] = (folder / name, o.get("final_url") or o["url"])
        for k, v in o.items():
            _captures(v, out, folder, k)
    elif isinstance(o, list):
        for v in o:
            _captures(v, out, folder)

def _fixture_dir(source: str) -> Optional[Path]:
    for base in ("adapters", "sources"):
        folder = FIXTURES / base / source.lower()
        if (folder / "meta.json").is_file():
            return folder
    return None

def captured(source: str) -> Dict[str, Tuple[Path, str]]:
    folder = _fixture_dir(source)
    out: Dict[str, Tuple[Path, str]] = {}
    _captures(json.loads((folder / "meta.json").read_text(encoding="utf-8")), out, folder)
    return out

CAPTURED_SOURCES = sorted(name for name in ADAPTERS if _fixture_dir(name) and name in SPECS)

def _portal(pages: Dict[str, Tuple[Path, str]], requested: List[str]) -> httpx.AsyncClient:
    by_final = {final: path for path, final in pages.values()}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        requested.append(url)
        if url in pages and pages[url][1] != url:
            return httpx.Response(302, headers={"location": pages[url][1]})  # the redirect the capture recorded
        path = pages[url][0] if url in pages else by_final.get(url)
        if path is None:
            return httpx.Response(404, text="not found")
        return httpx.Response(200, content=path.read_bytes(), headers={"content-type": "text/html; charset=utf-8"})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)

async def _discover(db_session, source: str, fetcher_cls=PageFetcher):
    requested: List[str] = []
    async with fetcher_cls(allow_browser=False, client=_portal(captured(source), requested)) as f:
        [summary] = await discover_all(db_session, f, [SPECS[source]])
        await db_session.commit()
    return summary, requested

async def _notices(db_session, source: str) -> List[RecruitmentNotice]:
    return list((await db_session.execute(
        select(RecruitmentNotice).join(GovernmentSource, GovernmentSource.id == RecruitmentNotice.source_id)
        .where(GovernmentSource.name == source))).scalars())

@pytest.mark.asyncio
@pytest.mark.parametrize("source", CAPTURED_SOURCES)
async def test_discovery_stores_what_the_adapter_reads_from_the_captured_pages(db_session, source):
    adapter = ADAPTERS[source]
    pages = captured(source)
    summary, requested = await _discover(db_session, source)
    source_row = (await db_session.execute(select(GovernmentSource))).scalar_one()
    policy = source_policy(source_row)
    # The configured page's own result (the source's status may be OK through its listing pages alone)
    main_ok = json.loads(source_row.configuration)["page_status"] == "OK"

    # The pages discovery read: the configured (or located) page when it loaded, then the adapter's LISTING_URLS
    read: List[Tuple[str, str]] = []
    if main_ok:
        read.append((summary.page_url, next(p for p, final in pages.values() if final == summary.page_url).read_text(encoding="utf-8")))
    for url in getattr(adapter, "LISTING_URLS", []):
        if url in pages and pages[url][1] not in {u for u, _ in read}:
            read.append((pages[url][1], pages[url][0].read_text(encoding="utf-8")))
    expected = {}
    for page_url, html in read:
        for row in adapter.parse_listing(html, page_url):
            if policy.allows(row.url):
                ident = (row.url, row.title) if row.url.rstrip("/") == page_url.rstrip("/") else (row.url, None)
                expected.setdefault(ident, (row, page_url))

    notices = await _notices(db_session, source)
    from_adapter = [n for n in notices if n.listing_evidence]
    assert len(from_adapter) == len(expected)
    by_ident = {(n.url, n.title if n.listing_row_key else None): n for n in from_adapter}
    for ident, (row, page_url) in expected.items():
        n = by_ident[ident]
        assert n.title == row.title and n.found_on_url == page_url
        assert (n.published_on, n.last_date, n.advertisement_number, n.kind_hint) == \
            (row.published_on, row.last_date, row.advertisement_number, row.kind_hint)
        assert json.loads(n.post_names) == row.post_names if row.post_names else n.post_names is None
        assert json.loads(n.listing_evidence) == row.evidence
        explicit = row.kind_hint in KIND_HINT_KINDS and row.evidence.get("kind_hint")
        assert n.notice_kind == (KIND_HINT_KINDS[row.kind_hint] if explicit else classify_notice(row.title, row.url))

    # Generic extraction stays the fallback: every link it finds on the configured page is stored too
    if main_ok:
        main_html = read[0][1]
        stored = {n.url for n in notices}
        assert {l.url for l in extract_notices(main_html, summary.page_url, policy)} <= stored
    assert all(policy.allows(n.url) for n in notices)
    assert summary.notices_seen == len(notices)
    # Nothing outside the source's policy was requested
    assert all(policy.allows(u) for u in requested if not u.endswith("/robots.txt"))

    # A second sweep finds the same notices again (row keys are stable), nothing new
    again, _ = await _discover(db_session, source)
    assert again.new_notices == 0 and again.notices_seen == summary.notices_seen
    assert len(await _notices(db_session, source)) == len(notices)

def test_every_adapter_with_a_registry_source_has_captured_pages():
    # Every adapter is named after a registry source, and every one of them runs through discovery above
    assert set(ADAPTERS) <= set(SPECS)
    assert set(CAPTURED_SOURCES) == set(ADAPTERS)

EMPLOYMENT_NEWS = "https://employmentnews.gov.in/NewEmp/AllJobs.aspx?k=All"

@pytest.mark.asyncio
async def test_employment_news_rows_share_the_listing_url_and_stay_distinct(db_session):
    summary, _ = await _discover(db_session, "EMPLOYMENT-NEWS")
    notices = await _notices(db_session, "EMPLOYMENT-NEWS")
    rows = [n for n in notices if n.listing_row_key]
    # The 12 "All JOBS" rows have no documents: each keeps the listing page as its url, none is made up
    assert len(rows) == 12 and {n.url for n in rows} == {EMPLOYMENT_NEWS}
    assert len({n.url_hash for n in rows}) == 12 and len({n.listing_row_key for n in rows}) == 12
    assert all(n.document_type == "HTML" and n.found_on_url == EMPLOYMENT_NEWS for n in rows)
    mecl = next(n for n in rows if n.title == "MINERAL EXPLORATION AND CONSULTANCY LIMITED - ACCOUNTANT & OTHERS")
    assert (mecl.published_on, mecl.last_date) == (date(2026, 9, 12), date(2026, 10, 11))
    assert json.loads(mecl.post_names) == ["ACCOUNTANT & OTHERS"]
    assert json.loads(mecl.listing_evidence) == {"title": "MINERAL EXPLORATION AND CONSULTANCY LIMITED",
                                                 "post_names": "ACCOUNTANT & OTHERS",
                                                 "published_on": "12/09/2026", "last_date": "11/10/2026",
                                                 "kind_hint": "All JOBS"}
    # The table under "All JOBS" lists jobs (organisation, post, method of appointment, last date): every row is a
    # VACANCY on the page's own word, not left to the title classifier (which reads these titles as OTHER)
    assert mecl.kind_hint == "VACANCY" and mecl.review_status == "MANUAL_REVIEW"
    assert all(n.kind_hint == "VACANCY" and n.notice_kind == "VACANCY" for n in rows)
    assert classify_notice(mecl.title, mecl.url) == "OTHER"

RPSC_DOC = "https://rpsc.rajasthan.gov.in/Static/RecruitmentAdvertisements/"

@pytest.mark.asyncio
async def test_rpsc_rows_carry_the_listing_fields_and_labels(db_session):
    await _discover(db_session, "RPSC")
    by_url = {n.url: n for n in await _notices(db_session, "RPSC")}
    corrigendum = by_url[RPSC_DOC + "12E5561DA6634F6A9548306FFA667C12.pdf"]
    assert corrigendum.title == "Corrigendum No. 15/2026-27 for Sub Inspector Comb. Comp. Exam - 2021"
    assert (corrigendum.published_on, corrigendum.advertisement_number, corrigendum.kind_hint) == \
        (date(2026, 9, 9), None, "CORRIGENDUM")
    assert corrigendum.notice_kind == "CORRIGENDUM" and corrigendum.document_type == "PDF"
    physio = by_url[RPSC_DOC + "C80D8376B256408692F17AAA61B754DE.pdf"]
    assert (physio.title, physio.advertisement_number, physio.published_on) == \
        ("Advt. No. 05/2026-27 for Physiotherapist - 2026", "05/2026-27", date(2026, 7, 21))
    assert physio.kind_hint == "VACANCY" and json.loads(physio.listing_evidence)["kind_hint"] == "Advt."

@pytest.mark.asyncio
async def test_listing_label_decides_the_kind_only_where_the_page_states_one(db_session):
    """Rows whose listing label differs from what the title keywords say, read from the captured pages."""
    for source in CAPTURED_SOURCES:
        await _discover(db_session, source)
    notices = (await db_session.execute(select(RecruitmentNotice))).scalars().all()
    labelled = [n for n in notices if n.kind_hint and json.loads(n.listing_evidence or "{}").get("kind_hint")]
    assert labelled
    for n in labelled:
        if n.kind_hint in KIND_HINT_KINDS:
            assert n.notice_kind == KIND_HINT_KINDS[n.kind_hint]
    overridden = [n for n in labelled if n.kind_hint in KIND_HINT_KINDS and classify_notice(n.title, n.url) != n.notice_kind]
    assert overridden  # the label is used where it says more than the keywords
    unlabelled = [n for n in notices if not n.kind_hint]
    assert all(n.notice_kind == classify_notice(n.title, n.url) for n in unlabelled)
    # Re-running the classifier over stored notices keeps the stated labels
    assert await reclassify_notices(db_session) == {}

DFS_POST = ("Filling up of existing, anticipated and unforeseen vacancies upto 31.12.2026 for the post of Registrar, "
            "Assistant Registrar and Recovery Officer on deputation basis in Debts Recovery Appellate Tribunals (DRATs) "
            "and Debts Recovery Tribunals (DRTs) under DFS, MoF.")
APSC_VO = "Veterinary Officer/Block Veterinary Officer (VO/BVO) under Animal Husbandry & Veterinary Department"
MECL = "MINERAL EXPLORATION AND CONSULTANCY LIMITED - ACCOUNTANT & OTHERS"

@pytest.mark.asyncio
async def test_triage_prefers_the_listing_dates(db_session):
    for source in ("DFS", "APSC", "RPSC"):
        await _discover(db_session, source)
    run = await triage.triage_notices(db_session, now=datetime(2026, 10, 1, 12, tzinfo=timezone.utc), apply=True)
    rows = {(r.source, r.title): r.decision for r in run.rows}

    # The title's "upto 31.12.2026" is the vacancy period, not the date of the notice: the listing's own
    # PUBLISHED DATE wins over the title heuristic, and its DUE DATE keeps the notice open
    dfs = rows[("DFS", DFS_POST)]
    assert triage.notice_date(DFS_POST, "https://financialservices.gov.in/sites/default/files/vacancy/2026-09/Extension-of-date.pdf") \
        == triage.NoticeDate(date(2026, 12, 31), "title_date", "31.12.2026")
    assert (dfs.notice_date.rule, dfs.notice_date.date, dfs.notice_date.text) == \
        ("listing_published_on", date(2026, 9, 8), "PUBLISHED DATE 2026-09-08")
    assert (dfs.triage_status, dfs.rule) == (triage.QUEUED, "open_last_date")
    assert "listing states last date 2026-12-31 [listing: 'DUE DATE 2026-12-31']" in dfs.reason

    # Published 59 days ago (inside the window), but the listing's closing date has passed
    vo = rows[("APSC", APSC_VO)]
    assert (vo.notice_date.rule, vo.notice_date.text) == ("listing_published_on", "03-08-2026")
    assert (vo.triage_status, vo.rule) == (triage.STALE, "closed_last_date")

    # No closing date on the RPSC listing: the window still applies, counted from the listing's release date
    physio = rows[("RPSC", "Advt. No. 05/2026-27 for Physiotherapist - 2026")]
    assert (physio.notice_date.rule, physio.notice_date.text) == ("listing_published_on", "21/07/2026")
    assert (physio.triage_status, physio.rule) == (triage.QUEUED, "fresh_stated_date")

    # The rule is recorded with the decision
    stored = {(t.notice_date_rule, t.notice_date_text, t.rule) for t in (await db_session.execute(select(NoticeTriage))).scalars()}
    assert ("listing_published_on", "PUBLISHED DATE 2026-09-08", "open_last_date") in stored
    assert run.date_rule_counts()["listing_published_on"] > 0

    # After the DUE DATE the same notice is closed
    later = await triage.triage_notices(db_session, now=datetime(2027, 1, 1, 12, tzinfo=timezone.utc))
    closed = next(r.decision for r in later.rows if r.title == DFS_POST)
    assert (closed.triage_status, closed.rule) == (triage.STALE, "closed_last_date")

@pytest.mark.asyncio
async def test_review_queue_shows_the_listing_fields(db_session):
    await _discover(db_session, "EMPLOYMENT-NEWS")
    total, items = await review_queue(db_session, source="EMPLOYMENT-NEWS", q="MINERAL EXPLORATION")
    [item] = [i for i in items if i["title"] == MECL]
    assert (item["published_on"], item["last_date"]) == (date(2026, 9, 12), date(2026, 10, 11))
    assert item["post_names"] == ["ACCOUNTANT & OTHERS"] and item["advertisement_number"] is None
    assert item["listing_evidence"]["last_date"] == "11/10/2026" and item["found_on_url"] == EMPLOYMENT_NEWS

class _RenderedAgriwelfare(PageFetcher):
    """fetch_browser returns the captured rendered page instead of launching Chromium."""

    async def fetch_browser(self, url, policy=None):
        html = (FIXTURES / "sources" / "agriwelfare" / "recruitmentDetail_rendered.html").read_text(encoding="utf-8")
        return FetchResult(url, 200, url, html)

@pytest.mark.asyncio
async def test_forced_browser_source_is_read_by_its_adapter_from_the_rendered_page(db_session):
    requested: List[str] = []
    async with _RenderedAgriwelfare(allow_browser=True, client=_portal(captured("AGRIWELFARE"), requested)) as f:
        [summary] = await discover_all(db_session, f, [SPECS["AGRIWELFARE"]])
    assert (summary.status, summary.fetch_mode) == ("OK", "BROWSER")
    by_url = {n.url: n for n in await _notices(db_session, "AGRIWELFARE")}
    officer = by_url["https://agriwelfare.gov.in/Documents/Recruitment/VC_SO_18092026.pdf"]
    assert (officer.published_on, officer.last_date) == (date(2026, 9, 13), date(2026, 11, 11))
    assert json.loads(officer.listing_evidence)["last_date"] == "11-11-2026"

@pytest.mark.asyncio
@pytest.mark.parametrize("source, capture", [("NHAI", "sources/nhai/vacancies-current-en.json"), ("NVS", "sources/nvs/api_jobs.json")])
async def test_discovery_reads_the_official_json_listing_endpoint(db_session, source, capture):
    """Single-page apps: the rows come from the endpoint their own page calls (LISTING_REQUEST), captured 2026-10-01."""
    adapter = ADAPTERS[source]
    request, body = adapter.LISTING_REQUEST, (FIXTURES / capture).read_bytes()
    seen: List[httpx.Request] = []

    def handler(r: httpx.Request) -> httpx.Response:
        seen.append(r)
        if str(r.url) == request.url and r.method == request.method:
            return httpx.Response(200, content=body, headers={"content-type": "application/json"})
        return httpx.Response(404, text="not found")

    async with PageFetcher(allow_browser=False, client=httpx.AsyncClient(transport=httpx.MockTransport(handler))) as f:
        await discover_all(db_session, f, [SPECS[source]])
        await db_session.commit()
    rows = [r for r in adapter.parse_listing(body.decode("utf-8"), request.url)]
    stored = [n for n in await _notices(db_session, source) if n.listing_evidence]
    assert rows and len(stored) == len({r.url for r in rows})
    assert {n.url for n in stored} == {r.url for r in rows}
    assert all(n.found_on_url == request.url for n in stored)
    assert any(r.method == request.method and str(r.url) == request.url for r in seen)

