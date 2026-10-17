"""
Consent enforcement (DPDP Act 2023 s.6, s.9). Personal data that eligibility needs (category/caste, date of birth,
gender, citizenship/nationality, disability, certificates, ex-serviceman and service details) is stored and processed only under an active ELIGIBILITY_CHECK consent;
for a person under 18 that consent must carry the name of the parent / lawful guardian who gave it.

Withdrawal stops processing: profile saves that would store those items and every eligibility run refuse with a
ConsentRequired (HTTP 403 via the handler in api/main.py) that says what to do. Nothing already stored is deleted.
A consent given under an older NOTICE_VERSION is not active either (CONSENT_OUTDATED): the person consents again.
"""
import json
import uuid
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.models.user import PersonalInfo
from govcareeros.backend.privacy import notice
from govcareeros.backend.services.consent import active_consent

ADULT_AGE = 18

# Profile column -> notice data item it belongs to
SENSITIVE_PERSONAL_FIELDS: Dict[str, str] = {
    "date_of_birth": "date_of_birth",
    "gender": "gender",
    "citizenship_status": "citizenship_status", "nationality": "citizenship_status",
    "category": "category",
    "is_pwbd": "disability", "disability_percentage": "disability", "disability_types": "disability",
    "is_ex_serviceman": "ex_serviceman", "armed_forces_service_months": "ex_serviceman",
    "is_central_govt_employee": "service_details", "central_govt_service_start": "service_details",
    "is_state_govt_employee": "service_details", "current_pay_level": "service_details",
    "current_level_regular_since": "service_details", "organisation_cadre": "service_details",
    "last_deputation_end": "service_details",
}

# A profile claim (Fact) about one of these predicates, or any claim under the certificates subject, states an
# ELIGIBILITY_CHECK item; the predicate is also matched on the notice item keys and "caste" (free-form facts)
_CERTIFICATE_SUBJECT = "reservation_certificates"
_FACT_PREDICATE_ITEMS: Dict[str, str] = {**SENSITIVE_PERSONAL_FIELDS, "caste": "category", "disability": "disability",
                                         "ex_serviceman": "ex_serviceman", "service_details": "service_details",
                                         "certificate": "certificates", "certificates": "certificates"}

def fact_items(subject: str, predicate: str) -> List[str]:
    """The ELIGIBILITY_CHECK data items a claim with this subject/predicate states ([] for e.g. education/degree)."""
    items = {_FACT_PREDICATE_ITEMS[p] for p in ((predicate or "").strip().lower(),) if p in _FACT_PREDICATE_ITEMS}
    if (subject or "").strip().lower() == _CERTIFICATE_SUBJECT:
        items.add("certificates")
    return sorted(items)

class ConsentRequired(Exception):
    """Processing refused for want of consent. `detail` is what the API returns with the 403."""

    def __init__(self, code: str, purpose: str, message: str, action: str, items: Iterable[str] = ()):
        super().__init__(message)
        self.detail = {"code": code, "purpose": purpose, "message": message, "action": action,
                       "data_items": sorted(set(items)), "notice_version": notice.NOTICE_VERSION}

def age_on(dob: Any, today: date) -> int:
    dob = dob.date() if isinstance(dob, datetime) else dob
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

def is_minor(dob: Any, today: Optional[date] = None) -> bool:
    return dob is not None and age_on(dob, today or date.today()) < ADULT_AGE

def sensitive_items(personal: Optional[Dict[str, Any]], has_certificates: bool) -> List[str]:
    """The notice data items that a profile with these personal_info values / certificate rows holds."""
    items = {SENSITIVE_PERSONAL_FIELDS[k] for k, v in (personal or {}).items() if k in SENSITIVE_PERSONAL_FIELDS and v is not None}
    if has_certificates:
        items.add("certificates")
    return sorted(items)

def _granted_items(consent) -> set:
    try:
        return set(json.loads(consent.data_items or "[]"))
    except (TypeError, ValueError):
        return set()

async def require_consent(session: AsyncSession, user_id: uuid.UUID, purpose: str = notice.ELIGIBILITY_CHECK,
                          date_of_birth: Any = None, items: Iterable[str] = (), today: Optional[date] = None) -> None:
    items = list(items)
    consent = await active_consent(session, user_id, purpose)
    if consent is None:
        raise ConsentRequired(
            "CONSENT_REQUIRED", purpose,
            f"Consent for {purpose} is not active, so this data cannot be stored or used for that purpose.",
            f"Read the notice (GET /privacy/notice) and give consent for {purpose} (POST /privacy/consents), "
            "or leave these fields empty.", items)
    # A consent covers the notice version it was given under and the data items listed in it; a changed notice (new
    # version, or items it did not list) needs consent again (notice.py: never silently carried over)
    if consent.notice_version != notice.NOTICE_VERSION or not set(items) <= _granted_items(consent):
        raise ConsentRequired(
            "CONSENT_OUTDATED", purpose,
            f"The consent notice has changed since you gave consent for {purpose} (version {consent.notice_version}; "
            f"now {notice.NOTICE_VERSION}), so this data cannot be stored or used for that purpose until you consent again.",
            f"Read the notice again (GET /privacy/notice) and give consent for {purpose} (POST /privacy/consents).", items)
    if is_minor(date_of_birth, today) and not (consent.guardian_name or "").strip():
        raise ConsentRequired(
            "GUARDIAN_CONSENT_REQUIRED", purpose,
            "The date of birth shows you are under 18: a parent or lawful guardian must give this consent.",
            f"Ask your parent or guardian to give consent for {purpose} with their name (guardian_name in POST /privacy/consents).",
            items or ["date_of_birth"])

async def has_consent(session: AsyncSession, user_id: uuid.UUID, purpose: str = notice.ELIGIBILITY_CHECK,
                      date_of_birth: Any = None) -> bool:
    try:
        await require_consent(session, user_id, purpose, date_of_birth)
    except ConsentRequired:
        return False
    return True

async def version_date_of_birth(session: AsyncSession, profile_version_id: uuid.UUID) -> Optional[datetime]:
    return (await session.execute(select(PersonalInfo.date_of_birth).where(
        PersonalInfo.profile_version_id == profile_version_id))).scalar_one_or_none()

async def require_for_version(session: AsyncSession, user_id: uuid.UUID, profile_version_id: uuid.UUID) -> None:
    """Eligibility processing of one profile version: active consent, and guardian consent if that version is a minor's."""
    await require_consent(session, user_id, notice.ELIGIBILITY_CHECK, await version_date_of_birth(session, profile_version_id))

async def allowed_for_version(session: AsyncSession, user_id: uuid.UUID, profile_version_id: uuid.UUID) -> bool:
    try:
        await require_for_version(session, user_id, profile_version_id)
    except ConsentRequired:
        return False
    return True
