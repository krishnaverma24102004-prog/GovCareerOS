"""
Data principal rights (DPDP Act 2023 s.11-13): access (export everything held about the person as JSON) and erasure.
Correction is the ordinary profile edit, which writes a new ProfileVersion.

Erasure has two steps. request_erasure deactivates the account at once (no login, tokens revoked, every consent
withdrawn so nothing is processed) and schedules the hard erase ERASURE_GRACE_DAYS later. erase_due, run by
scripts/privacy_erase.py, then deletes the personal data and leaves an anonymised users row.

Kept on purpose (docs/privacy/data_inventory.md): consent_records (proof of consent and of its withdrawal, s.6(10)),
review_audit_log rows and reviewer/operator references where the person acted as a reviewer or operator (audit of
decisions about official data, not data about them), and every account under a legal hold (legal_hold_reason).
"""
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.config.settings import settings
from govcareeros.backend.models.alert import UserAlert
from govcareeros.backend.models.application import Application, ApplicationStatusHistory
from govcareeros.backend.models.auth_token import AuthToken
from govcareeros.backend.models.consent import ConsentRecord
from govcareeros.backend.models.digest import DigestRun
from govcareeros.backend.models.eligibility import EligibilityEvidence, EligibilityResult, MatchScore, SkillGap
from govcareeros.backend.models.exam import ExamEligibilityEvidence, ExamEligibilityResult
from govcareeros.backend.models.exam_prep import StudyPlan, StudyPlanItem
from govcareeros.backend.models.fact import Evidence, Fact
from govcareeros.backend.models.resume import Resume, ResumeClaim
from govcareeros.backend.models.user import ProfileVersion, User
from govcareeros.backend.privacy import notice
from govcareeros.backend.repositories.profile_repository import SECTION_MODELS, VERSIONED_ENTITIES

# Tables keyed directly by users.id that hold the person's own data (deleted by the hard erase, in this order after
# their children). Tables that reference users.id only as the actor (reviewer, operator) are not listed: see RETAINED.
OWNED_BY_USER = [UserAlert, SkillGap, MatchScore, Resume, Application, StudyPlan, DigestRun, EligibilityResult,
                 ExamEligibilityResult, ProfileVersion, AuthToken]
RETAINED = {
    "consent_records": "proof of consent and withdrawal (DPDP Act s.6(10)); kept against the anonymised user row",
    "review_audit_log": "append-only audit of reviewer decisions; references the person only as the acting reviewer",
    "profile_facts.reviewer_id / extraction_drafts.reviewer_id / recruitment_notices.reviewer_id":
        "the person as reviewer of other records, not data about them",
    "consent_records.captured_by_id": "the person as the operator who recorded someone else's consent",
}

class ErasureError(ValueError):
    pass

def _row(obj: Any, exclude: Sequence[str] = ()) -> Dict[str, Any]:
    # Through the ORM, so encrypted columns come out decrypted
    return {c.key: getattr(obj, c.key) for c in obj.__table__.columns if c.key not in exclude}

async def _all(session: AsyncSession, stmt) -> List[Any]:
    return list((await session.execute(stmt)).scalars().all())

async def export_user_data(session: AsyncSession, user: User) -> Dict[str, Any]:
    """Everything stored about the person, as plain JSON-able dicts. The password hash and login token hashes are
    security credentials, not personal data the person can use, and are left out."""
    versions = []
    for v in await _all(session, select(ProfileVersion).where(ProfileVersion.user_id == user.id).order_by(ProfileVersion.version_number)):
        sections: Dict[str, Any] = {}
        for key, model in SECTION_MODELS.items():
            rows = [_row(r) for r in await _all(session, select(model).where(model.profile_version_id == v.id).order_by(model.created_at, model.id))]
            sections[key] = (rows[0] if rows else None) if key == "personal_info" else rows
        facts = []
        for f in await _all(session, select(Fact).where(Fact.profile_version_id == v.id).order_by(Fact.created_at)):
            facts.append({**_row(f), "evidence": [_row(e) for e in await _all(session, select(Evidence).where(Evidence.fact_id == f.id))]})
        versions.append({**_row(v), "sections": sections, "facts": facts})

    async def owned(model, child=None, fk=None):
        out = []
        for r in await _all(session, select(model).where(model.user_id == user.id).order_by(model.created_at)):
            item = _row(r)
            if child is not None:
                item["items"] = [_row(c) for c in await _all(session, select(child).where(getattr(child, fk) == r.id))]
            out.append(item)
        return out

    return {
        "exported_at": datetime.now(timezone.utc),
        "notice_version": notice.NOTICE_VERSION,
        "account": _row(user, exclude=("hashed_password",)),
        "consents": [_row(c) for c in await _all(session, select(ConsentRecord).where(ConsentRecord.user_id == user.id)
                                                 .order_by(ConsentRecord.granted_at))],
        "profile_versions": versions,
        "eligibility_results": await owned(EligibilityResult, EligibilityEvidence, "result_id"),
        "exam_eligibility_results": await owned(ExamEligibilityResult, ExamEligibilityEvidence, "result_id"),
        "match_scores": await owned(MatchScore),
        "skill_gaps": await owned(SkillGap),
        "alerts": await owned(UserAlert),
        "digests": await owned(DigestRun),
        "applications": await owned(Application, ApplicationStatusHistory, "application_id"),
        "resumes": await owned(Resume, ResumeClaim, "resume_id"),
        "study_plans": await owned(StudyPlan, StudyPlanItem, "plan_id"),
        "login_sessions": [_row(t, exclude=("token_hash",)) for t in await _all(session, select(AuthToken).where(AuthToken.user_id == user.id))],
    }

async def request_erasure(session: AsyncSession, user: User, now: Optional[datetime] = None) -> User:
    """Soft delete: the account stops working now and every consent is withdrawn; data is hard-erased when due."""
    now = now or datetime.now(timezone.utc)
    if user.erased_at is not None:
        raise ErasureError("account already erased")
    if user.erasure_requested_at is None:
        user.erasure_requested_at = now
        user.erasure_due_at = now + timedelta(days=settings.ERASURE_GRACE_DAYS)
    user.is_active = False
    await session.execute(update(AuthToken).where(AuthToken.user_id == user.id, AuthToken.revoked_at.is_(None)).values(revoked_at=now))
    await session.execute(update(ConsentRecord).where(ConsentRecord.user_id == user.id, ConsentRecord.withdrawn_at.is_(None))
                          .values(withdrawn_at=now))
    await session.flush()
    return user

async def cancel_erasure(session: AsyncSession, user: User) -> User:
    """Within the grace period, on the person's request to the grievance officer. Consents stay withdrawn."""
    if user.erased_at is not None:
        raise ErasureError("account already erased; nothing to restore")
    user.erasure_requested_at = user.erasure_due_at = None
    user.is_active = True
    await session.flush()
    return user

def _remove_user_folder(kind: str, user_id: uuid.UUID) -> None:
    root = (settings.storage_dir / kind).resolve()
    folder = (root / str(user_id)).resolve()
    if folder.parent == root and folder.is_dir():
        shutil.rmtree(folder)

async def hard_erase(session: AsyncSession, user: User, now: Optional[datetime] = None) -> User:
    """Delete the person's data; keep the users row as an anonymised stub (see RETAINED)."""
    if user.legal_hold_reason:
        raise ErasureError("account is under a legal hold")
    now = now or datetime.now(timezone.utc)
    uid = user.id
    versions = select(ProfileVersion.id).where(ProfileVersion.user_id == uid)
    # Children first (foreign keys), then the per-user tables in OWNED_BY_USER order
    await session.execute(delete(ResumeClaim).where(ResumeClaim.resume_id.in_(select(Resume.id).where(Resume.user_id == uid))))
    await session.execute(delete(EligibilityEvidence).where(
        EligibilityEvidence.result_id.in_(select(EligibilityResult.id).where(EligibilityResult.user_id == uid))))
    await session.execute(delete(ExamEligibilityEvidence).where(
        ExamEligibilityEvidence.result_id.in_(select(ExamEligibilityResult.id).where(ExamEligibilityResult.user_id == uid))))
    await session.execute(delete(ApplicationStatusHistory).where(
        ApplicationStatusHistory.application_id.in_(select(Application.id).where(Application.user_id == uid))))
    await session.execute(delete(StudyPlanItem).where(StudyPlanItem.plan_id.in_(select(StudyPlan.id).where(StudyPlan.user_id == uid))))
    for model in OWNED_BY_USER:
        if model is ProfileVersion:
            await session.execute(delete(Evidence).where(Evidence.fact_id.in_(select(Fact.id).where(Fact.profile_version_id.in_(versions)))))
            await session.execute(delete(Fact).where(Fact.profile_version_id.in_(versions)))
            for child in VERSIONED_ENTITIES:
                await session.execute(delete(child).where(child.profile_version_id.in_(versions)))
        await session.execute(delete(model).where(model.user_id == uid))
    for kind in ("resumes", "digests"):
        _remove_user_folder(kind, uid)
    user.email = f"erased-{uid}@erased.invalid"
    user.hashed_password = "!"
    user.full_name = None
    user.is_active = False
    user.is_reviewer = False
    user.erased_at = now
    await session.flush()
    return user

async def erase_due(session: AsyncSession, now: Optional[datetime] = None, dry_run: bool = False) -> Dict[str, List[str]]:
    """Hard-erase every account whose grace period has ended. Accounts under a legal hold are skipped and reported."""
    now = now or datetime.now(timezone.utc)
    due = await _all(session, select(User).where(User.erasure_due_at.is_not(None), User.erasure_due_at <= now,
                                                 User.erased_at.is_(None)).order_by(User.erasure_due_at))
    report: Dict[str, List[str]] = {"erased": [], "held": []}
    for user in due:
        if user.legal_hold_reason:
            report["held"].append(str(user.id))
            continue
        if not dry_run:
            await hard_erase(session, user, now)
        report["erased"].append(str(user.id))
    return report
