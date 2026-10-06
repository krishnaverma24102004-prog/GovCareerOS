"""Reviewer-only administration of B2B API customers: organisations, their keys (shown once) and daily usage."""
import uuid
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.api.deps import get_session
from govcareeros.backend.api.reviewer_deps import require_reviewer
from govcareeros.backend.models.b2b import ApiKey, ApiOrg
from govcareeros.backend.models.user import User
from govcareeros.backend.schemas.b2b import KeyCreatedOut, KeyIn, KeyOut, OrgIn, OrgOut, UsageOut
from govcareeros.backend.services import api_keys, review_audit

router = APIRouter(prefix="/admin/api", tags=["admin: api customers"])

class OrgCreate(OrgIn):
    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip()
        if not 1 <= len(v) <= 255:
            raise ValueError("name must be 1-255 characters")
        return v

class KeyCreate(KeyIn):
    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip()
        if not 1 <= len(v) <= 100:
            raise ValueError("name must be 1-100 characters")
        return v

    @field_validator("scopes")
    @classmethod
    def _scopes(cls, v: List[str]) -> List[str]:
        return api_keys.parse_scopes(v)

    @field_validator("rate_limit_per_minute")
    @classmethod
    def _rate(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and not 1 <= v <= 10_000:
            raise ValueError("rate_limit_per_minute must be 1-10000")
        return v

def _org_out(o: ApiOrg) -> OrgOut:
    return OrgOut(id=o.id, name=o.name, contact_email=o.contact_email, is_active=o.is_active, created_at=o.created_at)

def _key_out(k: ApiKey) -> KeyOut:
    return KeyOut(id=k.id, org_id=k.org_id, name=k.name, prefix=k.prefix, scopes=api_keys.scopes_of(k),
                  rate_limit_per_minute=api_keys.limit_of(k), created_at=k.created_at, last_used_at=k.last_used_at, revoked_at=k.revoked_at)

async def _org(session: AsyncSession, org_id: uuid.UUID) -> ApiOrg:
    org = await session.get(ApiOrg, org_id)
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "organisation not found")
    return org

@router.post("/orgs", response_model=OrgOut, status_code=status.HTTP_201_CREATED)
async def create_org(body: OrgCreate, user: User = Depends(require_reviewer), session: AsyncSession = Depends(get_session)):
    if (await session.execute(select(ApiOrg).where(ApiOrg.name == body.name))).scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "an organisation with this name exists")
    org = ApiOrg(name=body.name, contact_email=(body.contact_email or "").strip() or None, created_by_id=user.id)
    session.add(org)
    await session.flush()
    review_audit.record(session, user, "API_ORG_CREATE", "API_ORG", org.id, detail={"name": org.name})
    await session.refresh(org)
    return _org_out(org)

@router.get("/orgs", response_model=List[OrgOut])
async def list_orgs(user: User = Depends(require_reviewer), session: AsyncSession = Depends(get_session)):
    return [_org_out(o) for o in (await session.execute(select(ApiOrg).order_by(ApiOrg.name))).scalars()]

@router.post("/orgs/{org_id}/keys", response_model=KeyCreatedOut, status_code=status.HTTP_201_CREATED)
async def create_key(org_id: uuid.UUID, body: KeyCreate, user: User = Depends(require_reviewer),
                     session: AsyncSession = Depends(get_session)):
    """The key is in this response only; store it in the customer's secret store now."""
    org = await _org(session, org_id)
    if not org.is_active:
        raise HTTPException(status.HTTP_409_CONFLICT, "organisation is deactivated")
    row, key = await api_keys.issue_key(session, org, body.name, body.scopes, body.rate_limit_per_minute, user.id)
    review_audit.record(session, user, "API_KEY_CREATE", "API_KEY", row.id,
                        detail={"org": org.name, "prefix": row.prefix, "scopes": api_keys.scopes_of(row)})
    await session.refresh(row)
    return KeyCreatedOut(**_key_out(row).model_dump(), key=key)

@router.get("/orgs/{org_id}/keys", response_model=List[KeyOut])
async def list_keys(org_id: uuid.UUID, user: User = Depends(require_reviewer), session: AsyncSession = Depends(get_session)):
    await _org(session, org_id)
    return [_key_out(k) for k in (await session.execute(
        select(ApiKey).where(ApiKey.org_id == org_id).order_by(ApiKey.created_at, ApiKey.id))).scalars()]

@router.post("/keys/{key_id}/revoke", response_model=KeyOut)
async def revoke_key(key_id: uuid.UUID, user: User = Depends(require_reviewer), session: AsyncSession = Depends(get_session)):
    key = await session.get(ApiKey, key_id)
    if key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "key not found")
    if key.revoked_at is None:
        await api_keys.revoke(session, key)
        review_audit.record(session, user, "API_KEY_REVOKE", "API_KEY", key.id, detail={"prefix": key.prefix})
    api_keys.limiter.forget(key.id)
    await session.refresh(key)
    return _key_out(key)

@router.get("/orgs/{org_id}/usage", response_model=List[UsageOut])
async def org_usage(org_id: uuid.UUID, since: Optional[date] = Query(None, description="first UTC day to include"),
                    user: User = Depends(require_reviewer), session: AsyncSession = Depends(get_session)):
    """Answered requests per key, UTC day and endpoint (refused ones are not counted)."""
    await _org(session, org_id)
    keys = {k.id: k.prefix for k in (await session.execute(select(ApiKey).where(ApiKey.org_id == org_id))).scalars()}
    return [UsageOut(key_id=u.key_id, prefix=keys[u.key_id], day=u.day, endpoint=u.endpoint, requests=u.requests)
            for u in await api_keys.usage(session, keys, since)]
