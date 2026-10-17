import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from govcareeros.backend.models.base import Base, TimestampMixin, UTCDateTime

class ExtractionDraft(Base, TimestampMixin):
    """LLM-located, validator-checked vacancy data awaiting human review. Never a Vacancy by itself."""
    __tablename__ = "extraction_drafts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("government_sources.id"), index=True)
    notice_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("recruitment_notices.id"), index=True)
    raw_document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("raw_documents.id"), index=True)
    provider: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(200))
    prompt_version: Mapped[str] = mapped_column(String(100))
    # PENDING_REVIEW, APPROVED, REJECTED, FAILED
    status: Mapped[str] = mapped_column(String(30), default="PENDING_REVIEW", index=True)
    # JSON: {"vacancies": [{"fields": {name: verdict}}], "chunks": [...]}
    payload: Mapped[str] = mapped_column(Text)
    fields_offered: Mapped[int] = mapped_column(default=0)
    fields_accepted: Mapped[int] = mapped_column(default=0)
    llm_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[Optional[str]] = mapped_column(Text)
    reviewer_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime())
    review_note: Mapped[Optional[str]] = mapped_column(Text)
    created_vacancy_ids: Mapped[Optional[str]] = mapped_column(Text)  # JSON list

    @property
    def parser_version(self) -> str:
        """parser_version for the rows an approval creates (String(100) columns). `model` reads "a,b" when the
        NVIDIA chain moved mid-document, so a long chain is cut to fit; the draft itself keeps every model."""
        return f"llm:{self.prompt_version}:{self.model}"[:100]
