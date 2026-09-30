"""
Official notice -> RawDocument (OCR when scanned) -> parsed changes -> applied to stored vacancies.

Each (vacancy, notice document) produces one VacancyVersion recording what the notice says.
Effects are applied only for what the text states:
  CANCELLATION     -> Vacancy.status = CANCELLED
  DATE_EXTENSION   -> new application_deadline (old value kept in the version details)
  category clauses -> NOTICE_CONDITION requirement, surfaced by eligibility as a condition
Deadline and status are then recomputed from all of the vacancy's notices (apply_official_notices),
so the notice with the latest notice date wins whatever order the notices were found in.
Vacancy numbers not in the database are reported, not guessed.
"""
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from typing import Any, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.documents.pdf_text import extract_pages
from govcareeros.backend.models.notice import RecruitmentNotice
from govcareeros.backend.models.source import GovernmentSource
from govcareeros.backend.models.vacancy import Vacancy, VacancyRequirement, VacancyVersion
from govcareeros.backend.parsing.upsc_advertisement import IST
from govcareeros.backend.parsing.upsc_notice import ParsedNotice, parse_notice
from govcareeros.backend.services.document_ingestion import apply_official_notices, store_raw_document
from govcareeros.backend.services.source_discovery import url_hash

@dataclass
class NoticeApplication:
    url: str
    title: Optional[str]
    raw_document_id: Any
    kinds: List[str]
    advertisement_refs: List[str]
    applied_vacancy_ids: List[Any] = field(default_factory=list)
    already_applied_vacancy_ids: List[Any] = field(default_factory=list)
    unmatched_vacancy_refs: List[str] = field(default_factory=list)
    used_ocr: bool = False

async def apply_notice(session: AsyncSession, source: GovernmentSource, raw_document_id, parsed: ParsedNotice, url: str) -> NoticeApplication:
    result = NoticeApplication(url, parsed.title, raw_document_id, parsed.kinds, parsed.advertisement_refs, used_ocr=parsed.used_ocr)
    targets: List[Vacancy] = []
    for ref in parsed.vacancy_refs:
        v = (await session.execute(select(Vacancy).where(Vacancy.source_id == source.id, Vacancy.external_ref == ref))).scalar_one_or_none()
        if v is None:
            result.unmatched_vacancy_refs.append(ref)
        else:
            targets.append(v)
    if not parsed.vacancy_refs and parsed.advertisement_refs:
        # Notice names only the advertisement: it concerns every vacancy of it
        targets = list((await session.execute(select(Vacancy).where(
            Vacancy.source_id == source.id, Vacancy.advertisement_number.in_(parsed.advertisement_refs)))).scalars())

    notice_dt = datetime.combine(parsed.notice_date, time(0), tzinfo=IST) if parsed.notice_date else None
    section = f"Notice {parsed.notice_date.isoformat()}" if parsed.notice_date else "Notice"
    for vacancy in targets:
        exists = (await session.execute(select(VacancyVersion).where(
            VacancyVersion.vacancy_id == vacancy.id, VacancyVersion.raw_document_id == raw_document_id))).scalar_one_or_none()
        if exists:
            result.already_applied_vacancy_ids.append(vacancy.id)
            continue

        details: dict = {"title": parsed.title, "url": url, "parser_version": parsed.parser_version, "ocr": parsed.used_ocr}
        if parsed.cancels_vacancies:
            details["cancellation"] = {"sentence": parsed.cancellation.text, "page": parsed.cancellation.page,
                                       "previous_status": vacancy.status}
        if parsed.new_closing:
            details["deadline_change"] = {"old": vacancy.application_deadline.isoformat() if vacancy.application_deadline else None,
                                          "new": parsed.new_closing.isoformat()}
        if parsed.category_conditions:
            details["conditions"] = [{"text": c.text, "page": c.page} for c in parsed.category_conditions]
            for c in parsed.category_conditions:
                session.add(VacancyRequirement(
                    vacancy_id=vacancy.id, req_type="NOTICE_CONDITION", value=c.text, is_mandatory=True,
                    raw_document_id=raw_document_id, source_page=c.page, source_section=section, source_excerpt=c.text[:400],
                ))
        session.add(VacancyVersion(
            vacancy_id=vacancy.id, version_hash=str(raw_document_id), raw_document_id=raw_document_id,
            change_type=",".join(parsed.kinds), notice_date=notice_dt, details=json.dumps(details, ensure_ascii=False),
            change_log=parsed.title,
        ))
        result.applied_vacancy_ids.append(vacancy.id)
    await session.flush()
    for vacancy in targets:
        await apply_official_notices(session, vacancy)
    return result

async def ingest_notice_pdf(session: AsyncSession, source: GovernmentSource, url: str, body: bytes, title: Optional[str],
                            status: int = 200, content_type: str = "application/pdf") -> NoticeApplication:
    pages = extract_pages(body)
    ocr = any(p.ocr for p in pages)
    raw, _ = await store_raw_document(session, source, url, body, status, content_type, discovery_method="OCR" if ocr else "PDF")
    parsed = parse_notice(pages, title)
    raw.parser_version = parsed.parser_version

    # Keep the notice in the source's notice list, marked as parsed and linked to its document
    h = url_hash(url)
    notice = (await session.execute(select(RecruitmentNotice).where(
        RecruitmentNotice.source_id == source.id, RecruitmentNotice.url_hash == h))).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if notice is None:
        notice = RecruitmentNotice(source_id=source.id, url=url, url_hash=h, title=title or url.rsplit("/", 1)[-1],
                                   document_type="PDF", notice_kind="CORRIGENDUM", first_seen_at=now, last_seen_at=now)
        session.add(notice)
    notice.review_status, notice.raw_document_id, notice.last_seen_at = "PARSED", raw.id, now
    return await apply_notice(session, source, raw.id, parsed, url)
