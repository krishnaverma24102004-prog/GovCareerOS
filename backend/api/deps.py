import uuid
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.api.security import token_hash
from govcareeros.backend.database.session import AsyncSessionLocal
from govcareeros.backend.models.auth_token import AuthToken
from govcareeros.backend.models.user import User

_bearer = HTTPBearer(auto_error=False)

async def get_session() -> AsyncIterator[AsyncSession]:
    """One transaction per request: committed on success, rolled back on any error."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

async def current_user(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> User:
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "not authenticated", headers={"WWW-Authenticate": "Bearer"})
    if creds is None or creds.scheme.lower() != "bearer":
        raise unauthorized
    return await _token_user(session, creds, unauthorized)

async def profile_owner(
    profile_version_id: Optional[uuid.UUID] = None,
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> Optional[User]:
    """Public pages that add the caller's own data when asked to (?profile_version_id=). Without it the page is public
    and any token is ignored, so an expired one cannot hide it; with it, a missing or bad token is a 401."""
    if profile_version_id is None:
        return None
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "log in to see your own status", headers={"WWW-Authenticate": "Bearer"})
    return await current_user(creds, session)

async def _token_user(session: AsyncSession, creds: HTTPAuthorizationCredentials, unauthorized: HTTPException) -> User:
    res = await session.execute(
        select(User).join(AuthToken, AuthToken.user_id == User.id).where(
            AuthToken.token_hash == token_hash(creds.credentials),
            AuthToken.revoked_at.is_(None),
            AuthToken.expires_at > datetime.now(timezone.utc),
            User.is_active.is_(True),
        )
    )
    user = res.scalar_one_or_none()
    if user is None:
        raise unauthorized
    return user
