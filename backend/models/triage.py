import uuid
from datetime import date, datetime
from typing import Optional
from sqlalchemy import Date, ForeignKey, Integer, PrimaryKeyConstraint, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from govcareeros.backend.models.base import Base, TimestampMixin, UTCDateTime

class NoticeTriage(Base, TimestampMixin):
    """The current automatic triage decision for one RecruitmentNotice (backend/services/triage.py).

    Triage only sets this row: the notice itself (and its review_status) is never changed or deleted.
    QUEUED notices need human/LLM attention, highest priority first; STALE and NOT_REQUIRED ones are
    left out of the draft queue.
    """
    __tablename__ = "notice_triage"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_notice_triage"),
        UniqueConstraint("notice_id", name="uq_notice_triage_notice_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(default=uuid.uuid4)
    notice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recruitment_notices.id", name="fk_notice_triage_notice_id"))
    triage_status: Mapped[str] = mapped_column(String(20), index=True)  # QUEUED, STALE, NOT_REQUIRED
    priority: Mapped[int] = mapped_column(Integer, index=True)
    rule: Mapped[str] = mapped_column(String(40))  # which decision rule fired, e.g. "stale_stated_date"
    reason: Mapped[str] = mapped_column(Text)  # the decision and the priority breakdown, in words
    # Publication date as stated in the notice's own title / link; None when it states none (never guessed)
    notice_date: Mapped[Optional[date]] = mapped_column(Date)
    notice_date_rule: Mapped[Optional[str]] = mapped_column(String(40))
    notice_date_text: Mapped[Optional[str]] = mapped_column(String(200))  # the exact text the date was read from
    stale_after_days: Mapped[int] = mapped_column(Integer)
    rules_version: Mapped[str] = mapped_column(String(20))
    triaged_at: Mapped[datetime] = mapped_column(UTCDateTime())

class TriageDecision(Base):
    """Append-only log: one row each time triage changed a notice's status, priority or stated date."""
    __tablename__ = "triage_decisions"
    __table_args__ = (PrimaryKeyConstraint("id", name="pk_triage_decisions"),)

    id: Mapped[uuid.UUID] = mapped_column(default=uuid.uuid4)
    notice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recruitment_notices.id", name="fk_triage_decisions_notice_id"), index=True)
    previous_status: Mapped[Optional[str]] = mapped_column(String(20))
    triage_status: Mapped[str] = mapped_column(String(20))
    priority: Mapped[int] = mapped_column(Integer)
    rule: Mapped[str] = mapped_column(String(40))
    reason: Mapped[str] = mapped_column(Text)
    notice_date: Mapped[Optional[date]] = mapped_column(Date)
    notice_date_rule: Mapped[Optional[str]] = mapped_column(String(40))
    rules_version: Mapped[str] = mapped_column(String(20))
    decided_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
