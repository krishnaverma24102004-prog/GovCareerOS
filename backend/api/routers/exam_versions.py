"""Official changes to an examination (corrigenda, addenda, schedule notices), newest notice first. Public exam data."""
import json
import uuid
from dataclasses import asdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.api.deps import current_user, get_session
from govcareeros.backend.models.exam import Exam
from govcareeros.backend.models.exam_version import ExamVersion
from govcareeros.backend.models.user import User
from govcareeros.backend.schemas.attribution import DISCLAIMER, AttributionOut
from govcareeros.backend.services import attribution, change_log, deadline_helper

router = APIRouter(prefix="/exams", tags=["exams"])

class ExamVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    exam_id: uuid.UUID
    raw_document_id: uuid.UUID
    notice_id: Optional[uuid.UUID]
    change_type: Optional[str]
    notice_date: Optional[datetime]
    parser_version: Optional[str]
    change_log: Optional[str]
    details: Optional[Any]
    created_at: datetime
    attribution: Optional[AttributionOut] = None
    disclaimer: str = DISCLAIMER

    @field_validator("details", mode="before")
    @classmethod
    def _json(cls, v):
        return json.loads(v) if isinstance(v, str) else v

@router.get("/{exam_id}/versions", response_model=List[ExamVersionOut], dependencies=[Depends(current_user)])
async def list_versions(exam_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    exam = await session.get(Exam, exam_id)
    if exam is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "exam not found")
    stmt = (select(ExamVersion).where(ExamVersion.exam_id == exam_id)
            .order_by(ExamVersion.notice_date.desc().nulls_last(), ExamVersion.created_at.desc()))
    versions = (await session.execute(stmt)).scalars().all()
    return [ExamVersionOut.model_validate(v).model_copy(update={"attribution": a})
            for v, a in zip(versions, await attribution.for_exam_versions(session, exam, versions))]

@router.get("/{exam_id}/changes", dependencies=[Depends(current_user)])
async def list_changes(exam_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> List[Dict[str, Any]]:
    """Field-level change log, oldest notice first: each change with the earlier value's and the new value's page and quote."""
    exam = await session.get(Exam, exam_id)
    if exam is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "exam not found")
    return [{**asdict(vc), "lines": change_log.change_lines(vc), "disclaimer": DISCLAIMER} for vc in await change_log.exam_change_log(session, exam)]

@router.get("/{exam_id}/deadline")
async def deadline(exam_id: uuid.UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """The official last date and its evidence; a forwarding estimate for in-service applicants is separate and labelled."""
    exam = await session.get(Exam, exam_id)
    if exam is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "exam not found")
    _, evidence = await change_log.exam_current_evidence(session, exam, "application_end")
    raw_ids = [exam.raw_document_id] + list((await session.execute(select(ExamVersion.raw_document_id).where(ExamVersion.exam_id == exam.id))).scalars())
    return await deadline_helper.deadline_info(session, user.id, exam.application_end, evidence, raw_ids)
