"""
DFS (Department of Financial Services, Ministry of Finance) listing adapter.

https://financialservices.gov.in/vacancies is a server-rendered Drupal view ("Vacancies"), newest
first, 30 entries per page with a pager (?page=1, ...). Each entry is one grid item:

    <div class="views-view-responsive-grid__item-inner">
      <div class="views-field views-field-field-vacancy-name"><span class="field-content">FULL TITLE</span></div>
      <div class="views-field views-field-field-vacancy-description"><div class="field-content">TRUNCATED TITLE…</div></div>
      <div class="views-field views-field-field-v-publish-date"><span class="views-label">PUBLISHED DATE</span>
           <div class="field-content"><time datetime="2026-09-08T12:00:00Z">2026-09-08</time></div></div>
      <div class="views-field views-field-field-v-start-date"> START DATE (often empty) </div>
      <div class="views-field views-field-field-v-due-date"> DUE DATE (often empty) </div>
      <div class="views-field views-field-field-v-document"><div class="field-content"><a href="/sites/default/files/vacancy/....pdf">VIEW</a></div></div>
    </div>

One ListingRow per grid item that links a document on the official domain:
  title           the vacancy-name field (the full title), whitespace-normalised; the description field
                  is only a truncated copy of it and is not used
  url             the document link (the "VIEW" href) made absolute against the page URL
  published_on    the PUBLISHED DATE field, read from its visible text (the site shows yyyy-mm-dd;
                  Indian dd-mm-yyyy / dd.mm.yyyy / dd/mm/yyyy are read too) - never from anything else
  last_date       the DUE DATE field when it is filled; otherwise only when the title itself states the
                  closing date ("The last date for receipt of applications has now been extended to
                  19.05.2026"). Dates that are not closing dates ("vacancies upto 31.12.2026") are ignored.
                  START DATE is the application opening date, which ListingRow has no field for.
  kind_hint       from the title's own wording: CORRIGENDUM (corrigendum, addendum, extension in/of,
                  extended, cancellation, postponed), RULES (compendium / guidelines), VACANCY
                  (vacancy/vacancies, applications invited, inviting applications, advertisement,
                  engagement of); otherwise None
  post_names      the phrase after "post(s) of" / "appointment of", up to where it names the
                  organisation (" in ", " on ", " under ", " at ", " - ", a sentence end); split into
                  several posts only when the page lists them ("A, B and C")
  advertisement_number
                  never: the page shows no advertisement numbers
Every filled field's exact (whitespace-normalised) page text is kept in evidence; the date fields'
evidence is the field's label and value as the page shows them ("DUE DATE 2026-12-31").

Only the grid items of the view are read, so the site's menus, search box, filters, pager and footer
are skipped. An entry repeated on the page (same document) is reported once.
"""
import re
from datetime import date
from html.parser import HTMLParser
from typing import Dict, List, Optional, Tuple
from urllib.parse import urldefrag, urljoin

from govcareeros.backend.sources.adapters.base import ListingRow
from govcareeros.backend.sources.url_guard import UrlPolicy

SOURCE = "DFS"

# The registry still names the retired /beta/en/vacancies address (404 on 2026-10-01); these are the
# current listing pages: the view and its second pager page.
LISTING_URLS = [
    "https://financialservices.gov.in/vacancies",
    "https://financialservices.gov.in/vacancies?page=1",
]

_WS_RE = re.compile(r"\s+")
_ITEM_CLASS = "views-view-responsive-grid__item-inner"
_FIELD_PREFIX = "views-field-field-"
_FIELDS = {
    "vacancy-name": "title",
    "v-publish-date": "published",
    "v-start-date": "start",
    "v-due-date": "due",
    "v-document": "document",
}

_DATE_RES = [
    (re.compile(r"^(\d{4})-(\d{2})-(\d{2})$"), ("y", "m", "d")),
    (re.compile(r"^(\d{1,2})[-./](\d{1,2})[-./](\d{4})$"), ("d", "m", "y")),
]

# "The last date for receipt of applications has now been extended to 19.05.2026"
_TITLE_LAST_DATE_RE = re.compile(
    r"last\s+date\b[^.]*?\b(?:extended\s+)?(?:to|till|upto|up\s+to|is|on\s+or\s+before)\s+"
    r"(\d{1,2}[./-]\d{1,2}[./-]\d{4})", re.I)

_KIND_RULES = [
    ("CORRIGENDUM", re.compile(
        r"corrigendum|addendum|extension\s+(?:in|of)\b|\bextended\b|cancell?ation|cancell?ed|postpone(?:d|ment)?",
        re.I)),
    ("RULES", re.compile(r"\bcompendium\b|\bguidelines\b", re.I)),
    ("VACANCY", re.compile(
        r"\bvacanc(?:y|ies)\b|applications?\s+(?:are\s+)?invited|inviting\s+applications|"
        r"\badvertisement\b|engagement\s+of\b", re.I)),
]

_POST_START_RE = re.compile(r"\bposts?\s+of\s+|\bappointment\s+of\s+", re.I)
_POST_END_RE = re.compile(r"\s+(?:in|on|under|at)\s+|\s+-\s+|\.(?:\s|$)|\s*$", re.I)
_COUNT_RE = re.compile(r"\s*\(\s*\d+\s*No\.?\s*\)\s*$", re.I)

def _clean(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()

class _GridItems(HTMLParser):
    """Each grid item as {field role: (label text, value text, [hrefs])}."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items: List[Dict[str, Tuple[str, str, List[str]]]] = []
        self._divs: List[str] = []           # role of every open <div> inside an item: "item", field role, ""
        self._spans: List[str] = []          # "label" / "value" / ""
        self._item: Optional[Dict[str, Tuple[List[str], List[str], List[str]]]] = None
        self._field: Optional[str] = None
        self._in_value = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = (attrs.get("class") or "").split()
        if tag == "div":
            role = ""
            if self._item is None:
                if _ITEM_CLASS in classes:
                    role = "item"
                    self._item = {}
                else:
                    return                        # outside any item: not tracked
            else:
                field = next((c[len(_FIELD_PREFIX):] for c in classes if c.startswith(_FIELD_PREFIX)), None)
                if "views-field" in classes and field in _FIELDS:
                    role = _FIELDS[field]
                    self._field = role
                    self._item.setdefault(role, ([], [], []))
                elif "field-content" in classes and self._field:
                    role = "value"
                    self._in_value += 1
            self._divs.append(role)
        elif self._item is None:
            return
        elif tag == "span":
            role = ""
            if self._field and "views-label" in classes:
                role = "label"
            elif self._field and "field-content" in classes:
                role = "value"
                self._in_value += 1
            self._spans.append(role)
        elif tag == "a" and self._field and self._in_value:
            href = attrs.get("href")
            if href:
                self._item[self._field][2].append(href.strip())
        elif tag == "br" and self._field and self._in_value:
            self._item[self._field][1].append(" ")

    def handle_endtag(self, tag):
        if self._item is None:
            return
        if tag == "span" and self._spans:
            if self._spans.pop() == "value":
                self._in_value -= 1
        elif tag == "div" and self._divs:
            role = self._divs.pop()
            if role == "value":
                self._in_value -= 1
            elif role == "item":
                self.items.append({k: (_clean("".join(lab)), _clean("".join(val)), hrefs)
                                   for k, (lab, val, hrefs) in self._item.items()})
                self._item, self._field, self._in_value, self._spans = None, None, 0, []
            elif role in _FIELDS.values():
                self._field = None

    def handle_data(self, data):
        if self._item is None or not self._field:
            return
        if self._spans and self._spans[-1] == "label":
            self._item[self._field][0].append(data)
        elif self._in_value:
            self._item[self._field][1].append(data)

def _parse_date(text: str) -> Optional[date]:
    for pattern, order in _DATE_RES:
        m = pattern.match(text.strip())
        if m:
            parts = dict(zip(order, (int(g) for g in m.groups())))
            try:
                return date(parts["y"], parts["m"], parts["d"])
            except ValueError:
                return None
    return None

def _kind(title: str) -> Optional[Tuple[str, str]]:
    for kind, pattern in _KIND_RULES:
        m = pattern.search(title)
        if m:
            return kind, m.group(0)
    return None

def _post_names(title: str) -> Optional[Tuple[List[str], str]]:
    m = _POST_START_RE.search(title)
    if not m:
        return None
    rest = title[m.end():]
    phrase = rest[:_POST_END_RE.search(rest).start()].strip(" ,;:")
    if not phrase:
        return None
    if "," in phrase:
        parts = re.split(r"\s*,\s*", phrase)
        parts[-1:] = re.split(r"\s+and\s+", parts[-1], maxsplit=1)
    else:
        parts = [phrase]   # "Central Registrar and MD&CEO of ..." is one post, not a list
    names = []
    for p in parts:
        p = _COUNT_RE.sub("", p).strip(" ,;:")
        if p and p not in names:
            names.append(p)
    return (names, title[m.start():m.end() + len(phrase)]) if names else None

def parse_listing(html: str, base_url: str) -> List[ListingRow]:
    parser = _GridItems()
    parser.feed(html)
    parser.close()
    policy = UrlPolicy.from_url(base_url)

    rows: List[ListingRow] = []
    seen = set()
    for item in parser.items:
        title = item.get("title", ("", "", []))[1]
        hrefs = item.get("document", ("", "", []))[2]
        if not title:
            continue
        for href in hrefs:
            if href.lower().startswith(("javascript:", "mailto:", "tel:", "#")):
                continue
            url = urldefrag(urljoin(base_url, href))[0]
            if not policy.allows(url) or url in seen:
                continue
            seen.add(url)

            row = ListingRow(title=title, url=url)
            row.evidence["title"] = title
            row.evidence["url"] = href

            label, value, _ = item.get("published", ("", "", []))
            published = _parse_date(value) if value else None
            if published:
                row.published_on = published
                row.evidence["published_on"] = _clean(f"{label} {value}")

            label, value, _ = item.get("due", ("", "", []))
            due = _parse_date(value) if value else None
            if due:
                row.last_date = due
                row.evidence["last_date"] = _clean(f"{label} {value}")
            else:
                m = _TITLE_LAST_DATE_RE.search(title)
                stated = _parse_date(m.group(1)) if m else None
                if stated:
                    row.last_date = stated
                    row.evidence["last_date"] = m.group(0)

            kind = _kind(title)
            if kind:
                row.kind_hint, row.evidence["kind_hint"] = kind
            posts = _post_names(title)
            if posts:
                row.post_names, row.evidence["post_names"] = posts
            rows.append(row)
            break   # one document per entry
    return rows
