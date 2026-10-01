"""Reviewer administration (reviewer-only): who is a reviewer, and the audit log of every reviewer decision."""
import json
import uuid
from datetime import datetime
from typing import Any, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.api.deps import get_session
from govcareeros.backend.api.reviewer_deps import require_reviewer
from govcareeros.backend.models.audit import ReviewAuditLog
from govcareeros.backend.models.user import User
from govcareeros.backend.services import review_audit

router = APIRouter(prefix="/admin", tags=["admin"])

class UserRow(BaseModel):
    id: uuid.UUID
    email: str
    full_name: Optional[str]
    is_active: bool
    is_reviewer: bool

@router.get("/users", response_model=List[UserRow])
async def list_users(q: Optional[str] = Query(None, max_length=200), limit: int = Query(100, ge=1, le=500),
                     offset: int = Query(0, ge=0), user: User = Depends(require_reviewer),
                     session: AsyncSession = Depends(get_session)):
    stmt = select(User).order_by(User.is_reviewer.desc(), User.email).offset(offset).limit(limit)
    if q and q.strip():
        stmt = stmt.where(func.lower(User.email).contains(q.strip().lower(), autoescape=True))
    return [UserRow(id=u.id, email=u.email, full_name=u.full_name, is_active=u.is_active, is_reviewer=u.is_reviewer)
            for u in (await session.execute(stmt)).scalars()]

class ReviewerFlagIn(BaseModel):
    is_reviewer: bool
    note: Optional[str] = None

@router.post("/users/{user_id}/reviewer", response_model=UserRow)
async def set_reviewer(user_id: uuid.UUID, body: ReviewerFlagIn, user: User = Depends(require_reviewer),
                       session: AsyncSession = Depends(get_session)):
    target = await session.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    if target.is_reviewer and not body.is_reviewer:
        others = (await session.execute(select(func.count()).select_from(User).where(
            User.is_reviewer.is_(True), User.is_active.is_(True), User.id != target.id))).scalar_one()
        if others == 0:
            raise HTTPException(status.HTTP_409_CONFLICT, "cannot remove the last reviewer")
    if target.is_reviewer != body.is_reviewer:
        before = target.is_reviewer
        target.is_reviewer = body.is_reviewer
        note = (body.note or "").strip() or None
        review_audit.record(session, user, "REVIEWER_GRANT" if body.is_reviewer else "REVIEWER_REVOKE", "USER", target.id,
                            note, {"from": before, "to": body.is_reviewer})
        await session.flush()
    return UserRow(id=target.id, email=target.email, full_name=target.full_name, is_active=target.is_active,
                   is_reviewer=target.is_reviewer)

class AuditRow(BaseModel):
    id: uuid.UUID
    actor_id: uuid.UUID
    actor_email: str
    action: str
    object_type: str
    object_id: uuid.UUID
    note: Optional[str]
    detail: Optional[Any]
    at: datetime

class AuditPage(BaseModel):
    total: int
    offset: int
    limit: int
    items: List[AuditRow]

@router.get("/audit", response_model=AuditPage)
async def audit_log(object_type: Optional[Literal["DRAFT", "NOTICE", "FACT", "USER"]] = None,
                    object_id: Optional[uuid.UUID] = None, actor_id: Optional[uuid.UUID] = None,
                    action: Optional[str] = Query(None, max_length=40),
                    limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0, le=1_000_000),
                    user: User = Depends(require_reviewer), session: AsyncSession = Depends(get_session)):
    stmt = select(ReviewAuditLog, User.email).join(User, User.id == ReviewAuditLog.actor_id)
    if object_type:
        stmt = stmt.where(ReviewAuditLog.object_type == object_type)
    if object_id:
        stmt = stmt.where(ReviewAuditLog.object_id == object_id)
    if actor_id:
        stmt = stmt.where(ReviewAuditLog.actor_id == actor_id)
    if action:
        stmt = stmt.where(ReviewAuditLog.action == action)
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await session.execute(stmt.order_by(ReviewAuditLog.at.desc(), ReviewAuditLog.id).offset(offset).limit(limit))).all()
    return AuditPage(total=total, offset=offset, limit=limit, items=[
        AuditRow(id=a.id, actor_id=a.actor_id, actor_email=email, action=a.action, object_type=a.object_type,
                 object_id=a.object_id, note=a.note, detail=json.loads(a.detail) if a.detail else None, at=a.at)
        for a, email in rows])
