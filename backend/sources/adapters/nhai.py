"""
NHAI (National Highways Authority of India) listing adapter.

https://nhai.gov.in/#/vacancies/current is a single-page app: its rows have click handlers but no links, so
the page itself gives nothing to read. The app fills the list from one public endpoint on the same domain
(LISTING_REQUEST, captured 2026-10-01):

    POST https://nhai.gov.in/nhai/api/vacancies-current   multipart/form-data, the app's own field set
    -> {"total_count": 116, "list": [{id, title, job_role, date_val, archive_date_val, status, type,
                                      apply_online_link_val, upload_file: [{description, uf_target_id}]}]}

"totalrecord" is the page size; 200 returns every current vacancy in one response (116 on the capture).

Each vacancy gives one ListingRow for its advertisement, plus one per follow-up document:
  title                 the vacancy's "title" (follow-ups: "<title> - <file description>")
  url                   the file's uf_target_id (absolute https://nhai.gov.in/... links). The advertisement is
                        the first file whose description names it ("Advertisement", "Adv.", "Advt", "walk in"),
                        else the vacancy's first file
  published_on          "date_val" (yyyy-mm-dd), shown as the row's date on the page; advertisement row only
  post_names            "job_role", shown under "कार्य की भूमिका" (job role), as one name exactly as written
  kind_hint             follow-ups only, from the file description's own wording (kept as the evidence):
                        CORRIGENDUM (corrigendum, addendum, extension of the last date, cancellation,
                        postponement), ADMIT_CARD (admit card, city intimation), ANSWER_KEY (objection
                        management), RESULT (result, shortlisted, marks, score card); otherwise None
Supporting files (policy circulars, pay-equivalence guidelines, annexures, formats) are not notices and are
skipped. "archive_date_val" is shown as "समाप्ति तिथि" (expiry date) and the field is named for when the
vacancy is archived, so it is not read as a closing date for applications. Status (Open / Closed / Cancelled)
and type (Deputation, Contract, ...) have no ListingRow field and are not used; cancellations still arrive as
their own "Cancellation Notice" rows. The apply link points at vacancyportal.nhai.org, outside the official
domain, and is never used; neither is any file URL the policy refuses.
"""
import json
import re
from typing import List, Optional

from govcareeros.backend.sources.adapters._tables import clean, document_url, iso_date
from govcareeros.backend.sources.adapters.base import ListingRequest, ListingRow
from govcareeros.backend.sources.url_guard import UrlPolicy

SOURCE = "NHAI"

LISTING_REQUEST = ListingRequest(
    url="https://nhai.gov.in/nhai/api/vacancies-current",
    method="POST",
    form={"totalrecord": "200", "publish_from_dt": "", "publish_to_dt": "", "last_from_dt": "", "last_to_dt": "",
          "status": "", "position": "", "captcha_page": "0", "verification": "", "language": "en",
          "index": "0", "archive": "0"},
)

_ADVERT_RE = re.compile(r"\badv(?:t|ert\w*)?\b|walk\s*in", re.I)
_SUPPORTING_RE = re.compile(r"polic|annexure|\bformat\b|equivalen|guideline", re.I)
# Checked in this order: "Cancellation Notice" is a corrigendum, "Notice Result" a result
_KIND_RULES = [
    ("CORRIGENDUM", re.compile(r"corrigendum|addend|extension|extend|extented|cancell?ation|postpone|to\s+close\s+advt", re.I)),
    ("ADMIT_CARD", re.compile(r"admit\s*cards?|city\s+intimation", re.I)),
    ("ANSWER_KEY", re.compile(r"objection\s+management", re.I)),
    ("RESULT", re.compile(r"\bresults?\b|short-?listed|\bmarks\b|score\s*card", re.I)),
]
def _kind(description: str) -> Optional[str]:
    return next((kind for kind, rule in _KIND_RULES if rule.search(description)), None)

def parse_listing(html: str, base_url: str) -> List[ListingRow]:
    policy = UrlPolicy.from_url(base_url)
    try:
        payload = json.loads(html)
    except ValueError:
        return []
    items = payload.get("list") if isinstance(payload, dict) else None
    rows: List[ListingRow] = []
    seen = set()
    for item in items if isinstance(items, list) else []:
        title = clean(item.get("title") or "")
        files = [(clean(f.get("description") or ""), f.get("uf_target_id") or "") for f in item.get("upload_file") or []]
        files = [(d, href, url) for d, href in files if (url := document_url(href, base_url, policy))]
        if not title or not files:
            continue
        advert = next((f for f in files if _ADVERT_RE.search(f[0]) and not _kind(f[0])), files[0])
        for file in [advert] + [f for f in files if f is not advert]:
            description, href, url = file
            if url in seen:
                continue
            if file is advert:
                row = ListingRow(title=title, url=url, evidence={"title": title, "url": href})
                published = iso_date(item.get("date_val") or "")
                if published:
                    row.published_on, row.evidence["published_on"] = published, item["date_val"]
                job_role = clean(item.get("job_role") or "")
                if job_role:
                    row.post_names, row.evidence["post_names"] = [job_role], job_role
            else:
                kind = _kind(description)
                if not kind and _SUPPORTING_RE.search(description):
                    continue
                row = ListingRow(title=f"{title} - {description}" if description else title, url=url,
                                 evidence={"title": title, "url": href})
                if kind:
                    row.kind_hint, row.evidence["kind_hint"] = kind, description
            seen.add(url)
            rows.append(row)
    return rows
