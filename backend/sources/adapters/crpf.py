"""
CRPF (Central Reserve Police Force) listing adapter.

https://rect.crpf.gov.in/ lists current recruitments as numbered sections. Each section is an
<h3 class="heading-3"> heading ("6 ) Recruitment for the post of Constable (...) 2026 in CRPF")
followed by one table with the columns

    Sr. No. | Document | Date (dd-Mon-yyyy, e.g. "17-Sep-2026") | Download (PDF button)

The /Archived page has the same layout (its Date cell is sometimes "NA").

Each table row becomes a ListingRow:
  title                 the "Document" cell text
  url                   the row's document: the Download button's link (else the Document link),
                        absolute against the page URL; links off the official domain (exam agencies'
                        admit-card portals embedded in a description) are never used
  published_on          the "Date" cell; "NA" or anything that is not a date stays None
  advertisement_number  only where the text says "Advertisement No. ..." / "Advt. No. ..."
  kind_hint             from the row's own label (the part after "::" when the row has one):
                        CORRIGENDUM (corrigendum / addendum / amendment / extension of the last date ...),
                        ADMIT_CARD, ANSWER_KEY, RESULT (result / selected / qualified / shortlisted
                        candidates / merit list / cut-off marks), VACANCY (advertisement / notification /
                        walk-in / deputation / "Recruitment of|for ..." openings); otherwise None
  post_names            from "post of ...", "appointment|engagement|selection of ...", "Deputation as ...";
                        read from the row text, else from its section heading (evidence says which)
  last_date             never: the listing has no closing-date column
  evidence["section"]   the section heading the row sits under (context, exact text)

Only rows of tables that have "Document" and "Date" columns are read, so the site's menus, the
"Archived" button, the footer and quick links are skipped. A table nested inside a Document cell
(seen on /Archived) is read as part of that cell. Rows whose only links lead off the official
domain are dropped, and a document listed twice is kept once.

No LISTING_URLS: /Archived holds the closed recruitments (494 rows back to 2016), so only the
registry's current page is read.
"""
import re
from datetime import date
from html import unescape
from html.parser import HTMLParser
from typing import List, Optional, Tuple
from urllib.parse import urldefrag, urljoin, urlparse

from govcareeros.backend.sources.adapters.base import ListingRow
from govcareeros.backend.sources.url_guard import UrlPolicy

SOURCE = "CRPF"

_WS_RE = re.compile(r"\s+")
_SECTION_NUMBER_RE = re.compile(r"^\d+\s*\)\s*")
_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}
_DATE_NAMED_RE = re.compile(r"^(\d{1,2})[-/. ]([A-Za-z]{3,9})[-/. ,]+(\d{4})$")
_DATE_NUMERIC_RE = re.compile(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$")

_ADVT_NUMBER_RE = re.compile(r"\b(?:Advertisement|Advt)\b\.?\s*No\b\.?\s*:?\s*(?P<num>[A-Z0-9][A-Z0-9/.\-()]*\d[A-Z0-9/\-]*)", re.I)

# Kind labels, checked in this order (a "Corrigendum regarding ... recruitment" is a corrigendum)
_KIND_RULES = [
    ("CORRIGENDUM", re.compile(
        r"corrigendum|addendum|amendments?\b|\bamend\b|\bamend-|शुद्धिपत्र|संशोधन|"
        r"extension\s+(?:of|in)\s+(?:the\s+)?(?:last\s+date|submission)|date\s+extended|postpone|cancell?ation", re.I)),
    ("ADMIT_CARD", re.compile(r"\be-?\s?admit\s+cards?|\badmit\s+cards?|प्रवेश\s*पत्र", re.I)),
    ("ANSWER_KEY", re.compile(r"answer\s+keys?", re.I)),
    ("RESULT", re.compile(
        r"\bresults?\b|\bselected\s+candidates?|finally\s+selected|merit\s+list|final\s+merit|"
        r"\bqualified\s+candidates|candidates\s+(?:who\s+have\s+)?qualified|short[\s-]?listed\s+candidates|"
        r"cut[\s-]?off\s+marks|परिणाम", re.I)),
    ("VACANCY", re.compile(
        r"^(?:publication\s+of\s+)?(?:window\s+)?(?:advertisement|advt)\b|"
        r"(?:advertisement|notification)\s*/\s*(?:advertisement|notification)|नोटिफिकेशन|विज्ञापन|"
        r"^walk[\s-]*in|^(?:notice\s+for\s+)?deputation\b|^(?:special\s+)?recruitments?\s+(?:of|for|to|drive)\b|"
        r"^regarding\s+recruitment\s+for\b|^(?:release\s+of\s+)?vacanc(?:y|ies)\s+of\b|^filling\s+up\b|"
        r"^(?:contractual\s+)?appointment\s+of\b|^rectt\.?\s+rally|के\s+पद\s+के\s+लिए\s+भर्ती", re.I)),
]

# Where a post name starts, and where it ends (only outside parentheses)
_POST_START_RE = re.compile(
    r"\b(?:(?:for|to)\s+)?(?:the\s+)?(?<!following\s)posts?\s+of\s+|"
    r"\b(?:appointment|engagement|selection)\s+of\s+(?!candidates\b|retired\b)|"
    r"\bdeputation\s+as\s+|"
    r"\bvacancy\s+of\s+", re.I)
_POST_END_RE = re.compile(
    r"\s+(?:in|on|for|at|under|through|from|purely|pursuant|with|vide|regarding|reg|called|to\s+conduct|"
    r"exam|examination|ldce|GC|CPC|CRPF|C\.R\.P\.F|composite|group\s+centre|dtd|dated)\b|"
    r"\s*[-–]\s*\d{4}|\s+[-–]\s+|\s+\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\s+\d{4}\b|(?<=\))\d{4}|"
    r"\s*[:;]|\s*,(?:\s|$)|\.\s*$|\s+link\b|\s*\((?:eng|english|hindi)\)", re.I)
_POST_SPLIT_RE = re.compile(r"\s+(?:and|&)\s+|\s+/\s+|\s*,\s*", re.I)
_LEADING_COUNT_RE = re.compile(r"^\d+\s+(?:\([A-Za-z]+\)\s+)?")
_TRAILING_COUNT_RE = re.compile(r"\s*-\s*\d+$")

def _clean(text: str) -> str:
    return _WS_RE.sub(" ", unescape(text)).strip()

class _Listing(HTMLParser):
    """Section headings and the tables under them: [(heading, [[(cell text, [href, ...]), ...], ...])]."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: List[Tuple[str, List[List[Tuple[str, List[str]]]]]] = []
        self._heading: Optional[List[str]] = None
        self._section = ""
        self._depth = 0
        self._row: Optional[List[Tuple[str, List[str]]]] = None
        self._cell: Optional[Tuple[List[str], List[str]]] = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "h3" and self._depth == 0 and "heading-3" in (attrs.get("class") or "").split():
            self._heading = []
        elif tag == "table":
            self._depth += 1
            if self._depth == 1:
                self.tables.append((self._section, []))
        elif self._depth == 0:
            return
        elif self._depth > 1:
            # A table inside a Document cell is part of that cell's content
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
        elif tag in ("br", "p", "h1", "div") and self._cell is not None:
            self._cell[0].append(" ")

    def handle_endtag(self, tag):
        if tag == "h3" and self._heading is not None:
            self._section = _SECTION_NUMBER_RE.sub("", _clean("".join(self._heading)))
            self._heading = None
            return
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
        if self._heading is not None:
            self._heading.append(data)
        elif self._cell is not None:
            self._cell[0].append(data)

    def _close_cell(self):
        if self._cell is not None and self._row is not None:
            self._row.append((_clean("".join(self._cell[0])), self._cell[1]))
        self._cell = None

    def _close_row(self):
        self._close_cell()
        if self._row and self.tables:
            self.tables[-1][1].append(self._row)
        self._row = None

def _column(header: List[str], word: str) -> Optional[int]:
    for i, name in enumerate(header):
        if name.lower().strip(" .") == word:
            return i
    return None

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
    # "Recruitment of ... :: Window for online application form correction": the label is after "::"
    label = text.rsplit("::", 1)[-1].strip() if "::" in text else text
    for kind, pattern in _KIND_RULES:
        m = pattern.search(label)
        if m:
            return kind, m.group(0)
    return None

def _split_top_level(text: str) -> List[str]:
    parts, depth, start = [], 0, 0
    i = 0
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
    """(post names, exact text they were read from) for the first post phrase in `text`."""
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
            continue  # unbalanced brackets: no reliable end to the post name
        parts = [p.strip(" -/") for p in _split_top_level(phrase)]
        # 'Group "B" and "C" non ministerial': the pieces are not post names on their own
        if any(p and not (p[0].isalnum() or p[0] == "(") for p in parts):
            parts = [phrase]
        posts = []
        for part in parts:
            name = _TRAILING_COUNT_RE.sub("", _LEADING_COUNT_RE.sub("", part))
            if name and re.search(r"[A-Za-z]", name) and name not in posts:
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
    parser = _Listing()
    parser.feed(html)
    parser.close()
    policy = UrlPolicy.from_url(base_url)

    rows: List[ListingRow] = []
    seen = set()
    for section, table in parser.tables:
        if not table:
            continue
        header = [text for text, _ in table[0]]
        col_doc, col_date = _column(header, "document"), _column(header, "date")
        if col_doc is None or col_date is None:
            continue
        col_download = _column(header, "download")
        for cells in table[1:]:
            if len(cells) != len(header):
                continue
            found = _document_url(cells, [col_download, col_doc], base_url, policy)
            title = cells[col_doc][0]
            if not found or not title:
                continue
            url, href = found
            if url in seen:
                continue
            seen.add(url)

            row = ListingRow(title=title, url=url)
            row.evidence["title"] = title
            row.evidence["url"] = href
            if section:
                row.evidence["section"] = section

            date_text = cells[col_date][0]
            published = _parse_date(date_text)
            if published:
                row.published_on = published
                row.evidence["published_on"] = date_text

            advt = _advertisement_number(title) or (_advertisement_number(section) if section else None)
            if advt:
                row.advertisement_number, row.evidence["advertisement_number"] = advt

            kind = _kind(title)
            if kind:
                row.kind_hint, row.evidence["kind_hint"] = kind

            posts = _post_names(title) or (_post_names(section) if section else None)
            if posts:
                row.post_names, row.evidence["post_names"] = posts

            rows.append(row)
    return rows
