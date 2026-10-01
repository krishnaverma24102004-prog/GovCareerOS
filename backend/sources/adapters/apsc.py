"""
Assam Public Service Commission (apsc.nic.in) listing adapter.

The site is static PHP pages. The listings this adapter reads:

  * Year pages with one data table inside a Bootstrap panel whose heading names the section:
      advt_2026.php            "Recruitment Advertisements"   Advt. No | Post | Published Date | Closing Date
      notifications_2026.php   "Notifications"                SL | Notification | Date
      corig_2026.php           "Corrigendums"                 SL No. | Corrigendum | Date
      scr_tst_result_2026.php  "Written/Screening Test Results" SL No. | Advt. No | Post | Date
      final-results-2026.php   "Final Results"                SL | Advt. No. | Post | Date
    Columns are mapped from the table's own header row, so the same code reads all of them.
  * The home page's "Latest Updates" list (<ul class="scroll-updates" id="latest-updates">) and the
    "Latest Recruitment Advertisement" box every page carries in its footer (<ul class="scroll-updates">
    whose items start with an "ADVT. NO. 12/2026" label). These lists show no dates.

What a row states, and what this adapter reads from it:
  - title / url: each document link in the row's title cell (or list item). Links whose whole text is a
    Bootstrap label ("APPLY HERE", "CLICK TO VIEW MARKS", answer-key paper buttons, "Guidelines for
    Payment ...") are buttons beside the document, not documents of their own, and are skipped. A cell with
    two documents (a final result and its cut-off notification) yields two rows sharing the cell's
    advertisement number and date. URLs are resolved against the page and percent-encoded (file names
    contain spaces); only https links on apsc.nic.in are kept, so the admit-card / application portal
    (apscrecruitment.in), the marks/objection portal (nbapps.info) and other sites are dropped.
  - advertisement_number: the "Advt. No" column, else the "ADVT. NO." label, else an "Advt. No. 03/2026"
    reference in the title itself.
  - published_on / last_date: the "Published Date" / "Date" and "Closing Date" columns, in the page's
    dd-mm-yyyy format. Dates that only appear inside a title ("Advt. No. 31/2025 dtd. 09-09-2025" is the
    advertisement's date, not this document's) and the "APPLICATION START DATE" label are never used.
    The home-page lists state no dates, so their rows have none.
  - post_names: only on the advertisement table, whose "Post" column names the post followed by its
    department ("Junior Engineer (Electrical), Public Works (Building & NH) Department"); the part before
    the first top-level ", " / " under " / " in " is the post. An examination ("Combined Competitive
    (Preliminary) Examination, 2025") is not a post and gives none.
  - kind_hint: the section heading the listing sits under (Recruitment Advertisement -> VACANCY,
    Corrigendum -> CORRIGENDUM, Results -> RESULT); under a heading that labels nothing ("Notifications",
    "Latest Updates") only the title's own label counts (Corrigendum, Result, Answer Key, Admit Card).
  - Menus, the year pagination, footer link columns and anything inside HTML comments are not listings
    and are never read.

LISTING_URLS are the 2026 year pages; the registry URL (the home page) is read as well. The admit-card
page (admit_cards_2026.php) is not listed: every link on it points at apscrecruitment.in.
"""
import re
from datetime import date
from html.parser import HTMLParser
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from govcareeros.backend.sources.adapters.base import ListingRow
from govcareeros.backend.sources.url_guard import UrlPolicy

SOURCE = "APSC"
LISTING_URLS = [
    "https://apsc.nic.in/advt_2026.php",
    "https://apsc.nic.in/notifications_2026.php",
    "https://apsc.nic.in/corig_2026.php",
    "https://apsc.nic.in/scr_tst_result_2026.php",
    "https://apsc.nic.in/final-results-2026.php",
]

# --- a minimal, tolerant DOM -------------------------------------------------------------------------

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

class _Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: Dict[str, str], parent: Optional["_Node"]):
        self.tag, self.attrs, self.children, self.parent = tag, attrs, [], parent

    @property
    def classes(self) -> List[str]:
        return (self.attrs.get("class") or "").split()

    def iter(self, tag: Optional[str] = None):
        for child in self.children:
            if isinstance(child, _Node):
                if tag is None or child.tag == tag:
                    yield child
                yield from child.iter(tag)

    def text(self) -> str:
        parts: List[str] = []

        def walk(node: "_Node"):
            for child in node.children:
                if isinstance(child, str):
                    parts.append(child)
                else:
                    if child.tag == "br":
                        parts.append(" ")
                    walk(child)
        walk(self)
        return _norm("".join(parts))

    def element_children(self) -> List["_Node"]:
        return [c for c in self.children if isinstance(c, _Node)]

class _TreeBuilder(HTMLParser):
    """Builds a tree; comments (which hold old, disabled markup on this site) are dropped by HTMLParser."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root", {}, None)
        self._stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, {k: (v or "") for k, v in attrs}, self._stack[-1])
        self._stack[-1].children.append(node)
        if tag not in _VOID:
            self._stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self._stack[-1].children.append(_Node(tag, {k: (v or "") for k, v in attrs}, self._stack[-1]))

    def handle_endtag(self, tag):
        # Close the nearest open element of this tag; stray end tags are ignored
        for i in range(len(self._stack) - 1, 0, -1):
            if self._stack[i].tag == tag:
                del self._stack[i:]
                return

    def handle_data(self, data):
        self._stack[-1].children.append(data)

def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()  # \s covers the no-break spaces of &nbsp;

# --- field readers -----------------------------------------------------------------------------------

_DATE_RE = re.compile(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$")
_ADVT_CELL_RE = re.compile(r"^\d{1,3}/\d{4}$")
_ADVT_LABEL_RE = re.compile(r"ADVT\.?\s*NO\.?\s*:?\s*(\d{1,3}/\d{4})", re.I)
_ADVT_IN_TITLE_RE = re.compile(r"\bAdvt\.?\s*No\.?\s*:?\s*(\d{1,3}/\d{4})", re.I)

# Section heading -> kind; first match wins
_HEADING_KINDS = [
    ("CORRIGENDUM", re.compile(r"corrigendum", re.I)),
    ("RESULT", re.compile(r"\bresults?\b", re.I)),
    ("ADMIT_CARD", re.compile(r"admit\s+cards?|call\s+letters?", re.I)),
    ("VACANCY", re.compile(r"recruitment\s+advertisements?", re.I)),
]
# What a title says it is, at its start; first match wins (a corrigendum to a result is a corrigendum)
_TITLE_KINDS = [
    ("CORRIGENDUM", re.compile(r"^corrigendum\b", re.I)),
    ("RESULT", re.compile(r"^(?:final\s+)?results?\b|^notification\s*-\s*category\s+wise\s+cut-?off\s+marks\b", re.I)),
    ("ANSWER_KEY", re.compile(r"^notification\s+regarding\s+(?:provisional\s+|final\s+)?answer\s*keys?\b", re.I)),
    ("ADMIT_CARD", re.compile(r"^download\s+(?:admit\s+cards?|e-?admission\s+certificate)\b", re.I)),
]
# Where the post's name ends in the advertisement table's "Post" column
_POST_END_RE = re.compile(r",\s| under | in ")

def _parse_date(text: str) -> Optional[date]:
    m = _DATE_RE.match(text)
    if not m:
        return None
    day, month, year = (int(g) for g in m.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None

def _absolute(href: str, base_url: str) -> str:
    # `href` is already entity-decoded by HTMLParser
    parts = urlsplit(urljoin(base_url, href.strip()))
    path = quote(parts.path, safe="/%!$&'()*+,;=:@-._~")
    query = quote(parts.query, safe="=&%+/:,;@!$'()*-._~")
    return urlunsplit((parts.scheme, parts.netloc, path, query, ""))

def _is_label(node: _Node) -> bool:
    return node.tag == "span" and "label" in node.classes

def _is_button(anchor: _Node) -> bool:
    """An <a> whose visible content is only Bootstrap label spans (APPLY HERE, CLICK TO VIEW MARKS, ...)."""
    content = [c for c in anchor.children if not (isinstance(c, str) and not c.strip())]
    return bool(content) and all(isinstance(c, _Node) and _is_label(c) for c in content)

def _documents(container: _Node) -> List[_Node]:
    return [a for a in container.iter("a") if a.attrs.get("href") and not _is_button(a) and a.text()]

def _heading_of(node: _Node) -> Optional[str]:
    """Text of the nearest section heading before `node`: a panel heading or a footer list head."""
    current = node
    while current.parent is not None:
        siblings = current.parent.element_children()
        for sib in reversed(siblings[:siblings.index(current)]):
            for cand in [sib, *sib.iter()]:
                if {"panel-heading", "list-head"} & set(cand.classes):
                    return cand.text() or None
        current = current.parent
    return None

def _kind_from_heading(heading: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    for kind, pattern in _HEADING_KINDS:
        if heading and pattern.search(heading):
            return kind, heading
    return None, None

def _kind_from_title(title: str) -> Tuple[Optional[str], Optional[str]]:
    for kind, pattern in _TITLE_KINDS:
        m = pattern.search(title)
        if m:
            return kind, m.group(0)
    return None, None

def _post_name(post_cell_text: str) -> Optional[str]:
    if re.search(r"\bexamination\b", post_cell_text, re.I):
        return None
    depth, end = 0, len(post_cell_text)
    for i, ch in enumerate(post_cell_text):
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        elif depth == 0 and _POST_END_RE.match(post_cell_text, i):
            end = i
            break
    name = post_cell_text[:end].strip(" ,")
    return name or None

# --- listing structures ------------------------------------------------------------------------------

def _column_roles(header: List[str]) -> Dict[str, int]:
    roles: Dict[str, int] = {}
    for i, label in enumerate(header):
        low = label.lower()
        if re.match(r"^s\.?\s*l\b|^sl\.?\s*no", low):
            continue
        if "advt" in low:
            roles.setdefault("advt", i)
        elif "closing" in low or "last date" in low:
            roles.setdefault("last", i)
        elif "date" in low:
            roles.setdefault("published", i)
        else:
            roles.setdefault("title", i)
    return roles

def _table_entries(table: _Node):
    """(title-cell, {role: cell-text}, heading) for each data row of a table with a recognised header."""
    roles: Optional[Dict[str, int]] = None
    heading = None
    for tr in table.iter("tr"):
        cells = [c for c in tr.element_children() if c.tag in ("td", "th")]
        if cells and all(c.tag == "th" for c in cells):
            roles = _column_roles([c.text() for c in cells])
            heading = _heading_of(table)
            continue
        if roles is None or "title" not in roles or len(cells) <= max(roles.values()):
            continue
        texts = {role: cells[i].text() for role, i in roles.items() if role != "title"}
        yield cells[roles["title"]], texts, heading

def _list_entries(ul: _Node):
    """(item, {"advt_label": text}, heading) for each <li> of a scroll-updates list."""
    heading = _heading_of(ul)
    for li in ul.iter("li"):
        texts = {}
        for span in li.iter("span"):
            if _is_label(span) and _ADVT_LABEL_RE.search(span.text()):
                texts["advt_label"] = span.text()
                break
        # Only the item's leading document: the rest are answer-key paper buttons and portal links
        docs = _documents(li)
        if docs:
            yield docs[0], texts, heading

def _row(anchor: _Node, texts: Dict[str, str], heading: Optional[str], is_table: bool,
         base_url: str) -> ListingRow:
    title = anchor.text()
    row = ListingRow(title=title, url=_absolute(anchor.attrs["href"], base_url),
                     evidence={"title": title, "url": anchor.attrs["href"]})

    advt = texts.get("advt")
    if advt and _ADVT_CELL_RE.match(advt):
        row.advertisement_number, row.evidence["advertisement_number"] = advt, advt
    elif texts.get("advt_label"):
        row.advertisement_number = _ADVT_LABEL_RE.search(texts["advt_label"]).group(1)
        row.evidence["advertisement_number"] = texts["advt_label"]
    else:
        m = _ADVT_IN_TITLE_RE.search(title)
        if m:
            row.advertisement_number, row.evidence["advertisement_number"] = m.group(1), m.group(0)

    for role, field in (("published", "published_on"), ("last", "last_date")):
        value = _parse_date(texts.get(role, ""))
        if value:
            setattr(row, field, value)
            row.evidence[field] = texts[role]

    kind, ev = _kind_from_heading(heading)
    if not kind:
        kind, ev = _kind_from_title(title)
    if kind:
        row.kind_hint, row.evidence["kind_hint"] = kind, ev

    if is_table and kind == "VACANCY":
        post = _post_name(title)
        if post:
            row.post_names, row.evidence["post_names"] = [post], title
    return row

def parse_listing(html: str, base_url: str) -> List[ListingRow]:
    builder = _TreeBuilder()
    builder.feed(html)
    builder.close()
    policy = UrlPolicy.from_url(base_url)

    entries = []  # (anchor, texts, heading, is_table) in document order
    for table in builder.root.iter("table"):
        for cell, texts, heading in _table_entries(table):
            entries.extend((a, texts, heading, True) for a in _documents(cell))
    for ul in builder.root.iter("ul"):
        if "scroll-updates" in ul.classes:
            entries.extend((a, texts, heading, False) for a, texts, heading in _list_entries(ul))

    rows: List[ListingRow] = []
    seen = set()
    for anchor, texts, heading, is_table in entries:
        url = _absolute(anchor.attrs["href"], base_url)
        # Tables come first, so a document listed in a table keeps its dated table row over a list mention
        if not policy.allows(url) or url in seen:
            continue
        seen.add(url)
        rows.append(_row(anchor, texts, heading, is_table, base_url))
    return rows
