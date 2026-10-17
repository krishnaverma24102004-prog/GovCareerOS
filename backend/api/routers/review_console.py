"""
Reviewer console (reviewer-only): the prioritised work queue, document pages with located quotes for
side-by-side review, bulk notice actions, bulk draft rejection and profile-fact verification.

There is deliberately no bulk approve: a draft becomes vacancies only through POST /review/drafts/{id}/approve,
one draft at a time. Every decision here is appended to the review audit log.
"""
import uuid
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.api.deps import get_session
from govcareeros.backend.api.reviewer_deps import require_reviewer
from govcareeros.backend.models.artifact import RawDocument
from govcareeros.backend.models.extraction import ExtractionDraft
from govcareeros.backend.models.fact import Evidence, Fact
from govcareeros.backend.models.user import ProfileVersion, User
from govcareeros.backend.privacy import enforcement
from govcareeros.backend.services import extraction_drafts, review_audit, review_console

router = APIRouter(prefix="/review", tags=["review-console"])

MAX_PAGE = 200
MAX_BULK = 500

def _note(v: Optional[str]) -> Optional[str]:
    v = (v or "").strip()
    return v or None

class QueueItem(BaseModel):
    type: Literal["DRAFT", "NOTICE"]
    id: uuid.UUID
    draft_id: Optional[uuid.UUID]
    notice_id: Optional[uuid.UUID]
    source: str
    title: Optional[str]
    url: Optional[str]
    notice_kind: str
    document_type: Optional[str]
    status: str
    priority: Optional[Any]
    first_seen_at: Optional[datetime]
    created_at: Optional[datetime]
    vacancies: Optional[int]
    fields_offered: Optional[int]
    fields_accepted: Optional[int]
    model: Optional[str]
    review_note: Optional[str]
    reviewed_at: Optional[datetime]
    # From the source's listing row (listing adapter); None / empty where the listing states nothing.
    # listing_evidence: field -> the exact listing text it was read from, on the page found_on_url
    published_on: Optional[date] = None
    last_date: Optional[date] = None
    advertisement_number: Optional[str] = None
    post_names: List[str] = []
    kind_hint: Optional[str] = None
    listing_evidence: Dict[str, str] = {}
    found_on_url: Optional[str] = None

class QueuePage(BaseModel):
    total: int
    offset: int
    limit: int
    items: List[QueueItem]

@router.get("/queue", response_model=QueuePage)
async def queue(source: Optional[str] = None, kind: Optional[str] = None, status_filter: Optional[str] = Query(None, alias="status"),
                type: Optional[Literal["DRAFT", "NOTICE"]] = None, q: Optional[str] = Query(None, max_length=200),
                limit: int = Query(50, ge=1, le=MAX_PAGE), offset: int = Query(0, ge=0, le=1_000_000),
                user: User = Depends(require_reviewer), session: AsyncSession = Depends(get_session)):
    total, items = await review_console.review_queue(session, source=source or None, kind=kind or None,
                                                     status=status_filter or None, item_type=type, q=_note(q),
                                                     limit=limit, offset=offset)
    return QueuePage(total=total, offset=offset, limit=limit, items=items)

@router.get("/queue/facets")
async def queue_facets(user: User = Depends(require_reviewer), session: AsyncSession = Depends(get_session)) -> Dict[str, List[str]]:
    return await review_console.queue_facets(session)

@router.get("/drafts/{draft_id}/pages")
async def draft_pages(draft_id: uuid.UUID, user: User = Depends(require_reviewer), session: AsyncSession = Depends(get_session)):
    """The stored document's page texts and, for every quoted field, where its quote sits (start/end offsets
    into that page's text) so the console can highlight it next to the field's verdict."""
    draft = await session.get(ExtractionDraft, draft_id)
    if draft is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "draft not found")
    try:
        return await review_console.draft_pages(session, draft)
    except review_console.ConsoleError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))

class NoticeBulkIn(BaseModel):
    notice_ids: List[uuid.UUID] = Field(min_length=1, max_length=MAX_BULK)
    action: Literal["SKIP", "NOT_REQUIRED", "REQUEUE"]
    note: Optional[str] = Field(None, max_length=2000)

    @field_validator("note")
    @classmethod
    def _clean(cls, v: Optional[str]) -> Optional[str]:
        return _note(v)

class NoticeBulkOut(BaseModel):
    updated: int
    status: Dict[uuid.UUID, str]

@router.post("/notices/bulk", response_model=NoticeBulkOut)
async def notices_bulk(body: NoticeBulkIn, user: User = Depends(require_reviewer), session: AsyncSession = Depends(get_session)):
    try:
        notices = await review_console.bulk_notice_action(session, user, body.notice_ids, body.action, body.note)
    except LookupError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))
    except review_console.ConsoleError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    return NoticeBulkOut(updated=len(notices), status={n.id: n.review_status for n in notices})

class DraftBulkRejectIn(BaseModel):
    draft_ids: List[uuid.UUID] = Field(min_length=1, max_length=MAX_PAGE)
    note: str = Field(min_length=1, max_length=2000)

    @field_validator("note")
    @classmethod
    def _required(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("a note is required to reject")
        return v.strip()

@router.post("/drafts/bulk-reject")
async def drafts_bulk_reject(body: DraftBulkRejectIn, user: User = Depends(require_reviewer),
                             session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """All or nothing: every draft must exist and still be PENDING_REVIEW."""
    ids = list(dict.fromkeys(body.draft_ids))
    drafts = {d.id: d for d in (await session.execute(select(ExtractionDraft).where(ExtractionDraft.id.in_(ids)))).scalars()}
    missing = [str(i) for i in ids if i not in drafts]
    if missing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"drafts not found: {', '.join(missing)}")
    closed = [f"{d.id} ({d.status})" for d in drafts.values() if d.status != "PENDING_REVIEW"]
    if closed:
        raise HTTPException(status.HTTP_409_CONFLICT, f"drafts not pending review: {', '.join(closed)}")
    now = datetime.now(timezone.utc)
    for i in ids:
        await extraction_drafts.reject(session, drafts[i], user, body.note)
        review_audit.record(session, user, "DRAFT_REJECT", "DRAFT", i, body.note, {"to": drafts[i].status, "bulk": len(ids)}, now)
    await session.flush()
    return {"rejected": len(ids), "status": {str(i): drafts[i].status for i in ids}}

# --- profile facts (truth-locked resume) ------------------------------------------------------------------------

class EvidenceView(BaseModel):
    id: uuid.UUID
    evidence_type: str
    source: Optional[str]
    artifact_id: Optional[uuid.UUID]
    artifact_url: Optional[str]
    artifact_content_hash: Optional[str]
    artifact_content_type: Optional[str]
    created_at: datetime

class FactView(BaseModel):
    id: uuid.UUID
    profile_version_id: uuid.UUID
    version_number: int
    owner_id: uuid.UUID
    own: bool
    subject: str
    predicate: str
    value: str
    status: str
    reviewer_id: Optional[uuid.UUID]
    reviewed_at: Optional[datetime]
    review_note: Optional[str]
    created_at: datetime
    evidence: List[EvidenceView]

class FactPage(BaseModel):
    total: int
    offset: int
    limit: int
    items: List[FactView]

async def _evidence(session: AsyncSession, fact_ids: List[uuid.UUID]) -> Dict[uuid.UUID, List[EvidenceView]]:
    out: Dict[uuid.UUID, List[EvidenceView]] = {i: [] for i in fact_ids}
    if not fact_ids:
        return out
    rows = (await session.execute(
        select(Evidence, RawDocument).outerjoin(RawDocument, RawDocument.id == Evidence.artifact_id)
        .where(Evidence.fact_id.in_(fact_ids)).order_by(Evidence.created_at))).all()
    for e, doc in rows:
        out[e.fact_id].append(EvidenceView(
            id=e.id, evidence_type=e.evidence_type, source=e.source, artifact_id=e.artifact_id,
            artifact_url=doc.url if doc else None, artifact_content_hash=doc.content_hash if doc else None,
            artifact_content_type=doc.content_type if doc else None, created_at=e.created_at))
    return out

def _fact_view(fact: Fact, version: ProfileVersion, reviewer: User, evidence: List[EvidenceView]) -> FactView:
    return FactView(id=fact.id, profile_version_id=fact.profile_version_id, version_number=version.version_number,
                    owner_id=version.user_id, own=version.user_id == reviewer.id, subject=fact.subject,
                    predicate=fact.predicate, value=fact.value, status=fact.status, reviewer_id=fact.reviewer_id,
                    reviewed_at=fact.reviewed_at, review_note=fact.review_note, created_at=fact.created_at,
                    evidence=evidence)

def _reviewable():
    """(Fact, ProfileVersion) rows a reviewer may see and decide. Facts of a person who closed the account, asked for
    erasure or withdrew the consent the claim needs are neither listed nor decidable (404), as for a missing fact."""
    return (select(Fact, ProfileVersion).join(ProfileVersion, ProfileVersion.id == Fact.profile_version_id)
            .join(User, User.id == ProfileVersion.user_id)
            .where(enforcement.reviewable_fact_clause(Fact, ProfileVersion, User)))

@router.get("/facts", response_model=FactPage)
async def list_facts(status_filter: Literal["UNVERIFIED", "VERIFIED", "REJECTED"] = Query("UNVERIFIED", alias="status"),
                     limit: int = Query(50, ge=1, le=MAX_PAGE), offset: int = Query(0, ge=0, le=1_000_000),
                     user: User = Depends(require_reviewer), session: AsyncSession = Depends(get_session)):
    base = _reviewable().where(Fact.status == status_filter)
    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (await session.execute(base.order_by(Fact.created_at, Fact.id).offset(offset).limit(limit))).all()
    evidence = await _evidence(session, [f.id for f, _ in rows])
    return FactPage(total=total, offset=offset, limit=limit, items=[_fact_view(f, v, user, evidence[f.id]) for f, v in rows])

class FactDecisionIn(BaseModel):
    note: Optional[str] = Field(None, max_length=2000)

async def _decide(session: AsyncSession, reviewer: User, fact_id: uuid.UUID, target: str, note: Optional[str]) -> FactView:
    row = (await session.execute(_reviewable().where(Fact.id == fact_id))).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "fact not found")
    fact, version = row
    if version.user_id == reviewer.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "reviewers cannot verify or reject their own facts")
    allowed_from = ("UNVERIFIED",) if target == "VERIFIED" else ("UNVERIFIED", "VERIFIED")
    if fact.status not in allowed_from:
        raise HTTPException(status.HTTP_409_CONFLICT, f"fact is {fact.status}")
    evidence = (await _evidence(session, [fact.id]))[fact.id]
    if target == "VERIFIED" and not any(e.evidence_type == "DOCUMENT" and e.artifact_id for e in evidence):
        # A verified fact prints on resumes as true; it must have been checked against a stored document
        raise HTTPException(status.HTTP_409_CONFLICT, "no stored document attached as evidence; nothing to verify against")
    now = datetime.now(timezone.utc)
    before = fact.status
    fact.status, fact.reviewer_id, fact.reviewed_at, fact.review_note = target, reviewer.id, now, note
    if target == "VERIFIED":
        # APPROVED evidence is never user-settable (schemas/profile.EvidenceIn): it records this decision in the chain
        session.add(Evidence(fact_id=fact.id, evidence_type="APPROVED",
                             source=f"verified by reviewer {reviewer.id} at {now.isoformat()}" + (f": {note}" if note else "")))
    review_audit.record(session, reviewer, f"FACT_{'VERIFY' if target == 'VERIFIED' else 'REJECT'}", "FACT", fact.id, note,
                        {"from": before, "to": target}, now)
    await session.flush()
    return _fact_view(fact, version, reviewer, (await _evidence(session, [fact.id]))[fact.id])

@router.post("/facts/{fact_id}/verify", response_model=FactView)
async def verify_fact(fact_id: uuid.UUID, body: FactDecisionIn, user: User = Depends(require_reviewer),
                      session: AsyncSession = Depends(get_session)):
    return await _decide(session, user, fact_id, "VERIFIED", _note(body.note))

@router.post("/facts/{fact_id}/reject", response_model=FactView)
async def reject_fact(fact_id: uuid.UUID, body: FactDecisionIn, user: User = Depends(require_reviewer),
                      session: AsyncSession = Depends(get_session)):
    note = _note(body.note)
    if not note:
        raise HTTPException(422, "a note is required to reject a fact")
    return await _decide(session, user, fact_id, "REJECTED", note)
