"""
MHA (Ministry of Home Affairs) listing adapter.

https://www.mha.gov.in/en/notifications/vacancies is a Drupal view: one table with the columns

    SR-No | Keyword (the notice title) | Download/Link (a PDF link, "Download 3.46 MB)")

20 rows per page, newest first, with a pager (?page=1, ?page=2, ...). The page shows no dates:
no published-on or closing-date column, and dates inside a title (e.g. a corrigendum to a circular
"Dated: 09.06.2026") are about another document, so they are not read as this row's dates. The
dates in the PDF file names (".../SSB_vacancy_29092026.PDF") are not shown text and are never used.
Should the table ever gain a labelled date column, it is read: a column whose header says
"last date" / "closing date" fills last_date, any other "date" column fills published_on, each only
when the cell is a date in the page's own format (dd-mm-yyyy, dd.mm.yyyy, dd/mm/yyyy, dd-Mon-yyyy).

Each table row becomes a ListingRow:
  title                 the "Keyword" cell text
  url                   the row's PDF link (Download/Link cell, else a link in the title cell),
                        absolute against the page URL; links off the official domain are never used
  advertisement_number  only where the text says "Advertisement No." / "Advt. No." / "Adv no." ...
                        (e.g. "Vacancy for various post for direct recruitment through UPSC Adv no. 05/2026")
  kind_hint             from the title's own words: CORRIGENDUM (corrigendum / addendum / extension of
                        the last date / extension of date ...), ADMIT_CARD, ANSWER_KEY, RESULT, VACANCY
                        (vacancy circular, advertisement, filling up, appointment to the post,
                        engagement of, hiring of, ...); otherwise None
  post_names            from "post(s) of ...", "vacancies of ...", "engagement|hiring|appointment of ...",
                        "(retired ...) as Consultant", "persons - X (01) and Y (01)"; a name whose
                        brackets do not close in the title is left out rather than guessed
  published_on/last_date only from a labelled date column (none on the page today)

Only tables whose header has a title ("Keyword"/"Title") column and a "Download" column are read,
so the site's menus, quick links, pager and footer are skipped. A document listed twice is kept once.

LISTING_URLS: the pager's further pages, which hold the older (still recent) circulars.
"""
import re
from datetime import date
from html import unescape
from html.parser import HTMLParser
from typing import List, Optional, Tuple
from urllib.parse import urldefrag, urljoin, urlparse

from govcareeros.backend.sources.adapters.base import ListingRow
from govcareeros.backend.sources.url_guard import UrlPolicy

SOURCE = "MHA"

LISTING_URLS = [
    "https://www.mha.gov.in/en/notifications/vacancies?page=1",
    "https://www.mha.gov.in/en/notifications/vacancies?page=2",
]

_WS_RE = re.compile(r"\s+")
_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}
_DATE_NAMED_RE = re.compile(r"^(\d{1,2})[-/. ]([A-Za-z]{3,9})[-/. ,]+(\d{4})$")
_DATE_NUMERIC_RE = re.compile(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$")

_ADVT_NUMBER_RE = re.compile(
    r"\b(?:Advertisement|Advt|Adv)\b\.?\s*No\b\.?\s*:?\s*(?P<num>[A-Z0-9][A-Z0-9/.\-()]*\d[A-Z0-9/\-]*)", re.I)

# Kind labels, checked in this order ("Extension of last date ... for Engagement of ..." is a corrigendum)
_KIND_RULES = [
    ("CORRIGENDUM", re.compile(
        r"corrigendum|addendum|amendments?\b|"
        r"extension\s+(?:of|in)\s+(?:the\s+)?(?:last\s+date|date|submission)|date\s+extended|"
        r"postpone|cancell?ation", re.I)),
    ("ADMIT_CARD", re.compile(r"\be-?\s?admit\s+cards?", re.I)),
    ("ANSWER_KEY", re.compile(r"answer\s+keys?", re.I)),
    ("RESULT", re.compile(
        r"\bresults?\b|\bselected\s+candidates?|merit\s+list|short[\s-]?listed\s+candidates|cut[\s-]?off\s+marks",
        re.I)),
    ("VACANCY", re.compile(
        r"^vacancy\s+(?:circular|for)\b|^(?:advertisement|advt)\b|^fill?ing\s+up\b|"
        r"^appointment\s+(?:to|of)\b|^engagement\s+of\b|^hiring\s+of\b|^recruitment\s+(?:of|for|to)\b|"
        r"^walk[\s-]*in\b", re.I)),
]

_COUNT = r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)"
_RETIRED = rf"(?:{_COUNT}\s+)?(?:nos\.?\s+)?retired\b"
# Where a post name starts, and where it ends (only outside brackets)
_POST_START_RE = re.compile(
    rf"\bengagement\s+of\s+{_RETIRED}[^()]*?(?:\([^()]*\)[^()]*?)*\bas\s+|"
    r"\bposts?\s+of\s+|"
    r"\bvacanc(?:y|ies)\s+of\s+|"
    rf"\b(?:engagement|hiring|appointment)\s+of\s+(?!{_RETIRED})|"
    r"\bpersons?\s*-\s*", re.I)
_POST_END_RE = re.compile(
    r"\s+(?:in|on|for|at|under|through|from|with|vide|regarding|reg|of\s+the)\b|"
    r"\s*[-–]\s*reg\b|\s+[-–]\s+|\s*[:;]|\s*,(?:\s|$)|\.\s*$", re.I)
_POST_SPLIT_RE = re.compile(r"\s+(?:and|&)\s+|\s*,\s*(?:and\s+)?", re.I)
_LEADING_COUNT_RE = re.compile(rf"^{_COUNT}\s+(?:\(\d+\)\s+)?(?:nos\.?\s+)?", re.I)
_TRAILING_COUNT_RE = re.compile(r"\s*\(\d+\)$")
_NOT_A_POST_RE = re.compile(r"^(?:various|vacant|the)\b|\bpersons?$", re.I)

def _clean(text: str) -> str:
    return _WS_RE.sub(" ", unescape(text)).strip()

class _Tables(HTMLParser):
    """Every top-level table as rows of cells: [[(cell text, [href, ...]), ...], ...]."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: List[List[List[Tuple[str, List[str]]]]] = []
        self._depth = 0
        self._row: Optional[List[Tuple[str, List[str]]]] = None
        self._cell: Optional[Tuple[List[str], List[str]]] = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "table":
            self._depth += 1
            if self._depth == 1:
                self.tables.append([])
        elif self._depth == 0:
            return
        elif self._depth > 1:
            if self._cell is not None:
                if tag == "a" and attrs.get("href"):
                    self._cell[1].append(attrs["href"])
                elif tag in ("tr", "td", "th", "br", "p", "div"):
                    self._cell[0].append(" ")
        elif tag == "tr":
            self._close_row()
            self._row = []
        elif tag in ("td", "th"):
            self._close_cell()
            if self._row is None:
                self._row = []
            self._cell = ([], [])
        elif tag == "a" and self._cell is not None and attrs.get("href"):
            self._cell[1].append(attrs["href"])
        elif tag in ("br", "p", "div") and self._cell is not None:
            self._cell[0].append(" ")

    def handle_endtag(self, tag):
        if self._depth == 0:
            return
        if self._depth > 1:
            if tag == "table":
                self._depth -= 1
        elif tag in ("td", "th"):
            self._close_cell()
        elif tag == "tr":
            self._close_row()
        elif tag == "table":
            self._close_row()
            self._depth -= 1

    def handle_data(self, data):
        if self._cell is not None:
            self._cell[0].append(data)

    def _close_cell(self):
        if self._cell is not None and self._row is not None:
            self._row.append((_clean("".join(self._cell[0])), self._cell[1]))
        self._cell = None

    def _close_row(self):
        self._close_cell()
        if self._row and self.tables:
            self.tables[-1].append(self._row)
        self._row = None

def _column(header: List[str], *words: str) -> Optional[int]:
    for i, name in enumerate(header):
        name = name.lower().strip(" .")
        if any(name == w or name.startswith(w + "/") or name.startswith(w + " ") for w in words):
            return i
    return None

def _date_columns(header: List[str]) -> Tuple[Optional[int], Optional[int]]:
    """(published-on column, last-date column) from header labels that say "date"."""
    published = last = None
    for i, name in enumerate(header):
        low = name.lower()
        if "date" not in low:
            continue
        if re.search(r"last|closing|end", low):
            last = i if last is None else last
        elif published is None:
            published = i
    return published, last

def _parse_date(text: str) -> Optional[date]:
    m = _DATE_NAMED_RE.match(text)
    if m:
        month = _MONTHS.get(m.group(2)[:3].lower())
        day, year = int(m.group(1)), int(m.group(3))
    else:
        m = _DATE_NUMERIC_RE.match(text)
        if not m:
            return None
        day, month, year = (int(g) for g in m.groups())
    if not month:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None

def _advertisement_number(text: str) -> Optional[Tuple[str, str]]:
    m = _ADVT_NUMBER_RE.search(text)
    if not m:
        return None
    num = m.group("num").rstrip(".,;:)/-")
    return num, text[m.start():m.start("num") + len(num)]

def _kind(text: str) -> Optional[Tuple[str, str]]:
    for kind, pattern in _KIND_RULES:
        m = pattern.search(text)
        if m:
            return kind, m.group(0)
    return None

def _split_top_level(text: str) -> List[str]:
    parts, depth, start, i = [], 0, 0, 0
    while i < len(text):
        ch = text[i]
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        elif depth == 0:
            m = _POST_SPLIT_RE.match(text, i)
            if m and m.end() > i:
                parts.append(text[start:i])
                start = i = m.end()
                continue
        i += 1
    parts.append(text[start:])
    return parts

def _post_names(text: str) -> Optional[Tuple[List[str], str]]:
    """(post names, exact text they were read from) for the first usable post phrase in `text`."""
    for start in _POST_START_RE.finditer(text):
        depth, end = 0, len(text)
        for i in range(start.end(), len(text)):
            ch = text[i]
            if ch in "([":
                depth += 1
            elif ch in ")]":
                depth = max(0, depth - 1)
            elif depth == 0 and _POST_END_RE.match(text, i):
                end = i
                break
        phrase = text[start.end():end].strip()
        if depth or phrase.count("(") != phrase.count(")"):
            continue  # brackets never close: no reliable end to the post name
        posts = []
        for part in _split_top_level(phrase):
            name = _TRAILING_COUNT_RE.sub("", _LEADING_COUNT_RE.sub("", part.strip(" -/"))).strip()
            if name and re.search(r"[A-Za-z]", name) and not _NOT_A_POST_RE.search(name) and name not in posts:
                posts.append(name)
        if posts:
            return posts, text[start.start():start.end() + len(text[start.end():end].rstrip())]
    return None

def _document_url(cells: List[Tuple[str, List[str]]], cols: List[Optional[int]], base_url: str,
                  policy: UrlPolicy) -> Optional[Tuple[str, str]]:
    for col in cols:
        if col is None or col >= len(cells):
            continue
        for href in cells[col][1]:
            href = href.strip()
            if not href or href.lower().startswith(("javascript:", "mailto:", "tel:", "#")):
                continue
            url = urldefrag(urljoin(base_url, href))[0]
            if urlparse(url).scheme in ("http", "https") and policy.allows(url):
                return url, href
    return None

def parse_listing(html: str, base_url: str) -> List[ListingRow]:
    parser = _Tables()
    parser.feed(html)
    parser.close()
    policy = UrlPolicy.from_url(base_url)

    rows: List[ListingRow] = []
    seen = set()
    for table in parser.tables:
        if not table:
            continue
        header = [text for text, _ in table[0]]
        col_title = _column(header, "keyword", "title", "subject", "description")
        col_link = _column(header, "download", "link", "document")
        if col_title is None or col_link is None:
            continue
        col_published, col_last = _date_columns(header)
        for cells in table[1:]:
            if len(cells) != len(header):
                continue
            title = cells[col_title][0]
            found = _document_url(cells, [col_link, col_title], base_url, policy)
            if not found or not title:
                continue
            url, href = found
            if url in seen:
                continue
            seen.add(url)

            row = ListingRow(title=title, url=url)
            row.evidence["title"] = title
            row.evidence["url"] = href

            for col, attr in ((col_published, "published_on"), (col_last, "last_date")):
                if col is None:
                    continue
                parsed = _parse_date(cells[col][0])
                if parsed:
                    setattr(row, attr, parsed)
                    row.evidence[attr] = cells[col][0]

            advt = _advertisement_number(title)
            if advt:
                row.advertisement_number, row.evidence["advertisement_number"] = advt

            kind = _kind(title)
            if kind:
                row.kind_hint, row.evidence["kind_hint"] = kind

            posts = _post_names(title)
            if posts:
                row.post_names, row.evidence["post_names"] = posts

            rows.append(row)
    return rows
