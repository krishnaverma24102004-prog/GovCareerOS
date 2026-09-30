"""
Mass recruitment examinations (SSC CGL/CHSL/JE, ...): one notice, many posts, one set of age/education rules.

Mirrors the Vacancy tables: every stored value has an ExamFieldProvenance row or, for eligibility
inputs, an ExamRequirement row citing the notice's page and exact words. Requirement rows are
superseded, never edited, because past eligibility decisions cite them.
"""
import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from govcareeros.backend.models.base import Base, TimestampMixin, UTCDateTime

class Exam(Base, TimestampMixin):
    __tablename__ = "exams"
    # The notice's file number (e.g. SSC "F. No. HQ-C1102/5/2026-C-1") makes re-ingestion idempotent
    __table_args__ = (UniqueConstraint("source_id", "external_ref", name="uq_exams_source_external_ref"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("government_sources.id", name="fk_exams_source_id"), index=True)
    conducting_body: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(500), index=True)
    exam_code: Mapped[Optional[str]] = mapped_column(String(50), index=True)
    exam_year: Mapped[Optional[int]] = mapped_column(Integer)
    external_ref: Mapped[str] = mapped_column(String(200))
    notification_url: Mapped[Optional[str]] = mapped_column(String(1000))
    notice_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("recruitment_notices.id", name="fk_exams_notice_id"))
    raw_document_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("raw_documents.id", name="fk_exams_raw_document_id"))
    parser_version: Mapped[Optional[str]] = mapped_column(String(100))

    application_start: Mapped[Optional[datetime]] = mapped_column(UTCDateTime())
    application_end: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), index=True)
    fee_payment_end: Mapped[Optional[datetime]] = mapped_column(UTCDateTime())
    correction_start: Mapped[Optional[datetime]] = mapped_column(UTCDateTime())
    correction_end: Mapped[Optional[datetime]] = mapped_column(UTCDateTime())
    fee_amount: Mapped[Optional[int]] = mapped_column(Integer)  # rupees, general candidates
    fee_exemption: Mapped[Optional[str]] = mapped_column(Text)  # the notice's own words
    # JSON: [{"stage": "Tier-I (Computer Based Examination)", "schedule": "To be notified later"}]
    exam_dates: Mapped[Optional[str]] = mapped_column(Text)
    age_crucial_date: Mapped[Optional[datetime]] = mapped_column(UTCDateTime())
    education_cutoff_date: Mapped[Optional[datetime]] = mapped_column(UTCDateTime())
    education_text: Mapped[Optional[str]] = mapped_column(Text)
    tentative_vacancies: Mapped[Optional[int]] = mapped_column(Integer)

    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    # NOTIFIED, CANCELLED (set only from an official notice)
    status: Mapped[str] = mapped_column(String(30), default="NOTIFIED", server_default="NOTIFIED", index=True)

    posts: Mapped[List["ExamPost"]] = relationship(back_populates="exam")
    requirements: Mapped[List["ExamRequirement"]] = relationship(back_populates="exam")
    provenance: Mapped[List["ExamFieldProvenance"]] = relationship(back_populates="exam")

class ExamPost(Base, TimestampMixin):
    """A post filled through the exam, with what the notice states for that post (pay, and age/education when post-wise)."""
    __tablename__ = "exam_posts"
    __table_args__ = (UniqueConstraint("exam_id", "post_key", name="uq_exam_posts_exam_post_key"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    exam_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("exams.id", name="fk_exam_posts_exam_id"), index=True)
    post_key: Mapped[str] = mapped_column(String(255))  # normalised name (+ post code) for idempotent re-parse
    post_name: Mapped[str] = mapped_column(String(500))
    post_code: Mapped[Optional[str]] = mapped_column(String(50))
    department: Mapped[Optional[str]] = mapped_column(String(500))
    pay_level: Mapped[Optional[str]] = mapped_column(String(200))
    pay_scale: Mapped[Optional[str]] = mapped_column(Text)
    age_min: Mapped[Optional[int]] = mapped_column(Integer)
    age_max: Mapped[Optional[int]] = mapped_column(Integer)
    education_text: Mapped[Optional[str]] = mapped_column(Text)
    vacancy_count: Mapped[Optional[int]] = mapped_column(Integer)
    raw_document_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("raw_documents.id", name="fk_exam_posts_raw_document_id"))
    source_page: Mapped[Optional[int]] = mapped_column(Integer)
    source_excerpt: Mapped[Optional[str]] = mapped_column(Text)

    exam: Mapped["Exam"] = relationship(back_populates="posts")

class ExamRequirement(Base, TimestampMixin):
    """Same shape and req_type vocabulary as VacancyRequirement; post_id set when the rule applies to one post only."""
    __tablename__ = "exam_requirements"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    exam_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("exams.id", name="fk_exam_requirements_exam_id"), index=True)
    post_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("exam_posts.id", name="fk_exam_requirements_post_id"), index=True)
    req_type: Mapped[str] = mapped_column(String(100))
    value: Mapped[str] = mapped_column(Text)
    is_mandatory: Mapped[bool] = mapped_column(Boolean, default=True)
    raw_document_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("raw_documents.id", name="fk_exam_requirements_raw_document_id"))
    source_page: Mapped[Optional[int]] = mapped_column(Integer)
    source_section: Mapped[Optional[str]] = mapped_column(String(255))
    source_excerpt: Mapped[Optional[str]] = mapped_column(Text)
    superseded_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), index=True)

    exam: Mapped["Exam"] = relationship(back_populates="requirements")

class ExamFieldProvenance(Base, TimestampMixin):
    __tablename__ = "exam_field_provenance"
    __table_args__ = (UniqueConstraint("exam_id", "field_name", name="uq_exam_field_provenance_field"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    exam_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("exams.id", name="fk_exam_field_provenance_exam_id"), index=True)
    field_name: Mapped[str] = mapped_column(String(100))
    value: Mapped[Optional[str]] = mapped_column(Text)
    raw_document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("raw_documents.id", name="fk_exam_field_provenance_raw_document_id"), index=True)
    page: Mapped[Optional[int]] = mapped_column(Integer)
    section: Mapped[Optional[str]] = mapped_column(String(255))
    excerpt: Mapped[Optional[str]] = mapped_column(Text)

    exam: Mapped["Exam"] = relationship(back_populates="provenance")

class ExamEligibilityResult(Base, TimestampMixin):
    """Hard eligibility of one immutable profile version for an exam (optionally one post of it)."""
    __tablename__ = "exam_eligibility_results"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", name="fk_exam_eligibility_results_user_id"), index=True)
    exam_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("exams.id", name="fk_exam_eligibility_results_exam_id"), index=True)
    post_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("exam_posts.id", name="fk_exam_eligibility_results_post_id"))
    profile_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profile_versions.id", name="fk_exam_eligibility_results_profile_version_id"), index=True)
    engine_version: Mapped[str] = mapped_column(String(100))
    crucial_date: Mapped[Optional[datetime]] = mapped_column(UTCDateTime())
    status: Mapped[str] = mapped_column(String(100))  # ELIGIBLE, INELIGIBLE, CONDITIONAL, MANUAL_REVIEW
    confidence: Mapped[float] = mapped_column(Float)
    input_fingerprint: Mapped[Optional[str]] = mapped_column(String(64))

    evidence: Mapped[List["ExamEligibilityEvidence"]] = relationship(back_populates="result")

class ExamEligibilityEvidence(Base, TimestampMixin):
    __tablename__ = "exam_eligibility_evidence"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    result_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("exam_eligibility_results.id", name="fk_exam_eligibility_evidence_result_id"), index=True)
    requirement_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("exam_requirements.id", name="fk_exam_eligibility_evidence_requirement_id"))
    check_name: Mapped[str] = mapped_column(String(100))
    match_type: Mapped[Optional[str]] = mapped_column(String(100))
    requirement: Mapped[str] = mapped_column(Text)
    profile_evidence: Mapped[str] = mapped_column(Text)
    rule: Mapped[str] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(String(100))
    source_page: Mapped[Optional[str]] = mapped_column(String(500))

    result: Mapped["ExamEligibilityResult"] = relationship(back_populates="evidence")
