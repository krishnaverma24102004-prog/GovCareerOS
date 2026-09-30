"""Review queue for LLM extraction drafts. Reading is open to logged-in users; decisions need a reviewer."""
import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.api.deps import current_user, get_session
from govcareeros.backend.models.extraction import ExtractionDraft
from govcareeros.backend.models.notice import RecruitmentNotice
from govcareeros.backend.models.source import GovernmentSource
from govcareeros.backend.models.user import User
from govcareeros.backend.services import exam_drafts, extraction_drafts

router = APIRouter(prefix="/review", tags=["review"])

class DraftSummary(BaseModel):
    id: uuid.UUID
    source: str
    notice_title: Optional[str]
    status: str
    provider: str
    model: str
    fields_offered: int
    fields_accepted: int
    vacancies: int
    llm_seconds: float
    created_at: datetime

class DraftDetail(DraftSummary):
    raw_document_id: uuid.UUID
    notice_url: Optional[str]
    error: Optional[str]
    review_note: Optional[str]
    created_vacancy_ids: List[str]
    payload: Dict[str, Any]

class ApproveIn(BaseModel):
    vacancy_indexes: Optional[List[int]] = None
    note: Optional[str] = None

class RejectIn(BaseModel):
    note: str

async def _load(session: AsyncSession, draft_id: uuid.UUID):
    row = (await session.execute(
        select(ExtractionDraft, GovernmentSource.name, RecruitmentNotice.title, RecruitmentNotice.url)
        .join(GovernmentSource, GovernmentSource.id == ExtractionDraft.source_id)
        .outerjoin(RecruitmentNotice, RecruitmentNotice.id == ExtractionDraft.notice_id)
        .where(ExtractionDraft.id == draft_id))).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "draft not found")
    return row

def _summary(d: ExtractionDraft, source: str, title: Optional[str]) -> dict:
    return dict(id=d.id, source=source, notice_title=title, status=d.status, provider=d.provider, model=d.model,
                fields_offered=d.fields_offered, fields_accepted=d.fields_accepted,
                vacancies=len(json.loads(d.payload).get("vacancies", [])), llm_seconds=d.llm_seconds, created_at=d.created_at)

def _detail(d: ExtractionDraft, source: str, title: Optional[str], url: Optional[str]) -> DraftDetail:
    return DraftDetail(**_summary(d, source, title), raw_document_id=d.raw_document_id, notice_url=url, error=d.error,
                       review_note=d.review_note, created_vacancy_ids=json.loads(d.created_vacancy_ids or "[]"),
                       payload=json.loads(d.payload))

@router.get("/drafts", response_model=List[DraftSummary])
async def list_drafts(status_filter: Optional[str] = "PENDING_REVIEW", user: User = Depends(current_user),
                      session: AsyncSession = Depends(get_session)):
    stmt = (select(ExtractionDraft, GovernmentSource.name, RecruitmentNotice.title)
            .join(GovernmentSource, GovernmentSource.id == ExtractionDraft.source_id)
            .outerjoin(RecruitmentNotice, RecruitmentNotice.id == ExtractionDraft.notice_id)
            .order_by(ExtractionDraft.created_at.desc()))
    if status_filter:
        stmt = stmt.where(ExtractionDraft.status == status_filter)
    return [_summary(d, s, t) for d, s, t in (await session.execute(stmt)).all()]

@router.get("/drafts/{draft_id}", response_model=DraftDetail)
async def get_draft(draft_id: uuid.UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    return _detail(*(await _load(session, draft_id)))

@router.post("/drafts/{draft_id}/approve", response_model=DraftDetail)
async def approve(draft_id: uuid.UUID, body: ApproveIn, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    draft, source, title, url = await _load(session, draft_id)
    try:
        if exam_drafts.is_exam_draft(draft):
            await exam_drafts.approve_exam_draft(session, draft, user, body.note)
        else:
            await extraction_drafts.approve(session, draft, user, body.vacancy_indexes, body.note)
    except PermissionError as e:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(e))
    except extraction_drafts.DraftError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    except IntegrityError:  # a concurrent approval already created this vacancy
        raise HTTPException(status.HTTP_409_CONFLICT, "vacancy already approved")
    return _detail(draft, source, title, url)

@router.post("/drafts/{draft_id}/reject", response_model=DraftDetail)
async def reject(draft_id: uuid.UUID, body: RejectIn, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    draft, source, title, url = await _load(session, draft_id)
    try:
        await extraction_drafts.reject(session, draft, user, body.note)
    except PermissionError as e:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(e))
    except extraction_drafts.DraftError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    return _detail(draft, source, title, url)
