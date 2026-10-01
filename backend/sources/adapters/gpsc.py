"""
Listing adapter for the Gujarat Public Service Commission (https://gpsc.gujarat.gov.in/).

GPSC publishes its recruitment information in three structures, all on gpsc.gujarat.gov.in:

* the advertisement dashboard (dashboard?stage=Advertisement): one table per stage tab
  ("Advertisement", "Exam", "PT / Mains Results", "Interviews / Final Results") with columns
  "Post | Class | Dept", "Advertisement No." and stage-specific dates;
* the news list (newseventlist): every notice as "<day> <Mon> <year>" + a title of the form
  "<Category> - <advt no> <description> (<post>)", linking to newseventdetail?NewsID=...;
* the home page's latest-news box: the same notices, dated "<Mon> <day> <year>".

Only what those structures state is reported. "Updated on" is not a publication date, so it is
not used; the application closing date is the end of the "Online Application Submission Date"
range. The home page's "Current Advertisement" box links to gpsc-ojas.gujarat.gov.in, which is
outside the source's url_guard policy (gpsc.gujarat.gov.in only), so it is not read; the same
advertisements appear on the dashboard.
"""
import re
from datetime import date
from html import unescape
from typing import Dict, Iterator, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

from govcareeros.backend.sources.adapters.base import ListingRow
from govcareeros.backend.sources.url_guard import UrlPolicy

SOURCE = "GPSC"
OFFICIAL_HOST = "gpsc.gujarat.gov.in"
LISTING_URLS = [
    "https://gpsc.gujarat.gov.in/dashboard?stage=Advertisement",
    "https://gpsc.gujarat.gov.in/newseventlist",
]

_POLICY = UrlPolicy(OFFICIAL_HOST)

_TAG_RE = re.compile(r"<[^>]+>")
_HREF_RE = re.compile(r"<a\b[^>]*?href\s*=\s*[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
_ADVT_RE = re.compile(r"\b\d{1,4}/\d{4}-\d{2}\b")

# Dashboard: tab buttons name the stage of each <section id="TabN"> table
_TAB_BUTTON_RE = re.compile(r"<button\b[^>]*?data-bs-target=\"#(Tab\d+)\"[^>]*?data-name=\"([^\"]*)\"", re.I | re.S)
_TAB_SECTION_RE = re.compile(r"<section\b[^>]*?\bid=\"(Tab\d+)\"[^>]*>(.*?)</section>", re.I | re.S)
_TABLE_RE = re.compile(r"<table\b[^>]*>(.*?)</table>", re.I | re.S)
_TH_RE = re.compile(r"<th\b[^>]*>(.*?)</th>", re.I | re.S)
_TR_RE = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.I | re.S)
_TD_RE = re.compile(r"<td\b[^>]*>(.*?)</td>", re.I | re.S)
_DMY_RE = re.compile(r"\b(\d{2})-(\d{2})-(\d{4})\b")

# News list / home page news box
_LI_RE = re.compile(r"<li\b[^>]*>(.*?)</li>", re.I | re.S)
_NEWS_DATE_RE = re.compile(r"<(time|div)\b[^>]*class=['\"](?:news-date-container|dated)['\"][^>]*>(.*?)</\1>", re.I | re.S)
_SPAN_RE = re.compile(r"<span\b[^>]*>(.*?)</span>", re.I | re.S)
_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}
# "<Category> - <advt no>" at the start of a news title, e.g. "Final Result - 33/2025-26"
_NEWS_HEAD_RE = re.compile(r"^([A-Za-z][A-Za-z .()&]*?) - (\d{1,4}/\d{4}-\d{2})\b")
# Free-text titles: "... of Advertisement No. 51/2024-25, ..." / "... Advt. No. 9993/2026-27 ..."
_LABELLED_ADVT_RE = re.compile(r"\b(?:Advt\.?|Advertisement)\s+No\.?\s*(\d{1,4}/\d{4}-\d{2})\b", re.I)
_ADVT_LABEL_RE = re.compile(r"\b(?:Advt\.?|Advertisement)\s+No\b", re.I)

# Labels the page itself uses; the first match wins (a "Corrigendum Final Result" is a corrigendum)
_KIND_RULES = [
    ("CORRIGENDUM", re.compile(r"corrigendum|addendum", re.I)),
    ("ADMIT_CARD", re.compile(r"call\s+letters?", re.I)),
    ("RESULT", re.compile(r"\bresults?\b|merit\s+list", re.I)),
    ("ANSWER_KEY", re.compile(r"answer\s*key|\bkey\b", re.I)),          # "AnswerKey", "Provisional Key (Prelim)"
    ("SCHEDULE", re.compile(r"\binterview\s+schedule\b|\bschedule\s+\((?:prelim|main)\)", re.I)),
    ("RULES", re.compile(r"recruitment\s+rules", re.I)),
    ("VACANCY", re.compile(r"detailed\s+advertisement", re.I)),
]
# Dashboard tab name -> kind (only where the tab name says it)
_TAB_KINDS = [
    ("VACANCY", re.compile(r"^advertisement$", re.I)),
    ("RESULT", re.compile(r"\bresults?\b", re.I)),
]

def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", unescape(_TAG_RE.sub(" ", fragment))).strip()

def _official(href: str, base_url: str) -> Optional[str]:
    url = urljoin(base_url, unescape(href.strip()))
    if urlparse(url).scheme != "https" or not _POLICY.allows(url):
        return None
    return url

def _dmy(text: str) -> List[date]:
    found = []
    for d, m, y in _DMY_RE.findall(text):
        try:
            found.append(date(int(y), int(m), int(d)))
        except ValueError:
            continue
    return found

def _kind(text: str, rules) -> Optional[Tuple[str, str]]:
    for kind, pattern in rules:
        m = pattern.search(text)
        if m:
            return kind, m.group(0)
    return None

# ---------------------------------------------------------------- advertisement dashboard

def _dashboard_rows(html: str, base_url: str) -> Iterator[ListingRow]:
    tab_names = {tab: _text(name) for tab, name in _TAB_BUTTON_RE.findall(html)}
    for tab_id, section in _TAB_SECTION_RE.findall(html):
        stage = tab_names.get(tab_id)
        table = _TABLE_RE.search(section)
        if not stage or not table:
            continue
        head, _, body = table.group(1).partition("<tbody>")
        headers = [_text(h).lower() for h in _TH_RE.findall(head)]
        if not headers or not headers[0].startswith("post") or "advertisement no." not in headers:
            continue
        col = {name: i for i, name in enumerate(headers)}
        advt_col = col["advertisement no."]
        submission_col = next((i for h, i in col.items() if h.startswith("online application submission date")), None)
        result_col = next((i for h, i in col.items() if h.startswith("final result declared on")), None)
        stage_col = col.get("stage")
        tab_kind = _kind(stage, _TAB_KINDS)
        for tr in _TR_RE.findall(body):
            cells = _TD_RE.findall(tr)
            if len(cells) != len(headers):
                continue
            row = _dashboard_row(cells, base_url, stage, tab_kind, advt_col, submission_col, result_col, stage_col)
            if row:
                yield row

def _dashboard_row(cells, base_url, stage, tab_kind, advt_col, submission_col, result_col, stage_col) -> Optional[ListingRow]:
    advt_link = _HREF_RE.search(cells[advt_col])
    if not advt_link:
        return None
    advt_text = _text(advt_link.group(2))
    href = advt_link.group(1)
    advertisement_tab = bool(tab_kind) and tab_kind[0] == "VACANCY"
    if stage_col is not None and not advertisement_tab:
        # Later stages: link the stage section of the advertisement page the row itself points to
        stage_link = _HREF_RE.search(cells[stage_col])
        if stage_link and _official(stage_link.group(1), base_url):
            href = stage_link.group(1)
    url = _official(href, base_url)
    if not url:
        return None
    post_cell = _text(cells[0])                       # "Post | Class | Dept"
    post = post_cell.split("|")[0].strip()
    if not post:
        return None
    evidence: Dict[str, str] = {"post_names": post}
    row = ListingRow(title=post if advertisement_tab else f"{stage} - {post}",
                     url=url, post_names=[post], evidence=evidence)
    if _ADVT_RE.fullmatch(advt_text):
        row.advertisement_number = advt_text
        evidence["advertisement_number"] = advt_text
    if submission_col is not None:
        window = _text(cells[submission_col])         # "24-09-2026 02:00 PM - 08-10-2026 11:59 PM"
        dates = _dmy(window)
        if len(dates) == 2 and " - " in window:
            row.last_date = dates[1]
            evidence["last_date"] = window
    if result_col is not None:
        declared = _text(cells[result_col])
        dates = _dmy(declared)
        if len(dates) == 1:
            row.published_on = dates[0]
            evidence["published_on"] = declared
    if tab_kind:
        row.kind_hint = tab_kind[0]
        evidence["kind_hint"] = stage
    return row

# ---------------------------------------------------------------- news list / home news box

def _news_date(block: str) -> Optional[Tuple[date, str]]:
    parts = [_text(s) for s in _SPAN_RE.findall(block)]
    days = [p for p in parts if re.fullmatch(r"\d{1,2}", p)]
    months = [p for p in parts if p[:3].lower() in _MONTHS and re.fullmatch(r"[A-Za-z]+", p)]
    years = [p for p in parts if re.fullmatch(r"\d{4}", p)]
    if len(days) != 1 or len(months) != 1 or len(years) != 1:
        return None
    try:
        return date(int(years[0]), _MONTHS[months[0][:3].lower()], int(days[0])), " ".join(parts)
    except ValueError:
        return None

def _trailing_group(title: str) -> Optional[str]:
    """Text of the balanced parenthesised group that ends the title, if any."""
    if not title.endswith(")"):
        return None
    depth = 0
    for i in range(len(title) - 1, -1, -1):
        if title[i] == ")":
            depth += 1
        elif title[i] == "(":
            depth -= 1
            if depth == 0:
                return title[i + 1:-1].strip()
    return None

def _news_rows(html: str, base_url: str) -> Iterator[ListingRow]:
    for li in _LI_RE.findall(html):
        link = next(((h, t) for h, t in _HREF_RE.findall(li) if "newseventdetail" in h.lower()), None)
        dated = _NEWS_DATE_RE.search(li)
        if not link or not dated:
            continue
        url = _official(link[0], base_url)
        title = _text(link[1])
        if not url or not title:
            continue
        evidence: Dict[str, str] = {}
        row = ListingRow(title=title, url=url, evidence=evidence)
        published = _news_date(dated.group(2))
        if published:
            row.published_on, evidence["published_on"] = published
        head = _NEWS_HEAD_RE.match(title)
        if head:
            row.advertisement_number = head.group(2)
            evidence["advertisement_number"] = head.group(0)
            # The CMS appends the post in parentheses: "... (Director of Libraries, Class-1)". List
            # ordinals like "(Second List)" or "(PHASE-7)" end some titles instead, so a group is
            # taken as a post only when it reads like one (a class or a comma-separated designation)
            group = _trailing_group(title)
            if group and (re.search(r"class", group, re.I) or "," in group):
                row.post_names = [group]
                evidence["post_names"] = group
        else:
            labelled = _LABELLED_ADVT_RE.search(title)
            # only when the title names exactly one advertisement: "Advt no. 14/2026-27, 15/2026-27" and
            # "Advertisement No. 51/2024-25 ... and Advertisement No. 77/2024- 25" name several
            if labelled and len(_ADVT_RE.findall(title)) == 1 and len(_ADVT_LABEL_RE.findall(title)) == 1:
                row.advertisement_number = labelled.group(1)
                evidence["advertisement_number"] = labelled.group(0)
        kind = _kind(title, _KIND_RULES)
        if kind:
            row.kind_hint, evidence["kind_hint"] = kind
        yield row

def parse_listing(html: str, base_url: str) -> List[ListingRow]:
    rows: List[ListingRow] = []
    seen = set()
    for row in (*_dashboard_rows(html, base_url), *_news_rows(html, base_url)):
        if row.url not in seen:
            seen.add(row.url)
            rows.append(row)
    return rows
