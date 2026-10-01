"""
ESIC (Employees' State Insurance Corporation) recruitment listing: https://www.esic.gov.in/recruitments

The page (it redirects to https://esic.gov.in/recruitments) is one server-rendered table, newest first,
10 rows per page, paginated as /recruitments/index/page:N. Its columns:

    S.No. | Name of Office | Subject | Publish Date | Last Date | Console Sl. No.

- Subject holds one or more document links ("<title>- PDF <icon> size:(145.78 KB) ."). Each link
  becomes one ListingRow; the row's dates and label apply to every link in that row.
- Publish Date is ISO (2026-10-01) -> published_on.
- Last Date holds a label and, for some labels, a date in brackets: "Result", "Others",
  "Interview (2026-10-07)", "Last Date (2026-10-01)". A bracketed date in this column is the last
  date the page states for the row (for walk-ins it is the interview date; evidence keeps the
  label so that stays visible) -> last_date. "Result" -> kind_hint RESULT.
- Console Sl. No. (e.g. 12609/2026) is the website's own running number, not an advertisement
  number, so it is not reported as one. An advertisement number is filled only when the link
  text itself states one ("Advt. No. 01 of 2026", "Advertisement No. - 07-2026").
- Post names are not listed separately on this page, so post_names stays empty.

Only the recruitment table is read; menus, the search form and pagination links are ignored.
"""
import re
from datetime import date
from html.parser import HTMLParser
from typing import List, Optional, Tuple
from urllib.parse import urldefrag, urljoin, urlparse

from govcareeros.backend.sources.adapters.base import ListingRow

SOURCE = "ESIC"
# The table's own pagination (rows are newest first; page 1 is the registry URL)
LISTING_URLS = [
    "https://esic.gov.in/recruitments/index/page:2",
    "https://esic.gov.in/recruitments/index/page:3",
]

_WS = re.compile(r"\s+")
# "...HELD ON 25.09.2026- PDF size:(145.78 KB) ." -> the document's title is the part before "- PDF"
_FILE_SUFFIX = re.compile(r"\s*-\s*(?:PDF|DOCX?|XLSX?|JPE?G|PNG|ZIP|RAR)\b\s*(?:size\s*:\s*\([^)]*\))?\s*\.?\s*$", re.I)
_ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_DMY_DATE = re.compile(r"(\d{1,2})[-./](\d{1,2})[-./](\d{4})")
_LAST_DATE_CELL = re.compile(r"^(?P<label>[A-Za-z][A-Za-z ]*?)\s*(?:\(\s*(?P<date>[^()]*?)\s*\))?$")
_ADVT_NO = re.compile(
    r"\b(?:Advt\.?|Advertisement)\s*No\.?\s*[:\-–]?\s*(?P<no>\d+[A-Za-z0-9]*(?:\s*(?:of|/|-)\s*\d{4}))", re.I)
_CORRIGENDUM = re.compile(r"\b(?:corrigendum|addendum|erratum|cancell?ation\s+of\s+advertisement)\b", re.I)
_ADMIT_CARD = re.compile(r"\badmit\s+card\b", re.I)


def _clean(text: str) -> str:
    # Character references are already decoded by HTMLParser(convert_charrefs=True)
    return _WS.sub(" ", text).strip()


class _Cell:
    def __init__(self, is_header: bool):
        self.is_header = is_header
        self.parts: List[str] = []
        self.links: List[Tuple[str, List[str]]] = []   # (href, text parts)

    @property
    def text(self) -> str:
        return _clean(" ".join(self.parts))


class _TableParser(HTMLParser):
    """Collects every <table> as (header texts, rows of cells); anchors are kept per cell."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: List[Tuple[List[str], List[List[_Cell]]]] = []
        self._table = None
        self._row: Optional[List[_Cell]] = None
        self._cell: Optional[_Cell] = None
        self._link: Optional[Tuple[str, List[str]]] = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._table = ([], [])
            self.tables.append(self._table)
        elif self._table is None:
            return
        elif tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = _Cell(is_header=tag == "th")
            self._row.append(self._cell)
        elif tag == "a" and self._cell is not None:
            href = dict(attrs).get("href")
            self._link = (href.strip(), []) if href else None
            if self._link:
                self._cell.links.append(self._link)
        if self._cell is not None:
            self._cell.parts.append(" ")   # tags (incl. </br>) separate words

    def handle_endtag(self, tag):
        if self._table is None:
            return
        if self._cell is not None:
            self._cell.parts.append(" ")
        if tag == "a":
            self._link = None
        elif tag in ("td", "th"):
            self._cell, self._link = None, None
        elif tag == "tr" and self._row is not None:
            if self._row:
                headers, rows = self._table
                if all(c.is_header for c in self._row):
                    headers.extend(c.text for c in self._row)
                else:
                    rows.append(self._row)
            self._row, self._cell, self._link = None, None, None
        elif tag == "table":
            self._table = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.parts.append(data)
            if self._link is not None:
                self._link[1].append(data)


def _parse_date(text: str) -> Optional[date]:
    """The page writes dates as yyyy-mm-dd; dd-mm-yyyy / dd.mm.yyyy / dd/mm/yyyy are read as Indian day-first."""
    text = text.strip()
    m = _ISO_DATE.fullmatch(text)
    if m:
        y, mo, d = (int(g) for g in m.groups())
    else:
        m = _DMY_DATE.fullmatch(text)
        if not m:
            return None
        d, mo, y = (int(g) for g in m.groups())
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def _columns(headers: List[str]) -> Optional[dict]:
    names = {h.lower().rstrip(".").strip(): i for i, h in enumerate(headers)}
    if "subject" not in names or "publish date" not in names:
        return None
    return {
        "subject": names["subject"],
        "published": names["publish date"],
        "last": names.get("last date"),
    }


def _row_label(cell_text: str) -> Tuple[Optional[str], Optional[str]]:
    """'Interview (2026-10-07)' -> ('Interview', '2026-10-07'); 'Result' -> ('Result', None)."""
    m = _LAST_DATE_CELL.match(cell_text)
    if not m:
        return None, None
    return m.group("label").strip(), (m.group("date") or None)


def parse_listing(html: str, base_url: str) -> List[ListingRow]:
    parser = _TableParser()
    parser.feed(html)
    parser.close()
    base_host = (urlparse(base_url).hostname or "").lower()
    base_site = base_host[4:] if base_host.startswith("www.") else base_host

    out: List[ListingRow] = []
    seen = set()
    for headers, rows in parser.tables:
        cols = _columns(headers)
        if cols is None:
            continue
        for cells in rows:
            if len(cells) <= max(c for c in cols.values() if c is not None):
                continue
            published_text = cells[cols["published"]].text
            published_on = _parse_date(published_text)

            last_text = cells[cols["last"]].text if cols["last"] is not None else ""
            label, last_date_text = _row_label(last_text)
            last_date = _parse_date(last_date_text) if last_date_text else None

            for href, parts in cells[cols["subject"]].links:
                if href.lower().startswith(("javascript:", "mailto:", "tel:", "#")):
                    continue
                url = urldefrag(urljoin(base_url, href))[0]
                host = (urlparse(url).hostname or "").lower()
                if urlparse(url).scheme not in ("http", "https") or not (
                    host == base_site or host.endswith("." + base_site)
                ):
                    continue
                link_text = _clean(" ".join(parts))
                title = _FILE_SUFFIX.sub("", link_text).strip()
                if not title or url in seen:
                    continue
                seen.add(url)

                row = ListingRow(title=title, url=url)
                row.evidence["title"] = title
                row.evidence["url"] = href
                if published_on:
                    row.published_on = published_on
                    row.evidence["published_on"] = published_text
                if last_date:
                    row.last_date = last_date
                    row.evidence["last_date"] = last_text

                advt = _ADVT_NO.search(title)
                if advt:
                    row.advertisement_number = _clean(advt.group("no"))
                    row.evidence["advertisement_number"] = advt.group(0)

                corr = _CORRIGENDUM.search(title)
                admit = _ADMIT_CARD.search(title)
                if corr:
                    row.kind_hint, row.evidence["kind_hint"] = "CORRIGENDUM", corr.group(0)
                elif admit:
                    row.kind_hint, row.evidence["kind_hint"] = "ADMIT_CARD", admit.group(0)
                elif label and label.lower() == "result":
                    row.kind_hint, row.evidence["kind_hint"] = "RESULT", label
                out.append(row)
    return out
