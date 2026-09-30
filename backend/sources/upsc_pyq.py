"""
UPSC previous year question papers and examination pages (for syllabus notices).

Listing: https://www.upsc.gov.in/examinations/previous-question-papers groups papers under "Year: YYYY" headers;
each table caption names the examination and each list item is "<paper name><a href=...pdf> (size)</a>",
optionally in a category row (General / Optional / ...). Only PDF links are taken (the stenography dictation
papers are MP3s). The year is the listing's own group header, not guessed from the file name.

Examination page (/examinations/<name>): the "Download Notification" row links the examination notice PDF and
"Date of Commencement of Examination" carries the exam date.

All requests go through backend/sources/upsc (same https + upsc.gov.in URL policy, robots.txt check, honest
user agent); 401/403 raise AccessRestricted and are never worked around.
"""
import html as html_lib
import re
from dataclasses import dataclass
from datetime import date
from typing import List, Optional
from urllib.parse import urljoin

import httpx

from govcareeros.backend.sources import upsc

LISTING_URL = "https://www.upsc.gov.in/examinations/previous-question-papers"

# Examination name -> stable code; papers of the Preliminary and Main stages share one code
_EXAM_CODES = [
    (r"^Civil Services", "CSE"),
    (r"^Indian Forest Service", "IFS"),
    (r"^Combined Defence Services", "CDS"),
    (r"^National Defence Academy", "NDA"),
    (r"^Engineering Services", "ESE"),
    (r"^Combined Medical Services", "CMS"),
    (r"^Central Armed Police Forces", "CAPF"),
    (r"^Indian Economic Service", "IES_ISS"),
    (r"^Combined Geo-?Scientist", "GEOSCIENTIST"),
    (r"^CISF AC\s*\(EXE\)", "CISF_AC_LDCE"),
    (r"^CBI \(DSP\)", "CBI_DSP_LDCE"),
    (r"^Combined (Section Officer|SO)", "SO_LDCE"),
]

def exam_code_for(exam_name: str) -> str:
    name = exam_name.strip()
    for pattern, code in _EXAM_CODES:
        if re.search(pattern, name, re.I):
            return code
    # Unknown examination: its name without the year, as a code
    base = re.sub(r"[,\s-]*\d{4}.*$", "", name)
    return re.sub(r"[^A-Z0-9]+", "_", base.upper()).strip("_")[:40] or "UNKNOWN"

def exam_stage(exam_name: str) -> Optional[str]:
    m = re.search(r"\((Preliminary|Main)\)", exam_name, re.I)
    return m.group(1).upper() if m else None

def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()

@dataclass
class PaperLink:
    exam_name: str
    exam_code: str
    stage: Optional[str]
    year: int
    category: Optional[str]
    paper_name: str
    url: str
    size_label: Optional[str]

_GROUP_RE = re.compile(r'<div class="view-grouping-header">\s*Year:\s*(\d{4})\s*</div>', re.I)
_TABLE_RE = re.compile(r"<caption>(.*?)</caption>(.*?)</table>", re.I | re.S)
_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.I | re.S)
_CATEGORY_RE = re.compile(r'views-field-field-question-paper-category"\s*>(.*?)</td>', re.I | re.S)
_ITEM_RE = re.compile(r'<li>(.*?)<a\s[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.I | re.S)
_SIZE_RE = re.compile(r"\(([\d.]+\s*[KMG]B)\)", re.I)

def parse_listing(page_html: str, base_url: str = LISTING_URL) -> List[PaperLink]:
    links: List[PaperLink] = []
    seen = set()
    groups = _GROUP_RE.split(page_html)  # [before, year, body, year, body, ...]
    for i in range(1, len(groups), 2):
        year = int(groups[i])
        for caption, table in _TABLE_RE.findall(groups[i + 1]):
            exam_name = _text(caption)
            for row in _ROW_RE.findall(table):
                cat = _CATEGORY_RE.search(row)
                category = _text(cat.group(1)) if cat else None
                for label, href, inner in _ITEM_RE.findall(row):
                    url = urljoin(base_url, html_lib.unescape(href).strip())
                    if not url.lower().endswith(".pdf") or url in seen or not upsc.UPSC_POLICY.allows(url):
                        continue
                    seen.add(url)
                    size = _SIZE_RE.search(_text(inner))
                    links.append(PaperLink(
                        exam_name=exam_name, exam_code=exam_code_for(exam_name), stage=exam_stage(exam_name),
                        year=year, category=category or None, paper_name=_text(label) or url.rsplit("/", 1)[-1],
                        url=url, size_label=size.group(1) if size else None,
                    ))
    return links

async def discover_papers(client: httpx.AsyncClient) -> List[PaperLink]:
    response = await upsc._get(client, LISTING_URL)
    upsc._check(response)
    return parse_listing(response.text, str(response.url))

@dataclass
class ExamPage:
    url: str
    exam_name: Optional[str]
    exam_code: Optional[str]
    commencement_date: Optional[date]
    notice_urls: List[str]

_H1_RE = re.compile(r'<h1 class="heading1">(.*?)</h1>', re.I | re.S)
_COMMENCE_RE = re.compile(r'views-field-field-date-commenc-ex">.*?content="(\d{4}-\d{2}-\d{2})', re.I | re.S)
_NOTIFICATION_ROW_RE = re.compile(r"Download Notification\s*</th>(.*?)</td>", re.I | re.S)
_PDF_HREF_RE = re.compile(r'href="([^"]+\.pdf)"', re.I)

def parse_exam_page(page_html: str, url: str) -> ExamPage:
    h1 = _H1_RE.search(page_html)
    name = _text(h1.group(1)) if h1 else None
    commence = _COMMENCE_RE.search(page_html)
    row = _NOTIFICATION_ROW_RE.search(page_html)
    notices = []
    for href in _PDF_HREF_RE.findall(row.group(1) if row else ""):
        full = urljoin(url, html_lib.unescape(href))
        if upsc.UPSC_POLICY.allows(full) and full not in notices:
            notices.append(full)
    return ExamPage(url=url, exam_name=name, exam_code=exam_code_for(name) if name else None,
                    commencement_date=date.fromisoformat(commence.group(1)) if commence else None, notice_urls=notices)

async def fetch_exam_page(client: httpx.AsyncClient, url: str) -> ExamPage:
    response = await upsc._get(client, url)
    upsc._check(response)
    return parse_exam_page(response.text, str(response.url))

async def download_paper(client: httpx.AsyncClient, url: str) -> upsc.DownloadedDocument:
    """Question papers are PDFs like the advertisements; same guarded, robots-checked download."""
    return await upsc.download_pdf(client, url)
