"""
B2B verified-notice API, version 1 (API key in X-API-Key; docs/api/README.md).

    GET  /api/v1/notices              notices seen on official sources, filtered, cursor-paginated   notices:read
    GET  /api/v1/notices/{id}         one notice with attribution and parsed requirements (page, quote)  notices:read
    GET  /api/v1/changes?since=       event feed: new notices and field-level official changes      changes:read
    POST /api/v1/verify               the public notice check                                         verify:use
    /api/v1/webhooks                  register / list / remove webhooks, read their delivery log     webhooks:manage
    GET  /api/v1/openapi.json         the OpenAPI document of these endpoints only (no key needed)

Everything served is read from stored official documents; the official notification prevails.
"""
import json
import uuid
from datetime import datetime
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.api.b2b_deps import ApiCaller, require_scope
from govcareeros.backend.api.deps import get_session
from govcareeros.backend.api.routers.verify import VerifyOut, run_check
from govcareeros.backend.models.b2b import ApiEvent, Webhook, WebhookDelivery
from govcareeros.backend.models.notice import RecruitmentNotice
from govcareeros.backend.schemas.b2b import (
    ChangeEventOut, ChangePage, DeliveryOut, NoticeDetailOut, NoticePage, WebhookCreatedOut, WebhookIn, WebhookOut,
)
from govcareeros.backend.services import b2b_events, b2b_notices, notice_verifier, webhooks

PREFIX = "/api/v1"
# 422 as a number: its Starlette constant name changed across versions
router = APIRouter(prefix=PREFIX)

def _bad_cursor(e: Exception) -> HTTPException:
    return HTTPException(422, str(e))

@router.get("/notices", response_model=NoticePage, tags=["v1: notices"])
async def list_notices(
    source: Optional[str] = Query(None, max_length=100, description="source name, e.g. SSC, UPSC, HPSC"),
    jurisdiction: Optional[str] = Query(None, pattern=r"^IN(-[A-Z]{2})?$", description='"IN" (Union) or a state code such as "IN-HR"'),
    kind: Optional[str] = Query(None, max_length=30, description="VACANCY, CORRIGENDUM, EXAM_NOTICE, RESULT, ..."),
    status_: Optional[Literal["open", "closed"]] = Query(
        None, alias="status", description="open: closing date today or later (IST); closed: before today. Notices "
                                          "without a stated closing date match neither"),
    updated_since: Optional[datetime] = Query(None, description="notices whose record changed at or after this time "
                                                                "(ISO 8601; UTC when no offset). A re-crawl that sees the notice again counts"),
    cursor: Optional[str] = Query(None, max_length=500, description="next_cursor of the previous page"),
    limit: int = Query(50, ge=1, le=200),
    caller: ApiCaller = Depends(require_scope("notices:read", "notices.list")),
    session: AsyncSession = Depends(get_session),
):
    try:
        rows, nxt = await b2b_notices.list_notices(
            session, source=source, jurisdiction=jurisdiction, kind=kind, status=status_,
            updated_since=b2b_notices.utc(updated_since) if updated_since else None, cursor=cursor, limit=limit)
    except b2b_notices.BadCursor as e:
        raise _bad_cursor(e) from e
    return NoticePage(items=await b2b_notices.notice_summaries(session, rows), next_cursor=nxt)

@router.get("/notices/{notice_id}", response_model=NoticeDetailOut, tags=["v1: notices"])
async def get_notice(notice_id: uuid.UUID, caller: ApiCaller = Depends(require_scope("notices:read", "notices.get")),
                     session: AsyncSession = Depends(get_session)):
    notice = await session.get(RecruitmentNotice, notice_id)
    if notice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "notice not found")
    return await b2b_notices.notice_detail(session, notice)

@router.get("/changes", response_model=ChangePage, tags=["v1: changes"])
async def changes(
    since: Optional[str] = Query(None, max_length=500, description="cursor of the last event read (next_cursor); omit to start at the beginning"),
    type_: Optional[List[Literal["notice.new", "notice.changed"]]] = Query(None, alias="type", description="repeatable; default both"),
    limit: int = Query(100, ge=1, le=500),
    caller: ApiCaller = Depends(require_scope("changes:read", "changes.list")),
    session: AsyncSession = Depends(get_session),
):
    """Events in the order they were recorded. Keep next_cursor and poll with it; nothing recorded later is skipped."""
    try:
        after = b2b_events.feed_seq(since)
    except b2b_notices.BadCursor as e:
        raise _bad_cursor(e) from e
    events = await b2b_events.events_after(session, after, type_ or b2b_events.EVENT_TYPES, limit)
    items = [ChangeEventOut(cursor=b2b_events.feed_cursor(e.seq), type=e.event_type, created_at=e.created_at, notice_id=e.notice_id,
                            vacancy_id=e.vacancy_id, exam_id=e.exam_id, version_id=e.version_id, data=json.loads(e.payload))
             for e in events]
    return ChangePage(items=items, next_cursor=items[-1].cursor if items else b2b_events.feed_cursor(after))

@router.post("/verify", response_model=VerifyOut, tags=["v1: verify"])
async def verify(
    text: Optional[str] = Form(None, max_length=notice_verifier.MAX_TEXT_CHARS),
    url: Optional[str] = Form(None, max_length=2000),
    file: Optional[UploadFile] = File(None, description="the notice as a PDF, up to 10 MB"),
    ocr: bool = Form(True, description="read up to a few scanned pages of the PDF by OCR (slower)"),
    caller: ApiCaller = Depends(require_scope("verify:use", "verify")),
    session: AsyncSession = Depends(get_session),
):
    """Same check as the public POST /verify: is this notice, link or PDF on an official source? The link is never fetched."""
    return await run_check(session, text, url, file, ocr)

# ---------- webhooks ----------

def _hook_out(h: Webhook) -> WebhookOut:
    return WebhookOut(id=h.id, url=h.url, events=webhooks.events_of(h), created_at=h.created_at, disabled_at=h.disabled_at)

async def _own_hook(session: AsyncSession, caller: ApiCaller, webhook_id: uuid.UUID) -> Webhook:
    hook = await session.get(Webhook, webhook_id)
    if hook is None or hook.org_id != caller.org.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "webhook not found")
    return hook

@router.post("/webhooks", response_model=WebhookCreatedOut, status_code=status.HTTP_201_CREATED, tags=["v1: webhooks"])
async def register_webhook(body: WebhookIn, caller: ApiCaller = Depends(require_scope("webhooks:manage", "webhooks.create")),
                           session: AsyncSession = Depends(get_session)):
    """An https URL on a public host. The signing secret is in this response only."""
    try:
        hook, secret = await webhooks.register(session, caller.org, body.url.strip(), body.events)
    except webhooks.WebhookUrlRefused as e:
        raise HTTPException(422, str(e)) from e
    await session.refresh(hook)
    return WebhookCreatedOut(**_hook_out(hook).model_dump(), secret=secret)

@router.get("/webhooks", response_model=List[WebhookOut], tags=["v1: webhooks"])
async def list_webhooks(caller: ApiCaller = Depends(require_scope("webhooks:manage", "webhooks.list")),
                        session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(Webhook).where(Webhook.org_id == caller.org.id).order_by(Webhook.created_at, Webhook.id))).scalars()
    return [_hook_out(h) for h in rows]

@router.delete("/webhooks/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["v1: webhooks"])
async def remove_webhook(webhook_id: uuid.UUID, caller: ApiCaller = Depends(require_scope("webhooks:manage", "webhooks.delete")),
                         session: AsyncSession = Depends(get_session)):
    await webhooks.disable(session, await _own_hook(session, caller, webhook_id))

@router.get("/webhooks/{webhook_id}/deliveries", response_model=List[DeliveryOut], tags=["v1: webhooks"])
async def deliveries(webhook_id: uuid.UUID, limit: int = Query(100, ge=1, le=500),
                     caller: ApiCaller = Depends(require_scope("webhooks:manage", "webhooks.deliveries")),
                     session: AsyncSession = Depends(get_session)):
    """The delivery log, newest event first."""
    hook = await _own_hook(session, caller, webhook_id)
    rows = (await session.execute(select(WebhookDelivery, ApiEvent.event_type).join(ApiEvent, ApiEvent.seq == WebhookDelivery.event_seq)
                                  .where(WebhookDelivery.webhook_id == hook.id).order_by(WebhookDelivery.event_seq.desc()).limit(limit))).all()
    return [DeliveryOut(id=d.id, event_seq=d.event_seq, event_type=t, status=d.status, attempts=d.attempts,
                        next_attempt_at=d.next_attempt_at, last_attempt_at=d.last_attempt_at, response_status=d.response_status,
                        error=d.error, delivered_at=d.delivered_at) for d, t in rows]

@router.get("/openapi.json", include_in_schema=False)
async def openapi_v1(request: Request):
    """The OpenAPI document of /api/v1 only, for API customers (cut from the app's own document)."""
    full = request.app.openapi()
    return {**full, "info": {"title": "GovCareerOS verified-notice API", "version": "1", "description": (
                "Official recruitment notices with their source, document, page and quoted words. "
                "The official notification prevails. See docs/api/README.md.")},
            "paths": {p: v for p, v in full["paths"].items() if p.startswith(PREFIX)}}
