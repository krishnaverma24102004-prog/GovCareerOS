"""
Staff Selection Commission (ssc.gov.in): notice board through the site's own public JSON API.

ssc.gov.in is an Angular app whose HTML carries no notice links. Watching the XHR/fetch traffic of
https://ssc.gov.in/ and https://ssc.gov.in/home/notice-board in headless Chromium on 2026-09-30
(`observe_json_endpoints`) showed the app reads these public, no-login JSON endpoints:

  GET /api/general-website/portal/notice-boards?page=N&limit=10&...   notice board, newest first
      data[]: {id, headline, examId, createdAt, attachments[{fileName, type, size, path}]}
  GET /api/admin/5.1/liveExams?examId=                                 exams whose application window is open
  GET /api/attachment/<attachment path>                                the attachment itself (PDF)

The server caps `limit` at 10. robots.txt answered 404 (nothing disallowed) but is still checked on
every run. Requests go only to ssc.gov.in over https through the url_guard policy, with the honest
GovCareerOS user agent; 401/403 is reported as ACCESS_RESTRICTED and never worked around.
If the API disappears, `probe` falls back to rendering the notice-board page and reading its links.
"""
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlencode

from govcareeros.backend.sources.generic_notices import NoticeLink, ProbeResult, extract_notices
from govcareeros.backend.sources.notice_classifier import classify_notice
from govcareeros.backend.sources.url_guard import UrlPolicy, UrlRefused, literal_non_public

HOME_URL = "https://ssc.gov.in/"
NOTICE_BOARD_PAGE = "https://ssc.gov.in/home/notice-board"
API_BASE = "https://ssc.gov.in/api/"
LIVE_EXAMS_URL = API_BASE + "admin/5.1/liveExams?examId="
ATTACHMENT_BASE = API_BASE + "attachment/"
PAGE_SIZE = 10  # server-side maximum

SSC_SOURCE = {
    "name": "SSC",
    "organization": "Staff Selection Commission",
    "category": "RECRUITMENT_COMMISSION",
    "official_domain": "ssc.gov.in",
    "recruitment_url": NOTICE_BOARD_PAGE,
    "source_type": "API",
    "adapter_name": "ssc",
}
SSC_POLICY = UrlPolicy.for_source("ssc.gov.in", NOTICE_BOARD_PAGE)

class AccessRestricted(Exception):
    """ssc.gov.in refused access (401/403). Reported, never bypassed."""

class RobotsDisallowed(Exception):
    pass

def notice_board_url(page: int = 1) -> str:
    params = {"page": page, "limit": PAGE_SIZE, "contentType": "notice-boards", "key": "createdAt", "order": "DESC",
              "isAttachment": "true", "language": "english",
              "attributes": "id,headline,examId,contentType,redirectUrl,startDate,endDate,language,createdAt"}
    return API_BASE + "general-website/portal/notice-boards?" + urlencode(params, safe=",")

def attachment_url(path: str) -> str:
    # Paths come back Windows-style ("uploads\\masterData\\NoticeBoards\\x.pdf"); file names may contain spaces
    return ATTACHMENT_BASE + quote(path.replace("\\", "/").lstrip("/"), safe="/")

# ---------- classification ----------

_EXAM_NOTICE_RE = re.compile(r"^\s*Notice\s+of\s+(?:the\s+)?.{5,250}?Examination", re.I)
_ADMIT_RE = re.compile(r"admission\s+certificate|admit\s+card|e-?admit|city\s+of\s+examination|scribe.s\s+entry\s+pass", re.I)
_ANSWER_KEY_RE = re.compile(r"answer\s+keys?", re.I)
_SCHEDULE_RE = re.compile(r"schedule\s+of\s+(?:the\s+)?examinations?|revised\s+dates|examination\s+calendar", re.I)
_ALLOCATION_RE = re.compile(r"recommended\s+candidates|\bFRTA\b|tentative\s+allocation|final\s+marks", re.I)
_CHANGE_RE = re.compile(r"addendum|corrigendum|re-?opening\s+of\s+window|extension\s+of|cancell?ation|postpone", re.I)

def classify(title: str, url: str = "") -> str:
    """EXAM_NOTICE (the notification of an examination), CORRIGENDUM, ADMIT_CARD, ANSWER_KEY, SCHEDULE, RESULT
    (incl. allocation to posts), then the generic notice_classifier kinds. Order matters: "Addendum to Notice of ... Examination" is a change."""
    if _CHANGE_RE.search(title):
        return "CORRIGENDUM"
    if _EXAM_NOTICE_RE.search(title):
        return "EXAM_NOTICE"
    if _ADMIT_RE.search(title):
        return "ADMIT_CARD"
    if _ANSWER_KEY_RE.search(title):
        return "ANSWER_KEY"
    if _SCHEDULE_RE.search(title):
        return "SCHEDULE"
    if _ALLOCATION_RE.search(title):
        return "RESULT"
    kind = classify_notice(title, url)
    # "Tentative Vacancy of <exam>" lists an exam's vacancies; it is not a job notice of its own
    return "OTHER" if kind == "VACANCY" and re.search(r"(?:tentative|final)\s+vacanc", title, re.I) else kind

# ---------- notice board ----------

@dataclass
class SscNotice:
    id: str
    title: str
    url: str
    document_type: str
    published_at: Optional[datetime]
    exam_id: Optional[str]
    kind: str
    size: Optional[int] = None

def _doc_type(content_type: str, name: str) -> str:
    if "pdf" in (content_type or "").lower() or name.lower().endswith(".pdf"):
        return "PDF"
    return "DOC" if name.lower().endswith((".doc", ".docx")) else "HTML"

def parse_notice_board(data: Dict[str, Any]) -> List[SscNotice]:
    """One SscNotice per attachment (a board item can carry an English and a Hindi file)."""
    if str(data.get("statusCode")) != "200" or not isinstance(data.get("data"), list):
        raise ValueError(f"unexpected notice-board response: {str(data)[:200]}")
    notices, seen = [], set()
    for item in data["data"]:
        title = re.sub(r"\s+", " ", item.get("headline") or "").strip()
        published = datetime.fromisoformat(item["createdAt"].replace("Z", "+00:00")) if item.get("createdAt") else None
        for att in item.get("attachments") or []:
            if not att.get("path"):
                continue
            url = attachment_url(att["path"])
            if url in seen or not SSC_POLICY.allows(url):
                continue
            seen.add(url)
            notices.append(SscNotice(item["id"], title or att.get("fileName", url), url, _doc_type(att.get("type"), att.get("fileName", "")),
                                     published, item.get("examId"), classify(title, url), att.get("size")))
    return notices

@dataclass
class LiveExam:
    exam_code: str
    name: str
    application_start: Optional[str]
    application_end: Optional[str]

def parse_live_exams(data: Dict[str, Any]) -> List[LiveExam]:
    """Exams with an open application window, as the site lists them. A discovery hint only: exam facts come from the notice PDF."""
    return [LiveExam(e.get("examCode"), (e.get("examNames") or {}).get("examName") or e.get("examName"),
                     e.get("applicationStartDate"), e.get("applicationEndDate")) for e in data.get("data") or []]

# ---------- fetching (PageFetcher: robots.txt + url_guard on every request and redirect) ----------

async def _get(fetcher, url: str):
    if not await fetcher.robots_allows(url, SSC_POLICY):
        raise RobotsDisallowed(f"robots.txt disallows {url}")
    resp = await fetcher.get(url, SSC_POLICY)
    if resp.status_code in (401, 403):
        raise AccessRestricted(f"{url} returned {resp.status_code}")
    resp.raise_for_status()
    return resp

async def fetch_json(fetcher, url: str) -> Dict[str, Any]:
    resp = await _get(fetcher, url)
    if "json" not in resp.headers.get("content-type", ""):
        raise ValueError(f"{url} did not return JSON ({resp.headers.get('content-type')!r})")
    return resp.json()

async def fetch_notices(fetcher, pages: int = 1, first_page: int = 1) -> List[SscNotice]:
    notices: List[SscNotice] = []
    for page in range(first_page, first_page + pages):
        batch = parse_notice_board(await fetch_json(fetcher, notice_board_url(page)))
        notices += [n for n in batch if n.url not in {x.url for x in notices}]
        if len(batch) < PAGE_SIZE:
            break
    return notices

async def download_pdf(fetcher, url: str) -> bytes:
    resp = await _get(fetcher, url)
    if not resp.content.startswith(b"%PDF"):
        raise ValueError(f"{url} is not a PDF (content-type {resp.headers.get('content-type')!r})")
    return resp.content

async def probe(fetcher, pages: int = 1, allow_browser: bool = True, first_page: int = 1) -> ProbeResult:
    """Notice-level discovery for source_discovery: the JSON API first, the rendered page as fallback."""
    try:
        notices = await fetch_notices(fetcher, pages, first_page)
        links = [NoticeLink(n.title, n.url, n.document_type) for n in notices]
        return ProbeResult("OK" if links else "NO_NOTICES", 200, notice_board_url(first_page), None, "API", links)
    except AccessRestricted as e:
        return ProbeResult("ACCESS_RESTRICTED", detail=str(e), fetch_mode="API")
    except RobotsDisallowed as e:
        return ProbeResult("ROBOTS_DISALLOWED", detail=str(e), fetch_mode="API")
    except UrlRefused as e:
        return ProbeResult("URL_REFUSED", detail=str(e)[:200], fetch_mode="API")
    except Exception as e:
        if not allow_browser:
            return ProbeResult("UNREACHABLE", detail=f"API: {type(e).__name__}: {str(e)[:200]}", fetch_mode="API")
    rendered = await fetcher.fetch_browser(NOTICE_BOARD_PAGE, SSC_POLICY)
    if rendered.error:
        return rendered.error
    links = extract_notices(rendered.html, rendered.final_url or NOTICE_BOARD_PAGE, SSC_POLICY)
    return ProbeResult("OK" if links else "NO_NOTICES", rendered.status, rendered.final_url, "JSON API failed; rendered page used", "BROWSER", links)

# ---------- endpoint discovery ----------

@dataclass
class ObservedEndpoint:
    method: str
    url: str
    status: int
    content_type: str

async def observe_json_endpoints(page_url: str = HOME_URL, policy: UrlPolicy = SSC_POLICY, timeout_ms: int = 45000) -> List[ObservedEndpoint]:
    """Load a public page in unmodified headless Chromium and list the JSON calls it makes to the source's own domain.

    Same gate as PageFetcher.fetch_browser: robots.txt is checked first, navigations must stay inside the policy,
    nothing goes to a non-public address. No login, no cookies carried over, no user-agent change.
    """
    from playwright.async_api import async_playwright
    from govcareeros.backend.sources.generic_notices import PageFetcher

    policy.check(page_url)
    # robots.txt is honoured here too, exactly as for every other SSC fetch
    async with PageFetcher(allow_browser=False) as fetcher:
        if not await fetcher.robots_allows(page_url, policy):
            raise RobotsDisallowed(f"robots.txt disallows {page_url}")
    seen: List[ObservedEndpoint] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context()

        async def gate(route):
            req = route.request
            if (req.is_navigation_request() and not policy.allows(req.url)) or literal_non_public(req.url):
                await route.abort("blockedbyclient")
            else:
                await route.continue_()

        def record(resp):
            req = resp.request
            if req.resource_type in ("xhr", "fetch") and policy.allows(resp.url) and "json" in (resp.headers.get("content-type") or ""):
                seen.append(ObservedEndpoint(req.method, resp.url, resp.status, resp.headers.get("content-type", "")))

        await context.route("**/*", gate)
        context.on("response", record)
        try:
            page = await context.new_page()
            await page.goto(page_url, wait_until="networkidle", timeout=timeout_ms)
        finally:
            await context.close()
            await browser.close()
    return seen
