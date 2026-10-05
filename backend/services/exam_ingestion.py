"""
Exam notice PDF -> RawDocument -> deterministic parse -> Exam (+ posts, requirements, provenance).

Idempotent: an exam is keyed by source + the notice's file number, and the same document parsed by the
same parser version is not stored twice. Post-wise tables (parsing/ssc_tables.py) give each post its own
AGE_MAX and EDUCATION rows; corrigenda already applied to the exam (services/exam_corrigenda.py) are
re-applied after a re-parse. A re-parse with a newer parser keeps the exam id, replaces
field provenance, updates posts in place and supersedes (never deletes) requirement rows. A notice
whose deterministic parse lacks the core fields is left for the LLM draft path (MANUAL_REVIEW).
What a stored exam's notice asks of applicants (certificates, uploads, fee, documents, key dates) is parsed by
parsing/ssc_application.py into ApplicationRequirement rows for the same document (services/application_prep.py).
"""
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, List, Optional

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.documents.pdf_text import extract_pages
from govcareeros.backend.models.exam import Exam, ExamFieldProvenance, ExamPost, ExamRequirement
from govcareeros.backend.models.notice import RecruitmentNotice
from govcareeros.backend.models.source import GovernmentSource
from govcareeros.backend.parsing.ssc_application import parse_ssc_application_info
from govcareeros.backend.parsing.ssc_exam_notice import CONDUCTING_BODY, ParsedExam, exam_code_for
from govcareeros.backend.parsing.ssc_tables import norm, parse_exam_pdf
from govcareeros.backend.services.application_prep import store_application_requirements
from govcareeros.backend.services.exam_corrigenda import apply_exam_versions, ingest_corrigendum_pdf, refresh_education_summary
from govcareeros.backend.services.exam_drafts import create_exam_draft
from govcareeros.backend.services.document_ingestion import _as_text, get_or_create_source, store_raw_document
from govcareeros.backend.services.source_discovery import record_probe
from govcareeros.backend.sources import ssc
from govcareeros.backend.sources.url_guard import UrlRefused

# Without these an exam row would not tell a candidate what it is or until when to apply
CORE_FIELDS = ("name", "application_end")
_SCORED = ("name", "application_end", "age_crucial_date", "fee_amount", "education_cutoff_date")

@dataclass
class ExamIngestionResult:
    raw_document_id: Any
    exam_id: Optional[Any] = None
    created: bool = False
    reparsed: bool = False
    skipped_reason: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    requirement_warnings: List[str] = field(default_factory=list)  # from the application requirements parse

def _confidence(parsed: ParsedExam) -> float:
    have = sum(1 for f in _SCORED if parsed.value(f) is not None)
    reqs = parsed.requirements + [r for p in parsed.posts for r in p.requirements]
    have += any(r.req_type == "AGE_MAX" for r in reqs) + any(r.req_type == "EDUCATION" for r in reqs)
    return round(have / (len(_SCORED) + 2), 2)

async def store_parsed_exam(session: AsyncSession, source: GovernmentSource, raw, parsed: ParsedExam,
                            notice: Optional[RecruitmentNotice] = None) -> ExamIngestionResult:
    result = ExamIngestionResult(raw.id, warnings=list(parsed.warnings))
    missing = [f for f in CORE_FIELDS if parsed.value(f) is None]
    if missing:
        result.skipped_reason = f"core fields not parsed: {', '.join(missing)}"
        return result
    external_ref = parsed.value("file_number") or f"doc:{raw.content_hash[-16:]}"
    existing = (await session.execute(select(Exam).where(Exam.source_id == source.id, Exam.external_ref == external_ref))).scalar_one_or_none()
    if existing is not None and existing.raw_document_id != raw.id:
        # A second document under the same file number is a corrigendum/re-issue; change tracking for exams is not built yet
        result.exam_id, result.skipped_reason = existing.id, "already stored from another document; change not applied"
        return result
    if existing is not None and existing.parser_version == parsed.parser_version:
        result.exam_id, result.skipped_reason = existing.id, "already parsed with this parser version"
        return result

    name = parsed.value("name")
    values = dict(
        source_id=source.id, conducting_body=CONDUCTING_BODY, name=name, exam_code=exam_code_for(name),
        exam_year=parsed.value("exam_year"), external_ref=external_ref, notification_url=raw.url,
        notice_id=notice.id if notice else (existing.notice_id if existing else None), raw_document_id=raw.id,
        parser_version=parsed.parser_version, application_start=parsed.value("application_start"),
        application_end=parsed.value("application_end"), fee_payment_end=parsed.value("fee_payment_end"),
        correction_start=parsed.value("correction_start"), correction_end=parsed.value("correction_end"),
        fee_amount=parsed.value("fee_amount"), fee_exemption=parsed.value("fee_exemption"),
        exam_dates=json.dumps(parsed.value("exam_dates"), ensure_ascii=False) if parsed.value("exam_dates") else None,
        age_crucial_date=parsed.value("age_crucial_date"), education_cutoff_date=parsed.value("education_cutoff_date"),
        education_text="\n".join(r.value for r in parsed.requirements if r.req_type == "EDUCATION") or None,
        tentative_vacancies=parsed.value("tentative_vacancies"), confidence=_confidence(parsed),
    )
    if existing is not None:
        await session.execute(delete(ExamFieldProvenance).where(ExamFieldProvenance.exam_id == existing.id))
        await session.execute(update(ExamRequirement).where(ExamRequirement.exam_id == existing.id, ExamRequirement.superseded_at.is_(None))
                              .values(superseded_at=datetime.now(timezone.utc)))
        for key, val in values.items():
            setattr(existing, key, val)
        exam, result.reparsed = existing, True
    else:
        exam, result.created = Exam(id=uuid.uuid4(), **values), True
        session.add(exam)
    await session.flush()
    result.exam_id = exam.id

    for fname, pf in parsed.fields.items():
        session.add(ExamFieldProvenance(exam_id=exam.id, field_name=fname, value=_as_text(pf.value), raw_document_id=raw.id,
                                        page=pf.provenance.page, section=pf.provenance.section, excerpt=pf.provenance.excerpt))
    posts = {p.post_key: p for p in (await session.execute(select(ExamPost).where(ExamPost.exam_id == exam.id))).scalars()}
    for pp in parsed.posts:
        row = posts.get(pp.post_key) or ExamPost(id=uuid.uuid4(), exam_id=exam.id, post_key=pp.post_key)
        row.post_name, row.pay_level, row.pay_scale = pp.post_name, pp.pay_level or None, pp.pay_scale
        row.post_code, row.department, row.classification = pp.post_code, pp.department, pp.classification
        row.age_min, row.age_max, row.education_text, row.vacancy_count = pp.age_min, pp.age_max, pp.education_text, pp.vacancy_count
        row.raw_document_id, row.source_page, row.source_excerpt = raw.id, pp.provenance.page, pp.provenance.excerpt
        session.add(row)
        posts[pp.post_key] = row
    await session.flush()
    for pp in parsed.posts:
        for req in pp.requirements:
            session.add(ExamRequirement(
                exam_id=exam.id, post_id=posts[pp.post_key].id, req_type=req.req_type, value=_as_text(req.value), is_mandatory=req.is_mandatory,
                raw_document_id=raw.id, source_page=req.provenance.page, source_section=req.provenance.section[:255],
                source_excerpt=req.provenance.excerpt,
            ))
    for req in parsed.requirements:
        session.add(ExamRequirement(
            exam_id=exam.id, req_type=req.req_type, value=_as_text(req.value), is_mandatory=req.is_mandatory, raw_document_id=raw.id,
            source_page=req.provenance.page, source_section=req.provenance.section, source_excerpt=req.provenance.excerpt,
        ))
    if notice is not None:
        notice.raw_document_id, notice.review_status = raw.id, "PARSED"
    await session.flush()
    await refresh_education_summary(session, exam)
    # A re-parse rewrote the notice's own values; corrigenda stored for this exam apply on top again
    await apply_exam_versions(session, exam)
    return result

async def ingest_exam_pdf(session: AsyncSession, source: GovernmentSource, url: str, body: bytes,
                          notice: Optional[RecruitmentNotice] = None) -> ExamIngestionResult:
    raw, _ = await store_raw_document(session, source, url, body, 200, "application/pdf")
    pages = extract_pages(body)
    parsed = parse_exam_pdf(body, pages)
    raw.parser_version = parsed.parser_version
    result = await store_parsed_exam(session, source, raw, parsed, notice)
    if result.exam_id is not None and (await session.get(Exam, result.exam_id)).raw_document_id == raw.id:
        info = await store_application_requirements(session, source, raw, pages, parsed.value("file_number"), parse=parse_ssc_application_info)
        result.requirement_warnings = info.warnings
    if notice is not None and result.exam_id is None:
        # No deterministic result: a reviewer or the LLM draft path (services/exam_drafts.py) takes it from here
        notice.raw_document_id, notice.review_status = raw.id, "MANUAL_REVIEW"
    return result

async def run_ssc_pipeline(session: AsyncSession, fetcher, pages: int = 1, limit: int = 5, provider=None, first_page: int = 1) -> dict:
    """Notice board -> RecruitmentNotice rows -> exam notices (PDF) -> Exam rows. Each PDF in its own savepoint.
    With an LLM provider, a notice the parser cannot read becomes a review draft (services/exam_drafts.py)."""
    source = await get_or_create_source(session, ssc.SSC_SOURCE)
    probe = await ssc.probe(fetcher, pages, getattr(fetcher, "allow_browser", False), first_page)
    summary = await record_probe(session, source, probe, datetime.now(timezone.utc))
    pending = (await session.execute(select(RecruitmentNotice).where(
        RecruitmentNotice.source_id == source.id, RecruitmentNotice.notice_kind == "EXAM_NOTICE",
        RecruitmentNotice.document_type == "PDF", RecruitmentNotice.review_status == "NEW",
    ).order_by(RecruitmentNotice.first_seen_at.desc()).limit(limit))).scalars().all()
    results, drafts, errors = [], [], []
    for notice in pending:
        try:
            body = await ssc.download_pdf(fetcher, notice.url)
            async with session.begin_nested():
                result = await ingest_exam_pdf(session, source, notice.url, body, notice)
                results.append(result)
            if result.exam_id is None and provider is not None:
                async with session.begin_nested():
                    drafts.append(await create_exam_draft(session, source, notice.url, body, provider, notice))
        except (ssc.AccessRestricted, ssc.RobotsDisallowed, UrlRefused) as e:
            notice.review_status = type(e).__name__.upper()[:30]
            errors.append(f"{notice.url}: {e}")
        except Exception as e:  # one bad PDF must not stop the others
            errors.append(f"{notice.url}: {type(e).__name__}: {e}")
    corrigenda = await _apply_pending_corrigenda(session, source, fetcher, limit, errors)
    await session.flush()
    return {"probe": summary, "exams": results, "drafts": drafts, "corrigenda": corrigenda, "errors": errors}

async def _apply_pending_corrigenda(session: AsyncSession, source: GovernmentSource, fetcher, limit: int, errors: List[str]) -> list:
    """Corrigenda/addenda whose title names a stored exam, and schedule notices (their titles rarely name the exam)."""
    names = [f" {norm(e.name)} " for e in (await session.execute(select(Exam).where(Exam.source_id == source.id))).scalars()]
    if not names:
        return []
    pending = (await session.execute(select(RecruitmentNotice).where(
        RecruitmentNotice.source_id == source.id, RecruitmentNotice.notice_kind.in_(("CORRIGENDUM", "SCHEDULE")),
        RecruitmentNotice.document_type == "PDF", RecruitmentNotice.review_status == "NEW",
    ).order_by(RecruitmentNotice.first_seen_at))).scalars().all()
    wanted = [n for n in pending if n.notice_kind == "SCHEDULE" or any(name in f" {norm(n.title)} " for name in names)][:limit]
    applied = []
    for notice in wanted:
        try:
            body = await ssc.download_pdf(fetcher, notice.url)
            async with session.begin_nested():
                applied.append(await ingest_corrigendum_pdf(session, source, notice.url, body, notice))
        except (ssc.AccessRestricted, ssc.RobotsDisallowed, UrlRefused) as e:
            notice.review_status = type(e).__name__.upper()[:30]
            errors.append(f"{notice.url}: {e}")
        except Exception as e:
            errors.append(f"{notice.url}: {type(e).__name__}: {e}")
    return applied
