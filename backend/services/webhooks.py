"""
Webhooks: an org's https endpoints, told about notice.new / notice.changed events (services/b2b_events.py).

Never to a private address. A URL is refused at registration unless it is https, names a public host (no IP
literal or name that is loopback, private, link-local, ...; no single-label or .local/.internal names) and carries no
user:password. At delivery the host name is resolved and the connection refused when any address it resolves to is
non-public (url_guard.GuardedTransport, the guard every outbound fetch of the project uses), and redirects are not
followed, so a public URL cannot bounce a delivery inside. Refused deliveries are marked REFUSED and never retried.

Signature: every delivery carries
    X-GovCareerOS-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256(secret, "<t>." + raw body)>
`verify_signature` is the receiver-side check (also documented in docs/api/README.md).

Retries: a delivery that gets no 2xx answer is tried again after 1 min, 5 min, 30 min, 2 h and 6 h, then marked
FAILED. Every attempt is in webhook_deliveries (status, attempts, HTTP status, error). Deliveries of one webhook may
arrive out of order when one is retried; each body carries the event's `seq`.
"""
import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.models.b2b import ApiEvent, ApiOrg, Webhook, WebhookDelivery
from govcareeros.backend.services import b2b_events
from govcareeros.backend.sources.url_guard import GuardedTransport, UrlRefused, literal_non_public

SIGNATURE_HEADER = "X-GovCareerOS-Signature"
EVENT_HEADER = "X-GovCareerOS-Event"
DELIVERY_HEADER = "X-GovCareerOS-Delivery"
USER_AGENT = "GovCareerOS-Webhooks/1.0"
BACKOFF = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=30), timedelta(hours=2), timedelta(hours=6))
MAX_ATTEMPTS = len(BACKOFF) + 1
TIMEOUT_SECONDS = 10
MAX_WEBHOOKS_PER_ORG = 10
_PRIVATE_SUFFIXES = (".local", ".internal", ".intranet", ".lan", ".home", ".corp", ".home.arpa", ".localhost")

class WebhookUrlRefused(ValueError):
    pass

def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False

def url_refusal(url: str) -> Optional[str]:
    """Why this URL may not receive webhooks, or None. Names are resolved and checked again at every delivery."""
    if len(url) > 2000:
        return "URL is longer than 2000 characters"
    try:
        parts = urlparse(url)
        parts.port  # raises on a malformed port
    except ValueError:
        return "not a valid URL"
    if parts.scheme != "https":
        return "only https URLs are allowed"
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        return "the URL may not carry a user name or password"
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        return "the URL has no host"
    if literal_non_public(url):
        return f"{host} is not a public address"
    if re.fullmatch(r"[0-9.]+|0x[0-9a-f.x]+", host) and not _is_ip(host):  # 127.1, 2130706433, 0x7f.1: IPv4 spellings
        return f"{host} is not a public host name"                            # that ipaddress does not read
    if "." not in host or host.endswith(_PRIVATE_SUFFIXES):
        return f"{host} is not a public host name"
    return None

def check_url(url: str) -> None:
    reason = url_refusal(url)
    if reason:
        raise WebhookUrlRefused(reason)

def new_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(32)

def sign(secret: str, timestamp: int, body: bytes) -> str:
    return hmac.new(secret.encode(), str(timestamp).encode() + b"." + body, hashlib.sha256).hexdigest()

def signature_header(secret: str, timestamp: int, body: bytes) -> str:
    return f"t={timestamp},v1={sign(secret, timestamp, body)}"

def verify_signature(secret: str, header: str, body: bytes, tolerance_seconds: int = 300, now: Optional[float] = None) -> bool:
    """Receiver side: the header's v1 matches the body and its timestamp is within the tolerance (replays refused)."""
    try:
        fields = dict(part.split("=", 1) for part in header.split(","))
        ts = int(fields["t"])
    except (ValueError, KeyError):
        return False
    if abs((now if now is not None else time.time()) - ts) > tolerance_seconds:
        return False
    return hmac.compare_digest(fields.get("v1", ""), sign(secret, ts, body))

def events_of(webhook: Webhook) -> List[str]:
    return webhook.events.split()

async def register(session: AsyncSession, org: ApiOrg, url: str, events: Iterable[str]) -> Tuple[Webhook, str]:
    """The new webhook and its signing secret (returned once). Only events recorded from now on are delivered."""
    check_url(url)
    wanted = sorted(set(events))
    if not wanted or any(e not in b2b_events.EVENT_TYPES for e in wanted):
        raise WebhookUrlRefused(f"events must be among {list(b2b_events.EVENT_TYPES)}")
    active = (await session.execute(select(func.count()).select_from(Webhook).where(
        Webhook.org_id == org.id, Webhook.disabled_at.is_(None)))).scalar_one()
    if active >= MAX_WEBHOOKS_PER_ORG:
        raise WebhookUrlRefused(f"an organisation may register at most {MAX_WEBHOOKS_PER_ORG} webhooks")
    secret = new_secret()
    hook = Webhook(org_id=org.id, url=url, events=" ".join(wanted), secret=secret, after_seq=await b2b_events.last_seq(session))
    session.add(hook)
    await session.flush()
    return hook, secret

async def disable(session: AsyncSession, hook: Webhook, now: Optional[datetime] = None) -> None:
    """No further deliveries; pending ones are CANCELLED (the log keeps them)."""
    now = now or datetime.now(timezone.utc)
    hook.disabled_at = hook.disabled_at or now
    for d in (await session.execute(select(WebhookDelivery).where(
            WebhookDelivery.webhook_id == hook.id, WebhookDelivery.status == "PENDING"))).scalars():
        d.status, d.next_attempt_at, d.error = "CANCELLED", None, "webhook removed"
    await session.flush()

async def enqueue(session: AsyncSession, now: Optional[datetime] = None) -> int:
    """A PENDING delivery for each active webhook and each event after the last one it was given."""
    now = now or datetime.now(timezone.utc)
    hooks = (await session.execute(select(Webhook).join(ApiOrg, ApiOrg.id == Webhook.org_id).where(
        Webhook.disabled_at.is_(None), ApiOrg.is_active.is_(True)))).scalars().all()
    added = 0
    for hook in hooks:
        given = (await session.execute(select(func.max(WebhookDelivery.event_seq)).where(
            WebhookDelivery.webhook_id == hook.id))).scalar_one()
        after = max(given or 0, hook.after_seq or 0)
        while True:
            events = await b2b_events.events_after(session, after, events_of(hook), limit=500)
            if not events:
                break
            for e in events:
                session.add(WebhookDelivery(webhook_id=hook.id, event_seq=e.seq, status="PENDING", attempts=0, next_attempt_at=now))
            added += len(events)
            after = events[-1].seq
            await session.flush()
    return added

def make_client(resolve=None) -> httpx.AsyncClient:
    """Resolves every host and refuses non-public addresses; never follows redirects."""
    transport = GuardedTransport(resolve=resolve) if resolve else GuardedTransport()
    return httpx.AsyncClient(transport=transport, follow_redirects=False, timeout=TIMEOUT_SECONDS,
                             headers={"User-Agent": USER_AGENT})

def body_of(delivery: WebhookDelivery, event: ApiEvent) -> bytes:
    return json.dumps({"id": str(delivery.id), "type": event.event_type, "seq": event.seq,
                       "cursor": b2b_events.feed_cursor(event.seq), "created_at": event.created_at.isoformat(),
                       "data": json.loads(event.payload)}, ensure_ascii=False, separators=(",", ":")).encode()

def _failed(d: WebhookDelivery, now: datetime, error: str, status: Optional[int] = None) -> None:
    d.response_status, d.error = status, error[:1000]
    if d.attempts >= MAX_ATTEMPTS:
        d.status, d.next_attempt_at = "FAILED", None
    else:
        d.next_attempt_at = now + BACKOFF[d.attempts - 1]

async def deliver_due(session: AsyncSession, client: Optional[httpx.AsyncClient] = None, now: Optional[datetime] = None,
                      limit: int = 200) -> Dict[str, int]:
    """Send every PENDING delivery whose time has come. Counts by outcome."""
    now = now or datetime.now(timezone.utc)
    rows = (await session.execute(
        select(WebhookDelivery, Webhook, ApiEvent).join(Webhook, Webhook.id == WebhookDelivery.webhook_id)
        .join(ApiEvent, ApiEvent.seq == WebhookDelivery.event_seq)
        .where(WebhookDelivery.status == "PENDING", WebhookDelivery.next_attempt_at <= now)
        .order_by(WebhookDelivery.next_attempt_at, WebhookDelivery.event_seq).limit(limit))).all()
    counts = {"DELIVERED": 0, "RETRY": 0, "FAILED": 0, "REFUSED": 0}
    owns = client is None
    client = client or make_client()
    try:
        for d, hook, event in rows:
            d.attempts += 1
            d.last_attempt_at = now
            reason = url_refusal(hook.url)
            if reason:
                d.status, d.next_attempt_at, d.error = "REFUSED", None, reason
                counts["REFUSED"] += 1
                continue
            body = body_of(d, event)
            headers = {"Content-Type": "application/json", EVENT_HEADER: event.event_type, DELIVERY_HEADER: str(d.id),
                       SIGNATURE_HEADER: signature_header(hook.secret, int(now.timestamp()), body)}
            try:
                resp = await client.post(hook.url, content=body, headers=headers)
            except UrlRefused as e:  # resolved to a non-public address: never retried
                d.status, d.next_attempt_at, d.error = "REFUSED", None, str(e)[:1000]
                counts["REFUSED"] += 1
                continue
            except httpx.HTTPError as e:
                _failed(d, now, f"{type(e).__name__}: {e}")
            else:
                if 200 <= resp.status_code < 300:
                    d.status, d.next_attempt_at, d.response_status, d.error, d.delivered_at = "DELIVERED", None, resp.status_code, None, now
                    counts["DELIVERED"] += 1
                    continue
                _failed(d, now, f"HTTP {resp.status_code}" + (" (redirects are not followed)" if resp.is_redirect else ""), resp.status_code)
            counts["FAILED" if d.status == "FAILED" else "RETRY"] += 1
    finally:
        if owns:
            await client.aclose()
    await session.flush()
    return counts
