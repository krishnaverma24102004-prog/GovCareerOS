import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Text, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from govcareeros.backend.models.base import Base, TimestampMixin, UTCDateTime

class DigestRun(Base, TimestampMixin):
    """One generated weekly digest for a user: the period it covers, where the files are, and whether it was emailed."""
    __tablename__ = "digest_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", name="fk_digest_runs_user_id"), index=True)
    profile_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("profile_versions.id", name="fk_digest_runs_profile_version_id"))
    period_start: Mapped[datetime] = mapped_column(UTCDateTime())
    period_end: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    # Relative to settings.storage_dir, e.g. "digests/<user_id>/2026-09-29.html" (.txt and .json sit beside it)
    file_path: Mapped[str] = mapped_column(String(1000))
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    item_counts: Mapped[Optional[str]] = mapped_column(Text)  # JSON: {"matches": n, "deadlines": n, ...}
    omitted_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # facts left out for lack of a citation
    sent_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime())
    sent_to: Mapped[Optional[str]] = mapped_column(String(255))
