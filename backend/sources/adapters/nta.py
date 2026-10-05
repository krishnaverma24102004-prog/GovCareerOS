"""
NTA (National Testing Agency) listing adapter.

https://nta.ac.in/ has no recruitment page; its "Latest @ NTA" notice board (a <marquee class="latestPart">)
carries every notice, recruitment and admission exams alike (812 on the 2026-10-01 capture), one per <p>:

    <p> <i class="fa fa-arrow-circle-right"></i>
        <content style="color:#012B55">TITLE&nbsp;</content>
        <a href="/Download/Notice/Notice_20260909172041.pdf" class="orange-text"><strong>Read More&nbsp;</strong></a>
        <img src="/img/newicon.gif" /> </p>

The link text is only "Read More", so the generic extractor has no title to judge it by and finds nothing.
Here each title is paired with the link that follows it inside the same <p>:
  title   the <content> text (entities decoded, whitespace collapsed)
  url     that link, made absolute on nta.ac.in; only when the policy allows it
Only notices whose title uses the generic recruitment wording (generic_notices.mentions_recruitment:
"recruit", "vacanc", "post of", "deputation", "contractual", "notification for", ...) are kept, so the
board's admission-exam notices (CUET, NEET, JEE, UGC-NET) stay out. That wording is a keyword test, not a
judgement: a title such as "Invitation for Bids for Appointment of CA Firm ..." passes it and is kept.
The board shows no dates, numbers or labels; those fields stay None. A PDF listed twice is kept once.
"""
import re
from html import unescape
from typing import List

from govcareeros.backend.sources.adapters._tables import clean, document_url
from govcareeros.backend.sources.adapters.base import ListingRow
from govcareeros.backend.sources.generic_notices import mentions_recruitment
from govcareeros.backend.sources.url_guard import UrlPolicy

SOURCE = "NTA"

# A <content> title and the rest of its <p> (or up to the next title, should a </p> be missing)
_NOTICE_RE = re.compile(r"<content\b[^>]*>(.*?)</content>(.*?)(?=</p>|<content\b)", re.I | re.S)
_HREF_RE = re.compile(r"<a\b[^>]*?\bhref\s*=\s*[\"']([^\"']+)[\"']", re.I)
_TAG_RE = re.compile(r"<[^>]+>")

def parse_listing(html: str, base_url: str) -> List[ListingRow]:
    policy = UrlPolicy.from_url(base_url)
    rows: List[ListingRow] = []
    seen = set()
    for inner, tail in _NOTICE_RE.findall(html):
        title = clean(unescape(_TAG_RE.sub(" ", inner)))
        link = _HREF_RE.search(tail)
        if not title or not link or not mentions_recruitment(title):
            continue
        href = unescape(link.group(1))
        url = document_url(href, base_url, policy)
        if not url or url in seen:
            continue
        seen.add(url)
        rows.append(ListingRow(title=title, url=url, evidence={"title": title, "url": href}))
    return rows
