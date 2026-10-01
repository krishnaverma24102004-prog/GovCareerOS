"""
UKPSC (Uttarakhand Public Service Commission) listing adapter.

The "Candidate Corner" pages of https://psc.uk.gov.in (Recruitment, Admit Card, Results, Answer Key
and Online Objection) each render one server-side table, <table class="display">, with the columns

    S.No | Advertisement Name | Title | Publish Date | [End Date] | View | [Link To Apply]

One table row is one examination; its Title / Publish Date / End Date / View cells each hold a small
stack of entries (a nested table, or <div class="table-div"> blocks), one per document, in the same
order. Each document becomes a ListingRow:

  title           "<Advertisement Name>: <Title entry>" (either alone when the other is empty);
                  evidence "<Advertisement Name> | <Title entry>", both exactly as the cells show them
  url             the entry's own PDF link in the View column, absolute, on psc.uk.gov.in only
  published_on    the entry's "Publish Date" (dd-mm-yyyy)
  last_date       the entry's "End Date" (dd-mm-yyyy), only for an entry labelled "Advertisement": that
                  column is the end of whatever window the document opens (application correction,
                  document submission, answer-key objections, ...), and only for the advertisement is it
                  the closing date for applications
  kind_hint       from the entry's own label (Corrigendum/Addendum, Admit Card, Answer Key,
                  Result / Cut Off Marks / Marks of Candidates, Advertisement); otherwise from the page's
                  own heading (Admit Card -> ADMIT_CARD, Results -> RESULT, Answer Key -> ANSWER_KEY).
                  The Recruitment heading alone labels nothing: that page also lists rejection lists,
                  office memos and checklists.
  advertisement_number, post_names
                  never: these pages show neither (the "Advertisement Name" column holds the exam name)

Entries are matched by position only when the cells hold the same number of entries; a row whose
View cell does not line up with its Title cell is skipped rather than guessed. The "Link To Apply"
column points at the separate application portals and is not read. Only that table is read, so the
side menu, header and footer links are skipped.

Locale: the site opens in Hindi, and in that locale the listing has no titles at all (the Advertisement
Name cell is empty and the Title cell shows a placeholder such as {"meta_value":"Uttarakhand Public
Service Commission"}). Such entries have nothing to title them and are skipped, so a page fetched in
the default locale yields no rows; the titled English listing is served after the site's own
language switch (https://psc.uk.gov.in/local/lang) in the same session.
"""
import re
from datetime import date
from html.parser import HTMLParser
from typing import List, Optional, Tuple
from urllib.parse import urldefrag, urljoin

from govcareeros.backend.sources.adapters.base import ListingRow
from govcareeros.backend.sources.url_guard import UrlPolicy

SOURCE = "UKPSC"

LISTING_URLS = [
    "https://psc.uk.gov.in/candidate-corner/admitcards",
    "https://psc.uk.gov.in/candidate-corner/results",
    "https://psc.uk.gov.in/candidate-corner/answerkey",
]

_WS_RE = re.compile(r"\s+")
_DATE_RE = re.compile(r"^(\d{1,2})-(\d{1,2})-(\d{4})$")
# Untranslated record shown in place of a title in the default (Hindi) locale
_PLACEHOLDER_RE = re.compile(r"^\{.*\"meta_value\".*\}$", re.S)

# The entry's own label; first match wins (a "Corrigendum (Revised Selection Result ...)" is a corrigendum)
_KIND_RULES = [
    ("CORRIGENDUM", re.compile(r"\bcorrigendum\b|\baddendum\b", re.I)),
    ("ADMIT_CARD", re.compile(r"\badmit\s*cards?\b", re.I)),
    ("ANSWER_KEY", re.compile(r"\banswer\s*keys?\b", re.I)),
    ("RESULT", re.compile(r"\bresults?\b|\bcut\s*-?\s*off\s+marks\b|\bmarks\s+of\s+candidates\b|\bcandidates?\s+marks\b"
                          r"|\bselection\s+list\b|\bmerit\s+list\b", re.I)),
    ("VACANCY", re.compile(r"\badvertisement\b", re.I)),
]
# The page's own heading (<h5 class="page-title">)
_PAGE_KINDS = [
    ("ADMIT_CARD", re.compile(r"^admit\s*cards?\b", re.I)),
    ("RESULT", re.compile(r"^results?$", re.I)),
    ("ANSWER_KEY", re.compile(r"^answer\s*keys?\b", re.I)),
]

Entry = Tuple[str, List[str]]  # (text, hrefs)

def _clean(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()

class _Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: dict, parent: Optional["_Node"]):
        self.tag, self.attrs, self.children, self.parent = tag, attrs, [], parent

    def elements(self, tag: str) -> List["_Node"]:
        return [c for c in self.children if isinstance(c, _Node) and c.tag == tag]

    def find_all(self, tag: str) -> List["_Node"]:
        out = []
        for c in self.children:
            if isinstance(c, _Node):
                if c.tag == tag:
                    out.append(c)
                out += c.find_all(tag)
        return out

    def classes(self) -> List[str]:
        return (self.attrs.get("class") or "").split()

    def text(self) -> str:
        return _clean("".join(c if isinstance(c, str) else " " + c.text() + " " for c in self.children))

    def hrefs(self) -> List[str]:
        return [a.attrs["href"].strip() for a in self.find_all("a") if (a.attrs.get("href") or "").strip()]

class _Tree(HTMLParser):
    """A small element tree; tolerant of unclosed <td>/<tr>/<div> as these pages write them."""

    _VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root", {}, None)
        self._cur = self.root

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, {k: (v or "") for k, v in attrs}, self._cur)
        self._cur.children.append(node)
        if tag not in self._VOID:
            self._cur = node

    def handle_startendtag(self, tag, attrs):
        self._cur.children.append(_Node(tag, {k: (v or "") for k, v in attrs}, self._cur))

    def handle_endtag(self, tag):
        node = self._cur
        while node is not None and node.tag != tag:
            if node.tag == "table":  # never close past an enclosing table
                return
            node = node.parent
        if node is not None and node.parent is not None:
            self._cur = node.parent

    def handle_data(self, data):
        self._cur.children.append(data)

def _rows(table: _Node) -> List[_Node]:
    """The table's own <tr>s (thead/tbody/direct), not those of nested tables."""
    out = []
    for child in table.elements("thead") + table.elements("tbody") + [table]:
        out += child.elements("tr")
    return out

def _entries(cell: _Node) -> List[Entry]:
    """The stacked entries of a cell: rows of a nested table, <div class="table-div"> blocks, or the cell itself."""
    nested = cell.find_all("table")
    if nested:
        return [(tr.text(), tr.hrefs()) for tr in _rows(nested[0])]
    divs = [d for d in cell.find_all("div") if "table-div" in d.classes()]
    if divs:
        return [(d.text(), d.hrefs()) for d in divs]
    return [(cell.text(), cell.hrefs())]

def _column(header: List[str], name: str) -> Optional[int]:
    for i, h in enumerate(header):
        if h.lower() == name:
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

def _kind(text: str, rules) -> Optional[Tuple[str, str]]:
    for kind, pattern in rules:
        m = pattern.search(text)
        if m:
            return kind, m.group(0)
    return None

def _page_title(tree: _Tree) -> str:
    for h in tree.root.find_all("h5"):
        if "page-title" in h.classes():
            return h.text()
    return ""

def _listing_table(tree: _Tree) -> Optional[_Node]:
    for table in tree.root.find_all("table"):
        if "display" in table.classes():
            return table
    return None

def _entry_at(entries: Optional[List[Entry]], i: int, expected: int) -> Optional[Entry]:
    """Entry i of a column, only when that column lists one entry per document."""
    if entries is None or len(entries) != expected:
        return None
    return entries[i]

def parse_listing(html: str, base_url: str) -> List[ListingRow]:
    tree = _Tree()
    tree.feed(html)
    tree.close()
    table = _listing_table(tree)
    if table is None:
        return []
    rows = _rows(table)
    header_row = next((tr for tr in rows if tr.elements("th")), None)
    if header_row is None:
        return []
    header = [th.text() for th in header_row.elements("th")]
    col_name, col_title = _column(header, "advertisement name"), _column(header, "title")
    col_pub, col_end, col_view = _column(header, "publish date"), _column(header, "end date"), _column(header, "view")
    if col_title is None or col_view is None:
        return []

    page_title = _page_title(tree)
    page_kind = _kind(page_title, _PAGE_KINDS)
    policy = UrlPolicy.from_url(base_url)
    out: List[ListingRow] = []
    seen = set()

    for tr in rows:
        cells = tr.elements("td")
        if len(cells) != len(header):
            continue
        exam = cells[col_name].text() if col_name is not None else ""
        if _PLACEHOLDER_RE.match(exam):
            exam = ""
        titles = _entries(cells[col_title])
        views = _entries(cells[col_view])
        if len(views) != len(titles):
            continue  # documents cannot be matched to their titles
        pubs = _entries(cells[col_pub]) if col_pub is not None else None
        ends = _entries(cells[col_end]) if col_end is not None else None

        for i, (doc, _) in enumerate(titles):
            if _PLACEHOLDER_RE.match(doc):
                doc = ""
            if not (exam or doc):
                continue  # nothing on the page names this document
            hrefs = views[i][1]
            if not hrefs:
                continue
            url = urldefrag(urljoin(base_url, hrefs[0]))[0]
            if not policy.allows(url) or url in seen:
                continue
            seen.add(url)

            row = ListingRow(title=f"{exam}: {doc}" if exam and doc else (exam or doc), url=url)
            row.evidence["title"] = f"{exam} | {doc}" if exam and doc else (exam or doc)
            row.evidence["url"] = hrefs[0]

            pub = _entry_at(pubs, i, len(titles))
            if pub and _parse_date(pub[0]):
                row.published_on, row.evidence["published_on"] = _parse_date(pub[0]), pub[0]
            kind = _kind(doc, _KIND_RULES) if doc else None
            end = _entry_at(ends, i, len(titles))
            if kind and kind[0] == "VACANCY" and end and _parse_date(end[0]):
                row.last_date, row.evidence["last_date"] = _parse_date(end[0]), end[0]

            if kind is None and page_kind:
                kind = (page_kind[0], page_title)
            if kind:
                row.kind_hint, row.evidence["kind_hint"] = kind
            out.append(row)
    return out
