"""
DPDP Act 2023: the consent notice, consents (give / list / withdraw) and the person's rights (export, erase).
Correction is the ordinary profile edit (PATCH /profile).
"""
from datetime import timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.api.deps import current_user, get_session
from govcareeros.backend.config.settings import settings
from govcareeros.backend.models.consent import ConsentRecord
from govcareeros.backend.models.user import User
from govcareeros.backend.privacy import enforcement, notice, rights
from govcareeros.backend.repositories.profile_repository import ProfileRepository
from govcareeros.backend.schemas.privacy import ConsentIn, ConsentOut, ConsentsOut, EraseIn, EraseOut, WithdrawOut
from govcareeros.backend.services import consent as consent_service

router = APIRouter(prefix="/privacy", tags=["privacy"])

def _purpose(purpose: str) -> str:
    if purpose not in notice.PURPOSES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown purpose {purpose!r}; see GET /privacy/notice")
    return purpose

@router.get("/notice")
async def get_notice():
    """Public: shown before signup and before each consent."""
    return notice.notice()

@router.post("/consents", response_model=ConsentOut, status_code=status.HTTP_201_CREATED)
async def give_consent(body: ConsentIn, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    purpose = _purpose(body.purpose)
    if body.notice_version != notice.NOTICE_VERSION:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"the notice has changed (now {notice.NOTICE_VERSION}); read it again before consenting")
    guardian = (body.guardian_name or "").strip() or None
    # Age gate: if the profile already says the person is under 18, only a parent / guardian can give consent
    latest = await ProfileRepository(session).latest_version(user.id)
    dob = await enforcement.version_date_of_birth(session, latest.id) if latest else None
    if enforcement.is_minor(dob) and guardian is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, {
            "code": "GUARDIAN_CONSENT_REQUIRED", "purpose": purpose,
            "message": "The date of birth on your profile shows you are under 18: a parent or lawful guardian must give this consent.",
            "action": "Ask your parent or guardian to give consent with their name in guardian_name."})
    return await consent_service.record_consent(session, user.id, purpose, notice.data_items(purpose), notice.NOTICE_VERSION,
                                                guardian_name=guardian)

@router.get("/consents", response_model=ConsentsOut)
async def list_consents(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(ConsentRecord).where(ConsentRecord.user_id == user.id)
                                  .order_by(ConsentRecord.granted_at.desc()))).scalars().all()
    active = {}
    for r in rows:
        if r.withdrawn_at is None:
            active.setdefault(r.purpose, r)
    return ConsentsOut(notice_version=notice.NOTICE_VERSION, active=sorted(active),
                       outdated=sorted(p for p, r in active.items() if r.notice_version != notice.NOTICE_VERSION),
                       records=[ConsentOut.model_validate(r) for r in rows])

@router.delete("/consents/{purpose}", response_model=WithdrawOut)
async def withdraw_consent(purpose: str, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    """Stops processing for the purpose from now on. Stored data and past results are kept (erase deletes them)."""
    return WithdrawOut(purpose=purpose, withdrawn=await consent_service.withdraw(session, user.id, _purpose(purpose)))

@router.get("/export")
async def export(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    """Everything held about the person, as JSON (the right of access)."""
    return await rights.export_user_data(session, user)

@router.post("/erase", response_model=EraseOut, status_code=status.HTTP_202_ACCEPTED)
async def erase(body: EraseIn, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    """Closes the account now (logged out everywhere, all consents withdrawn); the data is erased after the grace period."""
    user = await rights.request_erasure(session, user)
    due = user.erasure_due_at.astimezone(timezone.utc)
    return EraseOut(erasure_requested_at=user.erasure_requested_at, erasure_due_at=due, grace_days=settings.ERASURE_GRACE_DAYS,
                    message=(f"Your account is closed. Your data will be erased on or after {due.date().isoformat()}. "
                             f"To cancel before then, contact: {notice.grievance()['contact']}"))
