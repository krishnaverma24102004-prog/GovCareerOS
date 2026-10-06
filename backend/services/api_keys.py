"""
API keys for the B2B API: issue (the key is returned once), look up by hash, revoke, rate-limit and meter.

A key reads "gco_<8 characters>_<43 characters>". The first part ("gco_" + 8) is its prefix: stored in clear so a
key can be named in listings and logs. Only the SHA-256 of the whole key is stored; a lost key is revoked and a
new one issued.

Rate limit: a token bucket per key, kept in this process's memory (capacity = the key's per-minute limit, refilled
continuously). It resets when the process restarts and each API worker process keeps its own buckets, so with N
workers a key gets up to N times its limit. Good enough for one local API process; a shared store (Redis) is the
next step if the API is scaled out.
"""
import hashlib
import secrets
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.config.settings import settings
from govcareeros.backend.models.b2b import ApiKey, ApiOrg, ApiUsage

SCOPES = {
    "notices:read": "list and read verified notices (/api/v1/notices)",
    "changes:read": "read the change feed (/api/v1/changes)",
    "verify:use": "check a notice (/api/v1/verify)",
    "webhooks:manage": "register, list and remove webhooks (/api/v1/webhooks)",
}
KEY_PREFIX = "gco_"
_PREFIX_LEN = len(KEY_PREFIX) + 8

def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()

def new_key() -> Tuple[str, str, str]:
    """(key, prefix, sha256). The key is never stored."""
    while True:
        head = secrets.token_urlsafe(6)[:8]
        if "_" not in head and "-" not in head:  # the prefix must read as one token in "gco_<head>_<secret>"
            break
    key = f"{KEY_PREFIX}{head}_{secrets.token_urlsafe(32)}"
    return key, key[:_PREFIX_LEN], key_hash(key)

def parse_scopes(scopes: Iterable[str]) -> List[str]:
    out = sorted(set(scopes))
    unknown = [s for s in out if s not in SCOPES]
    if unknown or not out:
        raise ValueError(f"unknown scope(s) {unknown}; choose from {sorted(SCOPES)}" if unknown else "a key needs at least one scope")
    return out

def scopes_of(key: ApiKey) -> List[str]:
    return key.scopes.split()

async def issue_key(session: AsyncSession, org: ApiOrg, name: str, scopes: Iterable[str],
                    rate_limit_per_minute: Optional[int] = None, created_by_id: Optional[uuid.UUID] = None) -> Tuple[ApiKey, str]:
    """The new row and the key itself; the caller shows the key once and keeps nothing of it."""
    key, prefix, digest = new_key()
    row = ApiKey(org_id=org.id, name=name, prefix=prefix, key_hash=digest, scopes=" ".join(parse_scopes(scopes)),
                 rate_limit_per_minute=rate_limit_per_minute, created_by_id=created_by_id)
    session.add(row)
    await session.flush()
    return row, key

async def lookup(session: AsyncSession, presented: str) -> Optional[Tuple[ApiKey, ApiOrg]]:
    """The active key (not revoked, org active) whose hash matches, or None."""
    if not presented or not presented.startswith(KEY_PREFIX):
        return None
    row = (await session.execute(select(ApiKey, ApiOrg).join(ApiOrg, ApiOrg.id == ApiKey.org_id).where(
        ApiKey.key_hash == key_hash(presented), ApiKey.revoked_at.is_(None), ApiOrg.is_active.is_(True)))).first()
    return (row[0], row[1]) if row else None

async def revoke(session: AsyncSession, key: ApiKey, now: Optional[datetime] = None) -> None:
    if key.revoked_at is None:
        key.revoked_at = now or datetime.now(timezone.utc)
    await session.flush()

# ---------- rate limit ----------

@dataclass
class _Bucket:
    capacity: float
    tokens: float
    updated: float = field(default_factory=time.monotonic)

class RateLimiter:
    """Token buckets per key id. `clock` is injectable for tests."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self._buckets: Dict[uuid.UUID, _Bucket] = {}

    def take(self, key_id: uuid.UUID, per_minute: int) -> Optional[float]:
        """None when the request may go ahead, else the seconds until a token is available."""
        now, rate = self.clock(), per_minute / 60.0
        b = self._buckets.get(key_id)
        if b is None or b.capacity != per_minute:  # a changed limit starts a full bucket
            b = self._buckets[key_id] = _Bucket(per_minute, per_minute, now)
        b.tokens = min(b.capacity, b.tokens + (now - b.updated) * rate)
        b.updated = now
        if b.tokens >= 1:
            b.tokens -= 1
            return None
        return (1 - b.tokens) / rate

    def forget(self, key_id: uuid.UUID) -> None:
        self._buckets.pop(key_id, None)

    def reset(self) -> None:
        self._buckets.clear()

limiter = RateLimiter()

def limit_of(key: ApiKey) -> int:
    return key.rate_limit_per_minute or settings.B2B_RATE_LIMIT_PER_MINUTE

# ---------- metering ----------

async def record_use(session: AsyncSession, key: ApiKey, endpoint: str, now: Optional[datetime] = None) -> None:
    """+1 on (key, UTC day, endpoint) and the key's last_used_at, in the request's own transaction (so a request that
    fails is not counted). Insert-or-increment with each dialect's ON CONFLICT."""
    now = now or datetime.now(timezone.utc)
    day = now.astimezone(timezone.utc).date()
    dialect = session.bind.dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    stmt = insert(ApiUsage).values(id=uuid.uuid4(), key_id=key.id, day=day, endpoint=endpoint, requests=1)
    await session.execute(stmt.on_conflict_do_update(index_elements=["key_id", "day", "endpoint"],
                                                     set_={"requests": ApiUsage.requests + 1}))
    key.last_used_at = now

async def usage(session: AsyncSession, key_ids: Iterable[uuid.UUID], since: Optional[date] = None) -> List[ApiUsage]:
    ids = list(key_ids)
    if not ids:
        return []
    stmt = select(ApiUsage).where(ApiUsage.key_id.in_(ids))
    if since:
        stmt = stmt.where(ApiUsage.day >= since)
    return list((await session.execute(stmt.order_by(ApiUsage.day, ApiUsage.endpoint))).scalars())
