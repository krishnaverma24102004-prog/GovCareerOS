import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from govcareeros.backend.models.base import Base, UTCDateTime

class ReviewAuditLog(Base):
    """Append-only record of every reviewer decision (drafts, notices, facts, reviewer flags). Never updated."""
    __tablename__ = "review_audit_log"
    __table_args__ = (Index("ix_review_audit_log_object", "object_type", "object_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", name="fk_review_audit_log_actor_id_users"), index=True)
    # e.g. DRAFT_APPROVE, DRAFT_REJECT, NOTICE_SKIP, NOTICE_NOT_REQUIRED, NOTICE_REQUEUE, FACT_VERIFY, FACT_REJECT, REVIEWER_GRANT
    action: Mapped[str] = mapped_column(String(40), index=True)
    # DRAFT, NOTICE, FACT, USER, SOURCE, API_ORG, API_KEY
    object_type: Mapped[str] = mapped_column(String(20))
    object_id: Mapped[uuid.UUID] = mapped_column()
    note: Mapped[Optional[str]] = mapped_column(Text)
    # JSON: what changed, e.g. {"from": "MANUAL_REVIEW", "to": "SKIPPED"}
    detail: Mapped[Optional[str]] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
