"""
API-key authentication for /api/v1: `X-API-Key: gco_...`. In this order: the key (401), its rate limit (429),
its scope (403); then the request is metered (services/api_keys.py).
"""
import math
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.api.deps import get_session
from govcareeros.backend.models.b2b import ApiKey, ApiOrg
from govcareeros.backend.services import api_keys

API_KEY_HEADER = "X-API-Key"
_api_key = APIKeyHeader(name=API_KEY_HEADER, auto_error=False, scheme_name="ApiKey",
                        description="An organisation's API key (gco_...), issued by GovCareerOS")

@dataclass
class ApiCaller:
    key: ApiKey
    org: ApiOrg

def require_scope(scope: str, endpoint: str):
    """Dependency for one endpoint: `endpoint` names it in the usage table (e.g. "notices.list")."""
    if scope not in api_keys.SCOPES:
        raise ValueError(f"unknown scope {scope!r}")

    async def caller(presented: str = Security(_api_key), session: AsyncSession = Depends(get_session)) -> ApiCaller:
        found = await api_keys.lookup(session, presented or "")
        if found is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing, unknown or revoked API key",
                                headers={"WWW-Authenticate": API_KEY_HEADER})
        key, org = found
        per_minute = api_keys.limit_of(key)
        wait = api_keys.limiter.take(key.id, per_minute)
        if wait is not None:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, f"rate limit of {per_minute} requests per minute exceeded",
                                headers={"Retry-After": str(max(1, math.ceil(wait))), "X-RateLimit-Limit": str(per_minute)})
        if scope not in api_keys.scopes_of(key):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"this key lacks the {scope} scope")
        await api_keys.record_use(session, key, endpoint)
        return ApiCaller(key, org)

    return caller
