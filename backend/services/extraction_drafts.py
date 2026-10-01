"""
Notice PDF -> RawDocument -> LLM-located + validator-checked draft -> human review -> Vacancy.

Only fields the validator accepted can reach the database, and they are stored as the
document's own words (verified quote) or values parsed from those words by fixed rules.
The recruitment mode (parsing/recruitment_mode.py) is read by fixed rules from the verified
quotes, else from the notice's own text layer, and stored with the deciding sentence.
"""
import json
import os
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.documents.pdf_text import PageText, extract_pages
from govcareeros.backend.extraction.llm_extractor import ExtractionResult, extract
from govcareeros.backend.extraction.validator import ALL_FIELDS, FieldVerdict
from govcareeros.backend.models.artifact import RawDocument
from govcareeros.backend.models.extraction import ExtractionDraft
from govcareeros.backend.models.notice import RecruitmentNotice
from govcareeros.backend.models.source import GovernmentSource
from govcareeros.backend.models.triage import NoticeTriage
from govcareeros.backend.models.user import User
from govcareeros.backend.models.vacancy import Vacancy, VacancyFieldProvenance, VacancyRequirement
from govcareeros.backend.parsing.recruitment_mode import ModeDetection, detect_in_pages, detect_recruitment_mode, govt_service_requirement
from govcareeros.backend.services.document_ingestion import store_raw_document
from govcareeros.backend.services.source_discovery import source_policy
from govcareeros.backend.services.triage import SKIP_FOR_DRAFTS
from govcareeros.backend.sources.url_guard import UrlRefused

class DraftError(ValueError):
    pass

def _jsonable(v: Any) -> Any:
    return v.isoformat() if isinstance(v, datetime) else v

def serialize(result: ExtractionResult) -> Dict[str, Any]:
    return {
        "vacancies": [{"fields": {k: {**asdict(f), "value": _jsonable(f.value)} for k, f in v.fields.items()}} for v in result.vacancies],
        "chunks": [{"pages": c.pages, "seconds": c.seconds, "prompt_tokens": c.prompt_tokens,
                    "output_tokens": c.output_tokens, "error": c.error} for c in result.chunks],
    }

async def create_draft(session: AsyncSession, source: GovernmentSource, url: str, body: bytes, provider,
                       notice: Optional[RecruitmentNotice] = None) -> ExtractionDraft:
    pages = extract_pages(body)
    raw, _ = await store_raw_document(session, source, url, body, 200, "application/pdf",
                                      discovery_method="OCR" if any(p.ocr for p in pages) else "PDF")
    result = await extract(pages, provider)
    offered = accepted = 0
    for v in result.vacancies:
        for f in v.fields.values():
            if f.reason not in ("not stated", "missing"):
                offered += 1
                accepted += f.accepted
    errors = [c.error for c in result.chunks if c.error]
    draft = ExtractionDraft(
        id=uuid.uuid4(), source_id=source.id, notice_id=notice.id if notice else None, raw_document_id=raw.id,
        provider=provider.name, model=result.model, prompt_version=result.prompt_version,
        status="FAILED" if errors and not result.vacancies else "PENDING_REVIEW",
        payload=json.dumps(serialize(result), ensure_ascii=False), fields_offered=offered, fields_accepted=accepted,
        llm_seconds=result.seconds, error="; ".join(errors) or None,
    )
    session.add(draft)
    if notice is not None:
        notice.raw_document_id = raw.id
        notice.review_status = "DRAFTED" if draft.status == "PENDING_REVIEW" else "DRAFT_FAILED"
    await session.flush()
    return draft

def _accepted(fields: Dict[str, Dict[str, Any]], name: str) -> Optional[Dict[str, Any]]:
    f = fields.get(name)
    return f if f and f.get("accepted") else None

# Verified quotes that can state how a post is filled, most specific first
_MODE_QUOTE_FIELDS = ("mode_of_selection", "post_name", "essential_qualification", "experience", "how_to_apply")

def _document_pages(raw: Optional[RawDocument]) -> List[PageText]:
    if raw is None or not raw.storage_path or not os.path.isfile(raw.storage_path):
        return []
    with open(raw.storage_path, "rb") as fh:
        # Text layer only: OCR takes minutes and approval is a request; scanned notices still get
        # their mode from the verified quotes
        return extract_pages(fh.read(), ocr=False)

def recruitment_mode(fields: Dict[str, Dict[str, Any]], doc_pages: List[PageText]) -> Optional[Tuple[ModeDetection, str]]:
    """Mode from the vacancy's own verified quotes, else from the whole notice; with where it was read."""
    for name in _MODE_QUOTE_FIELDS:
        f = _accepted(fields, name)
        found = detect_recruitment_mode(f["quote"]) if f and f.get("quote") else None
        if found:
            found.page = f["page"]
            return found, f"LLM-located quote ({name}), reviewer-approved; mode read by fixed rules"
    found = detect_in_pages(doc_pages) if doc_pages else None
    return (found, "notice text, mode read by fixed rules") if found else None

def _undecided(payload: Dict[str, Any]) -> List[int]:
    return [i for i, v in enumerate(payload["vacancies"]) if not v.get("decision")]

async def _close(session: AsyncSession, draft: ExtractionDraft, payload: Dict[str, Any]) -> None:
    """Every vacancy is decided: the draft leaves the queue and the notice is settled."""
    approved = any(v["decision"]["status"] == "APPROVED" for v in payload["vacancies"])
    draft.status, draft.reviewed_at = ("APPROVED" if approved else "REJECTED"), datetime.now(timezone.utc)
    if draft.notice_id:
        (await session.get(RecruitmentNotice, draft.notice_id)).review_status = "PARSED" if approved else "REJECTED"

async def approve(session: AsyncSession, draft: ExtractionDraft, reviewer: User,
                  vacancy_indexes: Optional[List[int]] = None, note: Optional[str] = None) -> List[Vacancy]:
    if not reviewer.is_reviewer:
        raise PermissionError("only reviewers can approve drafts")
    if draft.status != "PENDING_REVIEW":
        raise DraftError(f"draft is {draft.status}, not PENDING_REVIEW")
    payload = json.loads(draft.payload)
    source = await session.get(GovernmentSource, draft.source_id)
    raw_url = (await session.execute(select(RecruitmentNotice.url).where(RecruitmentNotice.id == draft.notice_id))).scalar_one_or_none() \
        if draft.notice_id else None
    indexes = vacancy_indexes if vacancy_indexes is not None else _undecided(payload)
    # Check every index before creating anything, so a bad request changes nothing
    if not indexes:
        raise DraftError("no vacancy selected; reject the draft if none of its vacancies is right")
    if len(set(indexes)) != len(indexes):
        raise DraftError(f"duplicate vacancy index in {indexes}")
    for i in indexes:
        if not 0 <= i < len(payload["vacancies"]):
            raise DraftError(f"vacancy index {i} out of range")
        if payload["vacancies"][i].get("decision"):
            raise DraftError(f"vacancy {i} is already {payload['vacancies'][i]['decision']['status']}")
        if _accepted(payload["vacancies"][i]["fields"], "post_name") is None:
            raise DraftError(f"vacancy {i} has no verified post name and cannot be approved")
    created: List[Vacancy] = []
    doc_pages = _document_pages(await session.get(RawDocument, draft.raw_document_id))
    for i in indexes:
        fields = payload["vacancies"][i]["fields"]
        post = _accepted(fields, "post_name")
        count = _accepted(fields, "vacancy_count")
        last = _accepted(fields, "last_date")
        pay = _accepted(fields, "pay_level")
        org = _accepted(fields, "organization")
        n_accepted = sum(1 for n in ALL_FIELDS if _accepted(fields, n))
        mode = recruitment_mode(fields, doc_pages)
        vacancy = Vacancy(
            recruitment_mode=mode[0].mode if mode else "UNKNOWN",
            id=uuid.uuid4(), organization=source.organization, department=(org["value"][:255] if org else None),
            post_name=post["value"][:255], vacancy_count=count["value"] if count else None,
            external_ref=f"draft:{draft.id}:{i}", pay_scale=pay["value"] if pay else None,
            application_mode="SEE_NOTICE", application_deadline=datetime.fromisoformat(last["value"]) if last else None,
            eligibility_crucial_date=None, notification_url=raw_url, source_url=source.recruitment_url, source_id=source.id,
            raw_document_id=draft.raw_document_id, parser_version=f"llm:{draft.prompt_version}:{draft.model}",
            confidence=round(n_accepted / len(ALL_FIELDS), 2),
        )
        session.add(vacancy)
        await session.flush()
        for name in ALL_FIELDS:
            f = _accepted(fields, name)
            if f:
                session.add(VacancyFieldProvenance(
                    vacancy_id=vacancy.id, field_name=name, value=str(f["value"]), raw_document_id=draft.raw_document_id,
                    page=f["page"], section=f"LLM-located ({f['match']}), reviewer-approved", excerpt=f["quote"][:2000],
                ))
        if mode:
            found, section = mode
            session.add(VacancyFieldProvenance(
                vacancy_id=vacancy.id, field_name="recruitment_mode", value=found.mode, raw_document_id=draft.raw_document_id,
                page=found.page, section=section, excerpt=found.excerpt,
            ))
            service = govt_service_requirement(found)
            if service:
                session.add(VacancyRequirement(
                    vacancy_id=vacancy.id, req_type="GOVT_SERVICE", value=json.dumps(service), is_mandatory=True,
                    raw_document_id=draft.raw_document_id, source_page=found.page, source_section=section,
                    source_excerpt=found.excerpt[:400],
                ))
        reqs = []
        for name, req_type, mandatory in (("essential_qualification", "EDUCATION", True), ("experience", "EXPERIENCE", True),
                                          ("mode_of_selection", "SELECTION_MODE", False), ("how_to_apply", "HOW_TO_APPLY", False)):
            f = _accepted(fields, name)
            if f:
                reqs.append((req_type, f["value"], mandatory, f))
        age = _accepted(fields, "age_limit")
        for limit in (age["value"] if age else []):
            reqs.append(("AGE_MAX", json.dumps(limit), True, age))
        for req_type, value, mandatory, f in reqs:
            session.add(VacancyRequirement(
                vacancy_id=vacancy.id, req_type=req_type, value=value, is_mandatory=mandatory,
                raw_document_id=draft.raw_document_id, source_page=f["page"], source_section="LLM-located, reviewer-approved",
                source_excerpt=f["quote"][:400],
            ))
        created.append(vacancy)
        payload["vacancies"][i]["decision"] = {"status": "APPROVED", "vacancy_id": str(vacancy.id), "reviewer_id": str(reviewer.id)}
    # The draft stays in the queue until every vacancy in it is approved or rejected
    draft.payload = json.dumps(payload, ensure_ascii=False)
    draft.reviewer_id, draft.review_note = reviewer.id, note or draft.review_note
    draft.created_vacancy_ids = json.dumps(json.loads(draft.created_vacancy_ids or "[]") + [str(v.id) for v in created])
    if not _undecided(payload):
        await _close(session, draft, payload)
    await session.flush()
    return created

async def reject(session: AsyncSession, draft: ExtractionDraft, reviewer: User, note: Optional[str]) -> ExtractionDraft:
    if not reviewer.is_reviewer:
        raise PermissionError("only reviewers can reject drafts")
    if draft.status != "PENDING_REVIEW":
        raise DraftError(f"draft is {draft.status}, not PENDING_REVIEW")
    # Rejects the vacancies not yet decided; ones already approved stay, and then the draft counts as approved
    payload = json.loads(draft.payload)
    for i in _undecided(payload):
        payload["vacancies"][i]["decision"] = {"status": "REJECTED", "reviewer_id": str(reviewer.id)}
    draft.payload = json.dumps(payload, ensure_ascii=False)
    draft.reviewer_id, draft.review_note = reviewer.id, note
    await _close(session, draft, payload)
    await session.flush()
    return draft

async def draft_pending_notices(session: AsyncSession, fetcher, provider, limit: int = 5,
                                source_names: Optional[List[str]] = None, commit_each: bool = False) -> List[ExtractionDraft]:
    """Create drafts for VACANCY PDF notices of sources without a deterministic parser.

    Triaged notices go highest priority first and STALE / NOT_REQUIRED ones are skipped (services/triage.py);
    notices not triaged yet follow, newest first."""
    stmt = (select(RecruitmentNotice, GovernmentSource).join(GovernmentSource, GovernmentSource.id == RecruitmentNotice.source_id)
            .outerjoin(NoticeTriage, NoticeTriage.notice_id == RecruitmentNotice.id)
            .where(RecruitmentNotice.notice_kind == "VACANCY", RecruitmentNotice.document_type == "PDF",
                   RecruitmentNotice.review_status == "MANUAL_REVIEW", GovernmentSource.adapter_name.is_(None),
                   or_(NoticeTriage.id.is_(None), NoticeTriage.triage_status.not_in(SKIP_FOR_DRAFTS)))
            .order_by(NoticeTriage.priority.desc().nulls_last(), RecruitmentNotice.first_seen_at.desc(),
                      RecruitmentNotice.url_hash).limit(limit))
    if source_names:
        stmt = stmt.where(GovernmentSource.name.in_(source_names))
    drafts = []
    for notice, source in (await session.execute(stmt)).all():
        policy = source_policy(source)
        try:
            if not await fetcher.robots_allows(notice.url, policy):
                notice.review_status = "ROBOTS_DISALLOWED"
                continue
            r = await fetcher.get(notice.url, policy)
        except UrlRefused:
            # Off-domain, non-https or non-public link (or redirect): never fetched, never evidence
            notice.review_status = "URL_REFUSED"
            continue
        if r.status_code != 200 or not r.content.startswith(b"%PDF"):
            notice.review_status = f"FETCH_FAILED_{r.status_code}"
            continue
        async with session.begin_nested():
            drafts.append(await create_draft(session, source, str(r.url), r.content, provider, notice))
        if commit_each:
            # LLM calls take minutes: keep finished drafts even if a later notice fails
            await session.commit()
    await session.flush()
    return drafts
