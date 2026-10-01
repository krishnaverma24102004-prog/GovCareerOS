"""
ISRO (Indian Space Research Organisation) listing adapter.

ISRO publishes recruitment on two static pages of www.isro.gov.in (both served as plain HTML;
list.min.js only paginates them in the browser):

1. https://www.isro.gov.in/Careers.html - the "Recruitment Notice" board: one table whose rows are
   <td class="news"> cells, each a link to a centre's recruitment page, e.g.

       Advt. No. LPSC/03/2026 dated 26.09.2026 - Recruitment to the post of Scientist / Engineer 'SC'

   Besides new advertisements the board carries updates about them (admit cards, screened-in lists,
   CBT / interview schedules, corrigenda), several of which point to the same recruitment page.

2. https://www.isro.gov.in/ViewAllOpportunities.html - "All Current Opportunities": one table with
   the columns

       Location | Post | Advertisement Number | Opening Date | Last Date of Submission | More Details

   where "More Details" is a button (onclick="window.location.href='LPSCRecruitment14.html'").

Each notice-board row with a link, and each opportunities-table row, becomes a ListingRow:
  title                 the row's visible text (notice board) / the "Post" cell (table)
  url                   the row's own link, made absolute against the page URL; rows without a link
                        (two plain-text notices on the board) are skipped, as are the board's
                        "Click here to view ... Current Openings" navigation links
  advertisement_number  the number after "Advt. No." / "Advertisement No." (board), or the
                        "Advertisement Number" cell without a trailing "Dated dd.mm.yyyy" / leading "No."
  published_on          table: the "Opening Date" cell. Board: the "dated ..." of the advertisement
                        number, but only on rows that are the advertisement itself (kind VACANCY);
                        on an update (admit card, schedule, corrigendum ...) that date belongs to the
                        advertisement it refers to, not to the update, so it is left empty
  last_date             table: the "Last Date of Submission" cell. Board: only where the text says the
                        application link is "active from <date> to <date>"
  post_names            from "post(s)/position(s) of ...", "Recruitment of ...", "Application for ..."
                        (or a Post cell that is only post names); empty when not listed
  kind_hint             CORRIGENDUM (leading "Corrigendum" / "Revised" label), ADMIT_CARD, RESULT
                        (selection panel, screened-in list), SCHEDULE (date / schedule of CBT, test,
                        interview), VACANCY (inviting applications / recruitment to the post ...)

Dates are read only in the page's own formats: "26.09.2026", "28-07-2026", "30/09/2026" (Indian
day-first), "September 29, 2026", "Sept 15,2026", "Oct 05,2026". A cell that is not one whole date
("September / October 2022.") or is empty stays None.
"""
import re
from datetime import date
from html.parser import HTMLParser
from typing import Dict, List, Optional, Tuple
from urllib.parse import urldefrag, urljoin

from govcareeros.backend.sources.adapters.base import ListingRow
from govcareeros.backend.sources.url_guard import UrlPolicy

SOURCE = "ISRO"
# The consolidated opportunities table (with advertisement numbers and both dates); Careers.html,
# the registry's recruitment URL, links to it as "view all Current Openings"
LISTING_URLS = ["https://www.isro.gov.in/ViewAllOpportunities.html"]

_WS_RE = re.compile(r"\s+")
_ONCLICK_HREF_RE = re.compile(r"""location\.href\s*=\s*['"]([^'"]+)['"]""", re.I)
_NAV_TEXT = {"here", "click here", "read more", "view", "more details"}

# ---------- dates ----------

_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august",
           "september", "october", "november", "december")
_NUMERIC_DATE = r"\d{1,2}[./-]\d{1,2}[./-]\d{4}"
_NAMED_DATE = r"[A-Za-z]{3,9}\.?\s*\d{1,2}\s*,\s*\d{4}"
_DATE = rf"(?:{_NUMERIC_DATE}|{_NAMED_DATE})"
_NUMERIC_DATE_RE = re.compile(r"^(\d{1,2})[./-](\d{1,2})[./-](\d{4})$")
_NAMED_DATE_RE = re.compile(r"^([A-Za-z]{3,9})\.?\s*(\d{1,2})\s*,\s*(\d{4})$")

def _parse_date(text: str) -> Optional[date]:
    """The whole text as one date in a format the page uses, else None."""
    text = text.strip()
    m = _NUMERIC_DATE_RE.match(text)
    if m:
        day, month, year = (int(g) for g in m.groups())
    else:
        m = _NAMED_DATE_RE.match(text)
        if not m:
            return None
        word = m.group(1).lower()
        # "Sept", "Oct", "September": a prefix (3+ letters) of exactly one month name
        months = [i for i, name in enumerate(_MONTHS, 1) if name.startswith(word)]
        if len(months) != 1:
            return None
        month, day, year = months[0], int(m.group(2)), int(m.group(3))
    try:
        return date(year, month, day)
    except ValueError:
        return None

# ---------- page structure ----------

class _Rows(HTMLParser):
    """The <td class=...> cells of every table row: class -> (text, [(href, anchor text)])."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: List[Dict[str, Tuple[str, List[Tuple[str, str]]]]] = []
        self._row: Optional[Dict[str, Tuple[str, List[Tuple[str, str]]]]] = None
        self._cell: Optional[Tuple[str, List[str], List[Tuple[str, str]]]] = None
        self._anchor: Optional[Tuple[str, List[str]]] = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "tr":
            self._close_row()
            self._row = {}
        elif tag in ("td", "th"):
            self._close_cell()
            if self._row is not None:
                self._cell = ((a.get("class") or "").strip(), [], [])
        elif self._cell is None:
            return
        elif tag == "a" and a.get("href"):
            self._close_anchor()
            self._anchor = (a["href"], [])
        elif tag == "br":
            self._data(" ")
        elif tag == "p":
            self._data(" ")
        if self._cell is not None and a.get("onclick"):
            m = _ONCLICK_HREF_RE.search(a["onclick"])
            if m:
                self._cell[2].append((m.group(1), ""))

    def handle_endtag(self, tag):
        if tag == "a":
            self._close_anchor()
        elif tag in ("td", "th"):
            self._close_cell()
        elif tag == "tr":
            self._close_row()
        elif tag == "p":
            self._data(" ")

    def handle_data(self, data):
        self._data(data)

    def _data(self, data):
        if self._cell is not None:
            self._cell[1].append(data)
        if self._anchor is not None:
            self._anchor[1].append(data)

    def _close_anchor(self):
        if self._anchor is not None and self._cell is not None:
            self._cell[2].append((self._anchor[0], _clean("".join(self._anchor[1]))))
        self._anchor = None

    def _close_cell(self):
        self._close_anchor()
        if self._cell is not None and self._row is not None:
            cls, text, links = self._cell
            self._row[cls] = (_clean("".join(text)), links)
        self._cell = None

    def _close_row(self):
        self._close_cell()
        if self._row:
            self.rows.append(self._row)
        self._row = None

    def close(self):
        super().close()
        self._close_row()

def _clean(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()

# ---------- fields ----------

# "Advt. No. LPSC/03/2026 dated 26.09.2026", "ADVT. NO.NESAC/RMT-TEMP/WI-02/2026 dated 17.07.2026",
# "Advertisement No.: ISRO:ICRB:01(A-JPA):2026 dated 27-07-2026", "Advt. No. HSFC:01:RMT:2026 Dated.10.08.2026",
# "Advt. No. URSC:03:2026 Dated:29.07.2026", "Advt. No. SDSC SHAR/RMT/02/2026 dated June 23, 2026"
_ADVT_DATED_RE = re.compile(
    rf"\b(?:Advertisement|Advt)\b\.?\s*No\b\.?\s*:?\s*(?P<num>[^\s].{{0,60}}?)\s*,?\s*"
    rf"\bDated\b[\s.:]*(?P<date>{_DATE})", re.I)
_ADVT_RE = re.compile(r"\b(?:Advertisement|Advt)\b\.?\s*No\b\.?\s*:?\s*(?P<num>\S*\d\S*)", re.I)
# The table's number cell: "VSSC-335 DATED 31.05.2025", "No. DS_5-24011/2/2019-SEC_5-DOS(1)"
_CELL_DATED_RE = re.compile(rf"\s*,?\s*\bDated\b[\s.:]*{_DATE}\s*$", re.I)
_CELL_NO_RE = re.compile(r"^No\b\.?\s*:?\s*", re.I)

_KIND_RULES = [
    # Only a leading label: "& Corrigendum dated 11-08-2026" inside an admit-card notice is a reference
    ("CORRIGENDUM", re.compile(r"^(?:Corrigendum(?:[\s-]*\d+)?|Addendum(?:[\s-]*\d+)?|Revised)\b\s*:?", re.I)),
    ("ADMIT_CARD", re.compile(r"\b(?:admit\s+card|call\s+letters?)\b", re.I)),
    ("RESULT", re.compile(r"\bselection\s+panel\b|\bscreened[\s-]?in\b|\bshort[\s-]?listed\b|\bresults?\b|"
                          r"\bselected\s+candidates\b|\bmerit\s+list\b", re.I)),
    ("SCHEDULE", re.compile(r"\bschedule\s+of\s+interviews?\b|\bdate\s+of\s+conducting\b|\bscheduled\s+on\b|"
                            r"\binterview\s+schedule\b", re.I)),
    ("VACANCY", re.compile(r"\binvit(?:ing|es)\s+(?:online\s+)?applications?\b|\brecruitment\s+(?:to|for|of)\b|"
                           r"\bapplications?\s+for\b|\bfilling\s+up\b|\bappointment\s+to\b|\brequirment\s+for\b|"
                           r"\bfor\s+the\s+posts?\s+of\b", re.I)),
]

# "active from 29.07.2026 to 28.08.2026" (the application link's window)
_ACTIVE_WINDOW_RE = re.compile(rf"\bactive\s+from\s+{_DATE}\s+to\s+(?P<date>{_DATE})", re.I)

_POSTS_OF_RE = re.compile(r"\b(?:posts?|positions?)\s+of\s+(?P<p>.+)|\bthe\s+post\s+(?!of\b)(?P<q>.+)", re.I)
_POSTS_FOR_RE = re.compile(r"^(?:Lateral\s+)?Recruitment\s+of\s+(?P<p>.+)|^Application\s+for\s+(?P<q>.+)|"
                           r"\bonline\s+applications\s+for\s+(?!engagement\b)(?P<r>.+)", re.I)
# A Post cell that is a sentence, not a list of posts
_SENTENCE_RE = re.compile(r"\b(?:recruitment|invit\w*|applications?|filling|appointment|requirment|posts?)\b", re.I)
# Where the post list ends: "... in LPSC", "... on Deputation basis", "... at URSC", "... against Advt"
# ("in" only before an organisation, "the disciplines ...", "various ISRO Centres" or an acronym:
# "Diploma in Commercial Practice" is a post)
_ORG_WORDS = r"(?:Limited|Ltd|Centre|Center|Laboratory|Department|CPSE|Institute|Corporation)"
_POST_TAIL_RE = re.compile(
    rf"\s+(?:in\s+(?=(?:the|various|different)\b|[A-Z]{{2,}}\b|.*\b{_ORG_WORDS}\b)|(?:on|at|against|under)\s+).*$"
    r"|\s+i\.\s?e\b.*$")
# A comma piece naming the organisation / place, not a post: "NewSpace India Limited", "M/s. ..."
_ORG_PIECE_RE = re.compile(rf"\b{_ORG_WORDS}\b|^M/s\b", re.I)
_LEADING_COUNT_RE = re.compile(r"^.*?\b(?:posts?|positions?)\s+of\s+", re.I)

def _split_top_level(text: str, sep: re.Pattern) -> List[str]:
    """Split on `sep` outside (), [] - "Temporary Research Personnel (Research Scientist,...)" is one post."""
    pieces, depth, start, i = [], 0, 0, 0
    while i < len(text):
        ch = text[i]
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        elif depth == 0:
            m = sep.match(text, i)
            if m and m.end() > i:
                pieces.append(text[start:i])
                start = i = m.end()
                continue
        i += 1
    pieces.append(text[start:])
    return pieces

_COMMA = re.compile(r"\s*,\s*")
_AND = re.compile(r"\s+(?:and|&)\s+", re.I)

# A one-word name that is a post of its own ("Assistant", "Technicians", "Cook", "Fireman"), as
# opposed to a modifier shared with the next name ("Graduate and Technician Apprentices")
_POST_WORD_RE = re.compile(r"(?:ant|ian|man|men|er|or|ist|ee|ook)s?$", re.I)
_GRADE_MARK_RE = re.compile(r"[-'‘’()\[\]]")
_BARE_GRADE_RE = re.compile(r"^['‘’]?[A-Z]{1,3}['‘’]?$")

def _shared_name(left: str) -> bool:
    words = left.split()
    return len(words) == 1 and not _GRADE_MARK_RE.search(left) and not _POST_WORD_RE.search(left)

def _posts_from(phrase: str) -> List[str]:
    posts: List[str] = []
    for piece in _split_top_level(phrase.strip(), _COMMA):
        trimmed = _POST_TAIL_RE.sub("", piece)
        ended = trimmed != piece  # "... 'SC' in the disciplines of Civil, Electrical": the list ends here
        piece = trimmed.strip(" .;:-")
        if piece and _ORG_PIECE_RE.search(piece):
            break  # the rest names the organisation / place
        parts = _split_top_level(piece, _AND) if piece else []
        # "Graduate and Technician Apprentices", "Purchase and Stores Officer" (shared name),
        # "Scientist/Engineer-SD & SC" (a second grade of the same post): one post, not two
        if len(parts) > 1 and (any(_shared_name(p) for p in parts[:-1]) or _BARE_GRADE_RE.match(parts[-1].strip())):
            parts = [piece]
        for part in parts:
            part = _LEADING_COUNT_RE.sub("", part).strip(" .;:-")
            if part:
                posts.append(part)
        if ended:
            break
    return posts

def _post_names(text: str, whole_cell: bool) -> Optional[Tuple[List[str], str]]:
    """(post names, exact text they were read from), or None when the text lists none."""
    m = _POSTS_OF_RE.search(text) or _POSTS_FOR_RE.search(text)
    if m:
        phrase = next(g for g in m.groups() if g)
        posts = _posts_from(phrase)
        return (posts, m.group(0).strip()) if posts else None
    if whole_cell and not _SENTENCE_RE.search(text):
        posts = _posts_from(text)
        return (posts, text) if posts else None
    return None

def _kind(text: str) -> Optional[Tuple[str, str]]:
    for kind, pattern in _KIND_RULES:
        m = pattern.search(text)
        if m:
            return kind, m.group(0).strip()
    return None

def _absolute(href: str, base_url: str, policy: UrlPolicy) -> Optional[str]:
    href = href.strip()
    if not href or href.lower().startswith(("javascript:", "mailto:", "tel:", "#")):
        return None
    url = urldefrag(urljoin(base_url, href))[0]
    return url if policy.allows(url) else None

def _notice_row(text: str, href: str, url: str) -> ListingRow:
    row = ListingRow(title=text, url=url)
    row.evidence["title"] = text
    row.evidence["url"] = href

    kind = _kind(text)
    if kind:
        row.kind_hint, row.evidence["kind_hint"] = kind

    m = _ADVT_DATED_RE.search(text)
    if m:
        row.advertisement_number = m.group("num").strip(" ,.")
        row.evidence["advertisement_number"] = text[m.start():m.end("num")].strip(" ,")
        published = _parse_date(m.group("date"))
        if published and row.kind_hint == "VACANCY":
            row.published_on = published
            row.evidence["published_on"] = text[m.start():m.end()]
    else:
        m = _ADVT_RE.search(text)
        if m:
            row.advertisement_number = m.group("num").rstrip(".,;:")
            row.evidence["advertisement_number"] = m.group(0).rstrip(".,;:")

    m = _ACTIVE_WINDOW_RE.search(text)
    if m:
        closing = _parse_date(m.group("date"))
        if closing:
            row.last_date = closing
            row.evidence["last_date"] = m.group(0)

    if row.kind_hint == "VACANCY":
        posts = _post_names(text, whole_cell=False)
        if posts:
            row.post_names, row.evidence["post_names"] = posts
    return row

def _table_row(cells: Dict[str, Tuple[str, List[Tuple[str, str]]]], href: str, url: str) -> Optional[ListingRow]:
    title = cells.get("post", ("", []))[0]
    if not title:
        return None
    row = ListingRow(title=title, url=url)
    row.evidence["title"] = title
    row.evidence["url"] = href

    number_text = cells.get("advNo", ("", []))[0]
    number = _CELL_NO_RE.sub("", _CELL_DATED_RE.sub("", number_text)).strip(" ,")
    if number:
        row.advertisement_number = number
        row.evidence["advertisement_number"] = number_text

    for field_name, cls in (("published_on", "openDate"), ("last_date", "closeDate")):
        cell_text = cells.get(cls, ("", []))[0]
        parsed = _parse_date(cell_text)
        if parsed:
            setattr(row, field_name, parsed)
            row.evidence[field_name] = cell_text

    posts = _post_names(title, whole_cell=True)
    if posts:
        row.post_names, row.evidence["post_names"] = posts

    m = _KIND_RULES[-1][1].search(title)  # the table lists advertisements: only VACANCY wording applies
    if m:
        row.kind_hint, row.evidence["kind_hint"] = "VACANCY", m.group(0)
    return row

def parse_listing(html: str, base_url: str) -> List[ListingRow]:
    parser = _Rows()
    parser.feed(html)
    parser.close()
    policy = UrlPolicy.from_url(base_url)

    rows: List[ListingRow] = []
    seen = set()
    for cells in parser.rows:
        if "advNo" in cells and "post" in cells:
            # Opportunities table: the "More Details" button's target
            links = cells.get("moreDetails", ("", []))[1] or [l for _, ls in cells.values() for l in ls]
            href = next((h for h, _ in links if _absolute(h, base_url, policy)), None)
            row = _table_row(cells, href, _absolute(href, base_url, policy)) if href else None
        elif "news" in cells:
            # Notice board: the row's own link; "Click here to view ... Current Openings" is navigation
            text, links = cells["news"]
            link = next(((h, t) for h, t in links if t and t.lower() not in _NAV_TEXT), None)
            url = _absolute(link[0], base_url, policy) if link else None
            if not url:
                continue
            title = _clean(re.sub(r"\s*\bRead More\s*$", "", link[1]))
            row = _notice_row(title, link[0], url) if title else None
        else:
            continue
        if row is None or (row.url, row.title) in seen:
            continue
        seen.add((row.url, row.title))
        rows.append(row)
    return rows
