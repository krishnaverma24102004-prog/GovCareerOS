import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from govcareeros.backend.models.base import Base, TimestampMixin, UTCDateTime

APPLICATION_STATUSES = ("PLANNED", "APPLIED", "FEE_PAID", "ADMIT_CARD", "EXAM", "RESULT", "SELECTED", "REJECTED", "WITHDRAWN")

class ApplicationRequirement(Base, TimestampMixin):
    """What a notice asks of an applicant (documents, certificates, uploads, fee, exemptions, payment, photo ID, portal, dates).

    Stored per notice document; a vacancy or exam gets the rows of the document it was parsed from.
    CERTIFICATE rows hold eligibility/certificate_types.CertificateRequirement as JSON (parsing/application_rules.py).
    A deterministic parse and a reviewer-approved LLM row may both state the same (req_type, code) of a document;
    both are kept (unique per parser_version) and the checklist warns where they differ (services/application_prep.py).
    """
    __tablename__ = "application_requirements"
    __table_args__ = (UniqueConstraint("raw_document_id", "req_type", "code", "parser_version",
                                       name="uq_application_requirements_doc_type_code_parser"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("government_sources.id", name="fk_application_requirements_source_id"), index=True)
    raw_document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("raw_documents.id", name="fk_application_requirements_raw_document_id"), index=True)
    advertisement_number: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    # DOCUMENT, UPLOAD_RULE, FEE, FEE_EXEMPTION, PAYMENT_MODE, FEE_RULE, PHOTO_ID, APPLICATION_PORTAL, CERTIFICATE, KEY_DATE
    req_type: Mapped[str] = mapped_column(String(40))
    code: Mapped[str] = mapped_column(String(60))
    # ALWAYS, IF_CLAIMED, CATEGORY_SC_ST_OBC, CATEGORY_OBC, CATEGORY_SC, CATEGORY_ST, CATEGORY_EWS, CATEGORY_RESERVED, PWBD, ESM,
    # AGE_RELAXATION, GOVT_SERVICE, CENTRAL_GOVT_EMPLOYEE, NAME_CHANGE
    applies_when: Mapped[str] = mapped_column(String(40))
    value: Mapped[str] = mapped_column(Text)  # JSON
    source_page: Mapped[Optional[int]] = mapped_column(Integer)
    source_section: Mapped[Optional[str]] = mapped_column(String(255))
    source_excerpt: Mapped[Optional[str]] = mapped_column(Text)
    parser_version: Mapped[str] = mapped_column(String(100))

class Application(Base, TimestampMixin):
    """A user's own application tracker entry, for a stored vacancy, a stored exam, or (free text) an exam not in the database."""
    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint("user_id", "vacancy_id", name="uq_applications_user_vacancy"),
        CheckConstraint("vacancy_id IS NOT NULL OR exam_ref IS NOT NULL", name="ck_applications_target"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", name="fk_applications_user_id"), index=True)
    vacancy_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("vacancies.id", name="fk_applications_vacancy_id"), index=True)
    exam_ref: Mapped[Optional[str]] = mapped_column(String(255))  # set to the exam's name when exam_id is given
    exam_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("exams.id", name="fk_applications_exam_id"), index=True)
    profile_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("profile_versions.id", name="fk_applications_profile_version_id"))
    status: Mapped[str] = mapped_column(String(20), default="PLANNED", index=True)
    application_number: Mapped[Optional[str]] = mapped_column(String(100))
    applied_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime())
    fee_paid_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime())
    exam_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime())
    notes: Mapped[Optional[str]] = mapped_column(Text)

    history: Mapped[List["ApplicationStatusHistory"]] = relationship(
        back_populates="application", order_by="ApplicationStatusHistory.changed_at", cascade="all, delete-orphan")

class ApplicationStatusHistory(Base, TimestampMixin):
    __tablename__ = "application_status_history"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id", name="fk_application_status_history_application_id"), index=True)
    from_status: Mapped[Optional[str]] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20))
    changed_at: Mapped[datetime] = mapped_column(UTCDateTime())
    note: Mapped[Optional[str]] = mapped_column(Text)

    application: Mapped["Application"] = relationship(back_populates="history")
