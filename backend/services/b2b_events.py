"""
The event log behind /api/v1/changes and webhooks (models.b2b.ApiEvent).

`collect` records what is not yet in the log, in one short transaction:
  notice.new      every RecruitmentNotice without a notice.new event (the first run records every stored notice)
  notice.changed  every VacancyVersion / ExamVersion without an event, with its field-level changes from
                  services/change_log.py (old and new value, each with document, page and quote)
Events are numbered by `seq` in the order collect writes them. Only collect writes events, and it holds a
transaction-level advisory lock on PostgreSQL (SQLite serialises writers anyway), so a reader that has seen seq N
will never later find an event numbered below N: polling with ?since=<cursor> misses nothing.

Run it from scripts/dispatch_webhooks.py (which then delivers webhooks), after the daily ingestion.
"""
import json
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

from sqlalchemy import and_, exists, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.models.b2b import ApiEvent
from govcareeros.backend.models.exam import Exam
from govcareeros.backend.models.exam_version import ExamVersion
from govcareeros.backend.models.notice import RecruitmentNotice
from govcareeros.backend.models.vacancy import Vacancy, VacancyVersion
from govcareeros.backend.schemas.attribution import DISCLAIMER
from govcareeros.backend.services import b2b_notices, change_log

NOTICE_NEW, NOTICE_CHANGED = "notice.new", "notice.changed"
EVENT_TYPES = (NOTICE_NEW, NOTICE_CHANGED)
_LOCK_KEY = 0x6763_6F5F_6576  # "gco_ev": one pg_advisory_xact_lock for every collect
_BATCH = 500

def _dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)

async def last_seq(session: AsyncSession) -> int:
    return (await session.execute(select(func.max(ApiEvent.seq)))).scalar_one() or 0

async def _notice_ids_by_document(session: AsyncSession, doc_ids) -> Dict:
    ids = {d for d in doc_ids if d}
    if not ids:
        return {}
    rows = (await session.execute(select(RecruitmentNotice.raw_document_id, RecruitmentNotice.id)
                                  .where(RecruitmentNotice.raw_document_id.in_(ids))
                                  .order_by(RecruitmentNotice.first_seen_at, RecruitmentNotice.id))).all()
    out: Dict = {}
    for doc_id, notice_id in rows:
        out.setdefault(doc_id, notice_id)  # the first notice that pointed at the document
    return out

def _version_payload(subject: dict, notice_id, change_notice_id, vc: Optional[change_log.VersionChanges]) -> dict:
    return {
        "notice_id": str(notice_id) if notice_id else None,  # the notice the vacancy / exam was read from
        "change_notice_id": str(change_notice_id) if change_notice_id else None,  # the notice that states the change
        "subject": subject,
        "version": {k: v for k, v in asdict(vc).items() if k != "changes"} if vc else None,
        "changes": [asdict(c) for c in vc.changes] if vc else [],
        "lines": change_log.change_lines(vc) if vc else [],
        "disclaimer": DISCLAIMER,
    }

async def _collect_notices(session: AsyncSession, now: datetime) -> int:
    done = exists().where(and_(ApiEvent.event_type == NOTICE_NEW, ApiEvent.notice_id == RecruitmentNotice.id))
    added = 0
    while True:
        batch = list((await session.execute(select(RecruitmentNotice).where(~done)
                                            .order_by(RecruitmentNotice.first_seen_at, RecruitmentNotice.id).limit(_BATCH))).scalars())
        if not batch:
            return added
        for n, out in zip(batch, await b2b_notices.notice_summaries(session, batch)):
            session.add(ApiEvent(event_type=NOTICE_NEW, dedupe_key=f"{NOTICE_NEW}:{n.id}", notice_id=n.id,
                                 payload=_dumps({"notice": out.model_dump(mode="json")}), created_at=now))
        await session.flush()
        added += len(batch)

async def _collect_versions(session: AsyncSession, now: datetime) -> int:
    cache = change_log.LogCache(session)
    added = 0
    exam_done = exists().where(and_(ApiEvent.event_type == NOTICE_CHANGED, ApiEvent.version_id == ExamVersion.id))
    for version, exam in (await session.execute(select(ExamVersion, Exam).join(Exam, Exam.id == ExamVersion.exam_id)
                                                .where(~exam_done).order_by(ExamVersion.created_at, ExamVersion.id))).all():
        vc = await cache.exam(exam, version.id)
        subject = {"type": "exam", "id": str(exam.id), "name": exam.name, "conducting_body": exam.conducting_body}
        session.add(ApiEvent(event_type=NOTICE_CHANGED, dedupe_key=f"{NOTICE_CHANGED}:{version.id}", notice_id=exam.notice_id,
                             exam_id=exam.id, version_id=version.id,
                             payload=_dumps(_version_payload(subject, exam.notice_id, version.notice_id, vc)), created_at=now))
        added += 1
    vac_done = exists().where(and_(ApiEvent.event_type == NOTICE_CHANGED, ApiEvent.version_id == VacancyVersion.id))
    rows = (await session.execute(select(VacancyVersion, Vacancy).join(Vacancy, Vacancy.id == VacancyVersion.vacancy_id)
                                  .where(~vac_done).order_by(VacancyVersion.created_at, VacancyVersion.id))).all()
    by_doc = await _notice_ids_by_document(session, [v.raw_document_id for _, v in rows] + [vv.raw_document_id for vv, _ in rows])
    for vv, vacancy in rows:
        vc = await cache.vacancy(vacancy, vv.id)
        subject = {"type": "vacancy", "id": str(vacancy.id), "name": vacancy.post_name, "organization": vacancy.organization,
                   "advertisement_number": vacancy.advertisement_number}
        notice_id = by_doc.get(vacancy.raw_document_id)
        session.add(ApiEvent(event_type=NOTICE_CHANGED, dedupe_key=f"{NOTICE_CHANGED}:{vv.id}", notice_id=notice_id,
                             vacancy_id=vacancy.id, version_id=vv.id,
                             payload=_dumps(_version_payload(subject, notice_id, by_doc.get(vv.raw_document_id), vc)), created_at=now))
        added += 1
    await session.flush()
    return added

async def collect(session: AsyncSession, now: Optional[datetime] = None) -> Dict[str, int]:
    """Record new notices and official changes as events. Commit right after: the lock is held until then."""
    if session.bind.dialect.name == "postgresql":
        await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _LOCK_KEY})
    now = now or datetime.now(timezone.utc)
    return {NOTICE_NEW: await _collect_notices(session, now), NOTICE_CHANGED: await _collect_versions(session, now)}

async def events_after(session: AsyncSession, after_seq: int, types: Sequence[str] = EVENT_TYPES, limit: int = 100) -> List[ApiEvent]:
    return list((await session.execute(select(ApiEvent).where(ApiEvent.seq > after_seq, ApiEvent.event_type.in_(list(types)))
                                       .order_by(ApiEvent.seq).limit(limit))).scalars())

def feed_cursor(seq: int) -> str:
    return b2b_notices.encode_cursor({"q": seq})

def feed_seq(cursor: Optional[str]) -> int:
    if not cursor:
        return 0
    value = b2b_notices.decode_cursor(cursor)
    if not isinstance(value, dict) or not isinstance(value.get("q"), int) or value["q"] < 0:
        raise b2b_notices.BadCursor("cursor is not one this API returned")
    return value["q"]
