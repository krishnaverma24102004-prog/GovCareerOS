"""
Webhooks: an org's https endpoints, told about notice.new / notice.changed events (services/b2b_events.py).

Never to a private address. A URL is refused at registration unless it is https, names a public host (no IP
literal or name that is loopback, private, link-local, ...; no single-label or .local/.internal names) and carries no
user:password. At delivery the host name is resolved and the connection refused when any address it resolves to is
non-public (url_guard.GuardedTransport, the guard every outbound fetch of the project uses); the connection is then
opened to one of the addresses checked, never to the name again (DNS rebinding). Redirects are not followed, so a
public URL cannot bounce a delivery inside. Refused deliveries are marked REFUSED and never retried.

Signature: every delivery carries
    X-GovCareerOS-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256(secret, "<t>." + raw body)>
`verify_signature` is the receiver-side check (also documented in docs/api/README.md).

Retries: a delivery that gets no 2xx answer within TIMEOUT_SECONDS is tried again after 1 min, 5 min, 30 min, 2 h and
6 h, then marked FAILED. Every attempt is in webhook_deliveries (status, attempts, HTTP status, error). Deliveries of
one webhook may arrive out of order when one is retried; each body carries the event's `seq`. A dispatcher claims each
delivery before sending it (see _claim), so dispatchers that overlap never send the same delivery twice.
"""
import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

import httpx
from sqlalchemy import func, select, update
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
TIMEOUT_SECONDS = 10  # one attempt in all: connecting, sending, the answer's status and headers
LEASE = timedelta(minutes=5)  # a claimed delivery is left to its dispatcher this long (well over one attempt)
MAX_WEBHOOKS_PER_ORG = 10
_PRIVATE_SUFFIXES = (".local", ".internal", ".intranet", ".lan", ".home", ".corp", ".home.arpa", ".localhost")
_LABEL = re.compile(r"(?!-)[a-z0-9-]{1,63}(?<!-)")  # one DNS label as sent (letters, digits, hyphens; IDNs as xn--)

log = logging.getLogger(__name__)

class WebhookUrlRefused(ValueError):
    pass

def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False

def _wire_host(url: str) -> Optional[str]:
    """The host as httpx sends it to DNS and TLS (an IDN IDNA-encoded), or None when httpx cannot build or read a
    request for this URL (a host that is not valid IDNA: a symbol, or an xn-- label that does not decode)."""
    try:
        parsed = httpx.URL(url)
        parsed.host  # decodes every xn-- label; an invalid one raises here as it would at delivery
        return parsed.raw_host.decode("ascii").rstrip(".").lower()
    except (httpx.InvalidURL, ValueError):  # idna.IDNAError and UnicodeError are ValueErrors
        return None

def url_refusal(url: str) -> Optional[str]:
    """Why this URL may not receive webhooks, or None. Names are resolved and checked again at every delivery."""
    if len(url) > 2000:
        return "URL is longer than 2000 characters"
    try:
        parts = urlparse(url)
        parts.port  # raises on a malformed port
    except ValueError:
        return "not a valid URL"
    wire = _wire_host(url)
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
    # The checks above read the URL as urllib does; the request is built by httpx, so its host must pass too
    if wire is None or not (_is_ip(wire) or (len(wire) <= 253 and all(_LABEL.fullmatch(l) for l in wire.split(".")))):
        return f"{host} is not a valid host name"
    if _is_ip(wire) and literal_non_public(f"https://[{wire}]/" if ":" in wire else f"https://{wire}/"):
        return f"{host} is not a public address"
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

def make_client(resolve=None, network_backend=None) -> httpx.AsyncClient:
    """Resolves every host, refuses non-public addresses and connects only to an address it checked (so a second DNS
    answer cannot rebind the delivery inside); never follows redirects. Both arguments are for tests."""
    transport = GuardedTransport(**({"resolve": resolve} if resolve else {}), network_backend=network_backend)
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

async def _claim(session: AsyncSession, delivery_id, due_by: datetime, at: datetime) -> bool:
    """Take one due delivery for this dispatcher and commit: attempts + 1, next attempt LEASE ahead. A dispatcher
    running at the same time then finds it not due and skips it; if this one dies mid-send, the delivery is tried
    again once the lease is over. False when another dispatcher took it (or it was removed) first."""
    taken = await session.execute(
        update(WebhookDelivery)
        .where(WebhookDelivery.id == delivery_id, WebhookDelivery.status == "PENDING", WebhookDelivery.next_attempt_at <= due_by)
        .values(attempts=WebhookDelivery.attempts + 1, last_attempt_at=at, next_attempt_at=at + LEASE)
        .execution_options(synchronize_session=False))
    await session.commit()
    return taken.rowcount == 1

async def _post(client: httpx.AsyncClient, url: str, body: bytes, headers: Dict[str, str]) -> httpx.Response:
    """The answer's status and headers. Its body is never read: a receiver may send it slowly or without end."""
    async with client.stream("POST", url, content=body, headers=headers) as resp:
        return resp

async def _attempt(client: httpx.AsyncClient, d: WebhookDelivery, hook: Webhook, event: ApiEvent, now: datetime) -> str:
    """Send one claimed delivery and record its outcome on `d`; the count it falls under."""
    reason = url_refusal(hook.url)
    if reason:
        d.status, d.next_attempt_at, d.error = "REFUSED", None, reason
        return "REFUSED"
    try:
        body = body_of(d, event)
        headers = {"Content-Type": "application/json", EVENT_HEADER: event.event_type, DELIVERY_HEADER: str(d.id),
                   SIGNATURE_HEADER: signature_header(hook.secret, int(now.timestamp()), body)}
        # TIMEOUT_SECONDS in all, connection to answer (httpx's own timeout is per read, so a trickle never ends it)
        resp = await asyncio.wait_for(_post(client, hook.url, body, headers), TIMEOUT_SECONDS)
    except (UrlRefused, httpx.InvalidURL) as e:  # resolved to a non-public address / unusable URL: never retried
        d.status, d.next_attempt_at, d.error = "REFUSED", None, str(e)[:1000]
        return "REFUSED"
    except httpx.HTTPError as e:
        _failed(d, now, f"{type(e).__name__}: {e}")
    except TimeoutError:
        _failed(d, now, f"no answer within {TIMEOUT_SECONDS} seconds")
    except Exception as e:  # anything else is this delivery's failure, never the whole batch's
        log.exception("webhook delivery %s to webhook %s failed unexpectedly", d.id, hook.id)
        _failed(d, now, f"unexpected error ({type(e).__name__})")
    else:
        if 200 <= resp.status_code < 300:
            d.status, d.next_attempt_at, d.response_status, d.error, d.delivered_at = "DELIVERED", None, resp.status_code, None, now
            return "DELIVERED"
        _failed(d, now, f"HTTP {resp.status_code}" + (" (redirects are not followed)" if resp.is_redirect else ""), resp.status_code)
    return "FAILED" if d.status == "FAILED" else "RETRY"

async def deliver_due(session: AsyncSession, client: Optional[httpx.AsyncClient] = None, now: Optional[datetime] = None,
                      limit: int = 200) -> Dict[str, int]:
    """Send the PENDING deliveries whose time has come, one at a time. Counts by outcome.

    Each delivery is claimed (committed) before it is sent and its outcome committed right after, so dispatchers
    running at the same time never send one delivery twice, and nothing sent is sent again because a later one
    failed. Each attempt has TIMEOUT_SECONDS in all; the answer's body is not read."""
    now = now or datetime.now(timezone.utc)
    due = (await session.execute(
        select(WebhookDelivery.id).where(WebhookDelivery.status == "PENDING", WebhookDelivery.next_attempt_at <= now)
        .order_by(WebhookDelivery.next_attempt_at, WebhookDelivery.event_seq).limit(limit))).scalars().all()
    counts = {"DELIVERED": 0, "RETRY": 0, "FAILED": 0, "REFUSED": 0}
    owns = client is None
    client = client or make_client()
    try:
        for delivery_id in due:
            if not await _claim(session, delivery_id, now, now):
                continue
            d, hook, event = (await session.execute(
                select(WebhookDelivery, Webhook, ApiEvent).join(Webhook, Webhook.id == WebhookDelivery.webhook_id)
                .join(ApiEvent, ApiEvent.seq == WebhookDelivery.event_seq).where(WebhookDelivery.id == delivery_id)
                .execution_options(populate_existing=True))).one()
            counts[await _attempt(client, d, hook, event, now)] += 1
            await session.commit()
    finally:
        if owns:
            await client.aclose()
    return counts
