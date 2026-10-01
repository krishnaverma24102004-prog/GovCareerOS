"""
BSF (Border Security Force) listing adapter.

https://rectt.bsf.gov.in/ (the recruitment portal, a server-rendered Yii page) shows two lists:

  notice strip      a column of button links (<a class="c-button ...">) above the openings list:
                    notices, results, admit-card links, mock-test links. One link per notice, the
                    button text is the notice title; no date, number or post column.
  openings list     "Current Recruitment Openings": <ul class="c-common__list-grid"> whose
                    <li class="c-common__list-item"> entries are the open recruitments; when there are
                    none it holds a single <li class="... emptyState">No Job(s) Available.</li>.

Each notice button / opening item becomes a ListingRow:
  title                 the button text / the item's text (whitespace collapsed)
  url                   the button's link / the item's first link that is not an account page
                        (/auth/, /registration/, /payment/), absolute against the page URL,
                        spaces percent-encoded
  advertisement_number  only where the text says "Advertisement No. ..." / "Advt. No. ..."
  published_on          only from a labelled date in the text ("Published on: 01-10-2026",
                        "dated 01.10.2026"); dd-mm-yyyy, dd.mm.yyyy, dd/mm/yyyy or dd-Mon-yyyy
  last_date             only from a labelled date ("Last Date: ...", "Closing date ..."); an
                        unlabelled date in a title (an exam "rescheduled on 18.09.2026") is never used
  post_names            from the text's own "the post of ..." phrase, up to where the post ends
                        ("... EXAM", "... IN BSF", "... THROUGH", "... WHOSE", "-2025"), split only
                        at " AND " outside parentheses; "HC(RO)/(RM)" stays one name as written
  kind_hint             from the text's own wording: CORRIGENDUM, ADMIT_CARD, ANSWER_KEY, RESULT
                        ("result", "shortlisted", "merit list"), VACANCY ("advertisement" and the
                        site's own spelling "ADVERTISMENT", walk-in, recruitment/open rally); an
                        openings-list item is VACANCY by its section heading "Current Recruitment
                        Openings" (the evidence is that heading); otherwise None
Every filled field carries the exact page text it came from in `evidence`.

Skipped: the header/side menus, login/CSC/profile links, the footer and the instructions modal
(they are outside both lists); buttons without a link (href="javascript:;" notes to candidates);
the empty-state item; anything inside HTML comments (the page keeps many retired buttons there);
and every link off the official domain. On the 2026-10-01 capture all live notice buttons point
off-domain (PDFs on d3t79nicn48uzj.cloudfront.net, exam-agency portals on digialm.com) and the
openings list is empty, so parse_listing returns no rows for that page.

No LISTING_URLS: the portal has no other public listing page (its menu links Home, profile,
login, the openings anchor and a helpdesk page).
"""
import re
from datetime import date
from html.parser import HTMLParser
from typing import List, Optional, Tuple
from urllib.parse import quote, urldefrag, urljoin, urlparse

from govcareeros.backend.sources.adapters.base import ListingRow
from govcareeros.backend.sources.url_guard import UrlPolicy

SOURCE = "BSF"

_WS_RE = re.compile(r"\s+")
_ACCOUNT_PATH_RE = re.compile(r"^/(?:auth|registration|payment)(?:/|$)", re.I)

_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}
_DATE = r"\d{1,2}[-./]\d{1,2}[-./]\d{4}|\d{1,2}[- ][A-Za-z]{3,9}[- ,]+\d{4}"
_PUBLISHED_RE = re.compile(
    rf"\b(?:published(?:\s+on|\s+date)?|date\s+of\s+publication|dated)\s*[:\-]?\s*(?P<date>{_DATE})\b", re.I)
_LAST_DATE_RE = re.compile(
    rf"\b(?:last\s+date(?:\s+(?:of|for)\s+(?:online\s+)?(?:application|registration|submission|receipt)s?)?|closing\s+date)"
    rf"\s*(?:is|:|-)?\s*(?P<date>{_DATE})\b", re.I)
_NUMERIC_DATE_RE = re.compile(r"^(\d{1,2})[-./](\d{1,2})[-./](\d{4})$")
_NAMED_DATE_RE = re.compile(r"^(\d{1,2})[- ]([A-Za-z]{3,9})[- ,]+(\d{4})$")

_ADVT_NUMBER_RE = re.compile(
    r"\b(?:Advertisement|Advt)\b\.?\s*No\b\.?\s*[:\-]?\s*(?P<num>[A-Z0-9][A-Z0-9/.\-()]*\d[A-Z0-9/\-()]*)", re.I)

# Checked in this order: a "Corrigendum ... recruitment" is a corrigendum, an "admit card ... result" an admit card
_KIND_RULES = [
    ("CORRIGENDUM", re.compile(r"\bcorrigendum\b|\baddendum\b|\bamendment\b", re.I)),
    ("ADMIT_CARD", re.compile(r"\be?-?admit\s*cards?\b", re.I)),
    ("ANSWER_KEY", re.compile(r"\banswer\s*keys?\b", re.I)),
    ("RESULT", re.compile(r"\bresults?\b|\bshort-?listed\b|\bmerit\s+list\b", re.I)),
    ("VACANCY", re.compile(r"\badvert(?:i|is)e?ment\b|\bwalk[\s-]*in\b|\brecruitment\s+rally\b|\bopen\s+rally\b", re.I)),
]

# Only "the post of": the page's "various posts of <setup>" and "filling up post of Group 'B' & 'C'"
# name a cadre or setup, not posts
_POST_START_RE = re.compile(r"\bthe\s+post\s+of\s+", re.I)
# Where the post name ends (only counted outside parentheses)
_POST_END_RE = re.compile(
    r"\s+(?:exam|examination|in|through|whose|under|for|on|at|by|with)\b|\s*[-:;,]\s|\s+-\s*\d|\s*-\d{4}\b|\.$", re.I)
_POST_SPLIT_RE = re.compile(r"\s+and\s+", re.I)

def _norm(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()   # character references are already decoded by the parser

class _Page(HTMLParser):
    """Collects notice buttons and openings-list items as (kind, text, hrefs).

    kind is "notice" (an <a class="c-button">) or "opening" (an <li class="c-common__list-item">).
    HTML comments never reach handle_starttag/handle_data, so retired, commented-out buttons are ignored.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items: List[Tuple[str, str, List[str]]] = []
        self.openings_heading: Optional[str] = None
        self._button: Optional[Tuple[Optional[str], List[str]]] = None   # (href, text parts)
        self._item: Optional[Tuple[List[str], List[str]]] = None         # (hrefs, text parts)
        self._item_depth = 0                                             # open <li>s inside the current item
        self._skip = 0                                                   # inside <script>/<style>
        self._heading: Optional[List[str]] = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "li":
            if self._item is not None:
                self._item_depth += 1
            elif "c-common__list-item" in classes and "emptyState" not in classes:
                self._item, self._item_depth = ([], []), 1
        elif tag == "a":
            href = (a.get("href") or "").strip()
            if self._item is not None:
                if href:
                    self._item[0].append(href)
            elif "c-button" in classes:
                self._button = (href or None, [])
        elif tag == "h2" and a.get("id") == "bsf-current-openings":
            self._heading = []

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag == "li" and self._item is not None:
            self._item_depth -= 1
            if self._item_depth == 0:
                hrefs, parts = self._item
                self.items.append(("opening", _norm(" ".join(parts)), hrefs))
                self._item = None
        elif tag == "a" and self._button is not None:
            href, parts = self._button
            self.items.append(("notice", _norm(" ".join(parts)), [href] if href else []))
            self._button = None
        elif tag == "h2" and self._heading is not None:
            self.openings_heading = _norm(" ".join(self._heading)) or None
            self._heading = None

    def handle_data(self, data):
        if self._skip:
            return
        if self._button is not None:
            self._button[1].append(data)
        if self._item is not None:
            self._item[1].append(data)
        if self._heading is not None:
            self._heading.append(data)

def _parse_date(text: str) -> Optional[date]:
    m = _NUMERIC_DATE_RE.match(text)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = _NAMED_DATE_RE.match(text)
        if not m or m.group(2)[:3].lower() not in _MONTHS:
            return None
        day, month, year = int(m.group(1)), _MONTHS[m.group(2)[:3].lower()], int(m.group(3))
    try:
        return date(year, month, day)
    except ValueError:
        return None

def _labelled_date(regex: re.Pattern, text: str) -> Optional[Tuple[date, str]]:
    for m in regex.finditer(text):
        parsed = _parse_date(m.group("date"))
        if parsed:
            return parsed, m.group(0)
    return None

def _split_outside_parens(text: str) -> List[str]:
    parts, start = [], 0
    for m in _POST_SPLIT_RE.finditer(text):
        if text[:m.start()].count("(") == text[:m.start()].count(")"):
            parts.append(text[start:m.start()])
            start = m.end()
    parts.append(text[start:])
    return [p.strip() for p in parts if p.strip()]

def _post_names(text: str) -> Optional[Tuple[List[str], str]]:
    start = _POST_START_RE.search(text)
    if not start:
        return None
    rest = text[start.end():]
    end = len(rest)
    for m in _POST_END_RE.finditer(rest):
        if rest[:m.start()].count("(") == rest[:m.start()].count(")"):
            end = m.start()
            break
    phrase = rest[:end].strip()
    if not phrase:
        return None
    return _split_outside_parens(phrase), text[start.start():start.end() + end].rstrip()

def _kind(text: str) -> Optional[Tuple[str, str]]:
    for kind, regex in _KIND_RULES:
        m = regex.search(text)
        if m:
            return kind, m.group(0)
    return None

def _absolute(href: str, base_url: str) -> Optional[str]:
    # Spaces in the page's own file names ("Result_pst-pet_Water Wing.pdf") are percent-encoded;
    # existing escapes are kept
    url = quote(urldefrag(urljoin(base_url, href.strip()))[0], safe=":/?#[]@!$&'()*+,;=%~")
    return url if urlparse(url).scheme in ("http", "https") else None

def _row_url(hrefs: List[str], base_url: str) -> Optional[Tuple[str, str]]:
    """(absolute url, href as written) of the item's document link; account pages (login/profile/payment) are never one."""
    for href in hrefs:
        url = _absolute(href, base_url)
        if url and not _ACCOUNT_PATH_RE.match(urlparse(url).path):
            return url, href
    return None

def _entries(html: str, base_url: str) -> List[ListingRow]:
    """Every notice button and opening item that has a title and a link, before the official-domain filter."""
    page = _Page()
    page.feed(html)
    page.close()
    rows: List[ListingRow] = []
    for kind_of_item, title, hrefs in page.items:
        target = _row_url(hrefs, base_url)
        if not title or not target:
            continue
        url, href = target
        row = ListingRow(title=title, url=url)
        row.evidence["title"] = title
        row.evidence["url"] = href
        advt = _ADVT_NUMBER_RE.search(title)
        if advt:
            row.advertisement_number = advt.group("num").rstrip(".")
            row.evidence["advertisement_number"] = advt.group(0)
        published = _labelled_date(_PUBLISHED_RE, title)
        if published:
            row.published_on, row.evidence["published_on"] = published
        last = _labelled_date(_LAST_DATE_RE, title)
        if last:
            row.last_date, row.evidence["last_date"] = last
        posts = _post_names(title)
        if posts:
            row.post_names, row.evidence["post_names"] = posts
        kind = _kind(title)
        if kind:
            row.kind_hint, row.evidence["kind_hint"] = kind
        elif kind_of_item == "opening" and page.openings_heading:
            row.kind_hint, row.evidence["kind_hint"] = "VACANCY", page.openings_heading
        rows.append(row)
    return rows

def parse_listing(html: str, base_url: str) -> List[ListingRow]:
    policy = UrlPolicy.from_url(base_url)
    rows, seen = [], set()
    for row in _entries(html, base_url):
        if not policy.allows(row.url) or row.url in seen:
            continue
        seen.add(row.url)
        rows.append(row)
    return rows
