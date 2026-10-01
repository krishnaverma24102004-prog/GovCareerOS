"""
NABARD (National Bank for Agriculture & Rural Development) listing adapter.

https://www.nabard.org/careers-notices1.aspx?cid=693&id=26 ("Career Notices" > What's New) is a
server-rendered list of notices, newest first. Each notice is one block:

    <div class="career_row">
      <div class="career_header"> <div class="career_title">NOTICE TITLE</div> </div>
      <div class="career_sub_title">(Disclaimer ...)</div>              (sometimes)
      <div class="download_file">                                        (one per attachment)
        <div class="download_pdf" style='display:inline|none;'> <a href="/auth/writereaddata/CareerNotices/....pdf">LINK TEXT</a> </div>
        <div class="print_btn" ...> print </div>
        <div class="ext_link" style='display:inline|none;'> <a href="https://...">LINK TEXT</a> </div>
      </div> ...
    </div>

Each attachment is either a PDF on nabard.org (download_pdf shown) or an external link (ext_link
shown); the hidden twin of each block is a blank placeholder and is ignored, as are the print buttons.

One ListingRow per attachment the page shows on the official domain:
  title         the notice's own title (career_title), whitespace-normalised
  url           the attachment's href made absolute against the page URL; links that leave nabard.org
                (IBPS registration / call-letter logins, mail "safelinks") are not official documents and
                are skipped, so a notice whose only link is external yields no row
  kind_hint     from the page's own wording, in the attachment's link text or the notice title, in this
                order of precedence: CORRIGENDUM (corrigendum, cancellation, withdrawal, extension,
                revision ...), ADMIT_CARD (call letter, admit card), RESULT (result, select/wait list,
                selected, shortlisted, score card, cut-offs, marks), VACANCY (advertisement / advt -
                including the site's spelling "Advtertisement" - vacancies, invitation of applications,
                engagement of)
  post_names    only where the title says "post(s) of ..." and the names can be read off unambiguously
  published_on, last_date, advertisement_number
                never: the page shows no publication or closing dates and no advertisement numbers
                (dates inside titles such as "Call Letter - 21.11.2024" are exam dates, not either field; the one
                mention of a last date, "Extension in last date of submission ...", states no date)

Only career_row blocks are read, so the site's menus, sidebar, search box and footer are skipped.
"""
import re
from html.parser import HTMLParser
from typing import List, Optional, Tuple
from urllib.parse import urldefrag, urljoin, urlparse

from govcareeros.backend.sources.adapters.base import ListingRow
from govcareeros.backend.sources.url_guard import UrlPolicy

SOURCE = "NABARD"

_WS_RE = re.compile(r"\s+")
_HIDDEN_RE = re.compile(r"display\s*:\s*none", re.I)
_ATTACHMENT_CLASSES = ("download_pdf", "ext_link")

_KIND_RULES = [
    ("CORRIGENDUM", re.compile(
        r"corrigendum|addendum|cancell?ation|cancell?ed|withdrawal|withdrawn|postpone(?:d|ment)?|"
        r"extension\s+(?:for|of|in)\b|revision\s+of\b|revised\b", re.I)),
    ("ADMIT_CARD", re.compile(r"call\s*letter|admit\s+card", re.I)),
    ("RESULT", re.compile(
        r"\bresults?\b|\bselect(?:ed)?\s+list\b|\bwait[\s-]*list(?:ed)?\b|\bselected\b|"
        r"short[\s-]*listed\b|score\s*card|cut[\s-]*offs?\b|\bmarks\b|merit\s+list", re.I)),
    ("VACANCY", re.compile(
        r"\badvt\b|\badvt?ertisement\b|\bvacancies\b|invitation\s+of\s+applications?|engagement\s+(?:of|as)\b", re.I)),
]

# "post of X", "posts of X, Y and Z", "Post Of X"
_POST_OF_RE = re.compile(r"\bposts?\s+of\s+", re.I)
# Where the post phrase ends: contract terms, the recruitment year, the stage that follows, the service group
_POST_END_RE = re.compile(
    r"\s+on\s+contract\b"
    r"|\s*[-–—]*\s*\b(?:19|20)\d{2}\b"
    r"|\s*[-–—:]+\s*(?:Roll|Registration|List|Result|Link|Mains?|Psychometric|Online|Individual|"
    r"Language|Preliminary|Revision|candidates)\b"
    r"|\s+in\s+(?:Group|Subordinate)\b"
    r"|\s*:\s*$", re.I)
# "a) X b) Y", "1) X 2) Y"
_ENUM_RE = re.compile(r"(?:^|\s)(?:[a-z]|\d{1,2})\)\s+")
_GENERIC_POST_RE = re.compile(r"^specialists?$", re.I)
_DEPARTMENT_SUFFIX_RE = re.compile(r"\s*[-–]\s*[A-Z]{2,6}$")

def _clean(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()

class _CareerRows(HTMLParser):
    """Each career_row block as (title, [(link text, href), ...]) for the attachments the page shows."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: List[Tuple[str, List[Tuple[str, str]]]] = []
        self._divs: List[str] = []          # role of every open <div>: "row", "title", "attachment", "hidden", ""
        self._title: Optional[List[str]] = None
        self._links: List[Tuple[str, str]] = []
        self._link: Optional[Tuple[str, List[str]]] = None

    def _inside(self, role: str) -> bool:
        return role in self._divs

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = (attrs.get("class") or "").split()
        if tag == "div":
            if "career_row" in classes and not self._inside("row"):
                role = "row"
                self._title, self._links = [], []
            elif not self._inside("row"):
                role = ""
            elif "career_title" in classes:
                role = "title"
            elif any(c in classes for c in _ATTACHMENT_CLASSES):
                role = "hidden" if _HIDDEN_RE.search(attrs.get("style") or "") else "attachment"
            else:
                role = ""
            self._divs.append(role)
        elif tag == "a" and self._inside("attachment") and not self._inside("hidden"):
            href = attrs.get("href")
            if href is not None and not _HIDDEN_RE.search(attrs.get("style") or ""):
                self._link = (href, [])
        elif tag == "br" and self._title is not None and self._inside("title"):
            self._title.append(" ")

    def handle_endtag(self, tag):
        if tag == "a" and self._link is not None:
            href, text = self._link
            self._links.append((_clean("".join(text)), href.strip()))
            self._link = None
        elif tag == "div" and self._divs:
            role = self._divs.pop()
            if role == "row":
                self.rows.append((_clean("".join(self._title or [])), self._links))
                self._title, self._links = None, []

    def handle_data(self, data):
        if self._link is not None:
            self._link[1].append(data)
        elif self._title is not None and self._inside("title"):
            self._title.append(data)

def _kind(link_text: str, title: str) -> Optional[Tuple[str, str]]:
    for kind, pattern in _KIND_RULES:
        for text in (link_text, title):
            m = pattern.search(text)
            if m:
                return kind, m.group(0)
    return None

def _split_posts(phrase: str) -> List[str]:
    if _ENUM_RE.search(phrase):
        parts = _ENUM_RE.split(phrase)
    else:
        parts = [p for p in re.split(r",\s*(?:and\s+)?", phrase)]
        # "X and Y" closes the list: split the last item at its first "and"
        last = re.split(r"\s+and\s+", parts.pop(), maxsplit=1)
        parts.extend(last)
    # "Technology Architect- SPPID", "... Treasury Management – FD": the trailing code is the department
    parts = [_DEPARTMENT_SUFFIX_RE.sub("", p.strip(" .,;:")).strip(" .,;:-–") for p in parts]
    parts = [p for p in parts if p]
    # A one-word piece means "and"/"," was part of a name ("Head - Finance, Compliance and Commercialization"):
    # the list cannot be read off reliably, so nothing is reported
    if len(parts) > 1 and any(len(p.split()) == 1 for p in parts):
        return []
    return [p for p in parts if not _GENERIC_POST_RE.match(p)]

def _post_names(title: str) -> Optional[Tuple[List[str], str]]:
    names: List[str] = []
    span: Optional[Tuple[int, int]] = None   # title text from the first to the last phrase read
    for m in _POST_OF_RE.finditer(title):
        rest = title[m.end():]
        end = _POST_END_RE.search(rest)
        phrase = rest[:end.start()] if end else rest
        posts = _split_posts(phrase.strip())
        if posts:
            names.extend(p for p in posts if p not in names)
            span = (span[0] if span else m.start(), m.end() + len(phrase))
    return (names, title[span[0]:span[1]].strip()) if names else None

def parse_listing(html: str, base_url: str) -> List[ListingRow]:
    parser = _CareerRows()
    parser.feed(html)
    parser.close()
    policy = UrlPolicy.from_url(base_url)

    rows: List[ListingRow] = []
    seen = set()
    for title, links in parser.rows:
        if not title:
            continue
        posts = _post_names(title)
        for link_text, href in links:
            if not href or href.lower().startswith(("javascript:", "mailto:", "tel:", "#")):
                continue
            url = urldefrag(urljoin(base_url, href))[0]
            if urlparse(url).path.lower().endswith("/careernotices/"):
                continue  # empty upload slot: the folder, not a document
            if not policy.allows(url) or url in seen:
                continue
            seen.add(url)

            row = ListingRow(title=title, url=url)
            row.evidence["title"] = title
            row.evidence["url"] = href
            kind = _kind(link_text, title)
            if kind:
                row.kind_hint, row.evidence["kind_hint"] = kind
            if posts:
                row.post_names = list(posts[0])
                row.evidence["post_names"] = posts[1]
            rows.append(row)
    return rows
