"""
Chhattisgarh Public Service Commission (psc.cg.gov.in) listing adapter.

The site is a frameset; each section (Advertisement.php, Notifications.php, Result.php) is a
page whose content area (`<div class="inner-content">`) holds one `<ul>` of `<li><a>` rows.
Each row is a single link whose text is the document's caption in file-name style, e.g.

    MICRO_BIOLOGIST_ADVERTISEMENT (08-09-2026)
    PSYCHIATRIST-2026_CORRIGENDUM (21-08-2026)
    STATE_ENGINEERING_SERVICE_EXAM-2026_WRITTEN_EXAM_RESULT (03-09-2026)

What the rows state, and what this adapter reads from them:
  - title: the link text (underscores shown as spaces); evidence keeps the text as written.
  - url: the link target, resolved against the page and percent-encoded (file names contain
    spaces and brackets). Only links on the official domain over https are kept: the
    "CLICK_HERE_TO_..." rows that point at the separate e-service portal (or other sites) are
    skipped, as are links outside the content list (breadcrumb, menus).
  - published_on: the document date the caption gives as "(dd-mm-yyyy)". When a caption carries
    more than one, the last one is the document's own date (earlier ones name the notice it
    amends, e.g. "..._(13-05-2023)_SUCHANA_PATRA_(20-05-2023)"). Captions without one get None.
  - kind_hint: only from a label the caption states (ADVERTISEMENT, CORRIGENDUM, RESULT,
    SELECTION_LIST, ADMIT_CARD, ...).
  - advertisement_number, last_date, post_names: the CGPSC listings state none of these for
    official-domain documents (closing dates appear only on e-service portal links, which are
    off-domain), so they stay empty rather than guessed from the caption.
"""
import re
from datetime import date
from html.parser import HTMLParser
from typing import List, Optional, Tuple
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from govcareeros.backend.sources.adapters.base import ListingRow
from govcareeros.backend.sources.url_guard import UrlPolicy

SOURCE = "CGPSC"
LISTING_URLS = [
    "https://psc.cg.gov.in/Notifications.php",
    "https://psc.cg.gov.in/Result.php",
]

_DATE_RE = re.compile(r"\((\d{2})-(\d{2})-(\d{4})\)")
_SEP = r"[_\s]+"  # \s includes the no-break spaces the page uses
# First match wins: a corrigendum of a result is a corrigendum; "ADVERTISEMENT" only labels a vacancy
_KIND_RULES = [
    ("CORRIGENDUM", re.compile(r"CORRIGENDUM(?:-\d+)?", re.I)),
    ("ADMIT_CARD", re.compile(rf"ADMIT{_SEP}CARD|CALL{_SEP}LETTER", re.I)),
    ("RESULT", re.compile(
        # the page's own spellings include "SELCTION_LIST" and "COUT_OFF_MARKS"
        rf"(?<![A-Z])RESULTS?(?![A-Z])|SELE?CTION{_SEP}LIST|MERIT{_SEP}LIST|"
        rf"STATEMENT{_SEP}OF{_SEP}MARKS|CO?UT{_SEP}OFF(?![A-Z])", re.I)),
    ("VACANCY", re.compile(r"(?<![A-Z])ADVERTISEMENT(?![A-Z])", re.I)),
]

class _ContentLinks(HTMLParser):
    """Collects (href, text) of <a> elements inside <li> within <div class="inner-content">."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: List[Tuple[str, str]] = []
        self._div_depth = 0          # open <div>s since entering the content area (0 = outside)
        self._li_depth = 0
        self._href: Optional[str] = None
        self._text: List[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "div":
            if self._div_depth:
                self._div_depth += 1
            elif "inner-content" in (a.get("class") or "").split():
                self._div_depth = 1
        elif not self._div_depth:
            return
        elif tag == "li":
            self._li_depth += 1
        elif tag == "a" and self._li_depth and a.get("href"):
            self._href, self._text = a["href"], []

    def handle_endtag(self, tag):
        if not self._div_depth:
            return
        if tag == "div":
            self._div_depth -= 1
        elif tag == "li":
            self._li_depth = max(0, self._li_depth - 1)
        elif tag == "a" and self._href is not None:
            self.links.append((self._href, "".join(self._text)))
            self._href = None

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

def _absolute(href: str, base_url: str) -> str:
    # `href` is already entity-decoded by HTMLParser
    parts = urlsplit(urljoin(base_url, href.strip()))
    path = quote(parts.path, safe="/%!$&'()*+,;=:@-._~")
    query = quote(parts.query, safe="=&%+/:,;@!$'()*-._~")
    return urlunsplit((parts.scheme, parts.netloc, path, query, ""))

def _published(text: str) -> Tuple[Optional[date], Optional[str]]:
    matches = list(_DATE_RE.finditer(text))
    if not matches:
        return None, None
    m = matches[-1]
    day, month, year = (int(g) for g in m.groups())
    try:
        return date(year, month, day), m.group(0)
    except ValueError:
        return None, None

def _kind(text: str) -> Tuple[Optional[str], Optional[str]]:
    for kind, pattern in _KIND_RULES:
        m = pattern.search(text)
        if m:
            return kind, m.group(0)
    return None, None

def parse_listing(html: str, base_url: str) -> List[ListingRow]:
    parser = _ContentLinks()
    parser.feed(html)
    parser.close()
    policy = UrlPolicy.from_url(base_url)
    rows: List[ListingRow] = []
    seen = set()
    for href, raw in parser.links:
        caption = raw.strip()
        title = re.sub(r"\s+", " ", caption.replace("_", " ")).strip()
        if not title:
            continue
        url = _absolute(href, base_url)
        if not policy.allows(url) or url in seen:
            continue
        seen.add(url)
        row = ListingRow(title=title, url=url, evidence={"title": caption, "url": href})
        row.published_on, ev = _published(caption)
        if ev:
            row.evidence["published_on"] = ev
        row.kind_hint, ev = _kind(caption)
        if ev:
            row.evidence["kind_hint"] = ev
        rows.append(row)
    return rows
