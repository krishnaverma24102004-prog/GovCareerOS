import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from govcareeros.backend.models.base import Base, TimestampMixin, UTCDateTime

class AuthToken(Base, TimestampMixin):
    """API session token. Only a SHA-256 of the token is stored."""
    __tablename__ = "auth_tokens"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    revoked_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime())
