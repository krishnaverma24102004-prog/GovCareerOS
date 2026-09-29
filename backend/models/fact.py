import uuid
from typing import Optional, List
from sqlalchemy import String, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from govcareeros.backend.models.base import Base, TimestampMixin

class Fact(Base, TimestampMixin):
    __tablename__ = "profile_facts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    profile_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profile_versions.id"), index=True)
    subject: Mapped[str] = mapped_column(String(255))
    predicate: Mapped[str] = mapped_column(String(255))
    value: Mapped[str] = mapped_column(Text)
    # UNVERIFIED, VERIFIED, REJECTED
    status: Mapped[str] = mapped_column(String(100), default="UNVERIFIED")

    profile_version: Mapped["ProfileVersion"] = relationship(back_populates="facts")
    evidence: Mapped[List["Evidence"]] = relationship(back_populates="fact")

class Evidence(Base, TimestampMixin):
    __tablename__ = "profile_evidence"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    fact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profile_facts.id"), index=True)
    # DOCUMENT, USER_ATTESTED, APPROVED
    evidence_type: Mapped[str] = mapped_column(String(100))
    artifact_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("raw_documents.id"), index=True)
    source: Mapped[Optional[str]] = mapped_column(Text)

    fact: Mapped["Fact"] = relationship(back_populates="evidence")
    artifact: Mapped[Optional["RawDocument"]] = relationship()
