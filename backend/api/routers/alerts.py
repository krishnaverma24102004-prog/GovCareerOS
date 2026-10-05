import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.api.deps import current_user, get_session
from govcareeros.backend.models.alert import UserAlert
from govcareeros.backend.models.user import User
from govcareeros.backend.services.radar import run_radar

router = APIRouter(tags=["alerts"])

class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    kind: str
    title: str
    body: str
    vacancy_id: Optional[uuid.UUID]
    profile_version_id: Optional[uuid.UUID]
    eligibility_result_id: Optional[uuid.UUID]
    vacancy_version_id: Optional[uuid.UUID]
    exam_id: Optional[uuid.UUID] = None
    exam_version_id: Optional[uuid.UUID] = None
    due_at: Optional[datetime]
    read_at: Optional[datetime]
    created_at: datetime

class RadarRunOut(BaseModel):
    evaluations: int
    alerts_created: Dict[str, int]

@router.post("/radar/run", response_model=RadarRunOut)
async def run_for_me(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    """Re-check the caller's latest profile version against all open vacancies and create alerts."""
    run = await run_radar(session, user_ids=[user.id])
    return RadarRunOut(evaluations=run.evaluations, alerts_created=run.alerts_created)

@router.get("/alerts", response_model=List[AlertOut])
async def list_alerts(unread_only: bool = False, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    stmt = select(UserAlert).where(UserAlert.user_id == user.id)
    if unread_only:
        stmt = stmt.where(UserAlert.read_at.is_(None))
    # Unread first, then the most urgent
    stmt = stmt.order_by(UserAlert.read_at.is_not(None), UserAlert.due_at.is_(None), UserAlert.due_at, UserAlert.created_at.desc())
    return (await session.execute(stmt)).scalars().all()

@router.post("/alerts/{alert_id}/read", response_model=AlertOut)
async def mark_read(alert_id: uuid.UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    alert = (await session.execute(select(UserAlert).where(UserAlert.id == alert_id, UserAlert.user_id == user.id))).scalar_one_or_none()
    if alert is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "alert not found")
    alert.read_at = alert.read_at or datetime.now(timezone.utc)
    await session.flush()
    await session.refresh(alert)
    return alert
