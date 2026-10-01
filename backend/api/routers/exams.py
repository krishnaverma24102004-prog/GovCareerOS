"""Examinations (SSC CHSL, ...) and per-user exam eligibility. Exam data is public; eligibility results are the caller's own."""
import json
import uuid
from datetime import datetime
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from govcareeros.backend.api.deps import current_user, get_session
from govcareeros.backend.eligibility import exam_engine
from govcareeros.backend.eligibility.engine import EligibilityInputError
from govcareeros.backend.models.exam import Exam, ExamEligibilityResult
from govcareeros.backend.models.user import User
from govcareeros.backend.schemas.api import EligibilityEvidenceOut, ProvenanceOut, RequirementOut

router = APIRouter(prefix="/exams", tags=["exams"])

class ExamPostOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    post_name: str
    post_code: Optional[str]
    department: Optional[str]
    classification: Optional[str] = None
    pay_level: Optional[str]
    pay_scale: Optional[str]
    age_min: Optional[int]
    age_max: Optional[int]
    education_text: Optional[str]
    vacancy_count: Optional[int]
    source_page: Optional[int]
    source_excerpt: Optional[str]

class ExamSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    conducting_body: str
    name: str
    exam_code: Optional[str]
    exam_year: Optional[int]
    status: str
    application_start: Optional[datetime]
    application_end: Optional[datetime]
    notification_url: Optional[str]
    tentative_vacancies: Optional[int]
    confidence: float

class ExamDetail(ExamSummary):
    external_ref: str
    raw_document_id: Optional[uuid.UUID]
    fee_payment_end: Optional[datetime]
    correction_start: Optional[datetime]
    correction_end: Optional[datetime]
    fee_amount: Optional[int]
    fee_exemption: Optional[str]
    exam_dates: Optional[Any]
    age_crucial_date: Optional[datetime]
    education_cutoff_date: Optional[datetime]
    education_text: Optional[str]
    posts: List[ExamPostOut] = []
    requirements: List[RequirementOut] = []
    provenance: List[ProvenanceOut] = []

    @field_validator("exam_dates", mode="before")
    @classmethod
    def _json(cls, v):
        return json.loads(v) if isinstance(v, str) else v

class ExamEligibilityIn(BaseModel):
    profile_version_id: uuid.UUID
    post_id: Optional[uuid.UUID] = None

class ExamEligibilityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    exam_id: uuid.UUID
    post_id: Optional[uuid.UUID]
    profile_version_id: uuid.UUID
    status: str
    confidence: float
    engine_version: str
    crucial_date: Optional[datetime]
    created_at: datetime
    evidence: List[EligibilityEvidenceOut]

@router.get("", response_model=List[ExamSummary], dependencies=[Depends(current_user)])
async def list_exams(q: Optional[str] = Query(None, description="substring of the exam name or code"),
                     open_only: bool = False, limit: int = Query(50, le=200), offset: int = 0,
                     session: AsyncSession = Depends(get_session)):
    stmt = select(Exam).order_by(Exam.application_end.desc().nulls_last(), Exam.name)
    if q:
        stmt = stmt.where(Exam.name.ilike(f"%{q}%") | Exam.exam_code.ilike(f"%{q}%"))
    if open_only:
        stmt = stmt.where(Exam.status == "NOTIFIED", Exam.application_end > datetime.now().astimezone())
    return (await session.execute(stmt.limit(limit).offset(offset))).scalars().all()

@router.get("/{exam_id}", response_model=ExamDetail, dependencies=[Depends(current_user)])
async def get_exam(exam_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    exam = (await session.execute(select(Exam).options(
        selectinload(Exam.posts), selectinload(Exam.requirements), selectinload(Exam.provenance)).where(Exam.id == exam_id))).scalar_one_or_none()
    if exam is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "exam not found")
    # Filter on the response, never on the ORM relationship (that would detach rows on commit)
    detail = ExamDetail.model_validate(exam)
    active = {r.id for r in exam.requirements if r.superseded_at is None}
    return detail.model_copy(update={"requirements": [r for r in detail.requirements if r.id in active]})

@router.post("/{exam_id}/eligibility", response_model=ExamEligibilityOut, status_code=status.HTTP_201_CREATED)
async def evaluate(exam_id: uuid.UUID, body: ExamEligibilityIn, user: User = Depends(current_user),
                   session: AsyncSession = Depends(get_session)):
    """Deterministic hard eligibility for an explicit profile version (optionally one post of the exam)."""
    try:
        return await exam_engine.evaluate_exam(session, user.id, body.profile_version_id, exam_id, body.post_id)
    except EligibilityInputError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))

@router.get("/{exam_id}/eligibility", response_model=List[ExamEligibilityOut])
async def list_results(exam_id: uuid.UUID, limit: int = Query(50, le=200), user: User = Depends(current_user),
                       session: AsyncSession = Depends(get_session)):
    stmt = (select(ExamEligibilityResult).options(selectinload(ExamEligibilityResult.evidence))
            .where(ExamEligibilityResult.exam_id == exam_id, ExamEligibilityResult.user_id == user.id)
            .order_by(ExamEligibilityResult.created_at.desc()).limit(limit))
    return (await session.execute(stmt)).scalars().all()

@router.get("/{exam_id}/eligibility/{result_id}", response_model=ExamEligibilityOut)
async def get_result(exam_id: uuid.UUID, result_id: uuid.UUID, user: User = Depends(current_user),
                     session: AsyncSession = Depends(get_session)):
    result = await exam_engine.get_exam_result(session, user.id, result_id)
    if result is None or result.exam_id != exam_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "eligibility result not found")
    return result
