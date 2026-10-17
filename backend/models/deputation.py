import uuid
from datetime import date, datetime
from typing import Optional
from sqlalchemy import Date, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from govcareeros.backend.models.base import Base, TimestampMixin, UTCDateTime

class DeputationCircular(Base, TimestampMixin):
    """A deputation vacancy circular read from one stored official PDF (backend/parsing/deputation_circular.py).
    `terms` holds every parsed term with its page and the circular's words; the columns beside it are copies of a
    few of those terms for listing and filtering. Re-parsing a document replaces its row only when the parser changed."""
    __tablename__ = "deputation_circulars"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    raw_document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("raw_documents.id", name="fk_deputation_circulars_raw_document_id"), unique=True, index=True)
    # The listing title of the RecruitmentNotice that links this document, verbatim; None when no notice links it
    title: Mapped[Optional[str]] = mapped_column(Text)
    parser_version: Mapped[str] = mapped_column(String(40))
    # Comma-joined MODES of the circular ("DEPUTATION,ABSORPTION"); None when the circular states none
    mode: Mapped[Optional[str]] = mapped_column(String(100))
    # DATE (closing_date set) or EMPLOYMENT_NEWS_DAYS (closing_days set; the date depends on the issue); None = not stated
    closing_kind: Mapped[Optional[str]] = mapped_column(String(30))
    closing_date: Mapped[Optional[date]] = mapped_column(Date, index=True)
    closing_days: Mapped[Optional[int]] = mapped_column(Integer)
    post_count: Mapped[int] = mapped_column(Integer, default=0)
    # JSON: parsing.deputation_circular.to_json(...) plus "govt_service" (recruitment_mode detection: value, page, quote)
    terms: Mapped[str] = mapped_column(Text)
    parsed_at: Mapped[datetime] = mapped_column(UTCDateTime())
