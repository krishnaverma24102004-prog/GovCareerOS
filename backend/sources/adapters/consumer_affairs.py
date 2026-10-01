"""
CONSUMER-AFFAIRS (Department of Consumer Affairs) listing adapter.

https://consumeraffairs.gov.in/pages/vacancy is one table:

    S.No | Title | Download | Published On | Archive Date | Corrigendum Doc

The Download link's text is only "download (1.64 MB)", so the generic extractor stores that as the title.
Each row becomes a ListingRow with the Title cell as title, the Download link as url and the "Published On"
cell (dd-mm-yyyy) as published_on. "Archive Date" is when the notice leaves the page, not a closing date
for applications, so it is not read as last_date. Each link in the "Corrigendum Doc" cell becomes a row of its
own, titled "<notice title> - <link text>" (e.g. "... - 1. Corrigendum"), kind_hint CORRIGENDUM from the
column label and no published_on (the row's date is the notice's). Links off the official domain are never
used; a document listed twice is kept once.
"""
from typing import List, Optional

from govcareeros.backend.sources.adapters._tables import document_url, day_month_year, tables
from govcareeros.backend.sources.adapters.base import ListingRow
from govcareeros.backend.sources.url_guard import UrlPolicy

SOURCE = "CONSUMER-AFFAIRS"

def _column(header: List[str], label: str) -> Optional[int]:
    return next((i for i, name in enumerate(header) if name.lower() == label), None)

def parse_listing(html: str, base_url: str) -> List[ListingRow]:
    policy = UrlPolicy.from_url(base_url)
    rows: List[ListingRow] = []
    seen = set()
    for table in tables(html):
        if not table.rows:
            continue
        header = [c.text for c in table.rows[0]]
        col_title, col_link, col_published, col_corrigendum = (
            _column(header, label) for label in ("title", "download", "published on", "corrigendum doc"))
        if col_title is None or col_link is None:
            continue
        for cells in table.rows[1:]:
            if len(cells) != len(header):
                continue
            title = cells[col_title].text
            published = day_month_year(cells[col_published].text) if col_published is not None else None
            documents = [(link, None) for link in cells[col_link].links]
            if col_corrigendum is not None:
                documents += [(link, "CORRIGENDUM") for link in cells[col_corrigendum].links]
            for link, kind in documents:
                url = document_url(link.href, base_url, policy)
                if not title or not url or url in seen:
                    continue
                seen.add(url)
                row = ListingRow(title=title if kind is None else f"{title} - {link.text}", url=url,
                                 evidence={"title": title, "url": link.href})
                if kind:
                    row.kind_hint, row.evidence["kind_hint"] = kind, header[col_corrigendum]
                if published and kind is None:  # the row's date belongs to the notice, not to its corrigendum
                    row.published_on, row.evidence["published_on"] = published, cells[col_published].text
                rows.append(row)
    return rows
