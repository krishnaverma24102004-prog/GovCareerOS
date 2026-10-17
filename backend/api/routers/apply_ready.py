"""
Apply-ready pages (services/apply_ready.py): public. With a bearer token and ?profile_version_id= of that user, items
also carry the candidate's own status; another user's profile version is a 404, a profile version without a valid token
a 401. Without ?profile_version_id= a token is not looked at.
"""
import uuid
from datetime import datetime
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.api.deps import get_session, profile_owner
from govcareeros.backend.models.user import User
from govcareeros.backend.schemas.attribution import AttributionOut
from govcareeros.backend.services import apply_ready
from govcareeros.backend.services.application_prep import PrepInputError

router = APIRouter(prefix="/apply-ready", tags=["apply-ready"])

class CitationOut(BaseModel):
    page: Optional[int]
    quote: Optional[str]
    document_url: Optional[str]

class ItemOut(BaseModel):
    label: str
    detail: Optional[str]
    status: Optional[Literal["OK", "FAILS", "NEEDS_DOCUMENT", "CANNOT_DECIDE", "UNKNOWN"]]
    citation: Optional[CitationOut]

class SectionOut(BaseModel):
    key: str
    title: str
    items: List[ItemOut]

class ApplyReadyOut(BaseModel):
    kind: Literal["vacancy", "exam"]
    id: str
    title: str
    organization: Optional[str]
    advertisement_number: Optional[str]
    document_url: Optional[str]
    # NOT_YET_OPEN: before the application window opens (application_start)
    status: Literal["OPEN", "CLOSED", "NOT_YET_OPEN", "UNKNOWN"]
    application_start: Optional[datetime]
    deadline: Optional[datetime]
    sections: List[SectionOut]
    warnings: List[str]
    disclaimer: str
    attribution: Optional[AttributionOut]

async def _page(build, record_id: uuid.UUID, profile_version_id: Optional[uuid.UUID], user: Optional[User], session: AsyncSession) -> dict:
    # profile_owner has already made a profile_version_id without a valid token a 401
    try:
        return await build(session, record_id, user.id if user and profile_version_id else None, profile_version_id)
    except PrepInputError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))

def _html(page: dict) -> HTMLResponse:
    return HTMLResponse(apply_ready.render_html(page), headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})

# The .html routes come first: "{id}.html" would otherwise match the JSON route and fail UUID validation
@router.get("/vacancies/{vacancy_id}.html", response_class=HTMLResponse)
async def vacancy_html(vacancy_id: uuid.UUID, profile_version_id: Optional[uuid.UUID] = None,
                       user: Optional[User] = Depends(profile_owner), session: AsyncSession = Depends(get_session)):
    return _html(await _page(apply_ready.vacancy_page, vacancy_id, profile_version_id, user, session))

@router.get("/exams/{exam_id}.html", response_class=HTMLResponse)
async def exam_html(exam_id: uuid.UUID, profile_version_id: Optional[uuid.UUID] = None,
                    user: Optional[User] = Depends(profile_owner), session: AsyncSession = Depends(get_session)):
    return _html(await _page(apply_ready.exam_page, exam_id, profile_version_id, user, session))

@router.get("/vacancies/{vacancy_id}", response_model=ApplyReadyOut)
async def vacancy(vacancy_id: uuid.UUID, profile_version_id: Optional[uuid.UUID] = None,
                  user: Optional[User] = Depends(profile_owner), session: AsyncSession = Depends(get_session)):
    return await _page(apply_ready.vacancy_page, vacancy_id, profile_version_id, user, session)

@router.get("/exams/{exam_id}", response_model=ApplyReadyOut)
async def exam(exam_id: uuid.UUID, profile_version_id: Optional[uuid.UUID] = None,
               user: Optional[User] = Depends(profile_owner), session: AsyncSession = Depends(get_session)):
    return await _page(apply_ready.exam_page, exam_id, profile_version_id, user, session)
