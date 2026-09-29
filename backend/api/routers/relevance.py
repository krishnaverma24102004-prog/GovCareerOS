import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.api.deps import current_user, get_session
from govcareeros.backend.eligibility import engine as eligibility_engine
from govcareeros.backend.models.eligibility import MatchScore
from govcareeros.backend.models.user import User
from govcareeros.backend.models.vacancy import Vacancy
from govcareeros.backend.relevance import service
from govcareeros.backend.relevance.scoring import DEFAULT_WEIGHTS

router = APIRouter(tags=["relevance"])

class RelevanceIn(BaseModel):
    profile_version_id: uuid.UUID
    vacancy_id: uuid.UUID
    weights: Optional[Dict[str, float]] = None

class ComponentOut(BaseModel):
    name: str
    score: Optional[float]
    weight: float
    evidence: List[str]

class RelevanceOut(BaseModel):
    id: uuid.UUID
    profile_version_id: uuid.UUID
    vacancy_id: uuid.UUID
    score: float
    engine_version: str
    created_at: datetime
    components: List[ComponentOut]
    weights: Dict[str, float]

class MatchOut(BaseModel):
    vacancy_id: uuid.UUID
    post_name: str
    organization: str
    department: Optional[str]
    application_deadline: Optional[datetime]
    open_for_applications: bool
    eligibility_status: str
    eligibility_result_id: uuid.UUID
    relevance_score: float
    relevance_id: uuid.UUID

def _check_weights(weights: Optional[Dict[str, float]]):
    if weights is None:
        return
    if set(weights) != set(DEFAULT_WEIGHTS) or any(w < 0 for w in weights.values()) or not sum(weights.values()):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"weights must give non-negative values for exactly {sorted(DEFAULT_WEIGHTS)}")

@router.post("/relevance", response_model=RelevanceOut, status_code=status.HTTP_201_CREATED)
async def score(body: RelevanceIn, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    """Transparent 0-100 career relevance. Independent of, and never overriding, eligibility."""
    _check_weights(body.weights)
    try:
        match = await service.evaluate(session, user.id, body.profile_version_id, body.vacancy_id, body.weights)
    except eligibility_engine.EligibilityInputError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))
    await session.refresh(match)
    return service.as_dict(match)

@router.get("/relevance/{match_id}", response_model=RelevanceOut)
async def get_score(match_id: uuid.UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    match = (await session.execute(select(MatchScore).where(MatchScore.id == match_id, MatchScore.user_id == user.id))).scalar_one_or_none()
    if match is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "relevance score not found")
    return service.as_dict(match)

_STATUS_ORDER = {"ELIGIBLE": 0, "CONDITIONAL": 1, "MANUAL_REVIEW": 2, "INELIGIBLE": 3}

@router.get("/matches", response_model=List[MatchOut])
async def matches(
    profile_version_id: uuid.UUID = Query(..., description="explicit profile version; the current profile is never implied"),
    include_closed: bool = False,
    user: User = Depends(current_user), session: AsyncSession = Depends(get_session),
):
    """Every stored vacancy evaluated for one profile version: eligibility first, then relevance."""
    now = datetime.now(timezone.utc)
    stmt = select(Vacancy).where(Vacancy.status == "ACTIVE")
    if not include_closed:
        stmt = stmt.where((Vacancy.application_deadline.is_(None)) | (Vacancy.application_deadline > now))
    out = []
    for vacancy in (await session.execute(stmt)).scalars().all():
        try:
            elig = await eligibility_engine.evaluate(session, user.id, profile_version_id, vacancy.id)
            rel = await service.evaluate(session, user.id, profile_version_id, vacancy.id)
        except eligibility_engine.EligibilityInputError as e:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))
        out.append(MatchOut(
            vacancy_id=vacancy.id, post_name=vacancy.post_name, organization=vacancy.organization, department=vacancy.department,
            application_deadline=vacancy.application_deadline,
            open_for_applications=vacancy.application_deadline is None or vacancy.application_deadline > now,
            eligibility_status=elig.status, eligibility_result_id=elig.id, relevance_score=rel.score, relevance_id=rel.id,
        ))
    return sorted(out, key=lambda m: (_STATUS_ORDER.get(m.eligibility_status, 9), -m.relevance_score))
