import os
from dataclasses import dataclass
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
    ocr: bool = False  # True when the text came from OCR, not the PDF's text layer

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

@lru_cache(maxsize=32)
def _extract_cached(pdf_bytes: bytes, tessdata: Optional[str]) -> Tuple[PageText, ...]:
    pages = []
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        for page in doc:
            text = page.get_text()
            used_ocr = False
            if tessdata and len(text.strip()) < _MIN_NATIVE_CHARS:
                textpage = page.get_textpage_ocr(language="eng", dpi=300, full=True, tessdata=tessdata)
                text = page.get_text(textpage=textpage)
                used_ocr = True
            pages.append(PageText(page_number=page.number + 1, text=text, ocr=used_ocr))
    return tuple(pages)
