"""
Notice-level discovery across sources: link extraction, classification, status handling
(access denied, login walls, JS pages, wrong URLs), robots.txt, and persistence.
Portals are served through httpx.MockTransport; titles are taken from real government pages.
"""
import json

import httpx
import pytest
from sqlalchemy import func, select

from govcareeros.backend.models import GovernmentSource, RecruitmentNotice
from govcareeros.backend.services.source_discovery import discover_all
from govcareeros.backend.sources.generic_notices import FetchResult, PageFetcher, analyze, extract_notices, recruitment_nav_links
from govcareeros.backend.sources.notice_classifier import classify_notice
from govcareeros.backend.sources.registry import SourceSpec

@pytest.mark.parametrize("title,url,kind", [
    ("ADVERTISEMENT OF RECRUITMENT FOR THE POST OF HEAD CONSTABLE (MOTOR MECHANIC)- 2026", "https://x.gov.in/360.pdf", "VACANCY"),
    ("Download", "https://doe.gov.in/files/vacancy_attachment/Fillingupof1post.pdf", "VACANCY"),
    ("VIEW", "https://financialservices.gov.in/sites/default/files/vacancy/2026-09/Extension-of-date.pdf", "CORRIGENDUM"),
    ("CORRIGENDUM -1 : DIRECT/DEPARTMENTAL RECRUITMENT FOR THE POST OF HEAD CONSTABLE", "https://x.gov.in/c.pdf", "CORRIGENDUM"),
    ("RESULT FOR RECRUITMENT OF FACULTY(BROAD SPECIALTY) ON CONTRACTUAL BASIS", "https://x.gov.in/r.pdf", "RESULT"),
    ("LIST OF CANDIDATES PROVISIONALLY SELECTED FOR THE POST OF DEAN", "https://x.gov.in/l.pdf", "RESULT"),
    ("Ministry of Steel (Technical Wing) Amendment Recruitment Rules, 2026", "https://steel.gov.in/node/4986", "RULES"),
    ("Invitation of online applications for engagement of 01(one) post of Consultant (Legal)", "https://x.gov.in/s", "VACANCY"),
    ("Annual Report 2025-26", "https://x.gov.in/ar.pdf", "OTHER"),
])
def test_classify_notice(title, url, kind):
    assert classify_notice(title, url) == kind

HOME = "https://ministry.example.gov.in/"
VACANCIES_HTML = """
<nav><a href="/recruitment">Recruitment</a><a href="/tenders">Tenders</a></nav>
<table>
 <tr><td>Filling up of 02 posts of Dy. Dock Master by Deputation</td>
     <td><a href="/files/Dy%20Dock%20Master%20by%20Deputation.pdf">Download(2.43 MB)</a></td></tr>
 <tr><td><a href="/files/tender-12.pdf">Tender for vacancy of canteen contractor</a></td></tr>
 <tr><td><a href="/whats-new/77893">Vacancies (Other Departments) circulated for information</a></td></tr>
 <tr><td><a href="https://elsewhere.example.org/job">Recruitment of Consultants at another organisation</a></td></tr>
 <tr><td><a href="mailto:hr@example.gov.in">Recruitment cell email</a></td></tr>
</table>"""

def test_extract_notices_filters_navigation_tenders_and_offsite_pages():
    notices = extract_notices(VACANCIES_HTML, HOME + "vacancies")
    assert [(n.url, n.document_type) for n in notices] == [
        ("https://ministry.example.gov.in/files/Dy%20Dock%20Master%20by%20Deputation.pdf", "PDF"),
        ("https://ministry.example.gov.in/whats-new/77893", "HTML"),
    ]

def test_recruitment_nav_links_prefers_vacancies():
    html = '<a href="/careers">Careers</a><a href="/about">About</a><a href="/en/vacancies">Vacancies</a><a href="https://other.org/vacancies">Vacancies</a>'
    assert recruitment_nav_links(html, HOME) == [HOME + "en/vacancies", HOME + "careers"]

@pytest.mark.parametrize("fetch,status", [
    (FetchResult(HOME, 403, HOME, "Access Denied"), "ACCESS_RESTRICTED"),
    (FetchResult(HOME + "vacancies", 200, HOME + "Authentication.aspx", "<form>login</form>" * 100), "MANUAL_LOGIN_REQUIRED"),
    (FetchResult(HOME + "vacancies", 200, HOME + "pagenotfound.aspx", "x" * 1000), "HTTP_ERROR"),
    (FetchResult(HOME, 200, HOME, '<html><div id="root"></div><script src="app.js"></script></html>'), "NEEDS_BROWSER"),
    (FetchResult(HOME, 200, HOME, "<p>" + "About the ministry. " * 60 + "</p>"), "NO_NOTICES"),
    (FetchResult(HOME + "vacancies", 200, HOME + "vacancies", VACANCIES_HTML), "OK"),
])
def test_analyze_statuses(fetch, status):
    assert analyze(fetch, "HTTP").status == status

def _portal(pages):
    def handler(request: httpx.Request) -> httpx.Response:
        body = pages.get(str(request.url))
        if body is None:
            return httpx.Response(404, text="not found")
        if isinstance(body, int):
            return httpx.Response(body, text="Access Denied")
        ctype = "text/plain" if request.url.path == "/robots.txt" else "text/html"
        return httpx.Response(200, text=body, headers={"content-type": ctype})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))

@pytest.mark.asyncio
async def test_discover_locates_page_from_homepage_when_configured_url_is_wrong():
    pages = {HOME: '<a href="/en/vacancies">Vacancies</a>', HOME + "en/vacancies": VACANCIES_HTML}
    async with PageFetcher(allow_browser=False, client=_portal(pages)) as f:
        result = await f.discover(HOME + "vacancies")  # 404
    assert result.status == "OK"
    assert result.final_url == HOME + "en/vacancies"
    assert "located via homepage link" in result.detail

@pytest.mark.asyncio
async def test_robots_disallow_is_respected():
    pages = {HOME + "robots.txt": "User-agent: *\nDisallow: /vacancies", HOME + "vacancies": VACANCIES_HTML}
    async with PageFetcher(allow_browser=False, client=_portal(pages)) as f:
        result = await f.probe(HOME + "vacancies")
    assert result.status == "ROBOTS_DISALLOWED" and not result.notices

@pytest.mark.asyncio
async def test_discover_all_persists_notices_idempotently(db_session):
    specs = [
        SourceSpec("TEST-MIN", "Test Ministry", "MINISTRY", HOME + "vacancies"),
        SourceSpec("TEST-BLOCKED", "Blocked Ministry", "MINISTRY", "https://blocked.example.gov.in/vacancies"),
    ]
    pages = {HOME + "vacancies": VACANCIES_HTML, "https://blocked.example.gov.in/vacancies": 403}
    async with PageFetcher(allow_browser=False, client=_portal(pages)) as f:
        first = {s.name: s for s in await discover_all(db_session, f, specs)}
        await db_session.commit()
        second = {s.name: s for s in await discover_all(db_session, f, specs)}
        await db_session.commit()

    assert first["TEST-MIN"].status == "OK" and first["TEST-MIN"].new_notices == 2
    assert first["TEST-MIN"].new_vacancy_notices == 2
    assert second["TEST-MIN"].new_notices == 0 and second["TEST-MIN"].notices_seen == 2
    assert first["TEST-BLOCKED"].status == "ACCESS_RESTRICTED" and first["TEST-BLOCKED"].notices_seen == 0

    assert (await db_session.execute(select(func.count()).select_from(RecruitmentNotice))).scalar() == 2
    notice = (await db_session.execute(select(RecruitmentNotice).where(RecruitmentNotice.document_type == "PDF"))).scalar_one()
    assert notice.notice_kind == "VACANCY"
    assert notice.review_status == "MANUAL_REVIEW"  # no vacancy parser for this source
    assert notice.first_seen_at.utcoffset() is not None

    sources = {s.name: s for s in (await db_session.execute(select(GovernmentSource))).scalars()}
    assert sources["TEST-MIN"].health_status == "HEALTHY"
    assert json.loads(sources["TEST-MIN"].configuration)["resolved_url"] == HOME + "vacancies"
    assert sources["TEST-BLOCKED"].health_status == "ACCESS_RESTRICTED"
    assert sources["TEST-BLOCKED"].last_failure is not None
