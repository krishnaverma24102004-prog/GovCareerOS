"""
Exam preparation data: official syllabus lines, previous year question papers, and per-user study plans.

Syllabus rows and question papers are official facts: each one points at the RawDocument (notice PDF / paper
PDF) it came from, with the page for syllabus lines. Study plans are the user's own, derived deterministically
from those rows (backend/services/study_plan.py).
"""
import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import Date, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from govcareeros.backend.models.base import Base, TimestampMixin, UTCDateTime

class Syllabus(Base, TimestampMixin):
    """One subtopic of an examination's syllabus, as printed in the official notice."""
    __tablename__ = "syllabus_entries"
    __table_args__ = (UniqueConstraint("raw_document_id", "exam_code", "section", "topic_order", "subtopic_order",
                                       name="uq_syllabus_entries_position"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    exam_code: Mapped[str] = mapped_column(String(40), index=True)
    section: Mapped[str] = mapped_column(String(200))  # the paper, e.g. "General Economics-I"
    section_order: Mapped[int] = mapped_column(Integer)  # position of the paper in the notice
    section_note: Mapped[Optional[str]] = mapped_column(Text)  # e.g. "For IES only"
    part: Mapped[Optional[str]] = mapped_column(String(200))
    topic: Mapped[str] = mapped_column(Text)
    topic_order: Mapped[int] = mapped_column(Integer)
    subtopic: Mapped[str] = mapped_column(Text)
    subtopic_order: Mapped[int] = mapped_column(Integer)
    raw_document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("raw_documents.id", name="fk_syllabus_entries_raw_document_id"), index=True)
    page: Mapped[int] = mapped_column(Integer)
    parser_version: Mapped[str] = mapped_column(String(50))

class QuestionPaper(Base, TimestampMixin):
    """A previous year question paper listed on the official site; raw_document_id is set once downloaded."""
    __tablename__ = "question_papers"
    __table_args__ = (UniqueConstraint("url_hash", name="uq_question_papers_url_hash"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    exam_code: Mapped[str] = mapped_column(String(40), index=True)
    exam_name: Mapped[str] = mapped_column(Text)
    stage: Mapped[Optional[str]] = mapped_column(String(20))  # PRELIMINARY, MAIN or None
    year: Mapped[int] = mapped_column(Integer, index=True)
    category: Mapped[Optional[str]] = mapped_column(String(200))
    paper_name: Mapped[str] = mapped_column(Text)
    official_url: Mapped[str] = mapped_column(Text)
    url_hash: Mapped[str] = mapped_column(String(64))
    size_label: Mapped[Optional[str]] = mapped_column(String(20))
    listing_url: Mapped[str] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime())
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime())
    raw_document_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("raw_documents.id", name="fk_question_papers_raw_document_id"))

class StudyPlan(Base, TimestampMixin):
    __tablename__ = "study_plans"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", name="fk_study_plans_user_id"), index=True)
    exam_code: Mapped[str] = mapped_column(String(40))
    start_date: Mapped[date] = mapped_column(Date)
    target_date: Mapped[date] = mapped_column(Date)  # the exam date
    weekly_hours: Mapped[float] = mapped_column(Float)
    total_weeks: Mapped[int] = mapped_column(Integer)
    revision_weeks: Mapped[int] = mapped_column(Integer)
    sections: Mapped[Optional[str]] = mapped_column(Text)  # JSON list of chosen papers; null = whole syllabus
    # The notice whose syllabus the plan was built from
    syllabus_raw_document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("raw_documents.id", name="fk_study_plans_syllabus_raw_document_id"))
    generator_version: Mapped[str] = mapped_column(String(50))

class StudyPlanItem(Base, TimestampMixin):
    __tablename__ = "study_plan_items"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("study_plans.id", name="fk_study_plan_items_plan_id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    planned_week: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20))  # TOPIC, REVISION, PYQ
    section: Mapped[Optional[str]] = mapped_column(String(200))
    topic: Mapped[str] = mapped_column(Text)
    planned_hours: Mapped[float] = mapped_column(Float)
    syllabus_page: Mapped[Optional[int]] = mapped_column(Integer)
    question_paper_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("question_papers.id", name="fk_study_plan_items_question_paper_id"))
    status: Mapped[str] = mapped_column(String(20), default="PENDING")  # PENDING, IN_PROGRESS, DONE, SKIPPED
    completed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime())
