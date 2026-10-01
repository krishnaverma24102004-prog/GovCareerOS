"""
STEEL (Ministry of Steel) listing adapter.

https://steel.gov.in/vacancy is a static Drupal page. Its listing is four blocks of the
"recruitment_vacancy" view, each a <div class="views-element-container"
id="block-steel-views-block-recruitment-vacancy-..."> with an <h2> heading and one table:

    Recruitment Rules/Notification                 rules notified for the Ministry's posts
    CPSE Vacancies                                 board-level selections in SAIL, NMDC, RINL, ...
    MoS Vacancies                                  Ministry / attached-institute vacancies
    Young Professional/ Consultant/Trainee etc.

    S. No. | Title (link to /node/NNNN) | Documents (PDF links + "(1.99 MB)") | Validity Date ("02-09-2026")

The CPSE and MoS blocks share one pager; ?page=1 is their second page (LISTING_URLS).

Each table row becomes one ListingRow:
  title                 the Title cell's text
  url                   the first document in the Documents cell (the notice PDF), made absolute;
                        the row's /node/ page when the row lists no document
  advertisement_number  only where the title states one ("No.S-19022/10/2025-SD.I",
                        "VACCANCY NOTIFICATION NO. NISST/P&A/PERS/CON/2025-1")
  last_date             the Validity Date cell (dd-mm-yyyy), for rows outside the Recruitment Rules
                        block. In the notices it is the closing date for applications (e.g. the SRTMI
                        Young Professionals advertisement: validity 10-06-2025, "Last date for sending
                        completed application is 10.06.2025"). Rules have no closing date, so their
                        Validity Date is not reported as one.
  published_on          never: the page shows no publication date (the "tentative date for
                        publishing the Advertisement" in two titles is a plan, not a date of issue)
  kind_hint             RESULT / CORRIGENDUM when the title says so ("result", "admit card",
                        "Extension of last date", "Revised Notification", "corrigendum", ...);
                        RULES for recruitment-rules titles; VACANCY for rows under a "... Vacancies"
                        heading or whose title uses the page's vacancy wording ("Vacancy circular",
                        "Engagement of", "Selection for the post of", "Advertisement")
  post_names            from the title's "post(s) of ...", "Engagement of ..." and "recruitment of ..."
                        wording (not for RULES rows)

Every filled field carries the exact page text it came from in `evidence`. Only rows of those view
tables are read, so the site's menus, header, footer and pager links are skipped.
"""
import re
from datetime import date
from html import unescape
from html.parser import HTMLParser
from typing import Dict, List, Optional, Tuple
from urllib.parse import urldefrag, urljoin, urlparse

from govcareeros.backend.sources.adapters.base import ListingRow
from govcareeros.backend.sources.url_guard import UrlPolicy

SOURCE = "STEEL"

LISTING_URLS = [
    "https://steel.gov.in/vacancy",
    "https://steel.gov.in/vacancy?page=1",
]

_BLOCK_ID_PREFIX = "block-steel-views-block-recruitment-vacancy"

_WS_RE = re.compile(r"\s+")
_DATE_RE = re.compile(r"^(\d{1,2})-(\d{1,2})-(\d{4})$")

_RESULT_RE = re.compile(
    r"\bresults?\b|admit\s*card|e-?admit|call\s+letter|merit\s+list|selected\s+candidates|"
    r"short[\s-]?listed|shortlist|answer\s+key|interview\s+schedule", re.I)
_CORRIGENDUM_RE = re.compile(
    r"corrigendum|addendum|extension\s+of\s+(?:the\s+)?last\s+date|last\s+date\s+(?:is\s+)?extended|"
    r"date\s+extended|revised\s+notification|cancell?ation|cancell?ed|postpone", re.I)
_RULES_RE = re.compile(r"recruitment\s+rules|\bRRs?\b|service\s+rules", re.I)
_VACANCY_HEADING_RE = re.compile(r"vacanc", re.I)
_VACANCY_TITLE_RE = re.compile(
    r"vacancy\s+circular|vacanc(?:y|ies)\s+notification|^engagement\s+of|notification\s+for\s+engagement|"
    r"selection\s+for\s+the\s+post\s+of|^advertisement\b|filling\s+up\s+of|recruitment\s+(?:in|of|for)\b", re.I)

# "No.S-19022/10/2025-SD.I", "NOTIFICATION NO. NISST/P&A/PERS/CON/2025-1"
_ADVT_NUMBER_RE = re.compile(
    r"(?:\b(?:vacc?ancy\s+)?notification\s+|\badvt\.?\s*|\badvertisement\s+)?\bno\.\s*:?\s*"
    r"(?P<num>[A-Z0-9][A-Z0-9/.\-&()]*[A-Z0-9)])", re.I)

# Where a post name starts in a title
_POST_INTRO_RE = re.compile(r"\bposts?\s+of\s+(?:the\s+)?|\bengagement\s+of\s+|\brecruitment\s+of\s+", re.I)
# Where it ends: a comma, the place/basis that follows, a " -reg" / " - ..." tail, or a "[...]" note
_POST_END_RE = re.compile(
    r",|\s+(?:in|on|under|by|through|at|against|for|purely|with)\s+|\s+-|\s*\[|\.\s*$", re.I)
_LEADING_COUNT_RE = re.compile(r"^(?:'?\d+'?|one|two|three|four|five|six)\s+", re.I)
# "(General Central Service Group 'A' Gazetted Non Ministerial)" describes the post, it is not its name
_TRAILING_QUALIFIER_RE = re.compile(r"\s*\((?:[^()]*\b(?:group|level|gazetted|posts?)\b[^()]*)\)\s*$", re.I)

def _clean(text: str) -> str:
    return _WS_RE.sub(" ", unescape(text)).strip()

class _Cell:
    def __init__(self, column: str):
        self.column = column
        self.parts: List[str] = []
        self.links: List[Tuple[str, str]] = []   # (href as written, link text)

    @property
    def text(self) -> str:
        return _clean("".join(self.parts))

class _VacancyTables(HTMLParser):
    """Rows of the recruitment_vacancy view tables: (block heading, {column header: _Cell})."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: List[Tuple[str, Dict[str, _Cell]]] = []
        self._depth = 0                       # open <div>s since entering a view block (0 = outside)
        self._heading: Optional[List[str]] = None
        self._heading_text = ""
        self._columns: Dict[str, str] = {}    # <th id> -> header text
        self._th: Optional[Tuple[str, List[str]]] = None
        self._row: Optional[Dict[str, _Cell]] = None
        self._cell: Optional[_Cell] = None
        self._link: Optional[Tuple[str, List[str]]] = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "div":
            if self._depth:
                self._depth += 1
            elif (a.get("id") or "").startswith(_BLOCK_ID_PREFIX):
                self._depth = 1
                self._heading_text = ""
                self._columns = {}
            return
        if not self._depth:
            return
        if tag == "h2" and not self._heading_text:
            self._heading = []
        elif tag == "th":
            self._th = (a.get("id") or "", [])
        elif tag == "tr":
            self._row = {}
        elif tag == "td" and self._row is not None:
            column = self._columns.get(a.get("headers") or "", a.get("headers") or "")
            self._cell = _Cell(column)
        elif tag == "a" and self._cell is not None:
            self._link = (a.get("href") or "", [])

    def handle_endtag(self, tag):
        if tag == "div" and self._depth:
            self._depth -= 1
            return
        if not self._depth:
            return
        if tag == "h2" and self._heading is not None:
            self._heading_text = _clean("".join(self._heading))
            self._heading = None
        elif tag == "th" and self._th is not None:
            self._columns[self._th[0]] = _clean("".join(self._th[1]))
            self._th = None
        elif tag == "a" and self._link is not None and self._cell is not None:
            self._cell.links.append((self._link[0], _clean("".join(self._link[1]))))
            self._link = None
        elif tag == "td" and self._cell is not None and self._row is not None:
            self._row[self._cell.column] = self._cell
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append((self._heading_text, self._row))
            self._row = None

    def handle_data(self, data):
        if self._heading is not None:
            self._heading.append(data)
        if self._th is not None:
            self._th[1].append(data)
        if self._cell is not None:
            self._cell.parts.append(data)
        if self._link is not None:
            self._link[1].append(data)

def _date(text: str) -> Optional[date]:
    m = _DATE_RE.match(text)
    if not m:
        return None
    day, month, year = (int(g) for g in m.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None

def _kind(title: str, heading: str) -> Optional[Tuple[str, str]]:
    for kind, pattern in (("RESULT", _RESULT_RE), ("CORRIGENDUM", _CORRIGENDUM_RE), ("RULES", _RULES_RE)):
        m = pattern.search(title)
        if m:
            return kind, m.group(0)
    m = _VACANCY_HEADING_RE.search(heading)
    if m:
        return "VACANCY", heading
    m = _VACANCY_TITLE_RE.search(title)
    if m:
        return "VACANCY", m.group(0)
    return None

def _advertisement_number(title: str) -> Optional[Tuple[str, str]]:
    for m in _ADVT_NUMBER_RE.finditer(title):
        num = m.group("num")
        if "/" in num and re.search(r"\d", num):
            return num, m.group(0)
    return None

def _name_end(rest: str) -> int:
    """First _POST_END_RE match outside parentheses ("Director (Technical, Projects & Raw
    Materials), Steel Authority ..." ends before ", Steel")."""
    for end in _POST_END_RE.finditer(rest):
        before = rest[:end.start()]
        if before.count("(") <= before.count(")"):
            return end.start()
    return len(rest)

def _post_names(title: str) -> Optional[Tuple[List[str], str]]:
    posts: List[str] = []
    span: Optional[Tuple[int, int]] = None
    for m in _POST_INTRO_RE.finditer(title):
        rest = title[m.end():]
        segment = rest[:_name_end(rest)]
        name = _LEADING_COUNT_RE.sub("", segment.strip())
        while True:
            stripped = _TRAILING_QUALIFIER_RE.sub("", name)
            if stripped == name:
                break
            name = stripped
        found = False
        # "Young Professionals/ Consultant" names two posts
        for part in name.split("/"):
            part = part.strip(" -")
            if part and part not in posts:
                posts.append(part)
                found = True
        if found:
            span = (span[0] if span else m.start(), m.end() + len(segment.rstrip()))
    # Evidence: the exact stretch of the title from the first intro to the end of the last name
    return (posts, title[span[0]:span[1]]) if posts else None

def parse_listing(html: str, base_url: str) -> List[ListingRow]:
    parser = _VacancyTables()
    parser.feed(html)
    parser.close()
    policy = UrlPolicy.from_url(base_url)

    def absolute(href: str) -> Optional[str]:
        href = href.strip()
        if not href or href.startswith("#") or href.lower().startswith(("javascript:", "mailto:", "tel:")):
            return None
        url = urldefrag(urljoin(base_url, href))[0]
        if urlparse(url).scheme not in ("http", "https") or not policy.allows(url):
            return None
        return url

    rows: List[ListingRow] = []
    seen = set()
    for heading, cells in parser.rows:
        title_cell = cells.get("Title")
        if title_cell is None or not title_cell.text:
            continue
        title = title_cell.text
        target: Optional[Tuple[str, str]] = None
        for href, _text in (cells["Documents"].links if "Documents" in cells else []) + title_cell.links:
            url = absolute(href)
            if url:
                target = (url, href)
                break
        if target is None or target[0] in seen:
            continue
        seen.add(target[0])

        row = ListingRow(title=title, url=target[0])
        row.evidence["title"] = title
        row.evidence["url"] = target[1]

        kind = _kind(title, heading)
        if kind:
            row.kind_hint, row.evidence["kind_hint"] = kind

        advt = _advertisement_number(title)
        if advt:
            row.advertisement_number, row.evidence["advertisement_number"] = advt

        is_rules = row.kind_hint == "RULES" or bool(_RULES_RE.search(heading))
        validity = cells.get("Validity Date")
        if validity is not None and not is_rules:
            parsed = _date(validity.text)
            if parsed:
                row.last_date = parsed
                row.evidence["last_date"] = validity.text

        if not is_rules:
            posts = _post_names(title)
            if posts:
                row.post_names, row.evidence["post_names"] = posts

        rows.append(row)
    return rows
