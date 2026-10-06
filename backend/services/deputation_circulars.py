"""
Stored official PDFs -> DeputationCircular rows, and a serving officer's assessment against one.

  * Only documents the recruitment-mode detector reads as DEPUTATION (backend/parsing/recruitment_mode.py) are
    stored as circulars; anything else is skipped with its reason, never forced into the deputation shape.
  * A document already parsed by the current PARSER_VERSION is left alone; an older parse is replaced.
  * The assessment is the deterministic eligibility.deputation.assess on the parsed terms and one profile version;
    nothing is decided by an LLM and the analogous-post question always goes to a human (NEEDS_HUMAN_REVIEW).
"""
import json
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.documents.pdf_text import extract_pages
from govcareeros.backend.eligibility import deputation as dep
from govcareeros.backend.eligibility.rules import Requirement
from govcareeros.backend.models.artifact import RawDocument
from govcareeros.backend.models.deputation import DeputationCircular
from govcareeros.backend.models.notice import RecruitmentNotice
from govcareeros.backend.parsing import deputation_circular as parsing
from govcareeros.backend.parsing.recruitment_mode import detect_in_pages, govt_service_requirement
from govcareeros.backend.parsing.upsc_advertisement import IST

OPEN, CLOSED, CLOSING_UNKNOWN = "OPEN", "CLOSED", "CLOSING_DATE_UNKNOWN"

@dataclass
class ParseRun:
    stored: List[uuid.UUID] = field(default_factory=list)
    unchanged: List[uuid.UUID] = field(default_factory=list)
    skipped: Dict[uuid.UUID, str] = field(default_factory=dict)  # raw_document_id -> why

def today() -> date:
    return datetime.now(IST).date()

def closing_status(row: DeputationCircular, as_of: date) -> str:
    """OPEN through the closing day itself; an Employment-News closing (date depends on the issue) or none stated is unknown."""
    if row.closing_date is None:
        return CLOSING_UNKNOWN
    return OPEN if as_of <= row.closing_date else CLOSED

def _read(raw: RawDocument) -> Optional[bytes]:
    path = Path(raw.storage_path) if raw.storage_path else None
    return path.read_bytes() if path and path.is_file() else None

async def parse_raw_document(session: AsyncSession, raw: RawDocument, body: Optional[bytes] = None, run: Optional[ParseRun] = None) -> Optional[DeputationCircular]:
    """Parse one stored document into its DeputationCircular row; None (with the reason in `run`) when it is not one."""
    run = run if run is not None else ParseRun()
    existing = (await session.execute(select(DeputationCircular).where(
        DeputationCircular.raw_document_id == raw.id))).scalar_one_or_none()
    if existing is not None and existing.parser_version == parsing.PARSER_VERSION:
        run.unchanged.append(raw.id)
        return existing
    body = body if body is not None else _read(raw)
    if body is None:
        run.skipped[raw.id] = "stored file missing"
        return None
    if not body.startswith(b"%PDF"):
        run.skipped[raw.id] = "not a PDF"
        return None
    pages = extract_pages(body)
    if not any(p.text.strip() for p in pages):
        run.skipped[raw.id] = "no text could be read"
        return None
    detection = detect_in_pages(pages)
    if detection is None or detection.mode != "DEPUTATION":
        run.skipped[raw.id] = f"recruitment mode {detection.mode if detection else 'not stated'}, not DEPUTATION"
        return None
    circular = parsing.parse_circular(pages)
    terms = parsing.to_json(circular)
    terms["govt_service"] = {"value": govt_service_requirement(detection), "page": detection.page, "quote": detection.excerpt}
    closing = circular.closing.value if circular.closing else None
    title = (await session.execute(select(RecruitmentNotice.title).where(RecruitmentNotice.raw_document_id == raw.id)
                                   .order_by(RecruitmentNotice.first_seen_at).limit(1))).scalar_one_or_none()
    row = existing or DeputationCircular(id=uuid.uuid4(), raw_document_id=raw.id)
    row.title, row.parser_version = title, parsing.PARSER_VERSION
    row.mode = ",".join(circular.mode.value) if circular.mode else None
    row.closing_kind = closing["kind"] if closing else None
    row.closing_date = closing.get("date") if closing else None
    row.closing_days = closing.get("days") if closing else None
    row.post_count, row.terms = len(circular.posts), json.dumps(terms, ensure_ascii=False)
    row.parsed_at = datetime.now(timezone.utc)
    if existing is None:
        session.add(row)
    await session.flush()
    run.stored.append(raw.id)
    return row

async def parse_stored(session: AsyncSession, raw_document_ids: Optional[Sequence[uuid.UUID]] = None,
                       source_id: Optional[uuid.UUID] = None, limit: Optional[int] = None) -> ParseRun:
    """Parse stored PDFs (the given ids, else every stored PDF not yet parsed by this parser version)."""
    stmt = select(RawDocument).where(RawDocument.storage_path.is_not(None)).order_by(RawDocument.retrieved_at, RawDocument.id)
    if raw_document_ids:
        stmt = stmt.where(RawDocument.id.in_(list(raw_document_ids)))
    else:
        done = select(DeputationCircular.raw_document_id).where(DeputationCircular.parser_version == parsing.PARSER_VERSION)
        stmt = stmt.where(RawDocument.id.not_in(done))
    if source_id:
        stmt = stmt.where(RawDocument.source_id == source_id)
    if limit:
        stmt = stmt.limit(limit)
    run = ParseRun()
    for raw in (await session.execute(stmt)).scalars().all():
        await parse_raw_document(session, raw, run=run)
    return run

def load_terms(row: DeputationCircular):
    """(parsed circular, GOVT_SERVICE requirement or None, the stored JSON) from a row's terms."""
    terms = json.loads(row.terms)
    circular = parsing.from_json(terms)
    gs = terms.get("govt_service") or {}
    req = Requirement(f"deputation_circular/{row.id}/govt_service", "GOVT_SERVICE", gs["value"], gs.get("page")) if gs.get("value") else None
    return circular, req, terms

def assess(row: DeputationCircular, profile, as_of: date, post_index: Optional[int] = None) -> Dict[str, Any]:
    """Every post of the circular (or the one at post_index) judged for the profile, and the circular's own checklist."""
    circular, govt_service, _ = load_terms(row)
    posts = [(i, p) for i, p in enumerate(circular.posts) if post_index is None or i == post_index]
    out = []
    for i, post in posts:
        res = dep.assess_post(profile, circular, post, as_of=as_of, govt_service=govt_service)
        out.append({**res, "post_index": i, "post_page": post.name.page,
                    "pay_level": post.pay_level.value if post.pay_level else None})
    closing = dep.closing_date(circular)
    return {"closing_date": closing, "posts": out, "forwarding_checklist": dep.forwarding_checklist(circular)}
