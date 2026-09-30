"""
Eligibility radar: re-checks every user's latest profile version against every active,
open vacancy and turns the results into in-app alerts.

Everything an alert says comes from stored data: the deterministic eligibility result,
the vacancy's stated deadline and age limits, and official notices (VacancyVersion).
Alerts are de-duplicated per user, so running the radar repeatedly is safe.
"""
import json
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.eligibility import engine as eligibility_engine
from govcareeros.backend.eligibility import rules
from govcareeros.backend.models.alert import UserAlert
from govcareeros.backend.models.eligibility import EligibilityResult
from govcareeros.backend.models.user import ProfileVersion, User
from govcareeros.backend.models.vacancy import Vacancy, VacancyVersion
from govcareeros.backend.parsing.upsc_advertisement import IST
from govcareeros.backend.relevance import service as relevance_service
from govcareeros.backend.services.application_tracker import application_update_alerts

DEADLINE_WINDOW = timedelta(days=7)
AGE_HEADROOM_YEARS = 1
_ALERTABLE = {"ELIGIBLE", "CONDITIONAL"}

@dataclass
class RadarRun:
    users: int = 0
    evaluations: int = 0
    alerts_created: Dict[str, int] = field(default_factory=dict)

def _fmt(dt: datetime) -> str:
    return dt.astimezone(IST).strftime("%d %b %Y %H:%M IST")

async def _add_alert(session: AsyncSession, run: RadarRun, **values) -> None:
    """Insert unless (user_id, dedupe_key) already exists."""
    values.setdefault("id", uuid.uuid4())
    insert = pg_insert if session.bind.dialect.name == "postgresql" else sqlite_insert
    stmt = insert(UserAlert).values(**values).on_conflict_do_nothing(index_elements=["user_id", "dedupe_key"])
    res = await session.execute(stmt)
    if res.rowcount:
        run.alerts_created[values["kind"]] = run.alerts_created.get(values["kind"], 0) + 1

async def _current_result(session: AsyncSession, version_id, vacancy_id, fingerprint: str) -> Optional[EligibilityResult]:
    """The decision made on exactly the vacancy's current inputs; after a notice, reparse or moved date there is none."""
    res = await session.execute(
        select(EligibilityResult).where(
            EligibilityResult.profile_version_id == version_id, EligibilityResult.vacancy_id == vacancy_id,
            EligibilityResult.input_fingerprint == fingerprint,
        ).order_by(EligibilityResult.created_at.desc()).limit(1)
    )
    return res.scalar_one_or_none()

def age_out_date(dob: date, max_age: int) -> date:
    """First day on which the candidate is older than max_age (i.e. turns max_age + 1)."""
    try:
        return dob.replace(year=dob.year + max_age + 1)
    except ValueError:  # 29 Feb
        return date(dob.year + max_age + 1, 3, 1)

async def run_for_user(session: AsyncSession, user: User, now: datetime, run: RadarRun) -> None:
    version = (await session.execute(
        select(ProfileVersion).where(ProfileVersion.user_id == user.id).order_by(ProfileVersion.version_number.desc()).limit(1)
    )).scalar_one_or_none()
    if version is None:
        return
    run.users += 1
    snapshot = await eligibility_engine.load_profile_snapshot(session, version.id)

    open_vacancies = (await session.execute(select(Vacancy).where(
        Vacancy.status == "ACTIVE", (Vacancy.application_deadline.is_(None)) | (Vacancy.application_deadline > now),
    ))).scalars().all()

    for vacancy in open_vacancies:
        reqs = await eligibility_engine.load_requirements(session, vacancy.id)
        result = await _current_result(session, version.id, vacancy.id, eligibility_engine.input_fingerprint(vacancy, reqs))
        if result is None:
            result = await eligibility_engine.evaluate(session, user.id, version.id, vacancy.id)
            await relevance_service.evaluate(session, user.id, version.id, vacancy.id)
            run.evaluations += 1
        base = dict(user_id=user.id, vacancy_id=vacancy.id, profile_version_id=version.id, eligibility_result_id=result.id)
        where = f"{vacancy.post_name} ({vacancy.organization}, Advt {vacancy.advertisement_number or '-'})"

        if result.status in _ALERTABLE:
            await _add_alert(session, run, kind="NEW_MATCH", dedupe_key=f"match:{vacancy.id}:{version.id}",
                             title=f"{result.status.title()}: {vacancy.post_name}",
                             body=f"Profile v{version.version_number} is {result.status} for {where}. "
                                  f"Last date {_fmt(vacancy.application_deadline) if vacancy.application_deadline else 'not stated'}.",
                             due_at=vacancy.application_deadline, **base)

        if vacancy.application_deadline and result.status in _ALERTABLE | {"MANUAL_REVIEW"} \
                and vacancy.application_deadline - now <= DEADLINE_WINDOW:
            days = max(0, (vacancy.application_deadline - now).days)
            await _add_alert(session, run, kind="DEADLINE_SOON", dedupe_key=f"deadline:{vacancy.id}:{vacancy.application_deadline.astimezone(timezone.utc).isoformat()}",
                             title=f"{days} day(s) left: {vacancy.post_name}",
                             body=f"Applications for {where} close {_fmt(vacancy.application_deadline)}. "
                                  + ("Your eligibility needs a manual check first." if result.status == "MANUAL_REVIEW" else ""),
                             due_at=vacancy.application_deadline, **base)

        # Age headroom from the vacancy's own stated limit for the candidate's category (or its single ALL limit).
        # A pending manual check does not change the age limit, so it is warned about as for deadlines.
        category = rules.normalize_category(snapshot.category)
        if snapshot.date_of_birth and category and result.status in _ALERTABLE | {"MANUAL_REVIEW"}:
            limit = rules.age_limit_for(reqs, category)
            crucial_dt = vacancy.eligibility_crucial_date or vacancy.application_deadline
            if limit and crucial_dt:
                age = rules.age_on(snapshot.date_of_birth, crucial_dt.astimezone(IST).date())
                headroom = limit.value["max_age"] - age
                if 0 <= headroom <= AGE_HEADROOM_YEARS:
                    out = age_out_date(snapshot.date_of_birth, limit.value["max_age"])
                    await _add_alert(session, run, kind="AGE_HEADROOM", dedupe_key=f"age:{vacancy.id}:{version.id}",
                                     title=f"Age limit close: {vacancy.post_name}",
                                     body=f"You are {age} on the crucial date; the {limit.value['category']} limit for this post is "
                                          f"{limit.value['max_age']} (p.{limit.source_page}). Posts with this limit stop accepting you "
                                          f"from {out.strftime('%d %b %Y')} unless a relaxation applies.",
                                     due_at=datetime(out.year, out.month, out.day, tzinfo=IST), **base)

    # Official notices about vacancies this user has been assessed for
    assessed = select(EligibilityResult.vacancy_id).where(EligibilityResult.user_id == user.id).distinct()
    for vv, vacancy in (await session.execute(
        select(VacancyVersion, Vacancy).join(Vacancy, Vacancy.id == VacancyVersion.vacancy_id).where(VacancyVersion.vacancy_id.in_(assessed))
    )).all():
        details = json.loads(vv.details or "{}")
        cancelled = vacancy.status == "CANCELLED" and "cancellation" in details
        await _add_alert(session, run, kind="VACANCY_CANCELLED" if cancelled else "VACANCY_UPDATED",
                         dedupe_key=f"notice:{vv.id}", user_id=user.id, vacancy_id=vacancy.id, vacancy_version_id=vv.id,
                         title=("Cancelled: " if cancelled else "Official notice: ") + vacancy.post_name,
                         body=f"{vv.change_type} notice" + (f" dated {vv.notice_date.astimezone(IST).strftime('%d %b %Y')}" if vv.notice_date else "")
                              + f" for {vacancy.post_name}: {vv.change_log or ''}".rstrip(),
                         due_at=vacancy.application_deadline)

async def run_radar(session: AsyncSession, now: Optional[datetime] = None, user_ids: Optional[List[uuid.UUID]] = None) -> RadarRun:
    now = now or datetime.now(timezone.utc)
    run = RadarRun()
    stmt = select(User).where(User.is_active.is_(True))
    if user_ids is not None:
        stmt = stmt.where(User.id.in_(user_ids))
    for user in (await session.execute(stmt)).scalars().all():
        await run_for_user(session, user, now, run)
        # Result/corrigendum notices for advertisements the user tracks an application for
        for values in await application_update_alerts(session, user.id):
            await _add_alert(session, run, **values)
    await session.flush()
    return run
