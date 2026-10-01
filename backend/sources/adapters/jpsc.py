"""
Jharkhand Public Service Commission (www.jpsc.gov.in) listing adapter.

The registry page is the homepage, which carries the live listings in three blocks:
  - right column: news tiles, `<li><a href=...><div class="scroll-content">Press Release<br/>
    regarding ... Advt.No.04/2024<br/>uploaded date :14-07-2026</div></a></li>`;
  - centre column: one-line tickers, `<div class="scroll-text"><a href=...>Press release
    regarding ... Advt.No.-22/2023 (17-07-2026)</a></div>`;
  - centre column: the "Latest Recruitments/Openings" list, `<ul id="ulid"><li><a
    href="exam_files.php?id=N">Recruitment of Drug Inspector Advt.No.-12/2025</a></li>`, each
    linking to a per-advertisement page.
A per-advertisement page (exam_files.php?id=N) has the advertisement as an `<h3>` heading inside
`<div class="testimonial">` followed by a table of document links such as
"Advertisement dtd.15-04-2026" or "Press Release and P.T Result dtd.02-07-2024".
The older section pages (advertisement.php, Results.php, circular_notices.php) are tables headed
by a section title (Advertisement / Corrigendum / Result / Answer Key) and a column header row
(Advt. No. | Particulars | Closing | Time, or Sl. No. | Particulars | Date of Publication).

What this adapter reads, only as the page states it:
  - title: the link text (whitespace collapsed). On a per-advertisement page the row title is
    "<heading>: <link text>"; evidence keeps the link text and the heading separately.
  - url: the link target resolved against the page and percent-encoded; only https links on
    jpsc.gov.in are kept (e.g. the SBI Collect payment link and the OTR portal are skipped).
  - advertisement_number: the "Advt. No." column, else the first "Advt.No.-NN/YYYY" in the text.
  - published_on: a "Date of Publication"/"Date of Publish" column, else a date the text labels
    as "uploaded date", "dtd." or "dated" (the last such label wins: "Order Dated 09-02-2026 ...
    dtd.01-06-2026" is listed on 01-06-2026). Bare dates such as the tickers' "(18-07-2026)" or
    "Admit Card for P.T. Examination, 05-03-2024" are not labelled and stay unread: they could be
    an exam date as easily as a posting date.
  - last_date: only the advertisement table's "Closing" column.
  - post_names: the phrase after "Recruitment of/for" up to the advertisement number, unless it
    names an examination/test or a generic "Posts" group.
  - kind_hint: a label the row text states (Corrigendum, Admit Card, Result / Answer Key / Cut
    off, Advertisement), else the section the page files the row under ("Corrigendum" table,
    "Result" table, "Latest Recruitments/Openings" list, ...).
Menus, footer, gallery and the RTI marquee are outside these blocks and are never read.
"""
import re
from datetime import date
from html.parser import HTMLParser
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote, urldefrag, urljoin, urlsplit, urlunsplit

from govcareeros.backend.sources.adapters.base import ListingRow
from govcareeros.backend.sources.url_guard import UrlPolicy

SOURCE = "JPSC"
LISTING_URLS = [
    "https://www.jpsc.gov.in/advertisement.php",
    "https://www.jpsc.gov.in/Results.php",
]

_POLICY = UrlPolicy("jpsc.gov.in")

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
# Opening one of these closes an open sibling of the same kind (the site always closes them,
# but this keeps a stray omission from nesting rows)
_AUTO_CLOSE = {"tr": {"table", "tbody", "thead"}, "td": {"tr"}, "th": {"tr"}, "li": {"ul", "ol"}}

_DATE = r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})"
_DATE_RE = re.compile(rf"^\s*{_DATE}\s*$")
_LABELLED_DATE_RE = re.compile(rf"(?<![A-Za-z])(?:uploaded\s*date|dtd|dated)\s*[.:\-–]*\s*{_DATE}", re.I)
_ADVT_RE = re.compile(r"Advt\.?\s*No\.?\s*[-–:.]?\s*(\d{1,3}/\d{4})", re.I)
_ADVT_CELL_RE = re.compile(r"^\s*(\d{1,3}/\d{4})\s*$")
_POST_RE = re.compile(r"Recruitment\s+(?:of|for)\s+(.+?)\s*(?:\((?:Regular|Backlog)\))?\s*[,.]?\s*\(?\s*Advt\b", re.I)
_NOT_A_POST_RE = re.compile(r"\bExamination\b|\bTest\b|\bPosts\b", re.I)

# First match wins: a corrigendum to a result is a corrigendum
_KIND_RULES = [
    ("CORRIGENDUM", re.compile(r"\bCorrigendum\b", re.I)),
    ("ADMIT_CARD", re.compile(r"\bAdmit\s*Card\b|\bE-?Admit\b", re.I)),
    ("RESULT", re.compile(r"\bResults?\b|\bAnswer\s*Key\b|\bCut\s*-?\s*off\s+Marks\b|\bMerit\s+List\b|\bSelection\s+List\b", re.I)),
    ("VACANCY", re.compile(r"\bAdvertisement\b", re.I)),
]
_SECTION_KINDS = [
    ("CORRIGENDUM", re.compile(r"^\s*Corrigendum\s*$", re.I)),
    ("RESULT", re.compile(r"^\s*(?:Results?|Answer\s+Keys?)\s*$", re.I)),
    ("VACANCY", re.compile(r"^\s*Advertisements?\s*$|Latest\s+Recruitments?\s*/\s*Openings", re.I)),
]

class _Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: Dict[str, str], parent: Optional["_Node"]):
        self.tag, self.attrs, self.children, self.parent = tag, attrs, [], parent

    def classes(self) -> List[str]:
        return (self.attrs.get("class") or "").split()

    def iter(self, tag: Optional[str] = None):
        for child in self.children:
            if isinstance(child, _Node):
                if tag is None or child.tag == tag:
                    yield child
                yield from child.iter(tag)

    def kids(self, tag: str) -> List["_Node"]:
        return [c for c in self.children if isinstance(c, _Node) and c.tag == tag]

    def text(self) -> str:
        parts: List[str] = []

        def walk(node: "_Node"):
            for child in node.children:
                if isinstance(child, str):
                    parts.append(child)
                elif child.tag == "br":
                    parts.append(" ")
                elif child.tag not in ("script", "style"):
                    walk(child)
        walk(self)
        return re.sub(r"\s+", " ", "".join(parts)).strip()

class _TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root", {}, None)
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        closes = _AUTO_CLOSE.get(tag)
        if closes:
            for i in range(len(self.stack) - 1, 0, -1):
                if self.stack[i].tag in closes:
                    break
                if self.stack[i].tag == tag:
                    del self.stack[i:]
                    break
        node = _Node(tag, {k: (v or "") for k, v in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        node = _Node(tag, {k: (v or "") for k, v in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return
        # stray end tag (the site has a few, e.g. "</a></span></td>"): ignored

    def handle_data(self, data):
        self.stack[-1].children.append(data)

def _parse(html: str) -> _Node:
    builder = _TreeBuilder()
    builder.feed(html)
    builder.close()
    return builder.root

def _absolute(href: str, base_url: str) -> Optional[str]:
    href = (href or "").strip()
    if not href or href.startswith("#") or href.lower().startswith(("javascript:", "mailto:", "tel:")):
        return None
    url = urldefrag(urljoin(base_url, href))[0]
    parts = urlsplit(url)
    path = quote(parts.path, safe="/%!$&'()*+,;=:@-._~")
    query = quote(parts.query, safe="=&%+/:,;@!$'()*-._~")
    url = urlunsplit((parts.scheme, parts.netloc, path, query, ""))
    return url if _POLICY.allows(url) else None

def _to_date(day: str, month: str, year: str) -> Optional[date]:
    try:
        return date(int(year), int(month), int(day))
    except ValueError:
        return None

def _first_link(node: _Node) -> Optional[_Node]:
    return next((a for a in node.iter("a") if a.attrs.get("href")), None)

class _Row:
    """Accumulates one listing row; every value is set together with the text it came from."""

    def __init__(self, title: str, url: str, title_evidence: str):
        self.row = ListingRow(title=title, url=url, evidence={"title": title_evidence})

    def advt(self, number: str, evidence: str):
        if self.row.advertisement_number is None:
            self.row.advertisement_number, self.row.evidence["advertisement_number"] = number, evidence

    def advt_from(self, text: str):
        m = _ADVT_RE.search(text)
        if m:
            self.advt(m.group(1), m.group(0))

    def published_from_label(self, text: str):
        matches = list(_LABELLED_DATE_RE.finditer(text))
        if matches and self.row.published_on is None:
            m = matches[-1]
            day = _to_date(*m.groups())
            if day:
                self.row.published_on, self.row.evidence["published_on"] = day, m.group(0)

    def date_cell(self, field_name: str, cell_text: str):
        m = _DATE_RE.match(cell_text)
        day = _to_date(*m.groups()) if m else None
        if day:
            setattr(self.row, field_name, day)
            self.row.evidence[field_name] = cell_text.strip()

    def posts_from(self, text: str):
        m = _POST_RE.search(text)
        if m and not self.row.post_names:
            name = m.group(1).strip(" ,.")
            if name and not _NOT_A_POST_RE.search(name):
                self.row.post_names, self.row.evidence["post_names"] = [name], m.group(1).strip()

    def kind_from(self, text: str, section: Optional[str] = None):
        for kind, pattern in _KIND_RULES:
            m = pattern.search(text)
            if m:
                self.row.kind_hint, self.row.evidence["kind_hint"] = kind, m.group(0)
                return
        if section:
            for kind, pattern in _SECTION_KINDS:
                if pattern.search(section):
                    self.row.kind_hint, self.row.evidence["kind_hint"] = kind, section
                    return

def _text_row(link: _Node, text: str, base_url: str, section: Optional[str] = None) -> Optional[ListingRow]:
    url = _absolute(link.attrs.get("href", ""), base_url)
    if not url or not text:
        return None
    row = _Row(text, url, text)
    row.advt_from(text)
    row.published_from_label(text)
    row.posts_from(text)
    row.kind_from(text, section)
    return row.row

def _homepage_rows(root: _Node, base_url: str) -> List[ListingRow]:
    rows: List[Optional[ListingRow]] = []
    # Right column news tiles
    for div in root.iter("div"):
        if "scroll-content" in div.classes() and div.parent is not None and div.parent.tag == "a":
            rows.append(_text_row(div.parent, div.text(), base_url))
    # Centre column tickers
    for div in root.iter("div"):
        if "scroll-text" in div.classes():
            link = _first_link(div)
            if link is not None:
                rows.append(_text_row(link, link.text(), base_url))
    # "Latest Recruitments/Openings": the section label is the span right before the list's box
    section = next((s.text() for s in root.iter("span") if _SECTION_KINDS[2][1].search(s.text())), None)
    for ul in root.iter("ul"):
        if ul.attrs.get("id") == "ulid":
            for li in ul.kids("li"):
                link = _first_link(li)
                if link is not None:
                    rows.append(_text_row(link, link.text(), base_url, section))
    return [r for r in rows if r]

def _advertisement_page_rows(root: _Node, base_url: str) -> List[ListingRow]:
    rows: List[ListingRow] = []
    for box in root.iter("div"):
        if "testimonial" not in box.classes():
            continue
        heading_node = next(box.iter("h3"), None)
        heading = heading_node.text() if heading_node is not None else ""
        for td in box.iter("td"):
            link = _first_link(td)
            if link is None:
                continue
            text = link.text()
            url = _absolute(link.attrs.get("href", ""), base_url)
            if not url or not text:
                continue
            row = _Row(f"{heading}: {text}" if heading else text, url, text)
            if heading:
                row.row.evidence["heading"] = heading
            row.advt_from(text)
            row.advt_from(heading)
            row.published_from_label(text)
            row.posts_from(heading)
            row.kind_from(text)
            rows.append(row.row)
    return rows

def _table_rows(root: _Node, base_url: str) -> List[ListingRow]:
    rows: List[ListingRow] = []
    for table in root.iter("table"):
        trs = [tr for tr in table.iter("tr") if _owner_table(tr) is table]
        section, columns, body_start = None, None, 0
        for i, tr in enumerate(trs[:3]):
            th = tr.kids("th")
            if th and section is None:
                section = th[0].text()
            cells = [c.text() for c in tr.kids("td")]
            if any(re.fullmatch(r"Particulars", c, re.I) for c in cells):
                columns, body_start = cells, i + 1
                break
        if not columns:
            continue
        col = {name: idx for idx, name in enumerate(columns)}

        def find(pattern: str) -> Optional[int]:
            return next((idx for name, idx in col.items() if re.fullmatch(pattern, name, re.I)), None)
        i_part = find(r"Particulars")
        i_advt = find(r"Advt\.?\s*No\.?")
        i_pub = find(r"Date\s+of\s+Publi(?:cation|sh)")
        i_close = find(r"Closing(?:\s+Date)?|Last\s+Date")
        for tr in trs[body_start:]:
            cells = tr.kids("td")
            if i_part is None or len(cells) <= i_part:
                continue
            link = _first_link(cells[i_part])
            if link is None:
                continue
            text = cells[i_part].text()
            url = _absolute(link.attrs.get("href", ""), base_url)
            if not url or not text:
                continue
            row = _Row(text, url, text)
            if i_advt is not None and i_advt < len(cells):
                m = _ADVT_CELL_RE.match(cells[i_advt].text())
                if m:
                    row.advt(m.group(1), cells[i_advt].text())
            row.advt_from(text)
            if i_pub is not None and i_pub < len(cells):
                row.date_cell("published_on", cells[i_pub].text())
            if i_close is not None and i_close < len(cells):
                row.date_cell("last_date", cells[i_close].text())
            row.published_from_label(text)
            row.posts_from(text)
            row.kind_from(text, section)
            rows.append(row.row)
    return rows

def _owner_table(node: _Node) -> Optional[_Node]:
    parent = node.parent
    while parent is not None and parent.tag != "table":
        parent = parent.parent
    return parent

def parse_listing(html: str, base_url: str) -> List[ListingRow]:
    root = _parse(html)
    # Page-specific blocks first: the sidebar tiles repeat on every page and may link the same
    # document a per-advertisement page lists with more context
    rows = _advertisement_page_rows(root, base_url) + _table_rows(root, base_url) + _homepage_rows(root, base_url)
    seen, out = set(), []
    for row in rows:
        if row.url in seen:
            continue
        seen.add(row.url)
        out.append(row)
    return out
