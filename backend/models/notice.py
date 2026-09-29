import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Text, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from govcareeros.backend.models.base import Base, TimestampMixin, UTCDateTime

class RecruitmentNotice(Base, TimestampMixin):
    """A recruitment-related link seen on an official source page (notice level, not yet a Vacancy)."""
    __tablename__ = "recruitment_notices"
    __table_args__ = (UniqueConstraint("source_id", "url_hash", name="uq_recruitment_notices_source_url"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("government_sources.id"), index=True)
    url: Mapped[str] = mapped_column(Text)
    url_hash: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(Text)
    document_type: Mapped[str] = mapped_column(String(20))  # PDF, DOC, HTML
    # VACANCY, CORRIGENDUM, RESULT, RULES, OTHER — keyword rules, see backend/sources/notice_classifier.py
    notice_kind: Mapped[str] = mapped_column(String(20), index=True)
    found_on_url: Mapped[Optional[str]] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime())
    # NEW -> MANUAL_REVIEW (no parser for this source) | PARSED (turned into Vacancy rows)
    review_status: Mapped[str] = mapped_column(String(30), default="NEW", index=True)
    raw_document_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("raw_documents.id"))
