from sqlalchemy import Boolean
import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from govcareeros.backend.models.base import Base, TimestampMixin, UTCDateTime

class BrowserSessionState(Base, TimestampMixin):
    __tablename__ = "browser_session_states"
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("government_sources.id"), index=True)
    profile_path: Mapped[str] = mapped_column(String(500))
    
    # states: ANONYMOUS, AUTHENTICATED, MANUAL_LOGIN_REQUIRED, ACCESS_RESTRICTED, SESSION_EXPIRED
    session_state: Mapped[str] = mapped_column(String(100), default="ANONYMOUS")
    
    last_authenticated_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime())
    last_access_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime())
    last_auth_failure_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime())
    
    manual_login_required: Mapped[bool] = mapped_column(Boolean, default=False)
    session_expired: Mapped[bool] = mapped_column(Boolean, default=False)

from sqlalchemy import Boolean
