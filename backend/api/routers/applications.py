"""
Application preparation checklist and the user's own application tracker.

All rows are scoped to the caller: another user's application (or profile version) is a 404.
"""
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from govcareeros.backend.api.deps import current_user, get_session
from govcareeros.backend.models.application import Application, ApplicationStatusHistory
from govcareeros.backend.models.user import ProfileVersion, User
from govcareeros.backend.models.vacancy import Vacancy
from govcareeros.backend.services.application_prep import PrepInputError, build_checklist
from govcareeros.backend.services.application_tracker import change_status

router = APIRouter(prefix="/applications", tags=["applications"])

Status = Literal["PLANNED", "APPLIED", "FEE_PAID", "ADMIT_CARD", "EXAM", "RESULT", "SELECTED", "REJECTED", "WITHDRAWN"]

class _Fields(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_version_id: Optional[uuid.UUID] = None
    application_number: Optional[str] = Field(None, max_length=100)
    applied_at: Optional[AwareDatetime] = None
    fee_paid_at: Optional[AwareDatetime] = None
    exam_at: Optional[AwareDatetime] = None
    notes: Optional[str] = None

class ApplicationIn(_Fields):
    vacancy_id: Optional[uuid.UUID] = None
    exam_ref: Optional[str] = Field(None, max_length=255, description="exam not stored as a vacancy, e.g. 'UPSC CSE 2027'")
    status: Status = "PLANNED"

    @model_validator(mode="after")
    def _target(self):
        if not self.vacancy_id and not (self.exam_ref or "").strip():
            raise ValueError("vacancy_id or exam_ref is required")
        return self

class ApplicationPatch(_Fields):
    status: Optional[Status] = None
    status_note: Optional[str] = None

class HistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    from_status: Optional[str]
    to_status: str
    changed_at: datetime
    note: Optional[str]

class ApplicationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    vacancy_id: Optional[uuid.UUID]
    exam_ref: Optional[str]
    profile_version_id: Optional[uuid.UUID]
    status: str
    application_number: Optional[str]
    applied_at: Optional[datetime]
    fee_paid_at: Optional[datetime]
    exam_at: Optional[datetime]
    notes: Optional[str]
    created_at: datetime
    history: List[HistoryOut]

async def _own(session: AsyncSession, user: User, application_id: uuid.UUID) -> Application:
    app = (await session.execute(select(Application).options(selectinload(Application.history)).where(
        Application.id == application_id, Application.user_id == user.id).execution_options(populate_existing=True))).scalar_one_or_none()
    if app is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "application not found")
    return app

async def _check_profile_version(session: AsyncSession, user: User, version_id: Optional[uuid.UUID]) -> None:
    if version_id and (await session.execute(select(ProfileVersion.id).where(
            ProfileVersion.id == version_id, ProfileVersion.user_id == user.id))).scalar_one_or_none() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "profile version not found")

@router.get("/prep")
async def prep(vacancy_id: uuid.UUID, profile_version_id: uuid.UUID,
               user: User = Depends(current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Documents, fee and how-to-apply checklist for one vacancy, personalised by an explicit profile version; every item cited."""
    try:
        return await build_checklist(session, user.id, vacancy_id, profile_version_id)
    except PrepInputError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))

@router.post("", response_model=ApplicationOut, status_code=status.HTTP_201_CREATED)
async def create(body: ApplicationIn, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    await _check_profile_version(session, user, body.profile_version_id)
    if body.vacancy_id:
        if await session.get(Vacancy, body.vacancy_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "vacancy not found")
        if (await session.execute(select(Application.id).where(
                Application.user_id == user.id, Application.vacancy_id == body.vacancy_id))).scalar_one_or_none():
            raise HTTPException(status.HTTP_409_CONFLICT, "this vacancy is already tracked")
    app = Application(id=uuid.uuid4(), user_id=user.id, **body.model_dump(exclude={"exam_ref"}), exam_ref=(body.exam_ref or "").strip() or None)
    session.add(app)
    session.add(ApplicationStatusHistory(application_id=app.id, from_status=None, to_status=app.status,
                                         changed_at=datetime.now(timezone.utc), note="created"))
    await session.flush()
    return await _own(session, user, app.id)

@router.get("", response_model=List[ApplicationOut])
async def list_applications(status_: Optional[Status] = Query(None, alias="status"), user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    stmt = select(Application).options(selectinload(Application.history)).where(Application.user_id == user.id) \
        .order_by(Application.created_at.desc())
    if status_:
        stmt = stmt.where(Application.status == status_)
    return (await session.execute(stmt)).scalars().all()

@router.get("/{application_id}", response_model=ApplicationOut)
async def get_application(application_id: uuid.UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    return await _own(session, user, application_id)

@router.patch("/{application_id}", response_model=ApplicationOut)
async def update(application_id: uuid.UUID, body: ApplicationPatch, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    app = await _own(session, user, application_id)
    changes = body.model_dump(exclude_unset=True, exclude={"status", "status_note"})
    if "profile_version_id" in changes:
        await _check_profile_version(session, user, changes["profile_version_id"])
    for key, val in changes.items():
        setattr(app, key, val)
    if body.status:
        entry = change_status(app, body.status, body.status_note)
        if entry is not None:
            session.add(entry)
    await session.flush()
    return await _own(session, user, app.id)

@router.delete("/{application_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove(application_id: uuid.UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    app = await _own(session, user, application_id)
    await session.execute(delete(ApplicationStatusHistory).where(ApplicationStatusHistory.application_id == app.id))
    await session.execute(delete(Application).where(Application.id == app.id, Application.user_id == user.id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
