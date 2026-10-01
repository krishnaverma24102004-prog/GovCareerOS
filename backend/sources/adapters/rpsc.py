"""
RPSC (Rajasthan Public Service Commission) listing adapter.

https://rpsc.rajasthan.gov.in/advertisements is one table, "Recruitment Advertisement/Corrigendum
Section", with the columns

    Sr. No. | Release Date (dd/mm/yyyy) | Exam Name | Description / Details | Link (PDF)

Each row becomes a ListingRow:
  title                 the "Description / Details" text (Exam Name when the description is empty)
  url                   the row's own document link, made absolute against the page URL
  published_on          the "Release Date" cell
  advertisement_number  the number after an "Advt." / "Advertisement" label in the description
                        ("Advt. No. 05/2026-27", "Corrigendum 11/2014-15 of Advt 03/2013-14");
                        a corrigendum's own number ("Corrigendum No. 15/2026-27") is not one
  kind_hint             VACANCY when the description opens with an advertisement label,
                        CORRIGENDUM when it opens with a corrigendum label (Corrigendum/Corrig./Corri./Corr.)
  post_names            only where the description says "for (the) post of ..."
  last_date             never: this listing does not show closing dates

Only the cells of that table are read, so the site's menus, footer and the exam-year filter are skipped.
"""
import re
from datetime import date
from html import unescape
from html.parser import HTMLParser
from typing import List, Optional, Tuple
from urllib.parse import urldefrag, urljoin, urlparse

from govcareeros.backend.sources.adapters.base import ListingRow
from govcareeros.backend.sources.url_guard import UrlPolicy

SOURCE = "RPSC"

_WS_RE = re.compile(r"\s+")
_DATE_RE = re.compile(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})$")

# Label at the start of the description (the page's own wording)
_CORRIGENDUM_LABEL_RE = re.compile(r"^(?:Detailed\s+)?Corr(?:igendum|ig|i)?\b\.?", re.I)
_ADVT_LABEL_RE = re.compile(
    r"^(?:Joint\s+Recruitment\s+|Joint\s+Detailed\s+|Joint\s+|Detailed\s+|Combined?\s+)?"
    r"(?:Advertisement|Advt|Adv)\b\.*", re.I)

# "Advt. No. 05/2026-27", "Advt.19/2023-24", "Detailed Advt.(No. 03/2016-17)", "Advt No. EC-6/2011-12",
# "Detailed Advertisement 08/EXAM/SI-PC/EP-I/2020-21", "Combine Advt .01/2013-14", "Advt.EC 1/2011-12"
_ADVT_NUMBER_RE = re.compile(
    r"\b(?:Advertisement|Advt|Adv)\b[\s.]*\(?\s*(?:No\b[\s.]*-?\s*)?"
    r"(?P<num>(?:[A-Z]{1,2}[\s-]?)?\d[^\s,;]*(?:\s[/-][^\s,;]+)?)", re.I)
# "Advt No. 08 /2017-18", "Advt.01/2014 -15": the stray space is the page's typing, not part of the number
_SPACE_BEFORE_SEP_RE = re.compile(r"\s+(?=[/-])")
# A real number carries its (financial) year: ".../2026-27", ".../13-14", ".../2011"
_HAS_YEAR_RE = re.compile(r"\d{2}-\d{2}|/\d{4}\b")

_POST_OF_RE = re.compile(r"\bfor\s+(?:the\s+)?post\s+of\s+(?P<posts>[^()]+?)\s*$", re.I)
_TRAILING_YEAR_RE = re.compile(r"(?:\s*-?\s*\d{4})+$")
_TRAILING_EXAM_RE = re.compile(r"\s+Exam(?:ination)?$", re.I)

def _clean(text: str) -> str:
    return _WS_RE.sub(" ", unescape(text)).strip()

class _Tables(HTMLParser):
    """Every <table> as rows of cells; each cell is (text, [href, ...]). Nested tables are flattened."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: List[List[List[Tuple[str, List[str]]]]] = []
        self._depth = 0
        self._row: Optional[List[Tuple[str, List[str]]]] = None
        self._cell: Optional[Tuple[List[str], List[str]]] = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._depth += 1
            if self._depth == 1:
                self.tables.append([])
        elif self._depth == 0:
            return
        elif tag == "tr":
            self._close_row()
            self._row = []
        elif tag in ("td", "th"):
            self._close_cell()
            if self._row is None:
                self._row = []
            self._cell = ([], [])
        elif tag == "a" and self._cell is not None:
            href = dict(attrs).get("href")
            if href:
                self._cell[1].append(href)
        elif tag == "br" and self._cell is not None:
            self._cell[0].append(" ")

    def handle_endtag(self, tag):
        if self._depth == 0:
            return
        if tag in ("td", "th"):
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
        if all(w in name.lower() for w in words):
            return i
    return None

def _parse_date(text: str) -> Optional[date]:
    m = _DATE_RE.match(text)
    if not m:
        return None
    day, month, year = (int(g) for g in m.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None

def _advertisement_number(description: str) -> Optional[Tuple[str, str]]:
    """(number, exact text it was read from), or None when no advertisement number is stated."""
    for m in _ADVT_NUMBER_RE.finditer(description):
        raw = m.group("num").rstrip(".,;:)/")
        num = _SPACE_BEFORE_SEP_RE.sub("", raw)
        if _HAS_YEAR_RE.search(num):
            end = m.start("num") + len(raw)
            return num, description[m.start():end]
    return None

def _kind(description: str) -> Optional[Tuple[str, str]]:
    m = _CORRIGENDUM_LABEL_RE.match(description)
    if m:
        return "CORRIGENDUM", m.group(0)
    m = _ADVT_LABEL_RE.match(description)
    if m:
        return "VACANCY", m.group(0)
    return None

def _post_names(description: str) -> Optional[Tuple[List[str], str]]:
    m = _POST_OF_RE.search(description)
    if not m:
        return None
    posts_text = _TRAILING_EXAM_RE.sub("", _TRAILING_YEAR_RE.sub("", m.group("posts"))).strip(" -")
    posts = [p.strip(" -") for p in re.split(r"\s+and\s+", posts_text, flags=re.I) if p.strip(" -")]
    return (posts, m.group(0)) if posts else None

def _listing_table(html: str) -> Optional[Tuple[List[str], List[List[Tuple[str, List[str]]]]]]:
    parser = _Tables()
    parser.feed(html)
    parser.close()
    for table in parser.tables:
        if not table:
            continue
        header = [text for text, _ in table[0]]
        if _column(header, "release", "date") is not None and _column(header, "description") is not None:
            return header, table[1:]
    return None

def parse_listing(html: str, base_url: str) -> List[ListingRow]:
    found = _listing_table(html)
    if not found:
        return []
    header, body = found
    col_date = _column(header, "release", "date")
    col_exam = _column(header, "exam")
    col_desc = _column(header, "description")
    col_link = _column(header, "link")
    policy = UrlPolicy.from_url(base_url)

    rows: List[ListingRow] = []
    seen = set()
    for cells in body:
        if len(cells) != len(header):
            continue
        hrefs = cells[col_link][1] if col_link is not None else []
        if not hrefs:
            hrefs = [h for _, cell_hrefs in cells for h in cell_hrefs]
        href = next((h for h in hrefs if not h.strip().lower().startswith(("javascript:", "mailto:", "#"))), None)
        if not href:
            continue
        url = urldefrag(urljoin(base_url, href.strip()))[0]
        if urlparse(url).scheme not in ("http", "https") or not policy.allows(url) or url in seen:
            continue

        description = cells[col_desc][0]
        exam_name = cells[col_exam][0] if col_exam is not None else ""
        title = description or exam_name
        if not title:
            continue
        seen.add(url)
        row = ListingRow(title=title, url=url)
        row.evidence["title"] = title
        row.evidence["url"] = href

        release_text = cells[col_date][0]
        published = _parse_date(release_text)
        if published:
            row.published_on = published
            row.evidence["published_on"] = release_text

        advt = _advertisement_number(description)
        if advt:
            row.advertisement_number, row.evidence["advertisement_number"] = advt

        kind = _kind(description)
        if kind:
            row.kind_hint, row.evidence["kind_hint"] = kind

        posts = _post_names(description)
        if posts:
            row.post_names, row.evidence["post_names"] = posts

        rows.append(row)
    return rows
