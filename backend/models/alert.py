import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Text, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from govcareeros.backend.models.base import Base, TimestampMixin, UTCDateTime

class UserAlert(Base, TimestampMixin):
    """In-app alert produced by the eligibility radar. One per (user, dedupe_key)."""
    __tablename__ = "user_alerts"
    __table_args__ = (UniqueConstraint("user_id", "dedupe_key", name="uq_user_alerts_user_dedupe"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    # NEW_MATCH, DEADLINE_SOON, AGE_HEADROOM, VACANCY_UPDATED, VACANCY_CANCELLED, APPLICATION_UPDATE
    kind: Mapped[str] = mapped_column(String(40), index=True)
    dedupe_key: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    vacancy_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("vacancies.id"), index=True)
    profile_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("profile_versions.id"))
    eligibility_result_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("eligibility_results.id"))
    vacancy_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("vacancy_versions.id"))
    due_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime())  # deadline or age-out date the alert is about
    read_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime())
