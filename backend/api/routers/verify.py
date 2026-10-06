"""
POST /verify: the free public notice check (no login). Takes pasted text, a link, or a PDF (up to 10 MB).

The link is analysed as a string only; nothing the user sends is ever fetched. The body size is limited before it is
parsed (backend/api/body_limit.py); the PDF is read off the event loop within page, time and OCR limits.
"""
from dataclasses import asdict
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.api.deps import get_session
from govcareeros.backend.services import notice_verifier

router = APIRouter(prefix="/verify", tags=["verify"])

class DomainCheckOut(BaseModel):
    input: str
    host: str
    status: str
    official_domain: Optional[str]
    reasons: List[str]

class NoticeMatchOut(BaseModel):
    kind: str
    title: str
    organization: str
    source: str
    official_url: Optional[str]
    matched_on: List[str]
    advertisement_number: Optional[str]
    closing_date: Optional[date]
    page: Optional[int]
    quote: Optional[str]
    status: Optional[str]
    vacancy_id: Optional[str]
    notice_id: Optional[str]

class RedFlagOut(BaseModel):
    code: str
    description: str
    quote: str
    citations: List[str]

class VerifyOut(BaseModel):
    verdict: str
    message: str
    verifier_version: str
    url: Optional[DomainCheckOut]
    links_in_text: List[DomainCheckOut]
    matches: List[NoticeMatchOut]
    red_flags: List[RedFlagOut]
    advertisement_numbers: List[str]
    text_chars: int
    pdf_pages: Optional[int]
    pdf_pages_read: Optional[int]
    ocr_pages: List[int]
    notes: List[str]

@router.post("", response_model=VerifyOut)
async def verify_notice(
    text: Optional[str] = Form(None, max_length=notice_verifier.MAX_TEXT_CHARS),
    url: Optional[str] = Form(None, max_length=2000),
    file: Optional[UploadFile] = File(None, description="the notice as a PDF, up to 10 MB"),
    ocr: bool = Form(True, description="read up to a few scanned pages of the PDF by OCR (slower)"),
    session: AsyncSession = Depends(get_session),
):
    return await run_check(session, text, url, file, ocr)

async def run_check(session: AsyncSession, text: Optional[str], url: Optional[str], file: Optional[UploadFile], ocr: bool) -> VerifyOut:
    """The check itself; also served to API-key holders as POST /api/v1/verify."""
    pdf = None  # status codes as numbers: their Starlette constant names changed across versions
    if file is not None and file.filename:
        pdf = await file.read(notice_verifier.MAX_PDF_BYTES + 1)
        if len(pdf) > notice_verifier.MAX_PDF_BYTES:
            raise HTTPException(413, "PDF is larger than 10 MB")
        if not pdf.startswith(b"%PDF-"):
            raise HTTPException(415, "only PDF files can be checked")
    if not (text and text.strip()) and not (url and url.strip()) and not pdf:
        raise HTTPException(422, "paste the notice text, a link, or upload a PDF")
    try:
        result = await notice_verifier.verify(session, text=text, url=url, pdf=pdf, ocr=ocr)
    except RuntimeError as e:  # PyMuPDF raises on a damaged file
        raise HTTPException(422, f"could not read this PDF: {e}") from e
    except notice_verifier.PdfTimeout as e:
        raise HTTPException(422, f"this PDF took longer than {notice_verifier.PDF_TIMEOUT_SECONDS} seconds to read") from e
    except notice_verifier.PdfBusy as e:
        raise HTTPException(503, "too many PDFs are being checked right now; try again in a minute",
                            headers={"Retry-After": "30"}) from e
    return VerifyOut.model_validate(asdict(result))
