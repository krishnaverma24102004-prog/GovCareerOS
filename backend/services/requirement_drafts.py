"""
Notice of a source without a deterministic requirements parser -> LLM-located, validator-checked draft of what
it asks of an applicant -> reviewer approves chosen items -> ApplicationRequirement rows.

Uses the ExtractionDraft table and review queue; the payload is marked {"kind": "requirements"} and keeps
"vacancies": [] so the shared list code works unchanged. Nothing is approved automatically or in bulk: the reviewer
names each item, and only items the validator accepted (quote found on the cited page) can be approved.
"""
import json
import os
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.documents.pdf_text import PageText, extract_pages
from govcareeros.backend.eligibility.certificate_types import CertificateKind
from govcareeros.backend.extraction.requirements_extractor import RequirementsExtractionResult, extract_requirements
from govcareeros.backend.models.application import ApplicationRequirement
from govcareeros.backend.models.artifact import RawDocument
from govcareeros.backend.models.extraction import ExtractionDraft
from govcareeros.backend.models.notice import RecruitmentNotice
from govcareeros.backend.models.source import GovernmentSource
from govcareeros.backend.models.user import User
from govcareeros.backend.parsing import application_rules as ar
from govcareeros.backend.services.extraction_drafts import DraftError

def is_requirements_draft(draft: ExtractionDraft) -> bool:
    return json.loads(draft.payload).get("kind") == "requirements"

def serialize(result: RequirementsExtractionResult) -> Dict[str, Any]:
    return {
        "kind": "requirements", "vacancies": [], "requirements": [asdict(v) for v in result.requirements],
        "chunks": [{"pages": c.pages, "seconds": c.seconds, "prompt_tokens": c.prompt_tokens,
                    "output_tokens": c.output_tokens, "error": c.error} for c in result.chunks],
    }

async def create_requirements_draft(session: AsyncSession, source: GovernmentSource, raw: RawDocument, pages: List[PageText],
                                    provider, notice: Optional[RecruitmentNotice] = None) -> ExtractionDraft:
    """Draft for a stored document. The notice is only linked: its review status belongs to the vacancy draft."""
    result = await extract_requirements(pages, provider)
    offered = [v for v in result.requirements if v.reason not in ("missing", "no supporting quote")]
    errors = [c.error for c in result.chunks if c.error]
    draft = ExtractionDraft(
        id=uuid.uuid4(), source_id=source.id, notice_id=notice.id if notice else None, raw_document_id=raw.id,
        provider=provider.name, model=result.model, prompt_version=result.prompt_version,
        status="FAILED" if errors and not any(v.accepted for v in offered) else "PENDING_REVIEW",
        payload=json.dumps(serialize(result), ensure_ascii=False), fields_offered=len(offered),
        fields_accepted=sum(v.accepted for v in offered), llm_seconds=result.seconds, error="; ".join(errors) or None,
    )
    session.add(draft)
    await session.flush()
    return draft

def _open(payload: Dict[str, Any]) -> List[int]:
    """Accepted items nobody has decided on yet; rejected-by-validator items can never be approved."""
    return [i for i, r in enumerate(payload["requirements"]) if r["accepted"] and not r.get("decision")]

def _needs_closing_date(r: Dict[str, Any]) -> bool:
    """A certificate whose crucial date or issue limit is "the closing date": that date comes from the draft's
    APPLICATION_END item, not from this item's own quote."""
    if r["req_type"] != "CERTIFICATE":
        return False
    own, _ = ar.certificate_from_clauses(CertificateKind(r["code"]), [(r["page"], r["quote"])], None)
    return bool((own.crucial_date is None and r["value"].get("crucial_date"))
                or (own.issued_on_or_before is None and r["value"].get("issued_on_or_before")))

def _close(draft: ExtractionDraft, payload: Dict[str, Any]) -> None:
    approved = any((r.get("decision") or {}).get("status") == "APPROVED" for r in payload["requirements"])
    draft.status, draft.reviewed_at = ("APPROVED" if approved else "REJECTED"), datetime.now(timezone.utc)

async def approve_requirements_draft(session: AsyncSession, draft: ExtractionDraft, reviewer: User, item_indexes: Optional[List[int]],
                                     note: Optional[str] = None, advertisement_number: Optional[str] = None) -> List[ApplicationRequirement]:
    if not reviewer.is_reviewer:
        raise PermissionError("only reviewers can approve drafts")
    if draft.status != "PENDING_REVIEW":
        raise DraftError(f"draft is {draft.status}, not PENDING_REVIEW")
    if not item_indexes:
        raise DraftError("name the requirement items to approve; nothing is approved in bulk")
    payload = json.loads(draft.payload)
    items = payload["requirements"]
    if len(set(item_indexes)) != len(item_indexes):
        raise DraftError(f"duplicate item index in {item_indexes}")
    existing = {(t, c) for t, c in (await session.execute(select(ApplicationRequirement.req_type, ApplicationRequirement.code).where(
        ApplicationRequirement.raw_document_id == draft.raw_document_id))).all()}
    # Check every index before storing anything, so a bad request changes nothing
    for i in item_indexes:
        if not 0 <= i < len(items):
            raise DraftError(f"item index {i} out of range")
        r = items[i]
        if r.get("decision"):
            raise DraftError(f"item {i} is already {r['decision']['status']}")
        if not r["accepted"]:
            raise DraftError(f"item {i} was not verified ({r['reason']}) and cannot be approved")
        if (r["req_type"], r["code"]) in existing:
            raise DraftError(f"document already has a {r['req_type']} {r['code']} requirement")
    # A date read through "the closing date" is stored only with the closing date it came from, itself approved
    closing_ok = ("KEY_DATE", "APPLICATION_END") in existing or any(
        r["req_type"] == "KEY_DATE" and r["code"] == "APPLICATION_END" and (j in item_indexes or (r.get("decision") or {}).get("status") == "APPROVED")
        for j, r in enumerate(items))
    for i in item_indexes:
        if not closing_ok and _needs_closing_date(items[i]):
            raise DraftError(f"item {i} takes its date from the closing date; approve the APPLICATION_END key date with it")
    created = []
    section = "LLM-located ({}), reviewer-approved"
    for i in item_indexes:
        r = items[i]
        row = ApplicationRequirement(
            source_id=draft.source_id, raw_document_id=draft.raw_document_id, advertisement_number=advertisement_number,
            req_type=r["req_type"], code=r["code"], applies_when=r["applies_when"], value=json.dumps(r["value"], ensure_ascii=False),
            source_page=r["page"], source_section=section.format(r["match"]), source_excerpt=r["quote"],
            parser_version=f"llm:{draft.prompt_version}:{draft.model}",
        )
        session.add(row)
        created.append(row)
        r["decision"] = {"status": "APPROVED", "reviewer_id": str(reviewer.id)}
    draft.payload = json.dumps(payload, ensure_ascii=False)
    draft.reviewer_id, draft.review_note = reviewer.id, note or draft.review_note
    if not _open(payload):
        _close(draft, payload)
    await session.flush()
    return created

async def reject_requirements_draft(session: AsyncSession, draft: ExtractionDraft, reviewer: User, note: Optional[str]) -> ExtractionDraft:
    """Rejects the items not yet decided; items already approved stay, and then the draft counts as approved."""
    if not reviewer.is_reviewer:
        raise PermissionError("only reviewers can reject drafts")
    if draft.status != "PENDING_REVIEW":
        raise DraftError(f"draft is {draft.status}, not PENDING_REVIEW")
    payload = json.loads(draft.payload)
    for i in _open(payload):
        payload["requirements"][i]["decision"] = {"status": "REJECTED", "reviewer_id": str(reviewer.id)}
    draft.payload = json.dumps(payload, ensure_ascii=False)
    draft.reviewer_id, draft.review_note = reviewer.id, note
    _close(draft, payload)
    await session.flush()
    return draft

async def draft_requirements_for(session: AsyncSession, vacancy_drafts: List[ExtractionDraft], provider,
                                 commit_each: bool = False) -> List[ExtractionDraft]:
    """A requirements draft for the document of each vacancy draft (stored copy; nothing is fetched again),
    unless the document already has requirement rows or a requirements draft."""
    out = []
    for vd in vacancy_drafts:
        raw = await session.get(RawDocument, vd.raw_document_id)
        if raw is None or not raw.storage_path or not os.path.isfile(raw.storage_path):
            continue
        has_rows = (await session.execute(select(ApplicationRequirement.id).where(
            ApplicationRequirement.raw_document_id == raw.id).limit(1))).first()
        drafted = [d for d in (await session.execute(select(ExtractionDraft).where(
            ExtractionDraft.raw_document_id == raw.id))).scalars() if is_requirements_draft(d)]
        if has_rows or drafted:
            continue
        with open(raw.storage_path, "rb") as fh:
            pages = extract_pages(fh.read())
        source = await session.get(GovernmentSource, raw.source_id)
        notice = await session.get(RecruitmentNotice, vd.notice_id) if vd.notice_id else None
        async with session.begin_nested():
            out.append(await create_requirements_draft(session, source, raw, pages, provider, notice))
        if commit_each:
            await session.commit()
    return out
