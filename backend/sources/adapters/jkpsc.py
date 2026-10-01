"""
Listing adapter for the Jammu & Kashmir Public Service Commission (https://jkpsc.nic.in/).

The commission publishes its notices on the homepage in two lists:
  * the scrolling ticker (`<div id="notification">` -> `<marquee>` -> `<ul>`)
  * "Whats New...." (`<div id="whatsNew">` -> `<ul class="notificationnews myBox">`)
(an older 2017 list inside an HTML comment is not shown by the page and is ignored).

Each notice is rendered as two consecutive anchors:
  * notices served by the download handler: a visible anchor
        <a href='Pdf/Downloader1.ashx?nid=18044&type=n' visible='true'>Title</a>
    followed by a hidden copy that carries the date and no link
        <a href='' visible='false'>30/09/2026 Title</a>
  * notices linked directly (PDF/HTML): a placeholder anchor with an empty nid
        <a href='Pdf/Downloader1.ashx?nid=&type=n' visible='false'> </a>
    followed by the real link whose text starts with the date
        <a href='http://jkpsc.nic.in/result/...html'>08/07/2026 Download Admit Card ...</a>

The dd/mm/yyyy date in front of a notice is the date the page shows for it; it is read as
`published_on`. The date is only attached when the hidden copy's title matches the linked title.
The page does not show closing dates, so `last_date` is never set (it stays None).
"""
import re
from datetime import date
from html import unescape
from typing import List, Optional, Tuple
from urllib.parse import urljoin, urlparse, urlunparse

from govcareeros.backend.sources.adapters.base import ListingRow
from govcareeros.backend.sources.url_guard import site

SOURCE = "JKPSC"

_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_SCRIPT_RE = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)
# The two notice lists on the homepage (the first </ul> after each opening div closes its list)
_BLOCK_RES = (
    re.compile(r"<div\b[^>]*\bid\s*=\s*[\"']whatsNew[\"'][^>]*>(.*?)</ul>", re.S | re.I),
    re.compile(r"<div\b[^>]*\bid\s*=\s*[\"']notification[\"'][^>]*>(.*?)</ul>", re.S | re.I),
)
_ANCHOR_RE = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.S | re.I)
_HREF_RE = re.compile(r"\bhref\s*=\s*([\"'])(.*?)\1", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_DATE_PREFIX_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})\s+")
_PLACEHOLDER_RE = re.compile(r"[?&]nid=(?:&|$)", re.I)       # Downloader1.ashx?nid=&type=n
_NAV_TEXT_RE = re.compile(r"^(?:view\s+more|more|archive|click\s+here)$", re.I)

# "notified vide Notification No 03-PSC DR-P of 2025" (also the page's misspelling "Notficaion")
_ADVT_RE = re.compile(
    r"\b(?:Not\w{0,6}ca\w{0,3}on|Advt\.?|Advertisement)\s+No\.?\s*:?\s*(\d{1,4}\s*-\s*[A-Za-z][\w\- ]*?\s+of\s+\d{4})",
    re.I,
)
# "post of Medical Officer (Allopathic) in Health ..." -> "Medical Officer (Allopathic)"
_POST_RE = re.compile(
    r"\bposts?\s+of\s+(.+?)(?=\s+(?:in|under|notified|vide|at)\b|\.\s|\.$|\s+-\s|$)",
    re.I,
)
# Labels the page itself puts in the notice text
_KIND_RES = (
    ("ADMIT_CARD", re.compile(r"\badmit\s*cards?\b", re.I)),
    ("CORRIGENDUM", re.compile(r"\bcorrigendum\b|\baddendum\b|\bextension\s+of\s+(?:last\s+)?date\b", re.I)),
    ("RESULT", re.compile(r"\bresults?\b|\bmarks\s*cards?\b|\bselection\s+list\b|\bprovisional\s+selection\b", re.I)),
)


def _clean(fragment: str) -> str:
    return re.sub(r"\s+", " ", unescape(_TAG_RE.sub(" ", fragment))).strip()


def _href(attrs: str) -> str:
    m = _HREF_RE.search(attrs)
    return unescape(m.group(2)).strip() if m else ""


def _same_title(a: str, b: str) -> bool:
    return a.casefold() == b.casefold()


def _absolute(href: str, base_url: str) -> Optional[str]:
    """Absolute URL on the commission's own domain. The page links some files as http://jkpsc.nic.in/...;
    the same host serves them over https, which is what the source's fetch policy allows."""
    url = urljoin(base_url, href)
    parts = urlparse(url)
    base = urlparse(base_url)
    host, base_site = site(parts.hostname or ""), site(base.hostname or "")
    if not host or not (host == base_site or host.endswith("." + base_site)):
        return None
    if parts.scheme == "http" and base.scheme == "https":
        parts = parts._replace(scheme="https")
    elif parts.scheme not in ("http", "https"):
        return None
    return urlunparse(parts._replace(fragment=""))


def _row(title: str, href: str, base_url: str, date_text: Optional[str]) -> Optional[ListingRow]:
    url = _absolute(href, base_url)
    if not url or not title:
        return None
    row = ListingRow(title=title, url=url, evidence={"title": title, "url": href})

    if date_text:
        d, m, y = (int(x) for x in date_text.split("/"))
        try:
            row.published_on = date(y, m, d)
            row.evidence["published_on"] = date_text
        except ValueError:
            pass

    advt = _ADVT_RE.search(title)
    if advt:
        row.advertisement_number = advt.group(1).strip()
        row.evidence["advertisement_number"] = advt.group(0).strip()

    post = _POST_RE.search(title)
    if post:
        name = post.group(1).strip().rstrip(",;:-").strip()
        if name:
            row.post_names = [name]
            row.evidence["post_names"] = post.group(0).strip().rstrip(",;:-").strip()

    for kind, pattern in _KIND_RES:
        label = pattern.search(title)
        if label:
            row.kind_hint = kind
            row.evidence["kind_hint"] = label.group(0)
            break
    return row


def _parse_block(block: str, base_url: str) -> List[ListingRow]:
    rows: List[ListingRow] = []
    pending: Optional[Tuple[str, str]] = None  # (title, href) of a linked notice awaiting its dated copy

    def flush(date_text: Optional[str] = None) -> None:
        nonlocal pending
        if pending:
            row = _row(pending[0], pending[1], base_url, date_text)
            if row:
                rows.append(row)
        pending = None

    for attrs, inner in _ANCHOR_RE.findall(block):
        href, text = _href(attrs), _clean(inner)
        dated = _DATE_PREFIX_RE.match(text)
        if dated:
            title, date_text = text[dated.end():].strip(), dated.group(0).strip()
            if href and not _PLACEHOLDER_RE.search(href):
                flush()
                row = _row(title, href, base_url, date_text)   # directly linked notice
                if row:
                    rows.append(row)
            elif pending and _same_title(pending[0], title):
                flush(date_text)                                # hidden dated copy of the linked notice
            # a dated text with no link and no matching notice has no URL to report
            continue
        flush()
        if not href or not text or _PLACEHOLDER_RE.search(href) or _NAV_TEXT_RE.match(text):
            continue
        if href.lower().startswith(("javascript:", "mailto:", "tel:", "#", "/#")):
            continue
        pending = (text, href)
    flush()
    return rows


def parse_listing(html: str, base_url: str) -> List[ListingRow]:
    html = _SCRIPT_RE.sub(" ", _COMMENT_RE.sub(" ", html))
    rows: List[ListingRow] = []
    seen = set()
    for block_re in _BLOCK_RES:
        for block in block_re.findall(html):
            for row in _parse_block(block, base_url):
                if row.url in seen:
                    continue
                seen.add(row.url)
                rows.append(row)
    return rows
