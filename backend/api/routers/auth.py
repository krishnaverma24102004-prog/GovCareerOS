from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.api.deps import current_user, get_session
from govcareeros.backend.api.security import hash_password, new_token, token_hash, verify_password
from govcareeros.backend.models.auth_token import AuthToken
from govcareeros.backend.models.user import User
from govcareeros.backend.schemas.api import ClaimIn, LoginIn, RegisterIn, TokenOut, UserOut
from govcareeros.backend.services import operator_console

router = APIRouter(prefix="/auth", tags=["auth"])

@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterIn, session: AsyncSession = Depends(get_session)):
    if (await session.execute(select(User).where(User.email == body.email))).scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "email already registered")
    user = User(email=body.email, hashed_password=hash_password(body.password), full_name=body.full_name)
    session.add(user)
    await session.flush()
    return user

@router.post("/claim", response_model=UserOut)
async def claim(body: ClaimIn, session: AsyncSession = Depends(get_session)):
    """Set email and password on an operator-created client account with the one-time code; then log in as usual."""
    try:
        return await operator_console.claim_account(session, body.token, body.email, body.password)
    except operator_console.OperatorError as e:
        raise HTTPException(e.status, str(e))

@router.post("/operator-consent/withdraw", status_code=status.HTTP_204_NO_CONTENT)
async def withdraw_operator_consent(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    """A client who claimed an operator-created account withdraws the operator's consent: the operator loses access."""
    if user.managed_by_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "this account was not created by an operator")
    await operator_console.withdraw_consent(session, user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

@router.post("/login", response_model=TokenOut)
async def login(body: LoginIn, session: AsyncSession = Depends(get_session)):
    user = (await session.execute(select(User).where(User.email == body.email.strip().lower()))).scalar_one_or_none()
    if user is None or not user.is_active or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid email or password")
    token, digest, expires_at = new_token()
    session.add(AuthToken(user_id=user.id, token_hash=digest, expires_at=expires_at))
    await session.flush()
    return TokenOut(access_token=token, expires_at=expires_at)

@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    creds: HTTPAuthorizationCredentials = Depends(HTTPBearer()),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    token = (await session.execute(select(AuthToken).where(AuthToken.token_hash == token_hash(creds.credentials)))).scalar_one()
    token.revoked_at = datetime.now(timezone.utc)

@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(current_user)):
    return user
