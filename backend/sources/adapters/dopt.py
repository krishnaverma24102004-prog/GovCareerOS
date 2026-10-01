"""
DOPT (Department of Personnel & Training) listing adapter.

https://doptcirculars.nic.in/Default.aspx?URL=UWnd5SMSSkgG ("Vacancy Circular > Issued by DoPT" in the
dopt.gov.in menu) is an ASP.NET grid (#GridView_UrlReport) with bilingual headers:

    क्र० स० / S.No. | न० / No. | दिनांक / Date | विषय / Subject | पीडीएफ / PDF

The PDF cell holds only an icon link, which is why the generic extractor misses most circulars. Each row
becomes a ListingRow:
  title                 the Subject cell (Hindi subject, when given, followed by the English one)
  url                   the English PDF link (id ..._hyp_EngFile_N), else the cell's first link;
                        documents.doptcirculars.nic.in is a subdomain of the official domain
  advertisement_number  the "No." cell: the circular's own reference number
  published_on          the "Date" cell (dd/mm/yyyy), the circular's date
No closing date is shown in the grid. A PDF listed twice is kept once.
"""
from typing import List, Optional

from govcareeros.backend.sources.adapters._tables import document_url, day_month_year, tables
from govcareeros.backend.sources.adapters.base import ListingRow
from govcareeros.backend.sources.url_guard import UrlPolicy

SOURCE = "DOPT"

def _column(header: List[str], word: str) -> Optional[int]:
    for i, name in enumerate(header):
        if name.split("/")[-1].strip(" .").lower() == word:
            return i
    return None

def parse_listing(html: str, base_url: str) -> List[ListingRow]:
    policy = UrlPolicy.from_url(base_url)
    rows: List[ListingRow] = []
    seen = set()
    for table in tables(html):
        if table.attrs.get("id") != "GridView_UrlReport" or not table.rows:
            continue
        header = [c.text for c in table.rows[0]]
        col_no, col_date, col_subject, col_pdf = (_column(header, w) for w in ("no", "date", "subject", "pdf"))
        if col_subject is None or col_pdf is None:
            continue
        for cells in table.rows[1:]:
            if len(cells) != len(header):
                continue  # pager row
            links = sorted(cells[col_pdf].links, key=lambda l: "EngFile" not in l.attrs.get("id", ""))
            link = next(((l, u) for l in links if (u := document_url(l.href, base_url, policy))), None)
            title = cells[col_subject].text
            if not title or not link or link[1] in seen:
                continue
            seen.add(link[1])
            row = ListingRow(title=title, url=link[1], evidence={"title": title, "url": link[0].href})
            if col_no is not None and cells[col_no].text:
                row.advertisement_number = row.evidence["advertisement_number"] = cells[col_no].text
            published = day_month_year(cells[col_date].text) if col_date is not None else None
            if published:
                row.published_on, row.evidence["published_on"] = published, cells[col_date].text
            rows.append(row)
    return rows
