import uuid
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.api.deps import current_user, get_session
from govcareeros.backend.eligibility import engine as eligibility_engine
from govcareeros.backend.gaps import service
from govcareeros.backend.models.user import User

router = APIRouter(prefix="/gaps", tags=["gaps"])

class GapOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    gap_type: str
    reason_code: Optional[str]
    blocking: bool
    requirement: str
    requirement_quote: Optional[str]
    requirement_page: Optional[int]
    requirement_id: Optional[uuid.UUID]
    profile_has: Optional[str]
    profile_field: Optional[str]
    next_step: Optional[str]
    due_date: Optional[date]

class GapsOut(BaseModel):
    profile_version_id: uuid.UUID
    vacancy_id: uuid.UUID
    eligibility_result_id: uuid.UUID
    eligibility_status: str
    engine_version: str
    gaps: List[GapOut]

class BlockerOut(BaseModel):
    gap_type: str
    reason_code: Optional[str]
    profile_field: Optional[str]
    vacancies: int
    vacancy_ids: List[uuid.UUID]
    posts: List[str]
    earliest_due_date: Optional[date]
    example_next_step: Optional[str]

class SummaryOut(BaseModel):
    profile_version_id: uuid.UUID
    vacancies_evaluated: int
    vacancies_without_blockers: int
    blockers: List[BlockerOut]

@router.get("", response_model=GapsOut)
async def gaps(
    profile_version_id: uuid.UUID = Query(..., description="explicit profile version; the current profile is never implied"),
    vacancy_id: uuid.UUID = Query(...),
    user: User = Depends(current_user), session: AsyncSession = Depends(get_session),
):
    """What stands between this profile version and this vacancy, each gap cited to the notification."""
    try:
        result, rows = await service.gaps_for(session, user.id, profile_version_id, vacancy_id)
    except eligibility_engine.EligibilityInputError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))
    return GapsOut(profile_version_id=profile_version_id, vacancy_id=vacancy_id, eligibility_result_id=result.id,
                   eligibility_status=result.status, engine_version=service.ENGINE_VERSION,
                   gaps=[GapOut.model_validate(g) for g in rows])

@router.get("/summary", response_model=SummaryOut)
async def gaps_summary(
    profile_version_id: uuid.UUID = Query(...), include_closed: bool = False,
    user: User = Depends(current_user), session: AsyncSession = Depends(get_session),
):
    """Most common blockers for one profile version across all active vacancies."""
    try:
        return await service.summary(session, user.id, profile_version_id, include_closed)
    except eligibility_engine.EligibilityInputError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))
