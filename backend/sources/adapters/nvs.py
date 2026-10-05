"""
NVS (Navodaya Vidyalaya Samiti) listing adapter.

https://navodaya.gov.in/nvs/en/Recruitment/ is a single-page app that routes to the homepage, whose
"Recruitment" (भर्ती) box links every item as href="#" with a click handler. The box is filled from the
site's public media feed (LISTING_REQUEST, captured 2026-10-01; media_type_id 7 is "Recruitment"):

    GET https://navodaya.gov.in/api/public/media?organization_id=100&media_type_id=7&page=1&limit=100
    -> {"data": {"items": [{id, source_type, external_url, title_english, title_hindi, start_date,
                            end_date, download_url, hindi_download_url}], "meta": {totalItems, ...}}}

limit=100 returns every item in one page (21 on the capture, meta.hasNextPage false).

Each item becomes a ListingRow:
  title          "title_english", else "title_hindi" when the English title is empty
  url            "download_url" (a FILE item: "/api/public/media/<id>/download?organization_id=100", made
                 absolute on navodaya.gov.in), else "external_url"; only when the policy allows it
  published_on   "start_date" (yyyy-mm-dd), which the box shows as "प्रकाशित तिथि" (published date)
"end_date" (empty on every captured item) is when the item leaves the box, not a closing date for
applications, and is not used. The Hindi file ("hindi_download_url", empty on every captured item) is the
same notice in Hindi and is not a row of its own. No advertisement number, post or kind is labelled: those
stay None (titles such as "View the result of the Tier-II examination ..." are left to notice classification).
"""
import json
from typing import List

from govcareeros.backend.sources.adapters._tables import clean, document_url, iso_date
from govcareeros.backend.sources.adapters.base import ListingRequest, ListingRow
from govcareeros.backend.sources.url_guard import UrlPolicy

SOURCE = "NVS"

LISTING_REQUEST = ListingRequest(
    url="https://navodaya.gov.in/api/public/media?organization_id=100&media_type_id=7&page=1&limit=100")

def parse_listing(html: str, base_url: str) -> List[ListingRow]:
    policy = UrlPolicy.from_url(base_url)
    try:
        payload = json.loads(html)
    except ValueError:
        return []
    data = payload.get("data") if isinstance(payload, dict) else None
    items = data.get("items") if isinstance(data, dict) else None
    rows: List[ListingRow] = []
    seen = set()
    for item in items if isinstance(items, list) else []:
        title = clean(item.get("title_english") or "") or clean(item.get("title_hindi") or "")
        href = item.get("download_url") or item.get("external_url") or ""
        url = document_url(href, base_url, policy)
        if not title or not url or url in seen:
            continue
        seen.add(url)
        row = ListingRow(title=title, url=url, evidence={"title": title, "url": href})
        published = iso_date(item.get("start_date") or "")
        if published:
            row.published_on, row.evidence["published_on"] = published, item["start_date"]
        rows.append(row)
    return rows
