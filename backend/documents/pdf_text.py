from dataclasses import dataclass
from typing import List
import pymupdf

@dataclass
class PageText:
    page_number: int  # 1-based, as printed in provenance
    text: str

def extract_pages(pdf_bytes: bytes) -> List[PageText]:
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        return [PageText(page_number=page.number + 1, text=page.get_text()) for page in doc]
