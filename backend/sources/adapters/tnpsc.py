"""
TNPSC (Tamil Nadu Public Service Commission) listing adapter.

The commission lists its recruitment notifications in two places, both on tnpsc.gov.in:

1. https://www.tnpsc.gov.in/English/notification.aspx (the registry URL), "Recruitment > Notifications":
   one table (id="noti_table") with the columns

       S No. | Notification Number | Name of the Post and services | Online Registration From | To | Date of Examination

   The post/services cell holds the notification PDF link (class "viewlink") and, under it, a list of
   follow-up documents (ADDENDUM_16A, CORRIGENDUM_5A, ERRATUM, DISTRIBUTION OF VACANCIES, ...).
   It covers notifications up to 2024; its banner says later years are on the Exam Dashboard.

2. The Exam Dashboard, https://tnpsc.gov.in/web/examdashboard/index.aspx (LISTING_URLS):
   - the index shows one card per notification of the current year, "1. <name> - 01/2026",
     linking to that notification's dashboard page (index.aspx?notid=...);
   - each notification's dashboard page (id="pnlExamInfo") is a table

       Document Category | Document Title | Link | Date of upload (dd-mm-yyyy)

     (Notification, Addendum, Press Release, Apply Online, Hall Ticket, Question Papers, ...).

Fields, all read from the page's own cells (anything the page does not show stays None):
  title                 notification table: the post/services link text; a follow-up document is
                        "<that title> - <document link text>". Dashboard: the card's name without its
                        serial number; on a notification page "<notification name> - <Document Title>"
  url                   the row's own document / page link, absolute; links off tnpsc.gov.in (the
                        apply.tnpscexams.in "Apply Online" / "Hall Ticket" portal) are skipped, and so are
                        links hidden with display:none
  advertisement_number  the "Notification Number" cell, or the "- 01/2026" suffix of a dashboard name
  published_on          only the dashboard's "Date of upload". The notification table shows no
                        publication date ("Online Registration From" is when applications open), so it
                        is never set there
  last_date             the notification table's "Online Registration To" cell; on a dashboard page, the
                        "Last date ... : 25.06.2026" text of its "Apply Online" row, given to the
                        "Notification" document
  kind_hint             VACANCY for notifications (the table's "Notification Number" column, the dashboard
                        category "Notification"); CORRIGENDUM for addendum / corrigendum / erratum /
                        supplemental notification / postponement / extension or change-of-date documents; RULES for
                        "... RULES"; ADMIT_CARD / RESULT / ANSWER_KEY from the dashboard category
  post_names            the post/services name with its trailing "(... Service / Department)" removed (kept
                        whole, as one entry, otherwise), only when the
                        name is a post: names of examinations ("COMBINED ... EXAMINATION", "Posts included
                        ...", "Group-...") and RIMC admissions name no post and give none

Navigation, menus, the year selector, the search box and the footer are never read: only the
notification table, the dashboard cards and the dashboard document table are.
"""
import re
from datetime import date
from html.parser import HTMLParser
from typing import Dict, Iterator, List, Optional, Tuple
from urllib.parse import urldefrag, urljoin, urlparse

from govcareeros.backend.sources.adapters.base import ListingRow
from govcareeros.backend.sources.url_guard import UrlPolicy

SOURCE = "TNPSC"
LISTING_URLS = ["https://tnpsc.gov.in/web/examdashboard/index.aspx"]

# ---------------------------------------------------------------- a small element tree (stdlib only)

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "wbr"}
_WS_RE = re.compile(r"\s+")

class _Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: Dict[str, str], parent: Optional["_Node"]):
        self.tag, self.attrs, self.parent = tag, attrs, parent
        self.children: List[object] = []  # _Node or str

    def iter(self, tag: Optional[str] = None) -> Iterator["_Node"]:
        for child in self.children:
            if isinstance(child, _Node):
                if tag is None or child.tag == tag:
                    yield child
                yield from child.iter(tag)

    def kids(self, tag: str) -> List["_Node"]:
        return [c for c in self.children if isinstance(c, _Node) and c.tag == tag]

    def raw_text(self) -> str:
        out = []
        for child in self.children:
            if isinstance(child, str):
                out.append(child)
            elif child.tag == "br":
                out.append(" ")
            elif child.tag not in ("script", "style"):
                out.append(child.raw_text())
        return "".join(out)

    def text(self) -> str:
        return _WS_RE.sub(" ", self.raw_text()).strip()

    def hidden(self) -> bool:
        return re.search(r"display\s*:\s*none", self.attrs.get("style", ""), re.I) is not None

class _TreeBuilder(HTMLParser):
    # Opening one of these closes an unclosed sibling of the listed tags first
    _AUTO_CLOSE = {"tr": ("tr", "td", "th"), "td": ("td", "th"), "th": ("td", "th"), "li": ("li",)}
    _SCOPE = {"tr": ("table", "thead", "tbody", "tfoot"), "td": ("tr", "table"), "th": ("tr", "table"),
              "li": ("ul", "ol")}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root", {}, None)
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        if tag in self._AUTO_CLOSE:
            self._auto_close(tag)
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
        # a stray end tag: ignore

    def handle_data(self, data):
        self.stack[-1].children.append(data)

    def _auto_close(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            open_tag = self.stack[i].tag
            if open_tag in self._SCOPE[tag]:
                return
            if open_tag in self._AUTO_CLOSE[tag]:
                del self.stack[i:]
                return

def _parse(html: str) -> _Node:
    builder = _TreeBuilder()
    builder.feed(html)
    builder.close()
    return builder.root

def _by_id(root: _Node, element_id: str) -> Optional[_Node]:
    return next((n for n in root.iter() if n.attrs.get("id") == element_id), None)

def _has_class(node: _Node, name: str) -> bool:
    return name in node.attrs.get("class", "").split()

# ---------------------------------------------------------------- field readers

_DATE_RE = re.compile(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})$")
_NOTIFICATION_NO_RE = re.compile(r"^\d{1,3}[A-Za-z]?/\d{4}$")  # "18/2024", "19e/2012"
# Dashboard names: "1. RASHTRIYA ... (JANUARY-2027 TERM) - 01/2026" (index card) or without "1. " (its page)
_DASHBOARD_NAME_RE = re.compile(r"^(?:\d+\.\s*)?(?P<name>.+?)\s+-\s+(?P<num>\d{1,3}[A-Za-z]?/\d{4})$")
_SERIAL_RE = re.compile(r"^\d+\.\s*")
# "Last date and time for submission of online application : 25.06.2026 11.59 PM"
_LAST_DATE_RE = re.compile(r"Last\s+date\b[^:]*:\s*(?P<date>\d{1,2}[./\-]\d{1,2}[./\-]\d{4})", re.I)

# Follow-up documents under a notification, by their link text
_FOLLOW_UP_KINDS = [
    ("CORRIGENDUM", re.compile(
        r"addendum|corrigendum|erratum|supplement|postpone|extension\s+of\s+last\s+date|"
        r"last\s+date\s+(?:of\s+payment\s+)?extended|change\s+of\s+(?:\w+\s+){0,2}(?:exam(?:ination)?\s+)?date|"
        r"new\s+date\s+of", re.I)),
    ("RULES", re.compile(r"\brules\b", re.I)),
]
# Dashboard "Document Category" cell
_CATEGORY_KINDS = [
    ("VACANCY", re.compile(r"^notification$", re.I)),
    ("CORRIGENDUM", re.compile(r"addendum|corrigendum|erratum", re.I)),
    ("ADMIT_CARD", re.compile(r"hall\s*ticket|admit\s*card", re.I)),
    ("ANSWER_KEY", re.compile(r"answer\s*keys?", re.I)),
    ("RESULT", re.compile(r"result|selection\s+list|rank\s+list|marks", re.I)),
]

# Names that are an examination / a group of posts / an admission, not a post
_NOT_A_POST_RE = re.compile(
    r"examination|\bexam\b|posts?\s+included|^group\b|combined|rashtriya|military\s+college|admission|supplement",
    re.I)
_POST_OF_PREFIX_RE = re.compile(r"^posts?\s+of\s+", re.I)
_SERVICE_GROUP_RE = re.compile(r"servic|department|\bdept\b", re.I)

def _parse_date(text: str) -> Optional[date]:
    m = _DATE_RE.match(text.strip())
    if not m:
        return None
    day, month, year = (int(g) for g in m.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None

def _absolute(href: str, base_url: str, policy: UrlPolicy) -> Optional[str]:
    href = href.strip()
    if not href or href.lower().startswith(("javascript:", "mailto:", "tel:", "#")):
        return None
    url = urldefrag(urljoin(base_url, href))[0]
    if urlparse(url).scheme not in ("http", "https") or not policy.allows(url):
        return None
    return url

def _trailing_group(name: str) -> Optional[int]:
    """Index of the "(" opening `name`'s last top-level "( ... )" group, if it ends in a balanced one."""
    if not name.endswith(")"):
        return None
    depth = 0
    for i in range(len(name) - 1, -1, -1):
        if name[i] == ")":
            depth += 1
        elif name[i] == "(":
            depth -= 1
            if depth == 0:
                return i
    return None

def _post_names(name: str) -> Optional[List[str]]:
    """The post(s) a name like "ASSISTANT JAILOR (MEN & WOMEN) (TAMIL NADU JAIL SUBORDINATE SERVICE)" states.

    The trailing "( ... Service / Department)" group names where the post sits and is dropped; any other
    trailing group ("ASSISTANT PUBLIC PROSECUTOR (GRADE II)") is part of the post's name.
    """
    if _NOT_A_POST_RE.search(name):
        return None
    posts = name
    start = _trailing_group(name)
    if start is not None and _SERVICE_GROUP_RE.search(name[start:]):
        posts = name[:start]
    posts = _POST_OF_PREFIX_RE.sub("", posts.strip(" ,-")).strip()
    return [posts] if posts else None

def _kind(label: str, rules) -> Optional[str]:
    for kind, pattern in rules:
        if pattern.search(label):
            return kind
    return None

def _add(rows: List[ListingRow], seen: set, row: ListingRow) -> None:
    if row.url not in seen:
        seen.add(row.url)
        rows.append(row)

# ---------------------------------------------------------------- notification.aspx table

def _notification_table(table: _Node, base_url: str, policy: UrlPolicy) -> List[ListingRow]:
    header_rows = [tr for thead in table.kids("thead") for tr in thead.kids("tr")]
    header = [th.text() for tr in header_rows for th in tr.kids("th")]
    number_label = next((h for h in header if "notification number" in h.lower()), None)
    if number_label is None:
        return []

    rows: List[ListingRow] = []
    seen: set = set()
    for tbody in table.kids("tbody"):
        for tr in tbody.kids("tr"):
            cells = tr.kids("td")
            # S No. | Notification Number | Name of the Post and services | From | To | Date of Examination
            if len(cells) != 6:
                continue
            number_text, post_cell, to_text = cells[1].text(), cells[2], cells[4].text()
            main = next((a for a in post_cell.iter("a") if _has_class(a, "viewlink") and not a.hidden()), None)
            if main is None:
                continue
            title = main.text()
            url = _absolute(main.attrs.get("href", ""), base_url, policy)
            if not title or not url:
                continue
            number = number_text if _NOTIFICATION_NO_RE.match(number_text) else None

            row = ListingRow(title=title, url=url)
            row.evidence.update(title=title, url=main.attrs["href"])
            if number:
                row.advertisement_number = number
                row.evidence["advertisement_number"] = number_text
            closing = _parse_date(to_text)
            if closing:
                row.last_date = closing
                row.evidence["last_date"] = to_text
            row.kind_hint = "VACANCY"
            row.evidence["kind_hint"] = number_label
            posts = _post_names(title)
            if posts:
                row.post_names = posts
                row.evidence["post_names"] = title
            _add(rows, seen, row)

            # Follow-up documents listed under the notification link
            for li in post_cell.iter("li"):
                for a in li.iter("a"):
                    label = a.text()
                    doc_url = _absolute(a.attrs.get("href", ""), base_url, policy)
                    if not label or not doc_url or a.hidden():
                        continue
                    doc = ListingRow(title=f"{title} - {label}", url=doc_url)
                    doc.evidence.update(title=label, url=a.attrs["href"])
                    if number:
                        doc.advertisement_number = number
                        doc.evidence["advertisement_number"] = number_text
                    kind = _kind(label, _FOLLOW_UP_KINDS)
                    if kind:
                        doc.kind_hint = kind
                        doc.evidence["kind_hint"] = label
                    _add(rows, seen, doc)
    return rows

# ---------------------------------------------------------------- Exam Dashboard

def _dashboard_index(examlist: _Node, base_url: str, policy: UrlPolicy) -> List[ListingRow]:
    rows: List[ListingRow] = []
    seen: set = set()
    for a in examlist.iter("a"):
        text = a.text()
        url = _absolute(a.attrs.get("href", ""), base_url, policy)
        if not text or not url or a.hidden() or "notid=" not in url:
            continue
        m = _DASHBOARD_NAME_RE.match(text)
        name = m.group("name") if m else _SERIAL_RE.sub("", text)
        row = ListingRow(title=name, url=url)
        row.evidence.update(title=text, url=a.attrs["href"])
        if m:
            row.advertisement_number = m.group("num")
            row.evidence["advertisement_number"] = text
        posts = _post_names(name)
        if posts:
            row.post_names = posts
            row.evidence["post_names"] = text
        _add(rows, seen, row)
    return rows

def _dashboard_notification(panel: _Node, base_url: str, policy: UrlPolicy) -> List[ListingRow]:
    heading = next((h for h in panel.iter("h5") if _has_class(h, "card-title")), None)
    table = next(panel.iter("table"), None)
    if heading is None or table is None:
        return []
    heading_text = heading.text()
    m = _DASHBOARD_NAME_RE.match(heading_text)
    name = m.group("name") if m else heading_text
    number = m.group("num") if m else None

    header = [th.text().lower() for th in table.iter("th")]
    try:
        col_cat = next(i for i, h in enumerate(header) if "category" in h)
        col_title = next(i for i, h in enumerate(header) if "title" in h)
        col_link = next(i for i, h in enumerate(header) if h == "link")
        col_date = next(i for i, h in enumerate(header) if "date" in h)
    except StopIteration:
        return []

    table_rows = [tr for tr in table.iter("tr") if len(tr.kids("td")) == len(header)]
    # The closing date is stated once, in the "Apply Online" row (whose own link leaves the domain)
    last: Optional[Tuple[date, str]] = None
    for tr in table_rows:
        cells = tr.kids("td")
        if cells[col_cat].text().lower() == "apply online":
            lm = _LAST_DATE_RE.search(cells[col_title].text())
            closing = _parse_date(lm.group("date")) if lm else None
            if closing:
                last = (closing, lm.group(0))
                break

    rows: List[ListingRow] = []
    seen: set = set()
    for tr in table_rows:
        cells = tr.kids("td")
        category, doc_title, upload_text = cells[col_cat].text(), cells[col_title].text(), cells[col_date].text()
        link = next((a for a in cells[col_link].iter("a") if not a.hidden()), None)
        url = _absolute(link.attrs.get("href", ""), base_url, policy) if link is not None else None
        if not url or not doc_title:
            continue
        row = ListingRow(title=f"{name} - {doc_title}", url=url)
        row.evidence.update(title=doc_title, url=link.attrs["href"])
        if number:
            row.advertisement_number = number
            row.evidence["advertisement_number"] = heading_text
        uploaded = _parse_date(upload_text)
        if uploaded:
            row.published_on = uploaded
            row.evidence["published_on"] = upload_text
        kind = _kind(category, _CATEGORY_KINDS)
        if kind:
            row.kind_hint = kind
            row.evidence["kind_hint"] = category
        if kind == "VACANCY":
            if last:
                row.last_date, row.evidence["last_date"] = last
            posts = _post_names(name)
            if posts:
                row.post_names = posts
                row.evidence["post_names"] = heading_text
        _add(rows, seen, row)
    return rows

# ---------------------------------------------------------------- entry point

def parse_listing(html: str, base_url: str) -> List[ListingRow]:
    root = _parse(html)
    policy = UrlPolicy.from_url(base_url)
    table = _by_id(root, "noti_table")
    if table is not None:
        return _notification_table(table, base_url, policy)
    panel = _by_id(root, "pnlExamInfo")
    if panel is not None:
        return _dashboard_notification(panel, base_url, policy)
    examlist = _by_id(root, "examlist")
    if examlist is not None:
        return _dashboard_index(examlist, base_url, policy)
    return []
