import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from govcareeros.backend.api.deps import current_user, get_session
from govcareeros.backend.eligibility import engine, rules
from govcareeros.backend.models.eligibility import EligibilityResult
from govcareeros.backend.models.user import User
from govcareeros.backend.models.vacancy import Vacancy
from govcareeros.backend.schemas.api import EligibilityIn, EligibilityOut

router = APIRouter(prefix="/eligibility", tags=["eligibility"])

@router.post("", response_model=EligibilityOut, status_code=status.HTTP_201_CREATED)
async def evaluate(body: EligibilityIn, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    """Deterministic hard eligibility for an explicit profile version. Never uses 'the current profile' implicitly."""
    try:
        return await engine.evaluate(session, user.id, body.profile_version_id, body.vacancy_id)
    except engine.EligibilityInputError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))

@router.get("", response_model=List[EligibilityOut])
async def list_results(
    vacancy_id: Optional[uuid.UUID] = None, profile_version_id: Optional[uuid.UUID] = None,
    limit: int = Query(50, le=200),
    current: bool = Query(False, description="only results the present engine gives for the vacancy as it now stands (same "
                                             "engine version and input fingerprint); needs vacancy_id"),
    user: User = Depends(current_user), session: AsyncSession = Depends(get_session),
):
    stmt = select(EligibilityResult).options(selectinload(EligibilityResult.evidence)) \
        .where(EligibilityResult.user_id == user.id).order_by(EligibilityResult.created_at.desc()).limit(limit)
    if current:
        # A result from an older engine, or from requirement rows since superseded, is history rather than the answer now
        if vacancy_id is None:
            raise HTTPException(422, "current=true needs vacancy_id")
        vacancy = await session.get(Vacancy, vacancy_id)
        if vacancy is None:
            return []
        fingerprint = engine.input_fingerprint(vacancy, await engine.load_requirements(session, vacancy_id))
        stmt = stmt.where(EligibilityResult.engine_version == rules.ENGINE_VERSION, EligibilityResult.input_fingerprint == fingerprint)
    if vacancy_id:
        stmt = stmt.where(EligibilityResult.vacancy_id == vacancy_id)
    if profile_version_id:
        stmt = stmt.where(EligibilityResult.profile_version_id == profile_version_id)
    return (await session.execute(stmt)).scalars().all()

@router.get("/{result_id}", response_model=EligibilityOut)
async def get_result(result_id: uuid.UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    result = await engine.get_result(session, user.id, result_id)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "eligibility result not found")
    return result
