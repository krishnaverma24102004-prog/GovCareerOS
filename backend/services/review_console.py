"""
Reviewer console: one prioritised work queue, quote locations for side-by-side review, and bulk notice actions.

The queue mixes two kinds of work:
- DRAFT: an ExtractionDraft waiting for a human (PENDING_REVIEW);
- NOTICE: a RecruitmentNotice nobody has dealt with yet (MANUAL_REVIEW, NEW, DRAFT_FAILED, FETCH_FAILED_*).
A notice with a pending draft is DRAFTED, so it shows up once, as its draft.

Bulk actions only move notices between "needs attention" states and SKIPPED / NOT_REQUIRED; nothing here
creates a vacancy. Approval stays one draft at a time (routers/review.py), after a human has looked at it.
"""
import asyncio
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.documents.pdf_text import PageText, extract_pages
from govcareeros.backend.models.artifact import RawDocument
from govcareeros.backend.models.extraction import ExtractionDraft
from govcareeros.backend.models.notice import RecruitmentNotice
from govcareeros.backend.models.triage import NoticeTriage
from govcareeros.backend.models.source import GovernmentSource
from govcareeros.backend.models.user import User
from govcareeros.backend.services import review_audit

ATTENTION_STATUSES = ("MANUAL_REVIEW", "NEW", "DRAFT_FAILED")
FETCH_FAILED = "FETCH_FAILED"
URGENT_KINDS = ("VACANCY", "CORRIGENDUM")
# Notices whose outcome lives elsewhere: a vacancy exists, or a draft is waiting for its own decision
LOCKED_STATUSES = ("PARSED", "DRAFTED")
NOTICE_ACTIONS = {"SKIP": "SKIPPED", "NOT_REQUIRED": "NOT_REQUIRED", "REQUEUE": None}  # None: back to discovery's status
_PRIORITY_WORDS = {"CRITICAL": 0, "URGENT": 0, "HIGH": 1, "MEDIUM": 2, "NORMAL": 2, "LOW": 3}

class ConsoleError(ValueError):
    pass

def _priority_rank(value: Any) -> Optional[float]:
    """Smaller = sooner. A numeric priority is read as "higher number = more urgent"; words map to tiers."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return -float(value)
    return _PRIORITY_WORDS.get(str(value).strip().upper())

def _sort_key(item: Dict[str, Any]) -> Tuple:
    rank = _priority_rank(item.get("priority"))
    seen = item["first_seen_at"] or item.get("created_at")
    return (
        rank is None, rank or 0.0,                      # triage priority, when the column exists and is set
        item["notice_kind"] not in URGENT_KINDS,        # then vacancies and corrigenda
        -(seen.timestamp() if seen else 0.0),           # then newest first
        item["type"] != "DRAFT",                        # a ready draft before a bare notice of the same moment
        item["title"] or "",
    )

# Triage keeps its decision in notice_triage (higher priority = sooner); notices it has not seen yet fall back
# to kind/recency ordering. STALE and NOT_REQUIRED notices leave the default queue but stay listable by status.
TRIAGED_OUT = ("STALE", "NOT_REQUIRED")

def _status_clause(column, status: str):
    return column.like(f"{FETCH_FAILED}\\_%", escape="\\") if status == FETCH_FAILED else column == status

# What the source's listing adapter read from the notice's listing row (source_discovery), shown next to the
# notice with the listing text each value was read from and the listing page it is on
_LISTING_COLUMNS = (RecruitmentNotice.published_on, RecruitmentNotice.last_date, RecruitmentNotice.advertisement_number,
                    RecruitmentNotice.post_names, RecruitmentNotice.kind_hint, RecruitmentNotice.listing_evidence,
                    RecruitmentNotice.found_on_url)

def _listing(published_on, last_date, advertisement_number, post_names, kind_hint, evidence, found_on_url) -> Dict[str, Any]:
    return dict(published_on=published_on, last_date=last_date, advertisement_number=advertisement_number,
                post_names=json.loads(post_names) if post_names else [], kind_hint=kind_hint,
                listing_evidence=json.loads(evidence) if evidence else {}, found_on_url=found_on_url)

def _title_clause(q: str):
    return func.lower(RecruitmentNotice.title).contains(q.strip().lower(), autoescape=True)

async def review_queue(session: AsyncSession, *, source: Optional[str] = None, kind: Optional[str] = None,
                       status: Optional[str] = None, item_type: Optional[str] = None, q: Optional[str] = None,
                       limit: int = 50, offset: int = 0) -> Tuple[int, List[Dict[str, Any]]]:
    items: List[Dict[str, Any]] = []
    priority = NoticeTriage.priority
    if item_type in (None, "DRAFT") and status in (None, "PENDING_REVIEW"):
        kind_expr = func.coalesce(RecruitmentNotice.notice_kind, "VACANCY")
        stmt = (select(ExtractionDraft.id, ExtractionDraft.created_at, ExtractionDraft.payload, ExtractionDraft.fields_offered,
                       ExtractionDraft.fields_accepted, ExtractionDraft.model, ExtractionDraft.status, GovernmentSource.name,
                       RecruitmentNotice.id, RecruitmentNotice.title, RecruitmentNotice.url, kind_expr,
                       RecruitmentNotice.document_type, RecruitmentNotice.first_seen_at, priority, *_LISTING_COLUMNS)
                .join(GovernmentSource, GovernmentSource.id == ExtractionDraft.source_id)
                .outerjoin(RecruitmentNotice, RecruitmentNotice.id == ExtractionDraft.notice_id)
                .outerjoin(NoticeTriage, NoticeTriage.notice_id == RecruitmentNotice.id)
                .where(ExtractionDraft.status == "PENDING_REVIEW"))
        if source:
            stmt = stmt.where(GovernmentSource.name == source)
        if kind:
            stmt = stmt.where(kind_expr == kind)
        if q:
            stmt = stmt.where(_title_clause(q))
        for (did, created, payload, offered, accepted, model, st, src, nid, title, url, nkind, doc_type, seen,
             prio, *listing) in (await session.execute(stmt)).all():
            items.append(dict(type="DRAFT", id=did, draft_id=did, notice_id=nid, source=src, title=title, url=url,
                              notice_kind=nkind, document_type=doc_type, status=st, priority=prio, first_seen_at=seen,
                              created_at=created, vacancies=len(json.loads(payload).get("vacancies", [])),
                              fields_offered=offered, fields_accepted=accepted, model=model,
                              review_note=None, reviewed_at=None, **_listing(*listing)))
    if item_type in (None, "NOTICE") and status != "PENDING_REVIEW":
        stmt = (select(RecruitmentNotice.id, RecruitmentNotice.title, RecruitmentNotice.url, RecruitmentNotice.notice_kind,
                       RecruitmentNotice.document_type, RecruitmentNotice.review_status, RecruitmentNotice.first_seen_at,
                       RecruitmentNotice.review_note, RecruitmentNotice.reviewed_at, GovernmentSource.name, priority,
                       *_LISTING_COLUMNS)
                .join(GovernmentSource, GovernmentSource.id == RecruitmentNotice.source_id)
                .outerjoin(NoticeTriage, NoticeTriage.notice_id == RecruitmentNotice.id))
        if status:
            stmt = stmt.where(_status_clause(RecruitmentNotice.review_status, status))
        else:
            stmt = stmt.where(or_(RecruitmentNotice.review_status.in_(ATTENTION_STATUSES),
                                  _status_clause(RecruitmentNotice.review_status, FETCH_FAILED)),
                              or_(NoticeTriage.triage_status.is_(None), NoticeTriage.triage_status.not_in(TRIAGED_OUT)))
        if source:
            stmt = stmt.where(GovernmentSource.name == source)
        if kind:
            stmt = stmt.where(RecruitmentNotice.notice_kind == kind)
        if q:
            stmt = stmt.where(_title_clause(q))
        for nid, title, url, nkind, doc_type, st, seen, note, reviewed, src, prio, *listing in (await session.execute(stmt)).all():
            items.append(dict(type="NOTICE", id=nid, draft_id=None, notice_id=nid, source=src, title=title, url=url,
                              notice_kind=nkind, document_type=doc_type, status=st, priority=prio, first_seen_at=seen,
                              created_at=None, vacancies=None, fields_offered=None, fields_accepted=None, model=None,
                              review_note=note, reviewed_at=reviewed, **_listing(*listing)))
    items.sort(key=_sort_key)
    return len(items), items[offset:offset + limit]

async def queue_facets(session: AsyncSession) -> Dict[str, List[str]]:
    """Values for the console's filter dropdowns."""
    sources = (await session.execute(select(GovernmentSource.name).order_by(GovernmentSource.name))).scalars().all()
    kinds = (await session.execute(select(RecruitmentNotice.notice_kind).distinct())).scalars().all()
    return {"sources": list(sources), "kinds": sorted(set(kinds) | set(URGENT_KINDS)),
            "statuses": ["PENDING_REVIEW", *ATTENTION_STATUSES, FETCH_FAILED, "SKIPPED", "NOT_REQUIRED"]}

# --- quote locations --------------------------------------------------------------------------------------------

def _flat_with_offsets(text: str) -> Tuple[str, List[int]]:
    """The page text as validator.find_quote sees it (whitespace runs -> one space, trimmed), and for every
    character of it the offset of the character it came from in the original text."""
    chars, offsets, prev_space = [], [], True  # True: leading whitespace is dropped
    for i, ch in enumerate(text):
        if ch.isspace():
            if prev_space:
                continue
            chars.append(" ")
            offsets.append(i)
            prev_space = True
        else:
            chars.append(ch)
            offsets.append(i)
            prev_space = False
    if chars and chars[-1] == " ":
        chars.pop()
        offsets.pop()
    return "".join(chars), offsets

def locate(quote: str, page_text: str) -> Optional[Tuple[int, int]]:
    """(start, end) of the quote in the ORIGINAL page text, or None. The validator stores the document's own
    words with whitespace collapsed, so the search runs on the collapsed text and maps back."""
    flat, offsets = _flat_with_offsets(page_text)
    needle = " ".join(quote.split())
    if not needle:
        return None
    i = flat.find(needle)
    if i < 0 and len(flat.lower()) == len(flat):
        i = flat.lower().find(needle.lower())
    if i < 0:
        return None
    return offsets[i], offsets[i + len(needle) - 1] + 1

def quote_spans(payload: Dict[str, Any], pages: Sequence[PageText]) -> List[Dict[str, Any]]:
    """Vacancy drafts: one span per field quote (vacancy_index + field). Requirements drafts
    (services/requirement_drafts.py): one per item quote (requirement_index; field = "REQ_TYPE CODE")."""
    by_page = {p.page_number: p.text for p in pages}
    spans = []

    def span(f: Dict[str, Any], **where) -> None:
        quote, page = f.get("quote"), f.get("page")
        if not quote:
            return
        at = locate(quote, by_page[page]) if page in by_page else None
        spans.append(dict(**where, accepted=bool(f.get("accepted")), reason=f.get("reason"),
                          match=f.get("match"), page=page if at else None, start=at[0] if at else None,
                          end=at[1] if at else None, quote=quote, located=at is not None))

    for vi, vacancy in enumerate(payload.get("vacancies", [])):
        for name, f in vacancy.get("fields", {}).items():
            span(f, vacancy_index=vi, requirement_index=None, field=name)
    for ri, r in enumerate(payload.get("requirements", [])):
        # The validator stores a quote only once it is found on the cited page: an item refused for its value (e.g. a
        # fee the quote does not charge that category) is highlighted red, one whose quote is not there has no span
        span(r, vacancy_index=None, requirement_index=ri, field=f"{r.get('req_type')} {r.get('code')}")
    return spans

async def draft_pages(session: AsyncSession, draft: ExtractionDraft) -> Dict[str, Any]:
    raw = await session.get(RawDocument, draft.raw_document_id)
    if raw is None or not raw.storage_path or not os.path.isfile(raw.storage_path):
        raise ConsoleError("the draft's stored document is not available")
    with open(raw.storage_path, "rb") as fh:
        body = fh.read()
    # Same extraction (incl. OCR) the draft was validated against; OCR can take a while, so off the event loop
    pages = await asyncio.to_thread(extract_pages, body)
    return dict(draft_id=draft.id, raw_document_id=raw.id, document_url=raw.url, content_hash=raw.content_hash,
                pages=[dict(page_number=p.page_number, text=p.text, ocr=p.ocr) for p in pages],
                spans=quote_spans(json.loads(draft.payload), pages))

# --- bulk notice actions ----------------------------------------------------------------------------------------

async def bulk_notice_action(session: AsyncSession, reviewer: User, notice_ids: Sequence[uuid.UUID], action: str,
                             note: Optional[str]) -> List[RecruitmentNotice]:
    """All or nothing: any unknown or locked notice refuses the whole request."""
    if action not in NOTICE_ACTIONS:
        raise ConsoleError(f"unknown action {action!r}")
    ids = list(dict.fromkeys(notice_ids))
    rows = (await session.execute(
        select(RecruitmentNotice, GovernmentSource.adapter_name)
        .join(GovernmentSource, GovernmentSource.id == RecruitmentNotice.source_id)
        .where(RecruitmentNotice.id.in_(ids)))).all()
    found = {n.id: (n, adapter) for n, adapter in rows}
    missing = [str(i) for i in ids if i not in found]
    if missing:
        raise LookupError(f"notices not found: {', '.join(missing)}")
    locked = [f"{n.id} ({n.review_status})" for n, _ in found.values() if n.review_status in LOCKED_STATUSES]
    if locked:
        raise ConsoleError(f"notices already parsed or drafted, decide them through their draft: {', '.join(locked)}")
    now = datetime.now(timezone.utc)
    out = []
    for i in ids:
        notice, adapter = found[i]
        # Requeue = the status discovery gives a fresh notice: NEW for a parsed source, MANUAL_REVIEW otherwise
        target = NOTICE_ACTIONS[action] or ("NEW" if adapter else "MANUAL_REVIEW")
        before = notice.review_status
        notice.review_status, notice.reviewer_id, notice.reviewed_at, notice.review_note = target, reviewer.id, now, note
        review_audit.record(session, reviewer, f"NOTICE_{action}", "NOTICE", notice.id, note,
                            {"from": before, "to": target, "bulk": len(ids)}, now)
        out.append(notice)
    await session.flush()
    return out
