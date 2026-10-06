import os
import time
from dataclasses import dataclass, field
from functools import lru_cache
from typing import List, Optional, Tuple

import pymupdf

from govcareeros.backend.config.settings import settings

# Pages with fewer extractable characters than this are treated as scanned images
_MIN_NATIVE_CHARS = 40
_DEFAULT_TESSDATA = r"C:\Program Files\Tesseract-OCR\tessdata"

@dataclass(frozen=True)
class PageText:
    page_number: int  # 1-based, as printed in provenance
    text: str
    # True when the text came from OCR: ours (Tesseract), or the invisible OCR layer a scanner put under the page image
    ocr: bool = False

def tessdata_dir() -> Optional[str]:
    for candidate in (settings.TESSDATA_DIR, os.environ.get("TESSDATA_PREFIX"), _DEFAULT_TESSDATA):
        if candidate and os.path.isfile(os.path.join(candidate, "eng.traineddata")):
            return candidate
    return None

def extract_pages(pdf_bytes: bytes, ocr: bool = True) -> List[PageText]:
    """Text per page; scanned pages fall back to Tesseract OCR (English) when it is installed."""
    tessdata = tessdata_dir() if ocr else None
    # OCR takes seconds per page; the same document bytes are only processed once per process
    return list(_extract_cached(pdf_bytes, tessdata))

_OCR_DPI = 300
# An upload's page is OCR'd only up to this many pixels at _OCR_DPI (A2 is 35 million): a page can declare up to 200
# inches a side, which renders to 3.6 billion pixels (gigabytes) before Tesseract sees it
MAX_OCR_PIXELS = 40_000_000

def scanner_ocr_layer(page: "pymupdf.Page") -> bool:
    """True for a scanned page whose text layer is the scanner's own OCR: every character is drawn invisibly
    (render mode 3) over an image, so what the reader sees is the image and the text is a machine reading of it,
    with OCR's errors ("Government of lndia" for "Government of India" on the DFS circular Image_962.pdf)."""
    if not page.get_images(full=False):
        return False
    spans = page.get_texttrace()
    return bool(spans) and all(span["type"] == 3 for span in spans)

def _ocr_text(page: "pymupdf.Page", tessdata: str) -> str:
    textpage = page.get_textpage_ocr(language="eng", dpi=_OCR_DPI, full=True, tessdata=tessdata)
    return page.get_text(textpage=textpage)

@lru_cache(maxsize=32)
def _extract_cached(pdf_bytes: bytes, tessdata: Optional[str]) -> Tuple[PageText, ...]:
    pages = []
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        for page in doc:
            text = page.get_text()
            used_ocr = scanner_ocr_layer(page)
            if tessdata and len(text.strip()) < _MIN_NATIVE_CHARS:
                text = _ocr_text(page, tessdata)
                used_ocr = True
            pages.append(PageText(page_number=page.number + 1, text=text, ocr=used_ocr))
    return tuple(pages)

@dataclass
class LimitedExtraction:
    pages: List[PageText]
    page_count: int  # pages in the document; len(pages) were read
    scanned_unread: List[int] = field(default_factory=list)  # scanned pages left without OCR (limit, budget or no Tesseract)
    stopped: Optional[str] = None  # why reading stopped before the last page: "pages", "chars" or "time"
    oversized: List[int] = field(default_factory=list)  # of scanned_unread, pages too large to OCR (MAX_OCR_PIXELS)

def extract_pages_limited(pdf_bytes: bytes, max_pages: int, max_chars: int, seconds: float,
                          ocr_pages: int = 0, ocr_seconds: float = 0.0) -> LimitedExtraction:
    """extract_pages for untrusted uploads: nothing is cached, and reading stops at max_pages, at max_chars of text
    or after `seconds`. At most ocr_pages scanned pages are OCR'd, and none once ocr_seconds have passed (one OCR
    call cannot be interrupted, so the budgets are checked before each page)."""
    start = time.monotonic()
    tessdata = tessdata_dir() if ocr_pages > 0 else None
    out = LimitedExtraction([], 0)
    chars = ocr_done = 0  # ocr_done: pages Tesseract read (a scanner's own OCR layer costs nothing)
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        if doc.needs_pass:
            raise RuntimeError("the PDF is password-protected")
        out.page_count = doc.page_count
        for index in range(doc.page_count):
            if index >= max_pages:
                out.stopped = "pages"
                break
            if chars >= max_chars:
                out.stopped = "chars"
                break
            # The first page is always read; >= because the clock can tick in 16 ms steps (Windows), so a page read
            # within one step measures 0 seconds
            if index and time.monotonic() - start >= seconds:
                out.stopped = "time"
                break
            page = doc.load_page(index)
            text = page.get_text()
            used_ocr = len(text.strip()) >= _MIN_NATIVE_CHARS and scanner_ocr_layer(page)
            if len(text.strip()) < _MIN_NATIVE_CHARS:
                pixels = page.rect.width * page.rect.height * (_OCR_DPI / 72) ** 2
                if tessdata and ocr_done < ocr_pages and time.monotonic() - start <= ocr_seconds and                         pixels <= MAX_OCR_PIXELS:
                    text, used_ocr = _ocr_text(page, tessdata), True
                    ocr_done += 1
                else:
                    out.scanned_unread.append(index + 1)
                    if tessdata and pixels > MAX_OCR_PIXELS:
                        out.oversized.append(index + 1)
            out.pages.append(PageText(page_number=index + 1, text=text[:max_chars - chars], ocr=used_ocr))
            chars += len(out.pages[-1].text)
    return out
