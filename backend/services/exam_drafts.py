"""
Exam notice PDF the deterministic parser could not read -> LLM-located, validator-checked draft -> reviewer -> Exam.

Uses the ExtractionDraft table and review queue of vacancy drafts; the payload is marked
{"kind": "exam"} and keeps "vacancies": [] so the shared list/reject code works unchanged.
Only accepted fields reach the Exam, stored as the notice's own words or values parsed from them.
"""
import json
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.documents.pdf_text import extract_pages
from govcareeros.backend.extraction.exam_extractor import EXAM_FIELDS, ExamExtractionResult, extract_exam
from govcareeros.backend.models.exam import Exam, ExamFieldProvenance, ExamPost, ExamRequirement
from govcareeros.backend.models.extraction import ExtractionDraft
from govcareeros.backend.models.notice import RecruitmentNotice
from govcareeros.backend.models.source import GovernmentSource
from govcareeros.backend.models.user import User
from govcareeros.backend.parsing.ssc_exam_notice import exam_code_for, post_key
from govcareeros.backend.services.document_ingestion import store_raw_document
from govcareeros.backend.services.extraction_drafts import DraftError, _jsonable

def is_exam_draft(draft: ExtractionDraft) -> bool:
    return json.loads(draft.payload).get("kind") == "exam"

def _verdicts(fields) -> Dict[str, Any]:
    return {k: {**asdict(f), "value": _jsonable(f.value)} for k, f in fields.items()}

def serialize(result: ExamExtractionResult) -> Dict[str, Any]:
    return {
        "kind": "exam", "vacancies": [], "exam": _verdicts(result.exam),
        "posts": [{"fields": _verdicts(p.fields)} for p in result.posts],
        "chunks": [{"pages": c.pages, "seconds": c.seconds, "prompt_tokens": c.prompt_tokens,
                    "output_tokens": c.output_tokens, "error": c.error, "model": c.model} for c in result.chunks],
    }

async def create_exam_draft(session: AsyncSession, source: GovernmentSource, url: str, body: bytes, provider,
                            notice: Optional[RecruitmentNotice] = None) -> ExtractionDraft:
    pages = extract_pages(body)
    raw, _ = await store_raw_document(session, source, url, body, 200, "application/pdf",
                                      discovery_method="OCR" if any(p.ocr for p in pages) else "PDF")
    result = await extract_exam(pages, provider)
    verdicts = list(result.exam.values()) + [f for p in result.posts for f in p.fields.values()]
    offered = [f for f in verdicts if f.reason not in ("not stated", "missing")]
    errors = [c.error for c in result.chunks if c.error]
    draft = ExtractionDraft(
        id=uuid.uuid4(), source_id=source.id, notice_id=notice.id if notice else None, raw_document_id=raw.id,
        provider=provider.name, model=result.model, prompt_version=result.prompt_version,
        status="FAILED" if errors and not any(f.accepted for f in verdicts) else "PENDING_REVIEW",
        payload=json.dumps(serialize(result), ensure_ascii=False), fields_offered=len(offered),
        fields_accepted=sum(f.accepted for f in offered), llm_seconds=result.seconds, error="; ".join(errors) or None,
    )
    session.add(draft)
    if notice is not None:
        notice.raw_document_id = raw.id
        notice.review_status = "DRAFTED" if draft.status == "PENDING_REVIEW" else "DRAFT_FAILED"
    await session.flush()
    return draft

def _ok(fields: Dict[str, Dict[str, Any]], name: str) -> Optional[Dict[str, Any]]:
    f = fields.get(name)
    return f if f and f.get("accepted") else None

def _dt(f: Optional[Dict[str, Any]]) -> Optional[datetime]:
    return datetime.fromisoformat(f["value"]) if f else None

async def approve_exam_draft(session: AsyncSession, draft: ExtractionDraft, reviewer: User, note: Optional[str] = None) -> Exam:
    if not reviewer.is_reviewer:
        raise PermissionError("only reviewers can approve drafts")
    if draft.status != "PENDING_REVIEW":
        raise DraftError(f"draft is {draft.status}, not PENDING_REVIEW")
    payload = json.loads(draft.payload)
    fields = payload["exam"]
    name, last = _ok(fields, "exam_name"), _ok(fields, "last_date")
    if not name or not last:
        raise DraftError("the exam name and the last date must both be verified before approval")
    source = await session.get(GovernmentSource, draft.source_id)
    notice = await session.get(RecruitmentNotice, draft.notice_id) if draft.notice_id else None
    fee, schedule, edu = _ok(fields, "fee"), _ok(fields, "exam_schedule"), _ok(fields, "essential_qualification")
    section = "LLM-located ({}), reviewer-approved"
    exam = Exam(
        id=uuid.uuid4(), source_id=source.id, conducting_body=source.organization, name=name["value"][:500],
        exam_code=exam_code_for(name["value"]), external_ref=f"draft:{draft.id}", notification_url=notice.url if notice else None,
        notice_id=draft.notice_id, raw_document_id=draft.raw_document_id, parser_version=draft.parser_version,
        application_start=_dt(_ok(fields, "application_start")), application_end=_dt(last),
        age_crucial_date=_dt(_ok(fields, "age_crucial_date")), fee_amount=fee["value"] if fee else None,
        exam_dates=json.dumps([{"stage": "as stated", "schedule": schedule["value"]}], ensure_ascii=False) if schedule else None,
        education_text=edu["value"] if edu else None,
        confidence=round(sum(1 for n in EXAM_FIELDS if _ok(fields, n)) / len(EXAM_FIELDS), 2),
    )
    session.add(exam)
    await session.flush()
    for fname in EXAM_FIELDS:
        f = _ok(fields, fname)
        if f:
            session.add(ExamFieldProvenance(exam_id=exam.id, field_name=fname, value=str(f["value"]), raw_document_id=draft.raw_document_id,
                                            page=f["page"], section=section.format(f["match"]), excerpt=f["quote"][:2000]))

    def requirement(req_type, value, f, post_id=None):
        session.add(ExamRequirement(exam_id=exam.id, post_id=post_id, req_type=req_type, value=value, raw_document_id=draft.raw_document_id,
                                    source_page=f["page"], source_section=section.format(f["match"]), source_excerpt=f["quote"][:400]))

    age = _ok(fields, "age_limit")
    for limit in (age["value"] if age else []):
        requirement("AGE_MAX", json.dumps(limit), age)
    if edu:
        requirement("EDUCATION", edu["value"], edu)
    for p in payload.get("posts", []):
        pf = p["fields"]
        pname = _ok(pf, "post_name")
        if not pname:
            continue
        pay, page_, pedu = _ok(pf, "pay_level"), _ok(pf, "age_limit"), _ok(pf, "essential_qualification")
        limits = page_["value"] if page_ else []
        post = ExamPost(id=uuid.uuid4(), exam_id=exam.id, post_key=post_key(pname["value"]), post_name=pname["value"][:500],
                        pay_level=pay["value"][:200] if pay else None, age_min=limits[0].get("min_age") if len(limits) == 1 else None,
                        age_max=limits[0]["max_age"] if len(limits) == 1 else None, education_text=pedu["value"] if pedu else None,
                        raw_document_id=draft.raw_document_id, source_page=pname["page"], source_excerpt=pname["quote"][:400])
        session.add(post)
        await session.flush()
        for limit in limits:
            requirement("AGE_MAX", json.dumps(limit), page_, post.id)
        if pedu:
            requirement("EDUCATION", pedu["value"], pedu, post.id)
    payload["exam_decision"] = {"status": "APPROVED", "exam_id": str(exam.id), "reviewer_id": str(reviewer.id)}
    draft.payload = json.dumps(payload, ensure_ascii=False)
    draft.status, draft.reviewed_at = "APPROVED", datetime.now(timezone.utc)
    draft.reviewer_id, draft.review_note = reviewer.id, note or draft.review_note
    if notice is not None:
        notice.review_status = "PARSED"
    await session.flush()
    return exam
