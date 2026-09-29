"""
UPSC public recruitment advertisements (document fallback path).

Uses only the public listing page and the public PDF links on it. On 401/403 the
source is reported ACCESS_RESTRICTED; no attempt is made to get around it.
"""
import re
from dataclasses import dataclass
from typing import List
from urllib.parse import urljoin

import httpx

LISTING_URL = "https://www.upsc.gov.in/recruitment/recruitment-advertisement"
# Identifies the client honestly; no browser impersonation
USER_AGENT = "GovCareerOS/0.1 (personal government-job tracker)"

UPSC_SOURCE = {
    "name": "UPSC",
    "organization": "Union Public Service Commission",
    "category": "CENTRAL_RECRUITMENT_COMMISSION",
    "official_domain": "upsc.gov.in",
    "recruitment_url": LISTING_URL,
    "source_type": "DOCUMENT",
    "adapter_name": "upsc_advertisement",
}

_ADVT_LINK_RE = re.compile(r'href="([^"]*AdvtNo-[^"]+?\.pdf)"', re.I)

class AccessRestricted(Exception):
    """The official site refused access (401/403). Reported, never bypassed."""

@dataclass
class AdvertisementLink:
    url: str

def _check(response: httpx.Response) -> None:
    if response.status_code in (401, 403):
        raise AccessRestricted(f"{response.request.url} returned {response.status_code}")
    response.raise_for_status()

def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=60, follow_redirects=True)

async def discover_advertisements(client: httpx.AsyncClient) -> List[AdvertisementLink]:
    response = await client.get(LISTING_URL)
    _check(response)
    return parse_listing(response.text, str(response.url))

def parse_listing(html: str, base_url: str = LISTING_URL) -> List[AdvertisementLink]:
    seen, links = set(), []
    for href in _ADVT_LINK_RE.findall(html):
        url = urljoin(base_url, href)
        # English advertisements only; Hindi versions carry the same vacancies
        if "-Engl-" not in url or url in seen:
            continue
        seen.add(url)
        links.append(AdvertisementLink(url=url))
    return links

@dataclass
class DownloadedDocument:
    url: str
    status: int
    content_type: str
    body: bytes

async def download_pdf(client: httpx.AsyncClient, url: str) -> DownloadedDocument:
    response = await client.get(url)
    _check(response)
    content_type = response.headers.get("content-type", "")
    if "pdf" not in content_type.lower() and not response.content.startswith(b"%PDF"):
        raise ValueError(f"{url} is not a PDF (content-type {content_type!r})")
    return DownloadedDocument(url=str(response.url), status=response.status_code, content_type=content_type, body=response.content)

HOME_URL = "https://www.upsc.gov.in/"
# PDFs that modify an earlier advertisement; advertisements themselves (AdvtNo-*) are handled separately
_NOTICE_HINT_RE = re.compile(r"advt|vacanc|corrigend|addend|clarif|extension|cancel|notice", re.I)
_ANCHOR_RE = re.compile(r'<a[^>]+href="([^"]+\.pdf)"[^>]*>(.*?)</a>', re.I | re.S)

@dataclass
class NoticeLink:
    url: str
    title: str

def parse_notice_links(html: str, base_url: str) -> List[NoticeLink]:
    links, seen = [], set()
    for href, inner in _ANCHOR_RE.findall(html):
        title = re.sub(r"<[^>]+>|\s+", " ", inner).strip()
        url = urljoin(base_url, href)
        name = url.rsplit("/", 1)[-1]
        if "AdvtNo-" in name or url in seen or not _NOTICE_HINT_RE.search(f"{title} {name}"):
            continue
        seen.add(url)
        links.append(NoticeLink(url=url, title=title))
    return links

async def discover_notices(client: httpx.AsyncClient) -> List[NoticeLink]:
    links: List[NoticeLink] = []
    for page in (HOME_URL, LISTING_URL):
        response = await client.get(page)
        _check(response)
        links += [l for l in parse_notice_links(response.text, str(response.url)) if l.url not in {x.url for x in links}]
    return links
