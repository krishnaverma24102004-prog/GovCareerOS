"""
Cyber-cafe / CSC operator console: an operator creates client accounts, records the client's consent with the client
present (backend/services/consent.py, method OPERATOR_ASSISTED), edits the client's profile through the ordinary
profile service, and makes per-application reports that are charged in TEST MODE (backend/services/billing.py).

Rules kept here so every route goes through them:
- an operator reaches only clients whose managed_by_id is the operator (anything else is "not found");
- nothing about a client is stored or processed without an active OPERATOR_ASSISTED_APPLICATION consent, and only
  for the data items that consent names; the client row is created bare, the consent recorded, then the name stored;
- a client claims the account with a one-time token (only its SHA-256 is stored) and then logs in like anyone else.
"""
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.api.security import hash_password, new_token, token_hash
from govcareeros.backend.config.settings import settings
from govcareeros.backend.models.application import Application, ApplicationStatusHistory
from govcareeros.backend.models.consent import ConsentRecord
from govcareeros.backend.models.exam import Exam
from govcareeros.backend.models.operator import ClaimToken, LedgerEntry, OperatorReport
from govcareeros.backend.models.user import ProfileVersion, User
from govcareeros.backend.models.vacancy import Vacancy
from govcareeros.backend.schemas.attribution import DISCLAIMER
from govcareeros.backend.services import billing, consent

PURPOSE = "OPERATOR_ASSISTED_APPLICATION"
NOTICE_VERSION = "operator-assisted-2026-10"
# What a client can agree to; profile sections map onto these (SECTION_ITEMS)
DATA_ITEMS = ("full_name", "personal_info", "education", "experience", "certificates", "career_details")
SECTION_ITEMS = {
    "personal_info": "personal_info", "education": "education", "experience": "experience",
    "reservation_certificates": "certificates", "projects": "career_details", "skills": "career_details",
    "certifications": "career_details", "languages": "career_details", "achievements": "career_details",
    "documents": "career_details",
}
# Read out to the client by the operator before consent is recorded (English, then Hinglish)
NOTICE_TEXT = (
    "The operator will enter your details on GovCareerOS only to check which government vacancies and exams you can "
    "apply for and to prepare your application checklist. Only the items you agree to are stored. You can withdraw this "
    "consent at any time and later take over this account with your own email and password. "
    "Operator aapki details sirf yeh dekhne ke liye bharega ki aap kin sarkari bharti/pariksha ke liye apply kar sakte "
    "hain aur aapki application checklist banane ke liye. Sirf wahi details save hongi jinke liye aap haan kahenge. "
    "Aap kabhi bhi consent wapas le sakte hain. Official notification hi antim maana jayega."
)

class OperatorError(Exception):
    """status: 403 (not allowed / no consent), 404 (not found), 409 (conflict), 422 (bad input)."""
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status

def consent_notice() -> Dict[str, Any]:
    return {"purpose": PURPOSE, "notice_version": NOTICE_VERSION, "data_items": list(DATA_ITEMS), "text": NOTICE_TEXT}

def _check_operator(operator: User) -> None:
    if not operator.is_operator or operator.managed_by_id is not None:
        raise OperatorError(403, "operator role required")

def _check_consent_input(client_present: bool, notice_version: str, data_items: Iterable[str]) -> List[str]:
    if not client_present:
        raise OperatorError(422, "consent can be recorded only with the client present")
    if notice_version != NOTICE_VERSION:
        raise OperatorError(422, f"consent notice {notice_version!r} is not the current one ({NOTICE_VERSION})")
    items = sorted(set(data_items))
    unknown = [i for i in items if i not in DATA_ITEMS]
    if unknown or not items:
        raise OperatorError(422, f"data_items must be a non-empty subset of {list(DATA_ITEMS)}")
    return items

async def create_client(session: AsyncSession, operator: User, full_name: Optional[str], *, client_present: bool,
                        notice_version: str, data_items: Iterable[str], guardian_name: Optional[str] = None,
                        now: Optional[datetime] = None) -> Tuple[User, ConsentRecord]:
    """A bare client row (no name, no email, no password) first, then the consent, then the name."""
    _check_operator(operator)
    items = _check_consent_input(client_present, notice_version, data_items)
    name = (full_name or "").strip() or None
    if name and "full_name" not in items:
        raise OperatorError(403, "the client did not consent to storing their name")
    client = User(id=uuid.uuid4(), email=None, hashed_password="", full_name=None, managed_by_id=operator.id)
    session.add(client)
    await session.flush()
    record = await consent.record_consent(session, client.id, PURPOSE, items, NOTICE_VERSION, method="OPERATOR_ASSISTED",
                                          captured_by_id=operator.id, guardian_name=(guardian_name or "").strip() or None,
                                          now=now)
    if name:
        client.full_name = name
        await session.flush()
    return client, record

async def regrant_consent(session: AsyncSession, operator: User, client: User, *, client_present: bool,
                          notice_version: str, data_items: Iterable[str], guardian_name: Optional[str] = None) -> ConsentRecord:
    """A new consent (e.g. after a withdrawal, or for more data items); the earlier active one is withdrawn first."""
    items = _check_consent_input(client_present, notice_version, data_items)
    await consent.withdraw(session, client.id, PURPOSE)
    return await consent.record_consent(session, client.id, PURPOSE, items, NOTICE_VERSION, method="OPERATOR_ASSISTED",
                                        captured_by_id=operator.id, guardian_name=(guardian_name or "").strip() or None)

async def withdraw_consent(session: AsyncSession, client: User) -> int:
    """Withdrawal stops all further processing; the name the operator stored is erased (DPDP s.6(6))."""
    n = await consent.withdraw(session, client.id, PURPOSE)
    if client.claimed_at is None:
        client.full_name = None
    await session.flush()
    return n

async def own_client(session: AsyncSession, operator: User, client_id: uuid.UUID) -> User:
    _check_operator(operator)
    client = (await session.execute(select(User).where(User.id == client_id, User.managed_by_id == operator.id)
                                    .execution_options(populate_existing=True))).scalar_one_or_none()
    if client is None:
        raise OperatorError(404, "client not found")
    return client

async def list_clients(session: AsyncSession, operator: User) -> List[User]:
    _check_operator(operator)
    return list((await session.execute(select(User).where(User.managed_by_id == operator.id)
                                       .order_by(User.created_at.desc(), User.id))).scalars().all())

async def require_consent(session: AsyncSession, client: User, items: Iterable[str] = ()) -> ConsentRecord:
    record = await consent.active_consent(session, client.id, PURPOSE)
    if record is None:
        raise OperatorError(403, "no active consent from this client; record consent with the client present first")
    missing = sorted(set(items) - set(json.loads(record.data_items)))
    if missing:
        raise OperatorError(403, f"the client has not consented to: {', '.join(missing)}")
    return record

async def may_process(session: AsyncSession, user: User) -> bool:
    """Batch jobs (radar, digest) may use this user's profile: an ordinary or claimed account always; an unclaimed
    operator-managed client only while their OPERATOR_ASSISTED_APPLICATION consent is active."""
    if user.managed_by_id is None or user.claimed_at is not None:
        return True
    return await consent.active_consent(session, user.id, PURPOSE) is not None

def profile_items(sections: Iterable[str]) -> List[str]:
    return sorted({SECTION_ITEMS[s] for s in sections if s in SECTION_ITEMS})

# --- account claim ---------------------------------------------------------------------------------------------

async def issue_claim_token(session: AsyncSession, operator: User, client: User, now: Optional[datetime] = None) -> Tuple[str, datetime]:
    """The raw token is returned once (to print or read out to the client); earlier unused tokens stop working."""
    if client.claimed_at is not None:
        raise OperatorError(409, "this client has already claimed the account")
    now = now or datetime.now(timezone.utc)
    await session.execute(update(ClaimToken).where(ClaimToken.user_id == client.id, ClaimToken.used_at.is_(None),
                                                   ClaimToken.revoked_at.is_(None)).values(revoked_at=now))
    token, digest, _ = new_token()
    expires = now + timedelta(hours=settings.CLAIM_TOKEN_TTL_HOURS)
    session.add(ClaimToken(user_id=client.id, created_by_id=operator.id, token_hash=digest, expires_at=expires))
    await session.flush()
    return token, expires

async def claim_account(session: AsyncSession, token: str, email: str, password: str, now: Optional[datetime] = None) -> User:
    now = now or datetime.now(timezone.utc)
    row = (await session.execute(select(ClaimToken).where(ClaimToken.token_hash == token_hash(token)))).scalar_one_or_none()
    if row is None or row.used_at is not None or row.revoked_at is not None or row.expires_at <= now:
        raise OperatorError(403, "invalid or expired claim token")
    client = await session.get(User, row.user_id)
    if client is None or client.claimed_at is not None or not client.is_active:
        raise OperatorError(403, "invalid or expired claim token")
    if (await session.execute(select(User.id).where(User.email == email))).scalar_one_or_none():
        raise OperatorError(409, "email already registered")
    client.email = email
    client.hashed_password = hash_password(password)
    client.claimed_at = now
    row.used_at = now
    await session.flush()
    return client

# --- per-application reports -----------------------------------------------------------------------------------

def apply_ready_links(kind: str, target_id: uuid.UUID, profile_version_id: Optional[uuid.UUID]) -> Dict[str, Optional[str]]:
    """URL shapes of the apply-ready contract (GET /apply-ready/{vacancies|exams}/{id}[.html]). The personalised link
    returns the client's own statuses only with the client's bearer token, i.e. after they claim the account."""
    base = f"/apply-ready/{'vacancies' if kind == 'vacancy' else 'exams'}/{target_id}"
    return {"json": base, "html": f"{base}.html",
            "personalised": f"{base}?profile_version_id={profile_version_id}" if profile_version_id else None}

async def _target(session: AsyncSession, kind: str, target_id: uuid.UUID) -> Dict[str, Any]:
    if kind == "vacancy":
        v = await session.get(Vacancy, target_id)
        if v is None:
            raise OperatorError(404, "vacancy not found")
        return {"title": v.post_name, "organization": v.organization, "advertisement_number": v.advertisement_number,
                "deadline": v.application_deadline.isoformat() if v.application_deadline else None,
                "document_url": v.notification_url}
    if kind == "exam":
        e = await session.get(Exam, target_id)
        if e is None:
            raise OperatorError(404, "exam not found")
        return {"title": e.name, "organization": e.conducting_body, "advertisement_number": None,
                "deadline": e.application_end.isoformat() if e.application_end else None, "document_url": e.notification_url}
    raise OperatorError(422, "kind must be vacancy or exam")

async def _application(session: AsyncSession, client: User, kind: str, target_id: uuid.UUID, title: str,
                       profile_version_id: Optional[uuid.UUID], operator: User) -> Application:
    """The client's tracker row for the target, created (PLANNED) when the client has none."""
    col = Application.vacancy_id if kind == "vacancy" else Application.exam_id
    app = (await session.execute(select(Application).where(Application.user_id == client.id, col == target_id))).scalar_one_or_none()
    if app is not None:
        return app
    app = Application(id=uuid.uuid4(), user_id=client.id, status="PLANNED", profile_version_id=profile_version_id,
                      vacancy_id=target_id if kind == "vacancy" else None, exam_id=target_id if kind == "exam" else None,
                      exam_ref=title[:255] if kind == "exam" else None)
    session.add(app)
    session.add(ApplicationStatusHistory(application_id=app.id, from_status=None, to_status="PLANNED",
                                         changed_at=datetime.now(timezone.utc), note=f"created by operator {operator.id}"))
    await session.flush()
    return app

async def create_report(session: AsyncSession, operator: User, client: User, kind: str, target_id: uuid.UUID,
                        idempotency_key: str, profile_version_id: Optional[uuid.UUID] = None,
                        provider: Optional[billing.PaymentProvider] = None) -> Tuple[OperatorReport, LedgerEntry, bool]:
    """(report, its single TEST-MODE charge, created). The same idempotency key returns the first report unchanged."""
    _check_operator(operator)
    key = idempotency_key.strip()
    if not key:
        raise OperatorError(422, "idempotency_key is required")
    existing = (await session.execute(select(OperatorReport).where(
        OperatorReport.operator_id == operator.id, OperatorReport.idempotency_key == key))).scalar_one_or_none()
    if existing is not None:
        if existing.client_id != client.id or (existing.vacancy_id or existing.exam_id) != target_id:
            raise OperatorError(409, "idempotency_key already used for a different report")
        entry, _ = await billing.charge_report(session, existing, provider)
        return existing, entry, False
    record = await require_consent(session, client)
    if profile_version_id is not None:
        if (await session.execute(select(ProfileVersion.id).where(
                ProfileVersion.id == profile_version_id, ProfileVersion.user_id == client.id))).scalar_one_or_none() is None:
            raise OperatorError(404, "profile version not found")
    else:
        profile_version_id = (await session.execute(select(ProfileVersion.id).where(ProfileVersion.user_id == client.id)
                                                    .order_by(ProfileVersion.version_number.desc()).limit(1))).scalar_one_or_none()
    target = await _target(session, kind, target_id)
    app = await _application(session, client, kind, target_id, target["title"], profile_version_id, operator)
    snapshot = {"kind": kind, "id": str(target_id), **target, "profile_version_id": str(profile_version_id) if profile_version_id else None,
                "links": apply_ready_links(kind, target_id, profile_version_id), "disclaimer": DISCLAIMER}
    report = OperatorReport(id=uuid.uuid4(), operator_id=operator.id, client_id=client.id, application_id=app.id, kind=kind,
                            vacancy_id=target_id if kind == "vacancy" else None, exam_id=target_id if kind == "exam" else None,
                            profile_version_id=profile_version_id, consent_id=record.id, idempotency_key=key,
                            snapshot=json.dumps(snapshot, ensure_ascii=False))
    try:
        async with session.begin_nested():
            session.add(report)
    except IntegrityError:
        # The same key raced in from a second click; return that report
        existing = (await session.execute(select(OperatorReport).where(
            OperatorReport.operator_id == operator.id, OperatorReport.idempotency_key == key))).scalar_one()
        entry, _ = await billing.charge_report(session, existing, provider)
        return existing, entry, False
    entry, _ = await billing.charge_report(session, report, provider)
    return report, entry, True

async def own_report(session: AsyncSession, operator: User, report_id: uuid.UUID) -> OperatorReport:
    _check_operator(operator)
    report = (await session.execute(select(OperatorReport).where(
        OperatorReport.id == report_id, OperatorReport.operator_id == operator.id))).scalar_one_or_none()
    if report is None:
        raise OperatorError(404, "report not found")
    return report

async def client_reports(session: AsyncSession, operator: User, client: User) -> List[Tuple[OperatorReport, Optional[LedgerEntry]]]:
    rows = (await session.execute(
        select(OperatorReport, LedgerEntry).outerjoin(LedgerEntry, (LedgerEntry.report_id == OperatorReport.id) & (LedgerEntry.entry_type == "CHARGE"))
        .where(OperatorReport.operator_id == operator.id, OperatorReport.client_id == client.id)
        .order_by(OperatorReport.created_at.desc(), OperatorReport.id))).all()
    return [(r, e) for r, e in rows]
