"""
EMPLOYMENT-NEWS (Employment News, Ministry of Information & Broadcasting) listing adapter.

https://employmentnews.gov.in/NewEmp/AllJobs.aspx?k=All has an "All JOBS" ASP.NET repeater table, one row per
advert, with these cells (span ids, N = row index):

    ContentPlaceHolder1_RepDetails_lblCustomerId_N   issued date (dd/mm/yyyy)
    ContentPlaceHolder1_RepDetails_Label3_N          organisation
    ContentPlaceHolder1_RepDetails_Label4_N          post
    ContentPlaceHolder1_RepDetails_Label5_N          method of appointment (Recruitment / Deputation / Contractual)
    ContentPlaceHolder1_RepDetails_Label6_N          last date (dd/mm/yyyy)

The rows carry no links: the full adverts are in the subscription e-version, which is never fetched. So each
row's url is the listing page itself, where the row's text is the official record. The title joins the
organisation and post cells ("ORGANISATION - POST"); each cell's own text is kept in evidence (title: the
organisation cell, post_names: the post cell). The method of appointment has no ListingRow field and is not used.

Dates: the issued-date header says "(MM/DD/YYYY)", but its cells read day first like the last-date column
("31/08/2026", "13/07/2026" on the captured page). A date column is therefore read day/month/year, and only
when no cell in it has a second part above 12; otherwise that column is left unread for every row.
"""
import re
from html import unescape
from typing import List

from govcareeros.backend.sources.adapters._tables import clean, day_month_year
from govcareeros.backend.sources.adapters.base import ListingRow

SOURCE = "EMPLOYMENT-NEWS"

_CELL_RE = re.compile(
    r'<span\b[^>]*\bid="ContentPlaceHolder1_RepDetails_(lblCustomerId|Label3|Label4|Label5|Label6)_(\d+)"[^>]*>(.*?)</span>',
    re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_SECOND_PART_RE = re.compile(r"^\d{1,2}/(\d{1,2})/\d{4}$")

def parse_listing(html: str, base_url: str) -> List[ListingRow]:
    by_row = {}
    for name, index, inner in _CELL_RE.findall(html):
        by_row.setdefault(int(index), {})[name] = clean(unescape(_TAG_RE.sub(" ", inner)))
    day_first = {
        name for name in ("lblCustomerId", "Label6")
        if all(not m or int(m.group(1)) <= 12 for m in (_SECOND_PART_RE.match(c.get(name, "")) for c in by_row.values()))
    }
    rows: List[ListingRow] = []
    for index in sorted(by_row):
        cells = by_row[index]
        organisation, post = cells.get("Label3", ""), cells.get("Label4", "")
        if not organisation or not post:
            continue
        row = ListingRow(
            title=f"{organisation} - {post}", url=base_url, post_names=[post],
            evidence={"title": organisation, "post_names": post},
        )
        for name, field_name in (("lblCustomerId", "published_on"), ("Label6", "last_date")):
            parsed = day_month_year(cells.get(name, "")) if name in day_first else None
            if parsed:
                setattr(row, field_name, parsed)
                row.evidence[field_name] = cells[name]
        rows.append(row)
    return rows
