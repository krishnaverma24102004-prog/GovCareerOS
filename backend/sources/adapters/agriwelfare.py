"""
AGRIWELFARE (Department of Agriculture & Farmers Welfare) listing adapter.

https://agriwelfare.gov.in/en/recruitmentDetail fills its table (#tblRecruitment) with a DataTables AJAX
call after load, so it is read from the rendered page (the registry sets force_browser). Columns, labelled
in Hindi on the captured page:

    क्रमांक (S.No) | शीर्षक / विषय (Title) | आरंभ तिथि (start) | अंतिम तिथि (last date) | प्रकाशन तिथि (published) | विवरण (PDF)

Each row becomes a ListingRow: title from the title cell, url from the row's PDF link (official domain
only), last_date and published_on from their columns when the cell is a dd-mm-yyyy date. The start date
has no ListingRow field and is not used. A PDF listed twice (the page repeats some rows) is kept once,
from its first (newest) row.
"""
from typing import List, Optional

from govcareeros.backend.sources.adapters._tables import document_url, day_month_year, tables
from govcareeros.backend.sources.adapters.base import ListingRow
from govcareeros.backend.sources.url_guard import UrlPolicy

SOURCE = "AGRIWELFARE"

# Header label -> role; Hindi labels as served, English ones in case the page switches language
_COLUMNS = {
    "title": ("शीर्षक", "title", "subject"),
    "last_date": ("अंतिम", "last", "end"),
    "published_on": ("प्रकाशन", "publish"),
}

def _column(header: List[str], words) -> Optional[int]:
    for i, name in enumerate(header):
        if any(w in name.lower() for w in words):
            return i
    return None

def parse_listing(html: str, base_url: str) -> List[ListingRow]:
    policy = UrlPolicy.from_url(base_url)
    rows: List[ListingRow] = []
    seen = set()
    for table in tables(html):
        if table.attrs.get("id") != "tblRecruitment" or not table.rows:
            continue
        header = [c.text for c in table.rows[0]]
        cols = {role: _column(header, words) for role, words in _COLUMNS.items()}
        if cols["title"] is None:
            continue
        for cells in table.rows[1:]:
            if len(cells) != len(header):
                continue  # DataTables' "loading" / "no data" placeholder row
            title = cells[cols["title"]].text
            link = next(((l, u) for c in cells for l in c.links if (u := document_url(l.href, base_url, policy))), None)
            if not title or not link or link[1] in seen:
                continue
            seen.add(link[1])
            row = ListingRow(title=title, url=link[1], evidence={"title": title, "url": link[0].href})
            for field_name in ("last_date", "published_on"):
                col = cols[field_name]
                parsed = day_month_year(cells[col].text) if col is not None else None
                if parsed:
                    setattr(row, field_name, parsed)
                    row.evidence[field_name] = cells[col].text
            rows.append(row)
    return rows
