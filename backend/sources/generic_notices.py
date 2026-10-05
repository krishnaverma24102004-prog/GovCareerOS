"""
Notice-level discovery that works on any official recruitment page.

Finds links whose text or URL indicates a recruitment notice (advertisement, vacancy
circular, deputation notice, ...). It does not claim to understand vacancy details;
that is left to source-specific parsers or manual review.

Rules: fetch only the source's official domain over https (url_guard), honour robots.txt, identify honestly (no stealth/UA spoofing), verify TLS with
the operating system's trust store (never disabled), and report 401/403 or a login
wall as ACCESS_RESTRICTED / MANUAL_LOGIN_REQUIRED instead of working around it.
"""
import re
import ssl
from dataclasses import dataclass, field
from html import unescape
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple
from urllib.parse import urldefrag, urljoin, urlparse

import httpx
import truststore

from govcareeros.backend.sources.url_guard import (
    POLICY, GuardedTransport, Robots, UrlPolicy, UrlRefused, install, literal_non_public, site,
)

if TYPE_CHECKING:
    from govcareeros.backend.sources.adapters.base import ListingRequest

USER_AGENT = "GovCareerOS/0.1 (personal government-job tracker)"

_RECRUITMENT_RE = re.compile(
    r"recruit|vacanc|advertis|\badvt\b|deputation|walk[\s-]?in|filling\s+up|post\s+of|"
    r"engagement\s+of|appointment\s+of|contractual|consultant|notification\s+for|applications?\s+(?:are\s+)?invited",
    re.I,
)
# Links that match the keywords above but are not job notices
_EXCLUDE_RE = re.compile(r"tender|\bRTI\b|citizen.?charter|grievance|sitemap|screen.?reader|login|feedback|archive", re.I)
_NAV_RE = re.compile(r"vacanc|recruit|career|\bjobs?\b", re.I)
_NAV_PRIORITY = ("vacanc", "recruit", "career", "job")
_ANCHOR_RE = re.compile(r"<a\b[^>]*?href\s*=\s*[\"']([^\"'#][^\"']*)[\"'][^>]*>(.*?)</a>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_SPA_MARKERS = re.compile(r"<div[^>]+id=[\"'](?:root|app|__next)[\"']|ng-version|window\.__NUXT__", re.I)
_ERROR_PAGE_RE = re.compile(r"pagenotfound|customerror|/error\b|404", re.I)
_LOGIN_PAGE_RE = re.compile(r"authentication\.aspx|/login|signin", re.I)

@dataclass
class NoticeLink:
    title: str
    url: str
    document_type: str  # PDF, DOC, HTML

@dataclass
class ProbeResult:
    # OK, NO_NOTICES, NEEDS_BROWSER, ACCESS_RESTRICTED, MANUAL_LOGIN_REQUIRED,
    # ROBOTS_DISALLOWED, URL_REFUSED, TLS_ERROR, UNREACHABLE, HTTP_ERROR
    status: str
    http_status: Optional[int] = None
    final_url: Optional[str] = None
    detail: Optional[str] = None
    fetch_mode: str = "HTTP"
    notices: List[NoticeLink] = field(default_factory=list)

@dataclass
class FetchResult:
    url: str
    status: Optional[int] = None
    final_url: Optional[str] = None
    html: str = ""
    error: Optional[ProbeResult] = None

def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", unescape(_TAG_RE.sub(" ", fragment))).strip()

def _first_line(e: Exception) -> str:
    lines = str(e).strip().splitlines()
    return (lines[0] if lines else "")[:200]

def _doc_type(url: str) -> str:
    path = urlparse(url).path.lower()
    if path.endswith(".pdf"):
        return "PDF"
    if path.endswith((".doc", ".docx")):
        return "DOC"
    return "HTML"

_site = site

def _anchors(html: str, base_url: str) -> List[Tuple[str, str]]:
    out = []
    for href, inner in _ANCHOR_RE.findall(html):
        href = unescape(href.strip())
        if href.lower().startswith(("javascript:", "mailto:", "tel:")):
            continue
        url = urldefrag(urljoin(base_url, href))[0]
        if urlparse(url).scheme in ("http", "https"):
            out.append((_text(inner), url))
    return out

def mentions_recruitment(text: str) -> bool:
    """The recruitment wording extract_notices looks for, for adapters that read titles out of a mixed notice board."""
    return bool(_RECRUITMENT_RE.search(text)) and not _EXCLUDE_RE.search(text)

def extract_notices(html: str, base_url: str, policy: Optional[UrlPolicy] = None) -> List[NoticeLink]:
    seen, notices = set(), []
    base_site = _site(urlparse(base_url).hostname or "")
    policy = policy or UrlPolicy.from_url(base_url)
    for title, url in _anchors(html, base_url):
        # Stored notices are fetched later as official evidence: only from the source's own domain
        if not policy.allows(url):
            continue
        haystack = f"{title} {url}"
        is_doc = _doc_type(url) != "HTML"
        # Documents need a recruitment hint anywhere; HTML links need it in their own text
        # so that site navigation ("Recruitment" menu items) doesn't swamp the results
        if not _RECRUITMENT_RE.search(haystack if is_doc else title) or _EXCLUDE_RE.search(haystack):
            continue
        if not is_doc and (len(title) < 25 or _site(urlparse(url).hostname or "") != base_site):
            continue
        if url in seen:
            continue
        seen.add(url)
        notices.append(NoticeLink(title=title[:500] or url.rsplit("/", 1)[-1], url=url, document_type=_doc_type(url)))
    return notices

def recruitment_nav_links(html: str, base_url: str, limit: int = 4, policy: Optional[UrlPolicy] = None) -> List[str]:
    """Menu links such as 'Vacancies' / 'Recruitment' / 'Careers' on a site's own pages."""
    base_site = _site(urlparse(base_url).hostname or "")
    policy = policy or UrlPolicy.from_url(base_url)
    ranked: Dict[str, int] = {}
    for title, url in _anchors(html, base_url):
        if _doc_type(url) != "HTML" or len(title) > 40 or _EXCLUDE_RE.search(url) or not policy.allows(url):
            continue
        host = _site(urlparse(url).hostname or "")
        if not (host == base_site or host.endswith("." + base_site)):
            continue
        m = _NAV_RE.search(title) or _NAV_RE.search(urlparse(url).path)
        if not m:
            continue
        rank = next(i for i, k in enumerate(_NAV_PRIORITY) if k in m.group(0).lower() or k in title.lower() + url.lower())
        ranked[url] = min(rank, ranked.get(url, 99))
    return sorted(ranked, key=ranked.get)[:limit]

# Language switch on bilingual portals whose homepage opens in Hindi ("/en", hreflang="en", "English")
_A_TAG_RE = re.compile(r"<a\b([^>]*?)href\s*=\s*[\"']([^\"'#][^\"']*)[\"']([^>]*)>(.*?)</a>", re.I | re.S)
_ENGLISH_ATTR_RE = re.compile(r"hreflang\s*=\s*[\"']en[\"']|(?:title|aria-label)\s*=\s*[\"']English[\"']", re.I)

def english_home_links(html: str, base_url: str, policy: Optional[UrlPolicy] = None) -> List[str]:
    policy = policy or UrlPolicy.from_url(base_url)
    out = []
    for before, href, after, inner in _A_TAG_RE.findall(html):
        url = urldefrag(urljoin(base_url, unescape(href.strip())))[0]
        is_english = _ENGLISH_ATTR_RE.search(before + after) or _text(inner).lower() == "english" or \
            re.fullmatch(r"/en/?", urlparse(url).path)
        if is_english and policy.allows(url) and url.rstrip("/") != base_url.rstrip("/") and url not in out:
            out.append(url)
    return out[:2]

_LOC_RE = re.compile(r"<loc>\s*(?:<!\[CDATA\[)?\s*(.*?)\s*(?:\]\]>)?\s*</loc>", re.I | re.S)
_SITEMAP_INDEX_RE = re.compile(r"<sitemapindex\b", re.I)
# Listing pages, not individual notices ("/vacancies", "/en/recruitment", "/careers-notices1.aspx")
_SITEMAP_PAGE_RE = re.compile(r"vacanc|recruit|career|\bjobs?\b|deputation", re.I)

def sitemap_locs(xml: str) -> Tuple[List[str], bool]:
    """(<loc> URLs, whether the document is a sitemap index of further sitemaps)."""
    return [unescape(u) for u in _LOC_RE.findall(xml)], bool(_SITEMAP_INDEX_RE.search(xml))

def sitemap_recruitment_urls(xml: str, policy: UrlPolicy, limit: int = 12) -> List[str]:
    """Recruitment listing pages named in a sitemap, on the source's own domain; shortest paths first."""
    ranked: Dict[str, Tuple[int, int, int]] = {}
    for url in sitemap_locs(xml)[0]:
        url = urldefrag(url)[0]
        path = urlparse(url).path
        if not policy.allows(url) or _doc_type(url) != "HTML" or _EXCLUDE_RE.search(url):
            continue
        m = _SITEMAP_PAGE_RE.search(path)
        if not m:
            continue
        rank = next((i for i, k in enumerate(_NAV_PRIORITY) if k in m.group(0).lower()), len(_NAV_PRIORITY))
        ranked[url] = min((rank, path.count("/"), len(path)), ranked.get(url, (99, 0, 0)))
    return sorted(ranked, key=ranked.get)[:limit]

def analyze(fetch: FetchResult, fetch_mode: str, policy: Optional[UrlPolicy] = None) -> ProbeResult:
    if fetch.error:
        fetch.error.fetch_mode = fetch_mode
        return fetch.error
    final = fetch.final_url or fetch.url
    if fetch.status in (401, 403):
        return ProbeResult("ACCESS_RESTRICTED", fetch.status, final, "server refused access", fetch_mode)
    if fetch.status and fetch.status >= 400:
        return ProbeResult("HTTP_ERROR", fetch.status, final, None, fetch_mode)
    path = urlparse(final).path + "?" + urlparse(final).query
    if _LOGIN_PAGE_RE.search(path) and not _LOGIN_PAGE_RE.search(urlparse(fetch.url).path):
        return ProbeResult("MANUAL_LOGIN_REQUIRED", fetch.status, final, "redirected to a login page", fetch_mode)
    if _ERROR_PAGE_RE.search(path) and not _ERROR_PAGE_RE.search(urlparse(fetch.url).path):
        return ProbeResult("HTTP_ERROR", fetch.status, final, "redirected to an error page", fetch_mode)

    notices = extract_notices(fetch.html, final, policy)
    if notices:
        return ProbeResult("OK", fetch.status, final, None, fetch_mode, notices)
    visible = _text(re.sub(r"<script.*?</script>|<style.*?</style>", " ", fetch.html, flags=re.S | re.I))
    if fetch_mode == "HTTP" and (_SPA_MARKERS.search(fetch.html) or len(visible) < 500):
        return ProbeResult("NEEDS_BROWSER", fetch.status, final, "page content is rendered by JavaScript", fetch_mode)
    return ProbeResult("NO_NOTICES", fetch.status, final, "page loaded but no recruitment links found", fetch_mode)

class PageFetcher:
    """HTTP first; a real (headless, unmodified) Chromium only for JavaScript-rendered pages.

    Each call runs under the source's UrlPolicy (default: derived from the URL it starts from),
    and the client refuses any request or redirect hop outside it.
    """

    def __init__(self, allow_browser: bool = True, browser_timeout_ms: int = 45000, client: Optional[httpx.AsyncClient] = None):
        self.allow_browser = allow_browser
        self.browser_timeout_ms = browser_timeout_ms
        self._pw = None
        self._browser = None
        self.client = install(client or httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT}, timeout=25, follow_redirects=True,
            # OS trust store: handles servers that omit intermediate certificates, still fully verified
            transport=GuardedTransport(verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)),
        ))
        self._robots = Robots(self.client, USER_AGENT)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.client.aclose()
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    async def robots_allows(self, url: str, policy: Optional[UrlPolicy] = None) -> bool:
        return await self._robots.allows(url, policy or UrlPolicy.from_url(url))

    async def get(self, url: str, policy: UrlPolicy) -> httpx.Response:
        """One fetch under the source's policy; raises UrlRefused outside it (including on redirect)."""
        return await self.client.get(url, extensions={POLICY: policy})

    async def _precheck(self, url: str, policy: UrlPolicy) -> Optional[FetchResult]:
        try:
            policy.check(url)
            if await self.robots_allows(url, policy):
                return None
        except UrlRefused as e:
            return FetchResult(url, error=ProbeResult("URL_REFUSED", detail=_first_line(e)))
        return FetchResult(url, error=ProbeResult("ROBOTS_DISALLOWED", detail="robots.txt disallows this page"))

    async def fetch_http(self, url: str, policy: Optional[UrlPolicy] = None) -> FetchResult:
        return await self._fetch("GET", url, policy or UrlPolicy.from_url(url))

    async def fetch_listing(self, request: "ListingRequest", policy: Optional[UrlPolicy] = None) -> FetchResult:
        """An adapter's declared JSON listing endpoint (adapters/base.py): GET, or POST with its fixed form body.
        Same checks as a page: the source policy, robots.txt, and the guarded client on every redirect hop."""
        if request.method not in ("GET", "POST"):
            return FetchResult(request.url, error=ProbeResult("URL_REFUSED", detail=f"method {request.method!r} not allowed"))
        # multipart/form-data fields, as the source's own page sends them
        files = {k: (None, v) for k, v in request.form.items()} if request.method == "POST" else None
        return await self._fetch(request.method, request.url, policy or UrlPolicy.from_url(request.url), files=files)

    async def _fetch(self, method: str, url: str, policy: UrlPolicy, **kwargs) -> FetchResult:
        refused = await self._precheck(url, policy)
        if refused:
            return refused
        try:
            resp = await self.client.request(method, url, extensions={POLICY: policy}, **kwargs)
        except UrlRefused as e:
            return FetchResult(url, error=ProbeResult("URL_REFUSED", detail=_first_line(e)))
        except (ssl.SSLError, httpx.ConnectError) as e:
            msg = str(e)
            status = "TLS_ERROR" if "CERTIFICATE" in msg.upper() or "SSL" in msg.upper() else "UNREACHABLE"
            return FetchResult(url, error=ProbeResult(status, detail=f"{type(e).__name__}: {_first_line(e)}"))
        except httpx.HTTPError as e:
            return FetchResult(url, error=ProbeResult("UNREACHABLE", detail=f"{type(e).__name__}: {_first_line(e)}"))
        return FetchResult(url, resp.status_code, str(resp.url), resp.text)

    async def fetch_browser(self, url: str, policy: Optional[UrlPolicy] = None) -> FetchResult:
        policy = policy or UrlPolicy.from_url(url)
        refused = await self._precheck(url, policy)
        if refused:
            return refused
        if self._browser is None:
            from playwright.async_api import async_playwright
            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(headless=True)
        context = await self._browser.new_context()

        async def gate(route):
            req = route.request
            # Navigations (and their redirects) follow the source policy; page assets may come from
            # CDNs, but never from a literal non-public address
            if (req.is_navigation_request() and not policy.allows(req.url)) or literal_non_public(req.url):
                await route.abort("blockedbyclient")
            else:
                await route.continue_()

        await context.route("**/*", gate)
        page = await context.new_page()
        try:
            resp = await page.goto(url, wait_until="networkidle", timeout=self.browser_timeout_ms)
            if not policy.allows(page.url):
                return FetchResult(url, error=ProbeResult("URL_REFUSED", detail=f"browser ended on {page.url}"))
            return FetchResult(url, resp.status if resp else None, page.url, await page.content())
        except Exception as e:
            return FetchResult(url, error=ProbeResult("UNREACHABLE", detail=f"browser: {type(e).__name__}: {_first_line(e)}"))
        finally:
            await context.close()

    async def probe(self, url: str, policy: Optional[UrlPolicy] = None, browser: bool = False) -> ProbeResult:
        """`browser`: the source is known to fill its listing with JavaScript, so render it straight away."""
        policy = policy or UrlPolicy.from_url(url)
        if browser and self.allow_browser:
            return analyze(await self.fetch_browser(url, policy), "BROWSER", policy)
        result = analyze(await self.fetch_http(url, policy), "HTTP", policy)
        if browser and result.status == "NO_NOTICES":
            # Browser disabled for this run: say what is missing rather than report an empty page
            result.status, result.detail = "NEEDS_BROWSER", "source is configured for browser fetch"
        if result.status == "NEEDS_BROWSER" and self.allow_browser:
            result = analyze(await self.fetch_browser(url, policy), "BROWSER", policy)
        return result

    async def sitemap_candidates(self, home: str, policy: UrlPolicy, max_sitemaps: int = 6, limit: int = 12) -> List[str]:
        """Recruitment pages listed in the site's sitemaps (robots.txt `Sitemap:` lines, else /sitemap.xml)."""
        try:
            queue = await self._robots.sitemaps(home, policy) or [urljoin(home, "/sitemap.xml")]
        except UrlRefused:
            return []
        seen, found = set(), []
        while queue and len(seen) < max_sitemaps and len(found) < limit:
            sm = queue.pop(0)
            if sm in seen or sm.lower().endswith(".gz"):
                continue
            seen.add(sm)
            fetched = await self.fetch_http(sm, policy)  # policy + robots checked like any page
            if fetched.error or fetched.status != 200 or "<loc>" not in fetched.html.lower():
                continue
            locs, is_index = sitemap_locs(fetched.html)
            if is_index:
                # Page sitemaps before post/media ones: listing pages live there
                queue += sorted((u for u in locs if policy.allows(u)), key=lambda u: ("page" not in u.lower(), len(u)))
                continue
            found += [u for u in sitemap_recruitment_urls(fetched.html, policy) if u not in found]
        return found[:limit]

    async def discover(self, url: str, policy: Optional[UrlPolicy] = None, browser: bool = False) -> ProbeResult:
        """Probe the configured page; if it's wrong/missing, look for the site's own Vacancies/Recruitment page:
        homepage menu, the English homepage's menu (bilingual portals open in Hindi), the sitemap, then the
        JavaScript-rendered homepage menu."""
        policy = policy or UrlPolicy.from_url(url)
        result = await self.probe(url, policy, browser)
        if result.status not in ("HTTP_ERROR", "NO_NOTICES"):
            return result

        parts = urlparse(result.final_url or url)
        home = f"{parts.scheme}://{parts.netloc}/"
        tried = {url.rstrip("/")}

        async def first_ok(candidates: List[str], how: str) -> Optional[ProbeResult]:
            for candidate in candidates:
                if candidate.rstrip("/") in tried:
                    continue
                tried.add(candidate.rstrip("/"))
                located = await self.probe(candidate, policy)
                if located.status == "OK":
                    located.detail = f"located via {how} (configured URL: {result.status} {result.http_status or ''})".strip()
                    return located
            return None

        home_fetch = await self.fetch_http(home, policy)
        if not home_fetch.error:
            home_url = home_fetch.final_url or home
            located = await first_ok(recruitment_nav_links(home_fetch.html, home_url, policy=policy), "homepage link")
            if located:
                return located
            for english in english_home_links(home_fetch.html, home_url, policy):
                en_fetch = await self.fetch_http(english, policy)
                if not en_fetch.error:
                    located = await first_ok(recruitment_nav_links(en_fetch.html, en_fetch.final_url or english, policy=policy),
                                             "English homepage link")
                    if located:
                        return located
        located = await first_ok(await self.sitemap_candidates(home, policy), "sitemap")
        if located:
            return located
        if self.allow_browser and not home_fetch.error:
            rendered = await self.fetch_browser(home, policy)
            if not rendered.error:
                located = await first_ok(recruitment_nav_links(rendered.html, rendered.final_url or home, policy=policy),
                                         "rendered homepage link")
                if located:
                    return located
        return result
