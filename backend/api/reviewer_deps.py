from fastapi import Depends, HTTPException, status

from govcareeros.backend.api.deps import current_user
from govcareeros.backend.models.user import User

async def require_reviewer(user: User = Depends(current_user)) -> User:
    """Signed in AND flagged as reviewer; everyone else gets 403 (401 when not signed in)."""
    if not user.is_reviewer:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "reviewer role required")
    return user
