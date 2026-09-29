from datetime import datetime
from sqlalchemy import Integer
import uuid
from typing import Optional
from sqlalchemy import String, Text, Boolean, ForeignKey, DateTime, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from govcareeros.backend.models.base import Base, TimestampMixin, UTCDateTime

class RawDocument(Base, TimestampMixin):
    __tablename__ = 'raw_documents'
    # The same bytes from the same source are stored once
    __table_args__ = (UniqueConstraint('source_id', 'content_hash', name='uq_raw_documents_source_hash'),)
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('government_sources.id'), index=True)
    retrieved_at: Mapped[datetime] = mapped_column(UTCDateTime())
    url: Mapped[Optional[str]] = mapped_column(String(1000))
    http_method: Mapped[Optional[str]] = mapped_column(String(10))
    status: Mapped[Optional[int]] = mapped_column(Integer)
    content_type: Mapped[Optional[str]] = mapped_column(String(100))
    content_hash: Mapped[str] = mapped_column(String(255), index=True)
    discovery_method: Mapped[str] = mapped_column(String(100))
    adapter_version: Mapped[Optional[str]] = mapped_column(String(100))
    storage_path: Mapped[Optional[str]] = mapped_column(String(500))
    parser_version: Mapped[Optional[str]] = mapped_column(String(100))
    raw_body: Mapped[Optional[str]] = mapped_column(Text)
