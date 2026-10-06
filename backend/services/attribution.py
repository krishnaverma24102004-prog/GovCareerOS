"""
Builds AttributionOut for vacancies, exams, exam versions and notices in a few batched queries (never one per row).
Everything comes from stored rows: the source registry entry, the RawDocument and the field provenance.
"""
import uuid
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.models.artifact import RawDocument
from govcareeros.backend.models.exam import Exam, ExamFieldProvenance
from govcareeros.backend.models.exam_version import ExamVersion
from govcareeros.backend.models.notice import RecruitmentNotice
from govcareeros.backend.models.source import GovernmentSource
from govcareeros.backend.models.vacancy import Vacancy, VacancyFieldProvenance
from govcareeros.backend.schemas.attribution import AttributionOut

@dataclass
class _Ref:
    source_id: uuid.UUID
    raw_document_id: Optional[uuid.UUID]
    page_url: Optional[str]
    page: Optional[int]

async def _build(session: AsyncSession, refs: Sequence[_Ref]) -> List[Optional[AttributionOut]]:
    source_ids = {r.source_id for r in refs}
    doc_ids = {r.raw_document_id for r in refs if r.raw_document_id}
    sources = {s.id: s for s in (await session.execute(
        select(GovernmentSource).where(GovernmentSource.id.in_(source_ids)))).scalars()} if source_ids else {}
    # Only the columns needed; raw_body can be large
    docs = {d.id: d for d in (await session.execute(
        select(RawDocument.id, RawDocument.url, RawDocument.retrieved_at).where(RawDocument.id.in_(doc_ids)))).all()} if doc_ids else {}
    out: List[Optional[AttributionOut]] = []
    for r in refs:
        source = sources.get(r.source_id)
        if source is None:
            out.append(None)
            continue
        doc = docs.get(r.raw_document_id)
        out.append(AttributionOut(
            source_name=source.name, organization=source.organization, jurisdiction=source.jurisdiction,
            official_page_url=r.page_url or source.recruitment_url,
            document_url=doc.url if doc else None, document_retrieved_at=doc.retrieved_at if doc else None,
            page=r.page if doc else None,
        ))
    return out

async def _notice_pages(session: AsyncSession, notice_ids: Iterable[Optional[uuid.UUID]]) -> Dict[uuid.UUID, Optional[str]]:
    ids = {i for i in notice_ids if i}
    if not ids:
        return {}
    return dict((await session.execute(
        select(RecruitmentNotice.id, RecruitmentNotice.found_on_url).where(RecruitmentNotice.id.in_(ids)))).all())

async def for_vacancies(session: AsyncSession, vacancies: Sequence[Vacancy]) -> List[Optional[AttributionOut]]:
    ids = [v.id for v in vacancies]
    pages = dict((await session.execute(
        select(VacancyFieldProvenance.vacancy_id, VacancyFieldProvenance.page).where(
            VacancyFieldProvenance.vacancy_id.in_(ids), VacancyFieldProvenance.field_name == "post_name"))).all()) if ids else {}
    return await _build(session, [_Ref(v.source_id, v.raw_document_id, v.source_url, pages.get(v.id)) for v in vacancies])

async def for_exams(session: AsyncSession, exams: Sequence[Exam]) -> List[Optional[AttributionOut]]:
    ids = [e.id for e in exams]
    pages = dict((await session.execute(
        select(ExamFieldProvenance.exam_id, ExamFieldProvenance.page).where(
            ExamFieldProvenance.exam_id.in_(ids), ExamFieldProvenance.field_name == "name"))).all()) if ids else {}
    found_on = await _notice_pages(session, (e.notice_id for e in exams))
    return await _build(session, [_Ref(e.source_id, e.raw_document_id, found_on.get(e.notice_id), pages.get(e.id)) for e in exams])

async def for_exam_versions(session: AsyncSession, exam: Exam, versions: Sequence[ExamVersion]) -> List[Optional[AttributionOut]]:
    # A version's document is the corrigendum/notice itself; which of its pages is cited lives in `details`
    found_on = await _notice_pages(session, (v.notice_id for v in versions))
    return await _build(session, [_Ref(exam.source_id, v.raw_document_id, found_on.get(v.notice_id), None) for v in versions])

async def for_notices(session: AsyncSession, notices: Sequence[RecruitmentNotice]) -> List[Optional[AttributionOut]]:
    """A notice link: the page it was found on, and its document once downloaded (else document_url is the link itself)."""
    built = await _build(session, [_Ref(n.source_id, n.raw_document_id, n.found_on_url, None) for n in notices])
    return [a.model_copy(update={"document_url": a.document_url or n.url}) if a else None for n, a in zip(notices, built)]
