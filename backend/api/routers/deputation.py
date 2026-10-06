"""
Deputation vacancy circulars (parsed from stored official PDFs) and a serving officer's assessment against one.
Circular data is public; the assessment reads the caller's own profile version and is not stored.
"""
import json
import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.api.deps import current_user, get_session
from govcareeros.backend.eligibility.engine import load_profile_snapshot
from govcareeros.backend.models.artifact import RawDocument
from govcareeros.backend.models.deputation import DeputationCircular
from govcareeros.backend.models.user import User
from govcareeros.backend.repositories.profile_repository import ProfileRepository
from govcareeros.backend.schemas.attribution import DISCLAIMER, AttributionOut
from govcareeros.backend.services import attribution
from govcareeros.backend.services import deputation_circulars as svc

router = APIRouter(prefix="/deputation", tags=["deputation"])

class QuotedOut(BaseModel):
    value: Any
    page: int
    quote: str

class CircularPostOut(BaseModel):
    index: int
    name: QuotedOut
    pay_level: Optional[QuotedOut] = None

class CircularSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    raw_document_id: uuid.UUID
    title: Optional[str]
    mode: List[str] = []
    closing_kind: Optional[str]
    closing_date: Optional[date]
    closing_days: Optional[int]
    closing: Optional[QuotedOut] = None
    status: str = svc.CLOSING_UNKNOWN
    post_count: int
    posts: List[CircularPostOut] = []
    parser_version: str
    parsed_at: datetime
    document_content_hash: Optional[str] = None
    attribution: Optional[AttributionOut] = None
    disclaimer: str = DISCLAIMER

class CircularDetail(CircularSummary):
    # Every parsed term with its page and the circular's words (parsing.deputation_circular.to_json + govt_service)
    terms: Dict[str, Any]

class AssessIn(BaseModel):
    profile_version_id: uuid.UUID
    post_index: Optional[int] = Field(None, ge=0, le=500)

class OutcomeOut(BaseModel):
    check: str
    outcome: str
    rule: str
    requirement: str
    profile_evidence: str
    source_page: Optional[int] = None
    note: Optional[str] = None
    data: Dict[str, Any] = {}

class ChecklistItemOut(BaseModel):
    code: str
    item: str
    page: int
    quote: str

class PostAssessmentOut(BaseModel):
    post_index: int
    post: str
    post_page: int
    pay_level: Optional[int]
    status: str
    decided_share: float
    outcomes: List[OutcomeOut]

class AssessmentOut(BaseModel):
    circular_id: uuid.UUID
    profile_version_id: uuid.UUID
    as_of: date
    closing_date: Optional[date]
    closing_status: str
    posts: List[PostAssessmentOut]
    forwarding_checklist: List[ChecklistItemOut]
    attribution: Optional[AttributionOut] = None
    disclaimer: str = DISCLAIMER

async def _summaries(session: AsyncSession, rows: List[DeputationCircular], model=CircularSummary) -> list:
    today = svc.today()
    hashes = dict((await session.execute(select(RawDocument.id, RawDocument.content_hash).where(
        RawDocument.id.in_([r.raw_document_id for r in rows])))).all()) if rows else {}
    out = []
    for r, a in zip(rows, await attribution.for_deputation_circulars(session, rows)):
        terms = json.loads(r.terms)
        posts = [CircularPostOut(index=i, name=p["name"], pay_level=p.get("pay_level")) for i, p in enumerate(terms.get("posts", []))]
        extra = {"terms": terms} if model is CircularDetail else {}
        out.append(model.model_validate({
            **{k: getattr(r, k) for k in ("id", "raw_document_id", "title", "closing_kind", "closing_date", "closing_days",
                                          "post_count", "parser_version", "parsed_at")},
            "mode": r.mode.split(",") if r.mode else [], "closing": terms.get("closing"), "status": svc.closing_status(r, today),
            "posts": posts, "document_content_hash": hashes.get(r.raw_document_id), "attribution": a, **extra}))
    return out

@router.get("/circulars", response_model=List[CircularSummary], dependencies=[Depends(current_user)])
async def list_circulars(open_only: bool = False, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0, le=1_000_000),
                         session: AsyncSession = Depends(get_session)):
    """Newest closing date first; circulars whose closing date depends on an Employment News issue come last."""
    stmt = select(DeputationCircular).order_by(DeputationCircular.closing_date.desc().nulls_last(), DeputationCircular.parsed_at.desc())
    if open_only:
        stmt = stmt.where(DeputationCircular.closing_date >= svc.today())
    rows = (await session.execute(stmt.limit(limit).offset(offset))).scalars().all()
    return await _summaries(session, list(rows))

async def _circular(session: AsyncSession, circular_id: uuid.UUID) -> DeputationCircular:
    row = await session.get(DeputationCircular, circular_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "deputation circular not found")
    return row

@router.get("/circulars/{circular_id}", response_model=CircularDetail, dependencies=[Depends(current_user)])
async def get_circular(circular_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    return (await _summaries(session, [await _circular(session, circular_id)], CircularDetail))[0]

@router.post("/circulars/{circular_id}/assess", response_model=AssessmentOut)
async def assess_circular(circular_id: uuid.UUID, body: AssessIn, user: User = Depends(current_user),
                          session: AsyncSession = Depends(get_session)):
    """Deterministic checks of the serving-officer fields of an explicit profile version against each post.
    Whether a post is analogous, the cooling-off and the feeder bar always go to a human (NEEDS_HUMAN_REVIEW)."""
    row = await _circular(session, circular_id)
    if await ProfileRepository(session).get_owned_version(user.id, body.profile_version_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "profile version not found")
    if body.post_index is not None and body.post_index >= row.post_count:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "post not found in this circular")
    as_of = svc.today()
    result = svc.assess(row, await load_profile_snapshot(session, body.profile_version_id), as_of, body.post_index)
    return AssessmentOut(
        circular_id=row.id, profile_version_id=body.profile_version_id, as_of=as_of, closing_date=result["closing_date"],
        closing_status=svc.closing_status(row, as_of),
        posts=[PostAssessmentOut(post_index=p["post_index"], post=p["post"], post_page=p["post_page"], pay_level=p["pay_level"],
                                 status=p["status"], decided_share=p["decided_share"],
                                 outcomes=[OutcomeOut(check=o.check, outcome=o.outcome, rule=o.rule, requirement=o.requirement,
                                                      profile_evidence=o.profile_evidence, source_page=o.source_page, note=o.note,
                                                      data=o.data) for o in p["outcomes"]])
               for p in result["posts"]],
        forwarding_checklist=result["forwarding_checklist"],
        attribution=(await attribution.for_deputation_circulars(session, [row]))[0])
