"""
Notices as the B2B API states them: RecruitmentNotice rows (official links seen on official source pages) with
their attribution, and the vacancies / exams parsed from the notice's document with every requirement's page and
quoted words. Read-only; nothing here decides eligibility.

Pagination is by cursor over (first_seen_at, id), which never change, so a page boundary stays put while new
notices arrive. A cursor is opaque to clients (base64 of JSON).
"""
import base64
import json
import uuid
from datetime import date, datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.models.exam import Exam, ExamPost, ExamRequirement
from govcareeros.backend.models.notice import RecruitmentNotice
from govcareeros.backend.models.source import GovernmentSource
from govcareeros.backend.models.vacancy import Vacancy, VacancyRequirement
from govcareeros.backend.parsing.upsc_advertisement import IST
from govcareeros.backend.schemas.b2b import NoticeDetailOut, NoticeOut, ParsedExamOut, ParsedVacancyOut, RequirementOut
from govcareeros.backend.services import attribution

class BadCursor(ValueError):
    pass

def encode_cursor(value) -> str:
    return base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).decode().rstrip("=")

def decode_cursor(cursor: str):
    try:
        return json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    except (ValueError, UnicodeDecodeError) as e:
        raise BadCursor("cursor is not one this API returned") from e

def _today() -> date:
    return datetime.now(IST).date()

def status_of(notice: RecruitmentNotice, today: Optional[date] = None) -> str:
    if notice.last_date is None:
        return "unknown"
    return "open" if notice.last_date >= (today or _today()) else "closed"

async def list_notices(session: AsyncSession, *, source: Optional[str] = None, jurisdiction: Optional[str] = None,
                       kind: Optional[str] = None, status: Optional[str] = None, updated_since: Optional[datetime] = None,
                       cursor: Optional[str] = None, limit: int = 50) -> Tuple[List[RecruitmentNotice], Optional[str]]:
    stmt = select(RecruitmentNotice).join(GovernmentSource, GovernmentSource.id == RecruitmentNotice.source_id)
    if source:
        stmt = stmt.where(GovernmentSource.name == source)
    if jurisdiction:
        stmt = stmt.where(GovernmentSource.jurisdiction == jurisdiction)
    if kind:
        stmt = stmt.where(RecruitmentNotice.notice_kind == kind)
    if status == "open":
        stmt = stmt.where(RecruitmentNotice.last_date >= _today())
    elif status == "closed":
        stmt = stmt.where(RecruitmentNotice.last_date < _today())
    if updated_since:
        stmt = stmt.where(RecruitmentNotice.updated_at >= updated_since)
    if cursor:
        after = decode_cursor(cursor)
        try:
            seen, after_id = datetime.fromisoformat(after["s"]), uuid.UUID(after["i"])
        except (KeyError, TypeError, ValueError) as e:
            raise BadCursor("cursor is not one this API returned") from e
        stmt = stmt.where(or_(RecruitmentNotice.first_seen_at > seen,
                              and_(RecruitmentNotice.first_seen_at == seen, RecruitmentNotice.id > after_id)))
    rows = list((await session.execute(
        stmt.order_by(RecruitmentNotice.first_seen_at, RecruitmentNotice.id).limit(limit + 1))).scalars())
    more = len(rows) > limit
    rows = rows[:limit]
    nxt = encode_cursor({"s": rows[-1].first_seen_at.isoformat(), "i": str(rows[-1].id)}) if more else None
    return rows, nxt

async def notice_summaries(session: AsyncSession, notices: Sequence[RecruitmentNotice]) -> List[NoticeOut]:
    source_ids = {n.source_id for n in notices}
    sources = {s.id: s for s in (await session.execute(
        select(GovernmentSource).where(GovernmentSource.id.in_(source_ids)))).scalars()} if source_ids else {}
    atts = await attribution.for_notices(session, notices)
    today = _today()
    out = []
    for n, att in zip(notices, atts):
        s = sources[n.source_id]
        out.append(NoticeOut(
            id=n.id, source=s.name, organization=s.organization, jurisdiction=s.jurisdiction, kind=n.notice_kind,
            title=n.title, url=n.url, document_type=n.document_type, published_on=n.published_on, last_date=n.last_date,
            status=status_of(n, today), advertisement_number=n.advertisement_number,
            post_names=json.loads(n.post_names) if n.post_names else [],
            listing_evidence=json.loads(n.listing_evidence) if n.listing_evidence else {},
            first_seen_at=n.first_seen_at, last_seen_at=n.last_seen_at, updated_at=n.updated_at, attribution=att))
    return out

def _req(r, post: Optional[str] = None) -> RequirementOut:
    return RequirementOut(req_type=r.req_type, value=r.value, is_mandatory=r.is_mandatory, post=post,
                          page=r.source_page, section=r.source_section, quote=r.source_excerpt)

async def parsed_records(session: AsyncSession, notice: RecruitmentNotice) -> Tuple[List[ParsedVacancyOut], List[ParsedExamOut]]:
    """Vacancies and exams read from this notice's document; current requirement rows only (superseded ones are kept
    in the database for past decisions, not served)."""
    vacancy_match = [Vacancy.notification_url == notice.url]
    exam_match = [Exam.notice_id == notice.id]
    if notice.raw_document_id:
        vacancy_match.append(Vacancy.raw_document_id == notice.raw_document_id)
        exam_match.append(Exam.raw_document_id == notice.raw_document_id)
    vacancies = list((await session.execute(select(Vacancy).where(or_(*vacancy_match))
                                            .order_by(Vacancy.post_name, Vacancy.id))).scalars())
    exams = list((await session.execute(select(Exam).where(or_(*exam_match)).order_by(Exam.name, Exam.id))).scalars())

    v_reqs: Dict[uuid.UUID, List[RequirementOut]] = {v.id: [] for v in vacancies}
    if vacancies:
        for r in (await session.execute(select(VacancyRequirement).where(
                VacancyRequirement.vacancy_id.in_(list(v_reqs)), VacancyRequirement.superseded_at.is_(None))
                .order_by(VacancyRequirement.source_page, VacancyRequirement.req_type, VacancyRequirement.id))).scalars():
            v_reqs[r.vacancy_id].append(_req(r))
    e_reqs: Dict[uuid.UUID, List[RequirementOut]] = {e.id: [] for e in exams}
    if exams:
        posts = dict((await session.execute(select(ExamPost.id, ExamPost.post_name).where(ExamPost.exam_id.in_(list(e_reqs))))).all())
        for r in (await session.execute(select(ExamRequirement).where(
                ExamRequirement.exam_id.in_(list(e_reqs)), ExamRequirement.superseded_at.is_(None))
                .order_by(ExamRequirement.source_page, ExamRequirement.req_type, ExamRequirement.id))).scalars():
            e_reqs[r.exam_id].append(_req(r, posts.get(r.post_id)))

    v_att = await attribution.for_vacancies(session, vacancies)
    e_att = await attribution.for_exams(session, exams)
    return ([ParsedVacancyOut(id=v.id, post_name=v.post_name, organization=v.organization,
                              advertisement_number=v.advertisement_number, vacancy_count=v.vacancy_count, status=v.status,
                              recruitment_mode=v.recruitment_mode, application_deadline=v.application_deadline,
                              attribution=a, requirements=v_reqs[v.id]) for v, a in zip(vacancies, v_att)],
            [ParsedExamOut(id=e.id, name=e.name, conducting_body=e.conducting_body, status=e.status,
                           application_start=e.application_start, application_end=e.application_end, fee_amount=e.fee_amount,
                           attribution=a, requirements=e_reqs[e.id]) for e, a in zip(exams, e_att)])

async def notice_detail(session: AsyncSession, notice: RecruitmentNotice) -> NoticeDetailOut:
    (summary,) = await notice_summaries(session, [notice])
    vacancies, exams = await parsed_records(session, notice)
    return NoticeDetailOut(**summary.model_dump(), vacancies=vacancies, exams=exams)

def utc(dt: datetime) -> datetime:
    """A client's timestamp without an offset is read as UTC."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
