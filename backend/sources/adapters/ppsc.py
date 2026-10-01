"""
Listing adapter for the Punjab Public Service Commission (https://ppsc.gov.in/).

The only listing reachable without a navigation session is the "Announcements" panel on the home
page: a scrolling <ul class="list-group"> of <li class="list-group-item-simpletab"> items, one link
each, e.g.

    FINAL RESULT FOR RECRUITMENT TO 101 POSTS OF HORTICULTURE DEVELOPMENT OFFICER IN THE DEPARTMENT
    OF HORTICULTURE GOVERNMENT OF PUNJAB UPLOADED ON 28-09-2026

The site's inner pages (OPEN ADVERTISEMENT, LATEST NOTIFICATIONS, Announcements "View All", and the
notice pages themselves) answer a direct request with a redirect to an error page ("Session has
Expired ...") and then back to the home page, so no LISTING_URLS are declared.

Everything is read from the link text itself:
  - published_on: only from an explicit "Uploaded on dd-mm-yyyy" / "Updated dd-mm-yyyy" at the end of
    the text. Other dates in a title (exam dates "To Be Held On ...", court order dates, an unlabelled
    trailing date) are not publication dates and are left alone.
  - last_date: the listing never states a closing date, so it is never filled.
  - advertisement_number: "Advt No 202270", "ADVERTISEMENT NOS 202229 TO 202236", ...
  - post_names: the phrase after "<n> POSTS OF" / "THE POSTS OF", as written.
  - kind_hint: only when the text says so (result, corrigendum/addendum/extension of last date,
    admit card, answer key, or a recruitment / advertisement heading).
Tender and quotation notices on the same board are skipped (not recruitment notices).
"""
import re
from datetime import date
from html import unescape
from html.parser import HTMLParser
from typing import List, Optional, Tuple
from urllib.parse import urljoin, urlparse

from govcareeros.backend.sources.adapters.base import ListingRow

SOURCE = "PPSC"

_LISTING_PANEL = "announcements"
_ITEM_CLASS = "list-group-item-simpletab"

_SKIP_RE = re.compile(r"\btender\b|\bquotations?\b", re.I)

# "UPLOADED ON 28-09-2026", "Updated 26-07-2024", "punjabuploaded on 26-02-2024",
# "updated at 6 P M on 21-10-2024", "Uploaded On The Website On 23-12-2022" -- only at the very end
_PUBLISHED_RE = re.compile(
    r"(up(?:loaded|dated)(?:\s+on\s+the\s+website)?(?:\s+at\s+\d{1,2}(?:[:.]\d{2})?\s*[AP]\.?\s*M\.?)?"
    r"(?:\s+on)?\s*[:\-]?\s*(\d{1,2})[-./](\d{1,2})[-./](\d{4}|\d{2}))\s*\.?\s*$",
    re.I,
)

_ADVT_RE = re.compile(
    r"\b(?:ADV|ADVT|ADVERTISEMENT)\.?\s*NOS?\.?\s*[:\-]?\s*(\d{4,8}(?:\s*(?:TO|AND|&|-)\s*\d{4,8})?)\b",
    re.I,
)

_NUMBER_WORD = (r"ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|NINE|TEN|ELEVEN|TWELVE|TWENTY|THIRTY|FORTY|"
                r"FIFTY|SIXTY|SEVENTY|EIGHTY|NINETY|HUNDRED")
# What ends a post name: the department / process / date part of the title
_POST_STOP = (
    r"(?=\s+(?:IN|INTHE|UNDER|FOR|TO\s+BE|THROUGH|UPLOADED|UPDATED|VIDE|ADVT|ADV|ADVNO|ADVERTISEMENT|"
    r"ALONG|AND\s+INTERVIEW|AT|ON|OF\s+PUNJAB|AS\s+PER|HELD|COMPETITIVE|JOINT|EXAM|EXAMINATION|MAINS|"
    r"RECONDUCT|FROM|WHICH|REGARDING|DUE|GOVT|GOVERNMENT|THE\s+DEPARTMENT|DEPARTMENT)\b"
    r"|\s+(?:AND\s+)?\d{1,4}\s+(?:POSTS?\b|[A-Z])"
    r"|\s*[,;(]|\s*$)"
)
_POST_RE = re.compile(
    r"\b(?:(?:\d{1,4}|" + _NUMBER_WORD + r")[\s-]+){0,6}POSTS?\s+OF\s+(?:THE\s+)?(?:OF\s+)?"
    r"(?!(?:\d|(?:" + _NUMBER_WORD + r")\b))"
    r"(.+?)" + _POST_STOP,
    re.I,
)
# Department text glued onto the post: "GROUP-AIN THE DEPARTMENT OF ...", "... GROUP-AIN"
_GLUED_DEPT_RE = re.compile(r"IN\s*THE\s+DEPARTMENT\b.*$|(?<=GROUP-[A-D])IN$", re.I)
_NOT_A_POST_RE = re.compile(r"\b(?:COMBINED|DEPARTMENT)\s*$", re.I)  # "posts of ... Combined [Competitive Exam]"

_KIND_RULES = [
    ("CORRIGENDUM", re.compile(r"\bcorrigendum\b|\baddendum\b|extension\s+(?:of|in)\s+(?:the\s+)?last\s+dates?|"
                               r"\bdate\s+extension\b", re.I)),
    ("ADMIT_CARD", re.compile(r"\badmit\s+cards?\b", re.I)),
    ("ANSWER_KEY", re.compile(r"\banswer\s+keys?\b", re.I)),
    ("RESULT", re.compile(r"\bresults?\b", re.I)),
    ("VACANCY", re.compile(r"^\s*(?:new\s+)?recruitment\s+(?:to|for|notice)\b|^\s*advertisement\s+for\s+recruitment\b|"
                           r"^\s*notification\s+regarding\s+the\s+\d+\s+posts?\b", re.I)),
]


class _AnnouncementItems(HTMLParser):
    """Collects (href, text) of the one link in each item of the home page's Announcements panel."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items: List[Tuple[str, str]] = []
        self._in_heading = False
        self._heading: List[str] = []
        self._panel = ""
        self._in_item = False
        self._href: Optional[str] = None
        self._text: List[str] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = (attrs.get("class") or "").lower().split()
        if tag in ("h3", "h4") and "panel-title" in classes:
            self._in_heading, self._heading = True, []
        elif tag == "li":
            self._in_item = _ITEM_CLASS in classes and self._panel.startswith(_LISTING_PANEL)
        elif tag == "a" and self._in_item and self._href is None:
            self._href, self._text = (attrs.get("href") or "").strip(), []

    def handle_endtag(self, tag):
        if tag in ("h3", "h4") and self._in_heading:
            self._in_heading = False
            self._panel = " ".join("".join(self._heading).split()).lower()
        elif tag == "a" and self._href is not None:
            if self._in_item:
                self.items.append((self._href, "".join(self._text)))
            self._href = None
        elif tag == "li":
            self._in_item = False
        elif tag == "ul":  # the panel's one list ends: items further down the page (logo strip) are not in it
            self._panel, self._in_item = "", False

    def handle_data(self, data):
        if self._in_heading:
            self._heading.append(data)
        if self._href is not None:
            self._text.append(data)


def _clean(text: str) -> str:
    return " ".join(unescape(text).split())


def _published(title: str) -> Tuple[Optional[date], Optional[str]]:
    m = _PUBLISHED_RE.search(title)
    if not m:
        return None, None
    day, month, year = int(m.group(2)), int(m.group(3)), m.group(4)
    full_year = int(year) if len(year) == 4 else 2000 + int(year)
    try:
        return date(full_year, month, day), m.group(1)
    except ValueError:  # not a real calendar date: leave it unfilled
        return None, None


def _advertisement(title: str) -> Tuple[Optional[str], Optional[str]]:
    matches = list(_ADVT_RE.finditer(title))
    if not matches:
        return None, None
    numbers: List[str] = []
    for m in matches:
        n = " ".join(m.group(1).split())
        if n not in numbers:
            numbers.append(n)
    return ", ".join(numbers), title[matches[0].start():matches[-1].end()]


def _posts(title: str) -> Tuple[List[str], Optional[str]]:
    names: List[str] = []
    spans: List[Tuple[int, int]] = []
    for m in _POST_RE.finditer(title):
        raw = m.group(1)
        name = _GLUED_DEPT_RE.sub("", raw).strip(" -")
        if not name or _NOT_A_POST_RE.search(name):
            continue
        if name not in names:
            names.append(name)
        spans.append((m.start(), m.start(1) + len(name)))
    if not names:
        return [], None
    return names, title[spans[0][0]:spans[-1][1]]


def _kind(title: str) -> Tuple[Optional[str], Optional[str]]:
    for kind, pattern in _KIND_RULES:
        m = pattern.search(title)
        if m:
            return kind, m.group(0).strip()
    return None, None


def parse_listing(html: str, base_url: str) -> List[ListingRow]:
    parser = _AnnouncementItems()
    parser.feed(html)
    parser.close()

    base = urlparse(base_url)
    rows: List[ListingRow] = []
    seen = set()
    for raw_href, raw_text in parser.items:
        title = _clean(raw_text)
        href = unescape(raw_href)
        if not title or not href or href.lower().startswith(("javascript:", "mailto:", "#")):
            continue
        if _SKIP_RE.search(title):
            continue
        url = urljoin(base_url, href)
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != base.hostname:
            continue
        if url in seen:
            continue
        seen.add(url)

        row = ListingRow(title=title, url=url, evidence={"title": title, "url": raw_href})
        row.published_on, ev = _published(title)
        if ev:
            row.evidence["published_on"] = ev
        row.advertisement_number, ev = _advertisement(title)
        if ev:
            row.evidence["advertisement_number"] = ev
        row.post_names, ev = _posts(title)
        if ev:
            row.evidence["post_names"] = ev
        row.kind_hint, ev = _kind(title)
        if ev:
            row.evidence["kind_hint"] = ev
        rows.append(row)
    return rows
