"""Official changes to an examination (corrigenda, addenda, schedule notices), newest notice first. Public exam data."""
import json
import uuid
from datetime import datetime
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.api.deps import current_user, get_session
from govcareeros.backend.models.exam import Exam
from govcareeros.backend.models.exam_version import ExamVersion

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

    @field_validator("details", mode="before")
    @classmethod
    def _json(cls, v):
        return json.loads(v) if isinstance(v, str) else v

@router.get("/{exam_id}/versions", response_model=List[ExamVersionOut], dependencies=[Depends(current_user)])
async def list_versions(exam_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    if await session.get(Exam, exam_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "exam not found")
    stmt = (select(ExamVersion).where(ExamVersion.exam_id == exam_id)
            .order_by(ExamVersion.notice_date.desc().nulls_last(), ExamVersion.created_at.desc()))
    return (await session.execute(stmt)).scalars().all()
