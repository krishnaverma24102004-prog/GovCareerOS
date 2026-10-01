"""
DRDO (Defence Research & Development Organisation) listing adapter.

DRDO's site is server-rendered Drupal. Current vacancies come from one Drupal view ("vacancies"),
shown in two places:

1. https://drdo.gov.in/drdo/en/offerings/vacancies (Offerings > Vacancies; paged, ?page=1 is the
   second page) - one card per vacancy:

    <div class="view view-vacancies ... view-display-id-page_1">
      <div class="view-content"> <ul> <li>
        <div class="vacanciess-box">
          <div class="vacanciess-title">TITLE</div>
          <div class="vacanciess-desc"><p>TITLE, often cut short ...</p></div>
          <div class="vacanciess-advertisment-no"> <span>Advertisement No</span>
              <div class="vacanciess-advertisment-no-content"> DGRE-01/JRF-RA/01/2026-27 </div> </div>   (may be empty)
          <div class="vacanciess-date-publish"> <span>Published Date</span>
              <div class="vacanciess-date-publish-content"><time datetime="...">29/09/2026</time></div> </div>
          <div class="vacanciess-start-date"> <span>Start Date</span> ... </div>
          <div class="vacanciess-due-date"> <span>End Date</span>
              <div class="vacanciess-due-date-content"> 15/10/2026 </div> </div>
          <div class="vacancy-apply-btn"></div>
          <div class="vacanciess-view-node"> <a href="http://drdo.gov.in/drdo/en/offerings/vacancies/SLUG">View More</a> </div>
      ... </ul> </div>
      <nav class="pager"> ... </nav>   <div class="view-footer"> Know More / View Archive </div>
    </div>

2. The registry page https://drdo.gov.in/drdo/en/ (homepage) - the "Vacancies" quick tab, a ticker
   of the same view (display block_1) with title links only (the "View All" button under the list is skipped):

    <div class="view view-vacancies ... view-display-id-block_1">
      <div class="view-content"> <ul class="newsTickermessage"> <li> <a href="/drdo/en/offerings/vacancies/SLUG">TITLE</a> ...

One ListingRow per vacancy:
  title                 the card's vacanciess-title text (homepage: the ticker link text), whitespace-normalised
  url                   the vacancy's own page on drdo.gov.in ("View More" link / ticker link), made absolute.
                        The cards link it as http://drdo.gov.in/...; on an https page a plain-http link to the
                        same host is read as its https form (the page is served there; url_guard is https-only).
  advertisement_number  the "Advertisement No" value, when the card shows one (cards with an empty value: None)
  published_on          the "Published Date" text, dd/mm/yyyy
  last_date             the "End Date" text, dd/mm/yyyy - except on rows the page labels as a result / selection
                        list or admit card, where the End Date is how long the list stays up, not a closing date
                        for applications
  kind_hint             from the title's own wording: CORRIGENDUM > ADMIT_CARD > RESULT > VACANCY
  post_names            only from "post(s) of ..." / "the post ..." / "position(s) of ..." in the title, and only
                        when the names can be read off unambiguously (see _post_names)
The "Start Date" (when the notice went up, identical to the Published Date on every captured card) has no
ListingRow field and is not used. The homepage ticker shows no dates or advertisement numbers, so its rows have
none. Dates are only taken from those labelled boxes: dates inside titles ("held on 15 Sep 2026") are never used.

Only elements inside the view-vacancies view's view-content are read, so menus, the quick-tab bar, the pager,
the exposed search form, "Know More" / "View Archive" buttons and the footer are skipped.
"""
import re
from datetime import date
from html.parser import HTMLParser
from typing import Dict, List, Optional, Tuple
from urllib.parse import urldefrag, urljoin, urlparse

from govcareeros.backend.sources.adapters.base import ListingRow
from govcareeros.backend.sources.url_guard import UrlPolicy

SOURCE = "DRDO"

# The registry page is the homepage, whose ticker carries titles and links only; the Vacancies page
# carries advertisement numbers and dates. Its second page holds the oldest current vacancies.
LISTING_URLS = [
    "https://drdo.gov.in/drdo/en/offerings/vacancies",
    "https://drdo.gov.in/drdo/en/offerings/vacancies?page=1",
]

_WS_RE = re.compile(r"\s+")
# The page's own date format: dd/mm/yyyy (also accepts '-' or '.' separators)
_DATE_RE = re.compile(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})")
_EMPTY_VALUE_RE = re.compile(r"^(?:-+|n/?a|nil|none|not applicable)$", re.I)

# Card field divs (exact class names, including the site's spelling "vacanciess" / "advertisment")
_FIELD_CLASSES = {
    "vacanciess-title": "title",
    "vacanciess-advertisment-no-content": "advt",
    "vacanciess-date-publish-content": "published",
    "vacanciess-due-date-content": "end",
    "vacanciess-view-node": "link",
}

_KIND_RULES = [
    ("CORRIGENDUM", re.compile(
        r"corrigendum|addendum|extension\s+of\s+(?:the\s+)?(?:last\s+)?date|date\s+(?:is\s+)?extended|"
        r"cancell?ation|cancell?ed|postpone(?:d|ment)?", re.I)),
    ("ADMIT_CARD", re.compile(r"admit\s*card|e-?admit|call\s*letter", re.I)),
    ("RESULT", re.compile(
        r"\bresults?\b|selected\s+candidates|provisionally\s+selected|wait[\s-]*listed|short[\s-]*listed|"
        r"merit\s+list|final\s+selection|select\s+list", re.I)),
    ("VACANCY", re.compile(
        r"\badvertisement\b|\badvt\b|vacanc|invites?\s+(?:eligible\s+candidates|applications)|"
        r"walk[\s-]*in|engagement\s+of|recruitment", re.I)),
]
_NO_CLOSING_DATE_KINDS = ("RESULT", "ADMIT_CARD")

# "for the post of JRF", "Post of Director", "posts of X and Y", "the post Junior Research Fellowship (JRF)",
# "position of DRDO Chair, ..."
_POST_OF_RE = re.compile(r"\b(?:the\s+(?:posts?|positions?)(?:\s+of)?|(?:posts?|positions?)\s+of)\s+", re.I)
# Where the post phrase ends: venue, date, mode or scheme that follows the names
_POST_END_RE = re.compile(
    r"\s+(?:at|through|held|on|in|under|from|for|with|by|vide)\b|\s*\((?:under|on|through)\b|\s+[-–—]\s+|[;:]", re.I)
# A recruitment year glued to the name: "Junior Research Fellowship-2026"
_YEAR_SUFFIX_RE = re.compile(r"\s*[-–]?\s*(?:19|20)\d{2}$")

def _clean(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()

class _VacancyView(HTMLParser):
    """Cards (field -> text, link href) and ticker links (text, href) of the view-vacancies view."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cards: List[Tuple[Dict[str, str], Optional[str]]] = []
        self.ticker: List[Tuple[str, str]] = []
        self._divs: List[str] = []      # role of each open <div>: view, content, box, a field name, or ""
        self._fields: Optional[Dict[str, List[str]]] = None
        self._href: Optional[str] = None
        self._anchor: Optional[Tuple[str, List[str]]] = None   # ticker link being read
        self._items = 0                 # open <li> elements inside view-content

    def _inside(self, role: str) -> bool:
        return role in self._divs

    def _field(self) -> Optional[str]:
        for role in reversed(self._divs):
            if role in _FIELD_CLASSES.values():
                return role
        return None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = (attrs.get("class") or "").split()
        if tag == "div":
            role = ""
            if "view-vacancies" in classes and not self._inside("view"):
                role = "view"
            elif self._divs and self._divs[-1] == "view" and "view-content" in classes:
                role = "content"
            elif self._inside("content") and "vacanciess-box" in classes and not self._inside("box"):
                role = "box"
                self._fields, self._href = {}, None
            elif self._inside("box"):
                role = next((_FIELD_CLASSES[c] for c in classes if c in _FIELD_CLASSES), "")
            self._divs.append(role)
        elif tag == "a":
            href = attrs.get("href")
            if href is None or not self._inside("content"):
                return
            if self._inside("box"):
                if self._field() == "link" and self._href is None:
                    self._href = href.strip()
            elif self._items and self._anchor is None:
                # Ticker entries are list items; the "View All" button below the list is not one
                self._anchor = (href.strip(), [])
        elif tag == "li" and self._inside("content"):
            self._items += 1
        elif tag == "br":
            self.handle_data(" ")

    def handle_endtag(self, tag):
        if tag == "a" and self._anchor is not None:
            href, text = self._anchor
            self.ticker.append((_clean("".join(text)), href))
            self._anchor = None
        elif tag == "li" and self._items and self._inside("content"):
            self._items -= 1
        elif tag == "div" and self._divs:
            role = self._divs.pop()
            if role == "content":
                self._items = 0
            if role == "box" and self._fields is not None:
                self.cards.append(({k: _clean("".join(v)) for k, v in self._fields.items()}, self._href))
                self._fields, self._href = None, None

    def handle_data(self, data):
        if self._anchor is not None:
            self._anchor[1].append(data)
        elif self._fields is not None:
            field = self._field()
            if field and field != "link":
                self._fields.setdefault(field, []).append(data)

def _date(text: Optional[str]) -> Optional[date]:
    m = _DATE_RE.fullmatch(text or "")
    if not m:
        return None
    day, month, year = (int(g) for g in m.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None

def _kind(title: str) -> Optional[Tuple[str, str]]:
    for kind, pattern in _KIND_RULES:
        m = pattern.search(title)
        if m:
            return kind, m.group(0)
    return None

def _post_names(title: str) -> Optional[Tuple[List[str], str]]:
    """Post names after "post(s) of" / "the post" / "position(s) of", with the title text they were read from.

    "X and Y" is split at "and"; a comma list ("A, B and C") only when every item has at least two words -
    single-word items mean the commas belong to the name ("Post of Director, DIA-CoE, BHU": the director of
    the DIA-CoE at BHU), and then no names are reported.
    """
    names: List[str] = []
    span: Optional[Tuple[int, int]] = None
    for m in _POST_OF_RE.finditer(title):
        rest = title[m.end():]
        end = _POST_END_RE.search(rest)
        phrase = (rest[:end.start()] if end else rest).strip(" .,")
        if not phrase:
            continue
        if "," in phrase:
            parts = re.split(r"\s*,\s*(?:and\s+)?", phrase)
            parts[-1:] = re.split(r"\s+and\s+", parts[-1], maxsplit=1)
            if any(len(p.split()) < 2 for p in parts):
                continue
        else:
            parts = re.split(r"\s+and\s+", phrase)
        parts = [_YEAR_SUFFIX_RE.sub("", p).strip(" .,-") for p in parts]
        parts = [p for p in parts if p]
        if parts:
            names.extend(p for p in parts if p not in names)
            span = (span[0] if span else m.start(), m.end() + len(rest[:end.start()] if end else rest))
    return (names, title[span[0]:span[1]].strip(" .,")) if names else None

def _absolute(href: str, base_url: str, policy: UrlPolicy) -> Optional[str]:
    if not href or href.lower().startswith(("javascript:", "mailto:", "tel:", "#")):
        return None
    url = urldefrag(urljoin(base_url, href))[0]
    parts, base = urlparse(url), urlparse(base_url)
    # The cards link each vacancy page as http://drdo.gov.in/...; the site serves it over https
    if parts.scheme == "http" and base.scheme == "https" and parts.hostname == base.hostname:
        url = parts._replace(scheme="https").geturl()
    return url if policy.allows(url) else None

def _row(title: str, href: str, url: str) -> ListingRow:
    row = ListingRow(title=title, url=url)
    row.evidence["title"] = title
    row.evidence["url"] = href
    kind = _kind(title)
    if kind:
        row.kind_hint, row.evidence["kind_hint"] = kind
    posts = _post_names(title)
    if posts:
        row.post_names = list(posts[0])
        row.evidence["post_names"] = posts[1]
    return row

def parse_listing(html: str, base_url: str) -> List[ListingRow]:
    parser = _VacancyView()
    parser.feed(html)
    parser.close()
    policy = UrlPolicy.from_url(base_url)

    rows: List[ListingRow] = []
    seen = set()
    for fields, href in parser.cards:
        title = fields.get("title", "")
        url = _absolute(href or "", base_url, policy)
        if not title or not url or url in seen:
            continue
        seen.add(url)
        row = _row(title, href, url)

        advt = fields.get("advt")
        if advt and not _EMPTY_VALUE_RE.match(advt):
            row.advertisement_number = advt
            row.evidence["advertisement_number"] = advt
        published = _date(fields.get("published"))
        if published:
            row.published_on = published
            row.evidence["published_on"] = fields["published"]
        end = _date(fields.get("end"))
        if end and row.kind_hint not in _NO_CLOSING_DATE_KINDS:
            row.last_date = end
            row.evidence["last_date"] = fields["end"]
        rows.append(row)

    for title, href in parser.ticker:
        url = _absolute(href, base_url, policy)
        if not title or not url or url in seen:
            continue
        seen.add(url)
        rows.append(_row(title, href, url))
    return rows
