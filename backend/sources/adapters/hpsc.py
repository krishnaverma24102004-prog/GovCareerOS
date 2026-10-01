"""
HPSC (Haryana Public Service Commission) listing adapter.

hpsc.gov.in is a DotNetNuke site; its listing pages are server-rendered and come in two shapes.

1. Date tables (Recruitment > "Advertisement/ Corrigendum" at /en-us/Instructions, Examination >
   "Results", Examination > "Admit card"):

       Date (dd.mm.yyyy) | Advertisements  | Link for Applying | Instructions
       Date (dd.mm.yyyy) | Subject

   The Advertisements/Subject cell links the notice itself; the other columns (apply-online portal,
   "Advertisement Closed", instruction PDFs) are not separate notices and are not read as rows.

2. The homepage (the registry URL, /en-us/) "What's New" list: one link per notice, whose text
   carries its own date ("Announcement dated 30.09.2026 regarding ...", "Result-II dated 28.09.2026 ...").
   The same list is a sidebar on every other page; there only the page's own table is read.

Each notice becomes a ListingRow:
  title                 the link cell's text; on old advertisement rows the trailing
                        "(Closing date for Online Submission: ...)" remark is left out of the title
  url                   the notice's own link, absolute; legacy absolute http:// links to hpsc.gov.in are
                        given as https:// (the site serves the same page there, and url_guard is https-only)
  published_on          the row's "Date" cell; in "What's New", the notice's own leading "<name> dated dd.mm.yyyy"
  last_date             only the page's "Closing date for Online Submission: dd.mm.yyyy" (old advertisement rows);
                        never anything else (the hard-copy dates shown next to it are not the closing date)
  advertisement_number  the first advertisement the row names ("Advt. No. 25 of 2026" -> "25/2026",
                        "(Advt. No. 18/2025)" -> "18/2025", "Advt No. 05 to 17 of 2026" -> "05 to 17 of 2026")
  kind_hint             CORRIGENDUM (text opens with "Corrigendum"), ADMIT_CARD ("admit card" in the text),
                        RESULT ("Result" in the text), VACANCY (text opens with "Advt"/"Advertisement");
                        otherwise, on the Results / Admit card pages, the page's own heading
  post_names            the posts the row lists after "for the post(s) of" or after "Advt. No. ... -", without the
                        department / cadre that follows; split only where the page separates posts
                        ("(i) ... (ii) ...", one "(Advt. No. ...)" per post, or a comma list of three or more)

Only those tables and that list are read, so the menus, the "Apply Online" sidebar, the footer and the
slider are skipped. A link outside hpsc.gov.in (one old corrigendum points at the exam agency's
www.digialm.com) is skipped; a document listed twice on a page is one row, from its first listing.
Every filled field carries the exact page text it came from in `evidence`.
"""
import re
from datetime import date
from html import unescape
from html.parser import HTMLParser
from typing import Dict, List, Optional, Tuple
from urllib.parse import urldefrag, urljoin, urlparse

from govcareeros.backend.sources.adapters.base import ListingRow
from govcareeros.backend.sources.url_guard import UrlPolicy

SOURCE = "HPSC"

LISTING_URLS = [
    "https://hpsc.gov.in/en-us/Instructions",
    "https://hpsc.gov.in/en-us/Examination/Results",
    "https://hpsc.gov.in/en-us/Examination/Admit-card",
]

_WS_RE = re.compile(r"\s+")
_DATE_RE = re.compile(r"^(\d{1,2})[./-](\d{1,2})[./-](\d{4})$")
_DMY = r"\d{1,2}[./-]\d{1,2}[./-]\d{4}"

# "Announcement dated 30.09.2026 regarding ...", "Result-II dated 28.09.2026 (In continuation of result dated ...)":
# only a "dated" in the notice's own leading name, never one inside a parenthesis or later in the sentence
_OWN_DATED_RE = re.compile(rf"^[^()]*?\bdated\s+(?P<date>{_DMY})", re.I)
_CLOSING_RE = re.compile(rf"Closing\s+date\s+for\s+Online\s+Submission\s*:\s*(?P<date>{_DMY})", re.I)
_CLOSING_REMARK_RE = re.compile(r"\s*\(\s*Closing\s+date\s+for\s+Online\s+Submission\b", re.I)

# Advertisement numbers as the page writes them: "25 of 2026", "19/2025", "1(4) of 2018", "6(2)/2015",
# "11(Viii)/2021", "4(2,4, & 6) of 2016", "05 to 17 of 2026", "18 & 19 of 2026", "26 of 2023 & 36/2023"
_ONE_NUMBER = r"\d+(?:\s*\([^()]*\))?(?:\s*(?:/|\bof\b)\s*\d{4})?"
_ADVT_RE = re.compile(
    r"\b(?:Advertisement|Advt)\b\.?\s*(?P<label>No\b\.?\s*:?\s*)?"
    rf"(?P<num>{_ONE_NUMBER}(?:\s*(?:,|&|\band\b|\bto\b)\s*{_ONE_NUMBER})*)", re.I)
_SIMPLE_OF_RE = re.compile(r"^(\S+?)\s+of\s+(\d{4})$", re.I)

_CORRIGENDUM_RE = re.compile(r"^Corrigendum\b", re.I)
_ADMIT_CARD_RE = re.compile(r"\badmit\s*card\b", re.I)
_RESULT_RE = re.compile(r"\bResults?\b", re.I)
_VACANCY_RE = re.compile(r"^(?:Advertisement|Advt)\b\.?", re.I)
_HEADING_KINDS = (("RESULT", re.compile(r"^Results?$", re.I)), ("ADMIT_CARD", re.compile(r"^Admit\s*card$", re.I)))

# Where the posts are listed: after "Advt. No. 25 of 2026 - ", or after "for the post(s) of" (skipping an
# "Advt. No. 41/2022 - " the page sometimes puts first)
_NUMBERS = rf"{_ONE_NUMBER}(?:\s*(?:,|&|\band\b|\bto\b)\s*{_ONE_NUMBER})*"
_ADVT_NUM = rf"(?:Advertisement|Advt)\b\.?\s*(?:No\b\.?\s*:?\s*)?{_NUMBERS}"
_ADVT_DASH_RE = re.compile(rf"\b{_ADVT_NUM}\s+-\s+(?:for\s+(?:the\s+)?posts?\s+of\s+)?", re.I)
_POSTS_OF_RE = re.compile(rf"\bfor\s+(?:the\s+)?posts?\s+of\b\s*-?\s*(?:{_ADVT_NUM}\s*(?:-|\bof\b)\s*)?", re.I)
# End of the post list: an advertisement / closing-date remark, or a "regarding" / "dated" / "for the exam" /
# "held on" / "in recruitment to" / "of <X> Category" clause
_POSTS_END_RE = re.compile(
    r"\s*\((?:Advt|Advertisement|Closing)\b|,?\s+(?:Advt|Advertisement)\b\.?\s*No\b|\s+regarding\b|\s+dated\b"
    r"|,?\s+for\s+the\s+exam\b|\s+in\s+recruitment\b|\s+held\s+on\b|\s+of\s+\w+\s+Category\b", re.I)
_ADVT_GROUP_RE = re.compile(r"\s*\((?:Advt|Advertisement)\b(?:[^()]|\([^()]*\))*\)", re.I)
# The organisation a post belongs to: " in <...Department/Corporation/...>" (no further " in " before the keyword)
_ORG_RE = re.compile(
    r"\s+in\s+(?:the\s+)?(?=(?:(?!\s+in\s).)*?\b(?:Department|Departmen|Directorate|Corporation|Board|Laboratory|Ltd|"
    r"Limited|Agency|Centre|Defence|HVPNL|HPGCL|HSIIDC|Hartron|FSL|College|Hospital|Commission|University|Institute)\b)",
    re.I)
# Cadre qualifier, not part of the post name: "... for Rest of Haryana Cadre & Mewat Cadre", "... for Mewat Cadre"
_CADRE_RE = re.compile(r"\s+for\s+(?:the\s+)?(?:Rest\s+of\s+\w+|ROH|Mewat|Haryana)\b.*?\bCadres?\b.*$", re.I)
_ENUM_RE = re.compile(r"\(\s*(?:i{1,3}|iv|v|vi{0,3}|ix|x)\s*\)\s*", re.I)
_TRAILING_EXAM_RE = re.compile(
    r"(?:\s*-?\s*(?:(?:Main|Mains|Preliminary|Pre)\s+)?Exam(?:ination)?)?\s*-?\s*\d{4}(?:\s*-\s*\d{2,4})?\s*$", re.I)
_VARIOUS_RE = re.compile(r"^various\s+posts?\b", re.I)


def _clean(text: str) -> str:
    return _WS_RE.sub(" ", unescape(text)).strip()

class _Page(HTMLParser):
    """The page heading, every <table> (rows of cells: text and links), and the "What's New" links."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.heading = ""
        self.tables: List[List[List[Tuple[str, List[Tuple[str, str]]]]]] = []
        self.whats_new: List[Tuple[str, str]] = []  # (href, link text)
        self._div_depth = 0
        self._heading_depth: Optional[int] = None
        self._heading_parts: List[str] = []
        self._news_depth: Optional[int] = None
        self._table_depth = 0
        self._row: Optional[list] = None
        self._cell: Optional[Tuple[List[str], List[Tuple[str, str]]]] = None
        self._link: Optional[Tuple[str, List[str]]] = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "div":
            self._div_depth += 1
            classes = (a.get("class") or "").split()
            if "pagetitle" in classes and self._heading_depth is None and not self.heading:
                self._heading_depth = self._div_depth
            if "whats_new" in classes and self._news_depth is None:
                self._news_depth = self._div_depth
        elif tag == "table":
            self._table_depth += 1
            if self._table_depth == 1:
                self.tables.append([])
        elif tag == "tr" and self._table_depth:
            self._close_row()
            self._row = []
        elif tag in ("td", "th") and self._table_depth:
            self._close_cell()
            if self._row is None:
                self._row = []
            self._cell = ([], [])
        elif tag == "a":
            href = a.get("href")
            if href and (self._cell is not None or self._news_depth is not None):
                self._link = (href, [])
        elif tag == "br":
            self._data(" ")

    def handle_endtag(self, tag):
        if tag == "div":
            if self._heading_depth == self._div_depth:
                self.heading = _clean("".join(self._heading_parts))
                self._heading_depth = None
            if self._news_depth == self._div_depth:
                self._news_depth = None
            self._div_depth -= 1
        elif tag == "a" and self._link is not None:
            href, parts = self._link
            text = _clean("".join(parts))
            if self._cell is not None:
                self._cell[1].append((href, text))
            elif self._news_depth is not None:
                self.whats_new.append((href, text))
            self._link = None
        elif not self._table_depth:
            return
        elif tag in ("td", "th"):
            self._close_cell()
        elif tag == "tr":
            self._close_row()
        elif tag == "table":
            self._close_row()
            self._table_depth -= 1

    def handle_data(self, data):
        self._data(data)

    def _data(self, data):
        if self._heading_depth is not None:
            self._heading_parts.append(data)
        if self._cell is not None:
            self._cell[0].append(data)
        if self._link is not None:
            self._link[1].append(data)

    def _close_cell(self):
        if self._cell is not None and self._row is not None:
            self._row.append((_clean("".join(self._cell[0])), self._cell[1]))
        self._cell = None

    def _close_row(self):
        self._close_cell()
        if self._row and self.tables:
            self.tables[-1].append(self._row)
        self._row = None

def _parse_date(text: str) -> Optional[date]:
    m = _DATE_RE.match(text.strip())
    if not m:
        return None
    day, month, year = (int(g) for g in m.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None

def _advertisement_number(text: str) -> Optional[Tuple[str, str]]:
    """(number, exact text it was read from) for the first advertisement the text names."""
    for m in _ADVT_RE.finditer(text):
        num = _WS_RE.sub(" ", m.group("num")).strip()
        # A bare number counts only behind a "No." label ("Advertisement No. 12 for Various Posts")
        if not re.search(r"\d{4}", num) and not m.group("label"):
            continue
        simple = _SIMPLE_OF_RE.match(num)
        if simple:
            num = f"{simple.group(1)}/{simple.group(2)}"
        num = re.sub(r"\s*/\s*", "/", num)
        return num, text[m.start():m.end()]
    return None

def _kind(text: str, heading: str) -> Optional[Tuple[str, str]]:
    m = _CORRIGENDUM_RE.search(text)
    if m:
        return "CORRIGENDUM", m.group(0)
    m = _ADMIT_CARD_RE.search(text)
    if m:
        return "ADMIT_CARD", m.group(0)
    m = _RESULT_RE.search(text)
    if m:
        return "RESULT", m.group(0)
    m = _VACANCY_RE.search(text)
    if m:
        return "VACANCY", m.group(0)
    for kind, heading_re in _HEADING_KINDS:
        if heading_re.match(heading):
            return kind, heading
    return None

def _split_top_level_commas(text: str) -> List[str]:
    parts, depth, start = [], 0, 0
    for i, ch in enumerate(text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch == "," and depth == 0:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return parts

def _post_name(text: str) -> str:
    org = _ORG_RE.search(text)
    if org:
        text = text[:org.start()]
    text = _CADRE_RE.sub("", text)
    # "(i) Lecturer (Financial Management) and (ii) ...": the joining word belongs to the list, not the post
    text = re.sub(r"\s+(?:and|&)\s*$", "", text, flags=re.I)
    return _TRAILING_EXAM_RE.sub("", text.strip(" ,.-&")).strip(" ,.-&")

def _post_names(title: str) -> Optional[Tuple[List[str], str]]:
    """The posts the row lists, as the page writes them, and the exact text they were read from.

    A list is split only where the page itself separates the posts: "(i) ... (ii) ...", one
    "(Advt. No. ...)" per post, or a comma list of three or more. "X and Y" stays one entry, since the
    page also writes single posts that way ("Assistant Director, Industrial Safety and Health")."""
    found = [m for m in (_ADVT_DASH_RE.search(title), _POSTS_OF_RE.search(title)) if m]
    if not found:
        return None
    m = min(found, key=lambda x: x.start())
    rest = title[m.end():]
    if len(_ADVT_GROUP_RE.findall(rest)) >= 2:
        # "Lecturer in Textile Technology (Advt. No. 83/2024) & Lecturer in Fashion Design (Advt. No. 11(Viii)/2021)"
        end = _CLOSING_REMARK_RE.search(rest)
        region = rest[:end.start()] if end else rest
        items = [re.sub(r"^(?:\s*(?:,|&|\band\b))+\s*", "", seg, flags=re.I) for seg in _ADVT_GROUP_RE.split(region)]
        items = [seg for seg in items if seg.strip(" ,.-&")]
        posts = [_post_name(seg) for seg in items]
        if any(" " not in p or re.search(r"\bCadres?$", p, re.I) for p in posts):
            # "... in the subjects of Biology (Advt. ...), Chemistry (Advt. ...)", "... Haryana Cadre (Advt. ...) &
            # Mewat Cadre (Advt. ...)": the advertisements split subjects or cadres there, not posts
            return None
    else:
        end = _POSTS_END_RE.search(rest)
        region = rest[:end.start()] if end else rest
        posts = []
        for item in _ENUM_RE.split(region):
            item = _post_name(item)
            parts = _split_top_level_commas(item)
            parts = [p.strip() for p in parts]
            # "Botany, Zoology, Chemistry, Physics" lists subjects of one post, not posts
            posts += parts if len(parts) >= 3 and all(" " in p for p in parts) else [item]
    region = region.rstrip(" ,.-")
    names: List[str] = []
    for post in posts:
        if (post and post[0].isupper() and not _VARIOUS_RE.match(post) and post.count("(") == post.count(")")
                and post not in names):
            names.append(post)
    if not names or not region:
        return None
    return names, title[m.start():m.end()] + region


def _absolute(href: str, base_url: str, policy: UrlPolicy) -> Optional[str]:
    href = href.strip()
    if not href or href.lower().startswith(("javascript:", "mailto:", "#")):
        return None
    url = urldefrag(urljoin(base_url, href))[0]
    parsed = urlparse(url)
    if parsed.scheme == "http" and not policy.allow_http:
        https = parsed._replace(scheme="https").geturl()
        if policy.allows(https):
            url = https
    return url if policy.allows(url) else None

def _row(title_text: str, href: str, url: str, published_text: Optional[str], heading: str) -> Optional[ListingRow]:
    closing = _CLOSING_REMARK_RE.search(title_text)
    title = (title_text[:closing.start()] if closing else title_text).rstrip(" -")
    if not title:
        return None
    row = ListingRow(title=title, url=url)
    row.evidence["title"] = title
    row.evidence["url"] = href

    if published_text is not None:
        published = _parse_date(published_text)
        if published:
            row.published_on = published
            row.evidence["published_on"] = published_text
    else:
        own = _OWN_DATED_RE.search(title)
        published = _parse_date(own.group("date")) if own else None
        if published:
            row.published_on = published
            row.evidence["published_on"] = own.group(0)[own.group(0).lower().rfind("dated"):]

    closes = _CLOSING_RE.search(title_text)
    last = _parse_date(closes.group("date")) if closes else None
    if last:
        row.last_date = last
        row.evidence["last_date"] = closes.group(0)

    advt = _advertisement_number(title)
    if advt:
        row.advertisement_number, row.evidence["advertisement_number"] = advt

    kind = _kind(title, heading)
    if kind:
        row.kind_hint, row.evidence["kind_hint"] = kind

    posts = _post_names(title)
    if posts:
        row.post_names, row.evidence["post_names"] = posts
    return row

def _column(header: List[str], *names: str) -> Optional[int]:
    for i, text in enumerate(header):
        if text.lower() in names:
            return i
    return None

def parse_listing(html: str, base_url: str) -> List[ListingRow]:
    page = _Page()
    page.feed(html)
    page.close()
    policy = UrlPolicy.from_url(base_url)
    rows: List[ListingRow] = []
    seen: Dict[str, ListingRow] = {}

    def add(title_text: str, href: str, published_text: Optional[str]):
        url = _absolute(href, base_url, policy)
        if not url or url in seen:
            return
        row = _row(title_text, href.strip(), url, published_text, page.heading)
        if row:
            seen[url] = row
            rows.append(row)

    for table in page.tables:
        if not table:
            continue
        header = [text for text, _ in table[0]]
        col_date = _column(header, "date")
        col_title = _column(header, "advertisements", "subject")
        if col_date is None or col_title is None:
            continue
        for cells in table[1:]:
            if len(cells) <= max(col_date, col_title):
                continue
            title_text, links = cells[col_title]
            if links:
                add(title_text, links[0][0], cells[col_date][0])

    if not rows:
        # The homepage has no listing table; its "What's New" list is the listing. (The same list is a
        # sidebar on the other pages, where the page's own table is read instead.)
        for href, text in page.whats_new:
            if text:
                add(text, href, None)
    return rows
