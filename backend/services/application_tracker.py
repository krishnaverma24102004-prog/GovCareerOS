"""
Application tracker helpers: status changes with history, and APPLICATION_UPDATE alerts.

An APPLICATION_UPDATE alert is produced when a RecruitmentNotice classified RESULT or CORRIGENDUM, seen on
the same official source as a tracked vacancy, names that vacancy's advertisement number in its link title
or URL (e.g. "Advt. No. 11 - 2026", "...-Advt-13-25-..."). Matching is a deterministic pattern on the
number and year; a notice that does not name the advertisement is not guessed to be about it. Withdrawn
applications get no alerts. The radar inserts the returned alerts de-duplicated per (application, notice).
"""
import re
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional
from urllib.parse import unquote

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.models.application import Application, ApplicationStatusHistory
from govcareeros.backend.models.notice import RecruitmentNotice
from govcareeros.backend.models.vacancy import Vacancy

_ALERT_NOTICE_KINDS = ("RESULT", "CORRIGENDUM")

def change_status(app: Application, new_status: str, note: Optional[str] = None, now: Optional[datetime] = None) -> Optional[ApplicationStatusHistory]:
    """Sets the status and returns the history row to add (None when unchanged)."""
    if app.status == new_status:
        return None
    entry = ApplicationStatusHistory(application_id=app.id, from_status=app.status, to_status=new_status,
                                     changed_at=now or datetime.now(timezone.utc), note=note)
    app.status = new_status
    return entry

def advertisement_pattern(advertisement_number: str) -> Optional[re.Pattern]:
    """'11/2026' -> matches 'Advt. No. 11 - 2026', 'AdvtNo-11-2026', 'Advertisement No.11/2026', 'Advt-11-26'."""
    m = re.fullmatch(r"\s*0*(\d+)\s*/\s*(\d{4})\s*", advertisement_number or "")
    if not m:
        return None
    number, year = m.group(1), m.group(2)
    return re.compile(rf"(?:advt|advertisement)\.?\s*(?:no\.?)?\s*[-_:.]?\s*0*{number}\s*[-/_–]\s*(?:{year}|{year[2:]})(?!\d)", re.I)

async def application_update_alerts(session: AsyncSession, user_id: uuid.UUID) -> List[Dict]:
    tracked = (await session.execute(
        select(Application, Vacancy).join(Vacancy, Vacancy.id == Application.vacancy_id)
        .where(Application.user_id == user_id, Application.status != "WITHDRAWN", Vacancy.advertisement_number.is_not(None))
    )).all()
    notices_by_source: Dict[uuid.UUID, List[RecruitmentNotice]] = {}
    alerts = []
    for app, vacancy in tracked:
        pattern = advertisement_pattern(vacancy.advertisement_number)
        if pattern is None:
            continue
        if vacancy.source_id not in notices_by_source:
            notices_by_source[vacancy.source_id] = list((await session.execute(select(RecruitmentNotice).where(
                RecruitmentNotice.source_id == vacancy.source_id, RecruitmentNotice.notice_kind.in_(_ALERT_NOTICE_KINDS),
            ))).scalars())
        for notice in notices_by_source[vacancy.source_id]:
            if not pattern.search(f"{notice.title} {unquote(notice.url)}"):
                continue
            label = "Result" if notice.notice_kind == "RESULT" else "Corrigendum/notice"
            alerts.append(dict(
                kind="APPLICATION_UPDATE", dedupe_key=f"application:{app.id}:{notice.id}", user_id=user_id, vacancy_id=vacancy.id,
                title=f"{label} for Advt {vacancy.advertisement_number}: {vacancy.post_name}",
                body=f"Official notice \"{notice.title}\" ({notice.url}) names Advt {vacancy.advertisement_number}, "
                     f"which your tracked application for {vacancy.post_name} (status {app.status}) belongs to.",
                due_at=None,
            ))
    return alerts
