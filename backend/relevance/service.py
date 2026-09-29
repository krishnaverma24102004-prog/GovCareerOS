"""Loads a profile version + vacancy from the database, scores relevance, stores the breakdown."""
import json
import uuid
from dataclasses import asdict
from typing import Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.eligibility import engine as eligibility_engine
from govcareeros.backend.models.eligibility import MatchScore
from govcareeros.backend.models.user import PersonalInfo, ProfileVersion, Project, Skill
from govcareeros.backend.models.vacancy import Vacancy
from govcareeros.backend.parsing.upsc_advertisement import IST
from govcareeros.backend.relevance.scoring import ENGINE_VERSION, ProfileExtras, VacancyText, score_relevance

async def evaluate(session: AsyncSession, user_id: uuid.UUID, profile_version_id: uuid.UUID, vacancy_id: uuid.UUID,
                   weights: Optional[Dict[str, float]] = None) -> MatchScore:
    version = await session.get(ProfileVersion, profile_version_id)
    if version is None or version.user_id != user_id:
        raise eligibility_engine.EligibilityInputError("profile version not found")
    vacancy = await session.get(Vacancy, vacancy_id)
    if vacancy is None:
        raise eligibility_engine.EligibilityInputError("vacancy not found")

    snapshot = await eligibility_engine.load_profile_snapshot(session, profile_version_id)
    reqs = await eligibility_engine.load_requirements(session, vacancy_id)

    async def rows(model):
        return (await session.execute(select(model).where(model.profile_version_id == profile_version_id))).scalars().all()

    info = (await rows(PersonalInfo) or [None])[0]
    extras = ProfileExtras(
        skills=[s.skill_name for s in await rows(Skill)],
        project_domains=[f"{p.domain or ''} {p.technologies or ''}" for p in await rows(Project)],
        preferred_locations=info.preferred_locations if info else None,
        career_interests=info.career_interests if info else None,
    )
    vac_text = VacancyText(
        post_name=vacancy.post_name, department=vacancy.department, ministry=vacancy.ministry,
        headquarters=vacancy.headquarters,
        duties=" ".join(str(r.value) for r in reqs if r.req_type == "DUTIES"),
        desirable=" ".join(str(r.value) for r in reqs if r.req_type == "DESIRABLE"),
    )
    crucial_dt = vacancy.eligibility_crucial_date or vacancy.application_deadline
    crucial = crucial_dt.astimezone(IST).date() if crucial_dt else None

    result = score_relevance(snapshot, extras, reqs, vac_text, crucial, weights)
    match = MatchScore(
        id=uuid.uuid4(), user_id=user_id, vacancy_id=vacancy_id, profile_version_id=profile_version_id,
        engine_version=ENGINE_VERSION, score=result.score,
        explanation=json.dumps({"components": [asdict(c) for c in result.components], "weights": result.weights_used}),
    )
    session.add(match)
    await session.flush()
    return match

def as_dict(match: MatchScore) -> dict:
    detail = json.loads(match.explanation or "{}")
    return {
        "id": match.id, "profile_version_id": match.profile_version_id, "vacancy_id": match.vacancy_id,
        "score": match.score, "engine_version": match.engine_version, "created_at": match.created_at,
        "components": detail.get("components", []), "weights": detail.get("weights", {}),
    }
