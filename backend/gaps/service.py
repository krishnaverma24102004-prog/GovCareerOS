"""
Gaps for (profile_version_id, vacancy_id), stored against the eligibility result they explain.

  - The eligibility result used is the latest stored one for that profile version and vacancy whose
    input fingerprint still matches the vacancy; otherwise eligibility is evaluated afresh.
  - Stored evidence rows keep text only, so the rule outcomes are re-derived from the same immutable
    inputs and checked against the stored rows (check, outcome, requirement). A mismatch means the
    rules changed without a result being re-run: the decision is re-evaluated rather than explained wrongly.
  - Gaps for a result are computed once and then returned as stored (keyed by result + engine version).
  - Quotes come from the requirement row: its text for text requirements, the page excerpt for
    structured ones (age limits, citizenship), so every gap cites the notification's own words.
"""
import json
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from govcareeros.backend.eligibility import engine as eligibility_engine, rules
from govcareeros.backend.gaps.engine import ENGINE_VERSION, compute_gaps
from govcareeros.backend.models.eligibility import EligibilityResult, MatchScore, SkillGap
from govcareeros.backend.models.user import ProfileVersion, Skill
from govcareeros.backend.models.vacancy import Vacancy, VacancyRequirement
from govcareeros.backend.parsing.upsc_advertisement import IST
from govcareeros.backend.relevance import scoring, service as relevance_service

def _signature(rows) -> List[Tuple[str, str, str]]:
    # Certificate rows are a separate section (eligibility.certificate_check) that gaps do not explain
    return sorted((r[0], r[1], str(r[2]) if r[2] else "") for r in rows if r[0] not in ("CRUCIAL_DATE", "CERTIFICATE"))

async def _current_result(session: AsyncSession, user_id, profile_version_id, vacancy: Vacancy,
                          reqs: List[rules.Requirement]) -> Optional[EligibilityResult]:
    fingerprint = eligibility_engine.input_fingerprint(vacancy, reqs)
    return (await session.execute(
        select(EligibilityResult).options(selectinload(EligibilityResult.evidence)).where(
            EligibilityResult.user_id == user_id, EligibilityResult.profile_version_id == profile_version_id,
            EligibilityResult.vacancy_id == vacancy.id, EligibilityResult.input_fingerprint == fingerprint,
            EligibilityResult.engine_version == rules.ENGINE_VERSION,
        ).order_by(EligibilityResult.created_at.desc()).limit(1)
    )).scalar_one_or_none()

async def _relevance_components(session: AsyncSession, user_id, profile_version_id, vacancy_id) -> List[dict]:
    match = (await session.execute(
        select(MatchScore).where(MatchScore.user_id == user_id, MatchScore.profile_version_id == profile_version_id,
                                 MatchScore.vacancy_id == vacancy_id, MatchScore.engine_version == scoring.ENGINE_VERSION)
        .order_by(MatchScore.created_at.desc()).limit(1)
    )).scalar_one_or_none()
    if match is None:
        match = await relevance_service.evaluate(session, user_id, profile_version_id, vacancy_id)
    return json.loads(match.explanation or "{}").get("components", [])

async def gaps_for(session: AsyncSession, user_id: uuid.UUID, profile_version_id: uuid.UUID,
                   vacancy_id: uuid.UUID) -> Tuple[EligibilityResult, List[SkillGap]]:
    version = await session.get(ProfileVersion, profile_version_id)
    if version is None or version.user_id != user_id:
        raise eligibility_engine.EligibilityInputError("profile version not found")
    vacancy = await session.get(Vacancy, vacancy_id)
    if vacancy is None:
        raise eligibility_engine.EligibilityInputError("vacancy not found")

    reqs = await eligibility_engine.load_requirements(session, vacancy_id)
    snapshot = await eligibility_engine.load_profile_snapshot(session, profile_version_id)
    crucial_dt = vacancy.eligibility_crucial_date or vacancy.application_deadline
    crucial = crucial_dt.astimezone(IST).date() if crucial_dt else None
    outcomes = rules.evaluate(snapshot, reqs, crucial)

    result = await _current_result(session, user_id, profile_version_id, vacancy, reqs)
    expected = _signature((o.check, o.outcome, o.requirement_id) for o in outcomes)
    if result is None or _signature((e.check_name, e.outcome, e.requirement_id) for e in result.evidence) != expected:
        result = await eligibility_engine.evaluate(session, user_id, profile_version_id, vacancy_id)

    stored = (await session.execute(select(SkillGap).where(
        SkillGap.eligibility_result_id == result.id, SkillGap.engine_version == ENGINE_VERSION))).scalars().all()
    if stored:
        return result, _ordered(stored)

    components = await _relevance_components(session, user_id, profile_version_id, vacancy_id)
    skills = (await session.execute(select(Skill.skill_name).where(Skill.profile_version_id == profile_version_id))).scalars().all()
    vac_text = scoring.VacancyText(
        post_name=vacancy.post_name, department=vacancy.department, ministry=vacancy.ministry, headquarters=vacancy.headquarters,
        duties=" ".join(str(r.value) for r in reqs if r.req_type == "DUTIES"),
        desirable=" ".join(str(r.value) for r in reqs if r.req_type == "DESIRABLE"),
    )
    gaps = compute_gaps(snapshot, reqs, crucial, outcomes, components, vac_text, skills)

    ids = {g.requirement_id for g in gaps if g.requirement_id}
    rows: Dict[uuid.UUID, VacancyRequirement] = {r.id: r for r in (await session.execute(
        select(VacancyRequirement).where(VacancyRequirement.id.in_(ids)))).scalars()} if ids else {}
    out = []
    for g in gaps:
        row = rows.get(g.requirement_id)
        quote = None
        if row is not None:
            quote = row.source_excerpt if row.req_type in eligibility_engine.JSON_REQUIREMENTS else row.value
        gap = SkillGap(
            id=uuid.uuid4(), user_id=user_id, vacancy_id=vacancy_id, profile_version_id=profile_version_id,
            eligibility_result_id=result.id, requirement_id=g.requirement_id, engine_version=ENGINE_VERSION,
            gap_type=g.gap_type, reason_code=g.reason_code, blocking=g.blocking, requirement=g.requirement,
            requirement_quote=quote, requirement_page=row.source_page if row is not None else g.requirement_page,
            profile_has=g.profile_has, profile_field=g.profile_field, next_step=g.next_step, due_date=g.due_date,
        )
        session.add(gap)
        out.append(gap)
    await session.flush()
    return result, out

def _ordered(gaps: List[SkillGap]) -> List[SkillGap]:
    return sorted(gaps, key=lambda g: (not g.blocking, g.gap_type, g.created_at or datetime.min.replace(tzinfo=timezone.utc)))

async def summary(session: AsyncSession, user_id: uuid.UUID, profile_version_id: uuid.UUID, include_closed: bool = False) -> dict:
    """Blocking gaps across all active vacancies, grouped by (type, reason, profile field), most common first."""
    version = await session.get(ProfileVersion, profile_version_id)
    if version is None or version.user_id != user_id:
        raise eligibility_engine.EligibilityInputError("profile version not found")
    now = datetime.now(timezone.utc)
    stmt = select(Vacancy).where(Vacancy.status == "ACTIVE").order_by(Vacancy.id)
    if not include_closed:
        stmt = stmt.where((Vacancy.application_deadline.is_(None)) | (Vacancy.application_deadline > now))
    groups: Dict[tuple, dict] = defaultdict(lambda: {"vacancy_ids": [], "posts": [], "earliest_due_date": None, "example_next_step": None})
    vacancies = (await session.execute(stmt)).scalars().all()
    clear = 0
    for vacancy in vacancies:
        _, gaps = await gaps_for(session, user_id, profile_version_id, vacancy.id)
        blockers = [g for g in gaps if g.blocking]
        clear += not blockers
        for g in blockers:
            grp = groups[(g.gap_type, g.reason_code, g.profile_field)]
            if vacancy.id not in grp["vacancy_ids"]:
                grp["vacancy_ids"].append(vacancy.id)
                grp["posts"].append(vacancy.post_name)
            grp["example_next_step"] = grp["example_next_step"] or g.next_step
            if g.due_date and (grp["earliest_due_date"] is None or g.due_date < grp["earliest_due_date"]):
                grp["earliest_due_date"] = g.due_date
    blockers = [{"gap_type": t, "reason_code": r, "profile_field": f, "vacancies": len(v["vacancy_ids"]), **v}
                for (t, r, f), v in groups.items()]
    blockers.sort(key=lambda b: (-b["vacancies"], b["gap_type"], b["reason_code"] or "", b["profile_field"] or ""))
    return {"profile_version_id": profile_version_id, "vacancies_evaluated": len(vacancies),
            "vacancies_without_blockers": clear, "blockers": blockers}
