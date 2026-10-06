"""
Runs hard eligibility for (profile_version_id, vacancy_id) and stores the decision with evidence.

Reads only rows that belong to the given ProfileVersion, which are never modified after
creation, so re-running for an old version reproduces the old decision. (The certificate section also reads
reviewer decisions on the version's certificate Facts and today's date; see certificate_check.)
"""
import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from govcareeros.backend.eligibility import certificate_check, rules
from govcareeros.backend.models.eligibility import EligibilityEvidence, EligibilityResult
from govcareeros.backend.models.user import Certification, Education, Experience, PersonalInfo, ProfileVersion
from govcareeros.backend.models.vacancy import Vacancy, VacancyRequirement
from govcareeros.backend.parsing.upsc_advertisement import IST

# Requirement types whose value column holds JSON
JSON_REQUIREMENTS = {"AGE_MAX", "AGE_RELAXATION", "RESERVATION", "AGE_RELAXATION_RULE", "CITIZENSHIP", "PWBD_SUITABILITY", "GOVT_SERVICE",
                     "CERTIFICATE"}

class EligibilityInputError(ValueError):
    pass

async def load_profile_snapshot(session: AsyncSession, profile_version_id: uuid.UUID) -> rules.ProfileSnapshot:
    version = await session.get(ProfileVersion, profile_version_id)
    if version is None:
        raise EligibilityInputError("profile version not found")

    async def rows(model):
        res = await session.execute(select(model).where(model.profile_version_id == profile_version_id))
        return res.scalars().all()

    info: Optional[PersonalInfo] = (await rows(PersonalInfo) or [None])[0]
    return rules.ProfileSnapshot(
        profile_version_id=profile_version_id,
        date_of_birth=rules.to_date(info.date_of_birth) if info else None,
        category=info.category if info else None,
        is_pwbd=info.is_pwbd if info else None,
        nationality=info.nationality if info else None,
        disability_percentage=info.disability_percentage if info else None,
        disability_types=[t.strip() for t in (info.disability_types or "").split(",") if t.strip()] if info else [],
        citizenship_status=info.citizenship_status if info else None,
        is_ex_serviceman=info.is_ex_serviceman if info else None,
        armed_forces_service_months=info.armed_forces_service_months if info else None,
        is_central_govt_employee=info.is_central_govt_employee if info else None,
        central_govt_service_start=rules.to_date(info.central_govt_service_start) if info else None,
        is_state_govt_employee=info.is_state_govt_employee if info else None,
        current_pay_level=info.current_pay_level if info else None,
        current_level_regular_since=rules.to_date(info.current_level_regular_since) if info else None,
        organisation_cadre=info.organisation_cadre if info else None,
        last_deputation_end=rules.to_date(info.last_deputation_end) if info else None,
        education=[rules.EducationEntry(e.degree, e.specialization, e.education_level, rules.to_date(e.end_date), e.equivalence)
                   for e in await rows(Education)],
        experience=[rules.ExperienceEntry(e.organization, e.designation, e.domain, e.responsibilities,
                                          rules.to_date(e.start_date), rules.to_date(e.end_date))
                    for e in await rows(Experience)],
        certifications=[c.name for c in await rows(Certification)],
    )

def requirement_value(r) -> object:
    """A requirement row's value: parsed JSON for structured types; a certificate requirement also carries its quote."""
    try:
        value = json.loads(r.value) if r.req_type in JSON_REQUIREMENTS else r.value
    except (json.JSONDecodeError, TypeError):
        return r.value
    if r.req_type == certificate_check.REQ_TYPE and isinstance(value, dict) and r.source_excerpt:
        value.setdefault("source_quote", r.source_excerpt)
    return value

async def load_requirements(session: AsyncSession, vacancy_id: uuid.UUID) -> List[rules.Requirement]:
    res = await session.execute(select(VacancyRequirement).where(
        VacancyRequirement.vacancy_id == vacancy_id, VacancyRequirement.superseded_at.is_(None)))
    return [rules.Requirement(r.id, r.req_type, requirement_value(r), r.source_page) for r in res.scalars()]

def input_fingerprint(vacancy: Vacancy, reqs: List[rules.Requirement]) -> str:
    """Identifies the vacancy-side inputs of a decision; requirement rows are never edited in place, only superseded."""
    crucial_dt = vacancy.eligibility_crucial_date or vacancy.application_deadline
    parts = [rules.ENGINE_VERSION, crucial_dt.astimezone(timezone.utc).isoformat() if crucial_dt else "-",
             vacancy.recruitment_mode or "UNKNOWN", *sorted(str(r.id) for r in reqs)]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()

def hard_outcomes(snapshot: rules.ProfileSnapshot, reqs: List[rules.Requirement], vacancy: Vacancy) -> List[rules.RuleOutcome]:
    """The hard checks of one vacancy (no database, nothing stored); evaluate() stores them, other views only read them."""
    # Indian notifications state dates in IST; the calendar date there is what counts
    crucial_dt = vacancy.eligibility_crucial_date or vacancy.application_deadline
    crucial = crucial_dt.astimezone(IST).date() if crucial_dt else None
    outcomes = rules.evaluate(snapshot, reqs, crucial, vacancy.recruitment_mode)
    if vacancy.eligibility_crucial_date is None and vacancy.application_deadline is not None:
        outcomes.append(rules.RuleOutcome("CRUCIAL_DATE", "NOT_EVALUATED",
                                          "the notice text used does not state a crucial date; the closing date was used for age and experience",
                                          "-", f"closing date {crucial.isoformat()}"))
    return outcomes

async def evaluate(session: AsyncSession, user_id: uuid.UUID, profile_version_id: uuid.UUID, vacancy_id: uuid.UUID) -> EligibilityResult:
    version = await session.get(ProfileVersion, profile_version_id)
    if version is None or version.user_id != user_id:
        raise EligibilityInputError("profile version not found")
    vacancy = await session.get(Vacancy, vacancy_id)
    if vacancy is None:
        raise EligibilityInputError("vacancy not found")

    snapshot = await load_profile_snapshot(session, profile_version_id)
    reqs = await load_requirements(session, vacancy_id)
    crucial_dt = vacancy.eligibility_crucial_date or vacancy.application_deadline
    outcomes = hard_outcomes(snapshot, reqs, vacancy)
    status, confidence = rules.overall_status(outcomes)
    # Separate section, after the verdict so it cannot change status/confidence (see certificate_check)
    closing = vacancy.application_deadline.astimezone(IST).date() if vacancy.application_deadline else None
    outcomes += certificate_check.check_certificates(snapshot, reqs, await certificate_check.load_held(session, profile_version_id),
                                                     closing, datetime.now(IST).date())

    result = EligibilityResult(
        id=uuid.uuid4(), user_id=user_id, vacancy_id=vacancy_id, profile_version_id=profile_version_id,
        engine_version=rules.ENGINE_VERSION, crucial_date=crucial_dt, status=status, confidence=confidence,
        input_fingerprint=input_fingerprint(vacancy, reqs),
    )
    session.add(result)
    for o in outcomes:
        session.add(EligibilityEvidence(
            result_id=result.id, requirement_id=o.requirement_id, check_name=o.check, match_type=o.match_type,
            requirement=o.requirement, profile_evidence=o.profile_evidence,
            rule=o.rule + (f" [note: {o.note}]" if o.note else ""), outcome=o.outcome,
            source_page=str(o.source_page) if o.source_page else None,
        ))
    await session.flush()
    return await get_result(session, user_id, result.id)

async def get_result(session: AsyncSession, user_id: uuid.UUID, result_id: uuid.UUID) -> Optional[EligibilityResult]:
    res = await session.execute(
        select(EligibilityResult).options(selectinload(EligibilityResult.evidence))
        .where(EligibilityResult.id == result_id, EligibilityResult.user_id == user_id)
        .execution_options(populate_existing=True)
    )
    return res.scalar_one_or_none()
