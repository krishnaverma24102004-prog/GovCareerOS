"""
Shared HTML table reading for listing adapters (not an adapter itself: modules starting with "_" are skipped).

`tables(html)` returns every <table> with its attributes and rows; a nested table is its own entry and its
text stays out of the enclosing cell. Cell text is whitespace-collapsed and entity-decoded, exactly as shown.
"""
import re
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser
from typing import Dict, List, Optional
from urllib.parse import urldefrag, urljoin, urlparse

from govcareeros.backend.sources.url_guard import UrlPolicy

_WS_RE = re.compile(r"\s+")
_DMY_RE = re.compile(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$")

@dataclass
class Link:
    href: str
    attrs: Dict[str, str]
    text: str = ""

@dataclass
class Cell:
    text: str
    attrs: Dict[str, str]
    links: List[Link] = field(default_factory=list)

@dataclass
class Table:
    attrs: Dict[str, str]
    rows: List[List[Cell]] = field(default_factory=list)

def clean(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()

class _Reader(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.done: List[Table] = []
        self._stack: List[dict] = []  # open tables: {"table", "row", "cell": [parts, attrs, links], "link"}

    def handle_starttag(self, tag, attrs):
        attrs = {k: v or "" for k, v in attrs}
        if tag == "table":
            self._stack.append({"table": Table(attrs), "row": None, "cell": None, "link": None})
            return
        if not self._stack:
            return
        top = self._stack[-1]
        if tag == "tr":
            self._close_row(top)
            top["row"] = []
        elif tag in ("td", "th"):
            self._close_cell(top)
            if top["row"] is None:
                top["row"] = []
            top["cell"] = [[], attrs, []]
        elif top["cell"] is not None:
            if tag == "a" and attrs.get("href"):
                top["link"] = Link(attrs["href"], attrs)
                top["cell"][2].append(top["link"])
            elif tag in ("br", "p", "div", "li"):
                top["cell"][0].append(" ")

    def handle_endtag(self, tag):
        if not self._stack:
            return
        top = self._stack[-1]
        if tag == "table":
            self._close_row(top)
            self.done.append(self._stack.pop()["table"])
        elif tag in ("td", "th"):
            self._close_cell(top)
        elif tag == "tr":
            self._close_row(top)
        elif tag == "a" and top["link"] is not None:
            top["link"].text = clean(top["link"].text)
            top["link"] = None

    def handle_data(self, data):
        if self._stack and self._stack[-1]["cell"] is not None:
            top = self._stack[-1]
            top["cell"][0].append(data)
            if top["link"] is not None:
                top["link"].text += data

    def _close_cell(self, top):
        if top["cell"] is not None and top["row"] is not None:
            parts, attrs, links = top["cell"]
            top["row"].append(Cell(clean("".join(parts)), attrs, links))
        top["cell"], top["link"] = None, None

    def _close_row(self, top):
        self._close_cell(top)
        if top["row"]:
            top["table"].rows.append(top["row"])
        top["row"] = None

def tables(html: str) -> List[Table]:
    reader = _Reader()
    reader.feed(html)
    reader.close()
    return reader.done

def document_url(href: str, base_url: str, policy: Optional[UrlPolicy] = None) -> Optional[str]:
    """Absolute URL for a link in the listing, only when it stays on the source's official domain."""
    href = href.strip()
    if not href or href.lower().startswith(("javascript:", "mailto:", "tel:", "#")):
        return None
    url = urldefrag(urljoin(base_url, href))[0]
    policy = policy or UrlPolicy.from_url(base_url)
    return url if urlparse(url).scheme in ("http", "https") and policy.allows(url) else None

def day_month_year(text: str) -> Optional[date]:
    """dd-mm-yyyy / dd/mm/yyyy / dd.mm.yyyy, the order every listing read with this helper uses."""
    m = _DMY_RE.match(text.strip())
    if not m:
        return None
    day, month, year = (int(g) for g in m.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None
