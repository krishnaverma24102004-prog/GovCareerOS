"""
Source-specific listing adapters.

A listing adapter turns one official source's recruitment listing page into structured rows,
deterministically. It only reports what the page states: every value it fills carries the exact
text it came from (`evidence`), and anything the page does not state stays None.

An adapter is a module in this package that defines:
    SOURCE: str                                  # registry name, e.g. "RPSC"
    def parse_listing(html: str, base_url: str) -> List[ListingRow]
and optionally
    LISTING_URLS: List[str]                      # extra official listing pages to read (same domain)

Modules are discovered automatically (see adapters/__init__.py); no shared file needs editing.
Fetching stays in the generic fetcher (robots.txt + url_guard); adapters never fetch.
"""
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

@dataclass
class ListingRow:
    title: str
    url: str                                  # absolute URL of the notice / advertisement document or page
    advertisement_number: Optional[str] = None
    published_on: Optional[date] = None
    last_date: Optional[date] = None          # closing date for applications, when the listing states it
    post_names: List[str] = field(default_factory=list)
    kind_hint: Optional[str] = None           # VACANCY / CORRIGENDUM / RESULT / ... when the listing labels it
    evidence: Dict[str, str] = field(default_factory=dict)  # field name -> exact listing text it was read from
