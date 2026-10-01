"""
DST (Department of Science & Technology) listing adapter.

https://dst.gov.in/administrationfinance/recruitment-cell is a Drupal page whose content region
(<div id="content">) holds the "administration_finance" view:

    views-field-body   three paragraphs about the Recruitment Cell, then one <p> per notice, each a
                       single link (to a /news/... page or a PDF under /sites/default/files/)
    views-field-php    "Links:" followed by a <ul> of PDF links, each text ending in its file size "(1.01 MB)"

The page shows no dates, no advertisement table and no pager. Each link in that region becomes a
ListingRow:
  title                 the link text (the "(5.17 MB)" file-size suffix of the "Links:" list removed)
  url                   the link, made absolute against the page URL; a URL listed twice is emitted once
  advertisement_number  only where the title states one ("against the advt. no. DST/01/2026-Estt.III")
  kind_hint             RULES when the title is about recruitment/service rules ("Recruitment Rules",
                        "draft recruitment rules", "RRs", "... Rules, 2013"); VACANCY when it opens with
                        the page's own advertisement wording ("Advertisement", "Re-advertisement",
                        "Filling up", "Engagement of"); otherwise None
  post_names            VACANCY rows only, from the title's "post(s) of ...", "Engagement of ... (NN posts)"
                        and "hiring of ..." wording
  published_on/last_date never: the listing does not show any date

Only links inside the content region are read, so the site's menus, header, footer, carousels and
quick-tabs are skipped.
"""
import re
from html import unescape
from html.parser import HTMLParser
from typing import List, Optional, Tuple
from urllib.parse import urldefrag, urljoin, urlparse

from govcareeros.backend.sources.adapters.base import ListingRow
from govcareeros.backend.sources.url_guard import UrlPolicy

SOURCE = "DST"

_WS_RE = re.compile(r"\s+")
_FILE_SIZE_RE = re.compile(r"\s*\(\s*\d+(?:\.\d+)?\s*(?:bytes|KB|MB|GB)\s*\)\s*$", re.I)

_RULES_RE = re.compile(r"recruitment\s+rules|\bRRs?\b|\brules,\s*\d{4}\b|draft\s+(?:amendment\s+)?rules", re.I)
_VACANCY_LABEL_RE = re.compile(r"^(?:Re-?\s*)?Advertisement\b|^Filling\s+up\b|^Engagement\s+of\b", re.I)

# "against the advt. no. DST/01/2026-Estt.III"
_ADVT_NUMBER_RE = re.compile(
    r"\b(?:advt|advertisement)\b\.?\s*no\b\.?\s*:?\s*(?P<num>[A-Z0-9][A-Z0-9/.\-()]*[A-Z0-9)])", re.I)

# Where a post name starts in a vacancy title
_POST_INTRO_RE = re.compile(r"\bposts?\s+of\s+(?:the\s+)?|^Engagement\s+of\s+|\bhiring\s+of\s+", re.I)
# Where it ends: a comma, the place/basis that follows, or the next "and 01 post of ..."
_POST_END_RE = re.compile(
    r",|\s+(?:in|on|under|by|through|at|against|purely)\s+|"
    r"\s+and\s+(?:'?\d+'?|one|two|three|four|five|six)\s+(?:temporary\s+)?posts?\b", re.I)
_LEADING_COUNT_RE = re.compile(r"^(?:'?\d+'?|one|two|three|four|five|six)\s+", re.I)
# "(Group 'A', Gazetted)", "(Level 6)", "(02 posts)" describe the post, they are not part of its name
_TRAILING_QUALIFIER_RE = re.compile(r"\s*\((?:[^()]*\b(?:group|level|posts?)\b[^()]*)\)\s*$", re.I)

def _clean(text: str) -> str:
    return _WS_RE.sub(" ", unescape(text)).strip()

class _ContentLinks(HTMLParser):
    """(href, link text) of every <a> inside <div id="content">, in page order."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: List[Tuple[str, str]] = []
        self._div_depth = 0          # open <div>s since entering the content region (0 = outside)
        self._href: Optional[str] = None
        self._text: List[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "div":
            if self._div_depth:
                self._div_depth += 1
            elif dict(attrs).get("id") == "content":
                self._div_depth = 1
        elif tag == "a" and self._div_depth:
            self._close_link()
            self._href = dict(attrs).get("href") or ""
            self._text = []

    def handle_endtag(self, tag):
        if tag == "a":
            self._close_link()
        elif tag == "div" and self._div_depth:
            self._div_depth -= 1
            if not self._div_depth:
                self._close_link()

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def _close_link(self):
        if self._href is not None:
            self.links.append((self._href, _clean("".join(self._text))))
        self._href = None
        self._text = []

def _kind(title: str) -> Optional[Tuple[str, str]]:
    m = _RULES_RE.search(title)
    if m:
        return "RULES", m.group(0)
    m = _VACANCY_LABEL_RE.search(title)
    if m:
        return "VACANCY", m.group(0)
    return None

def _advertisement_number(title: str) -> Optional[Tuple[str, str]]:
    m = _ADVT_NUMBER_RE.search(title)
    if not m:
        return None
    return m.group("num"), m.group(0)

def _name_end(rest: str) -> int:
    """Where the post name ends: the first _POST_END_RE match outside parentheses
    ("Scientific Attache (Group 'A', Gazetted), one each" ends before ", one each")."""
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
        name = name.strip(" -")
        if name and name not in posts:
            posts.append(name)
            end_at = m.end() + len(segment.rstrip())
            span = (span[0] if span else m.start(), end_at)
    # Evidence: the exact stretch of the title from the first "post(s) of" to the end of the last name
    return (posts, title[span[0]:span[1]]) if posts else None

def parse_listing(html: str, base_url: str) -> List[ListingRow]:
    parser = _ContentLinks()
    parser.feed(html)
    parser.close()
    policy = UrlPolicy.from_url(base_url)

    rows: List[ListingRow] = []
    seen = set()
    for href, text in parser.links:
        href = href.strip()
        if not href or href.startswith("#") or href.lower().startswith(("javascript:", "mailto:", "tel:")):
            continue
        url = urldefrag(urljoin(base_url, href))[0]
        if urlparse(url).scheme not in ("http", "https") or not policy.allows(url) or url in seen:
            continue
        title = _FILE_SIZE_RE.sub("", text)
        if not title:
            continue
        seen.add(url)
        row = ListingRow(title=title, url=url)
        row.evidence["title"] = title
        row.evidence["url"] = href

        advt = _advertisement_number(title)
        if advt:
            row.advertisement_number, row.evidence["advertisement_number"] = advt

        kind = _kind(title)
        if kind:
            row.kind_hint, row.evidence["kind_hint"] = kind

        if row.kind_hint == "VACANCY":
            posts = _post_names(title)
            if posts:
                row.post_names, row.evidence["post_names"] = posts

        rows.append(row)
    return rows
