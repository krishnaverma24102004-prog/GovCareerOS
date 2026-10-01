"""
One official change to an examination (re-opened window, revised dates, schedule, a post's row read anew,
posts added), with the document that states it. Same pattern as VacancyVersion: the exam row holds the
current values, recomputed from the notice's own values plus every ExamVersion in notice-date order
(services/exam_corrigenda.py), and `details` keeps old/new values with the page and sentence quoted.
"""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from govcareeros.backend.models.base import Base, TimestampMixin, UTCDateTime

class ExamVersion(Base, TimestampMixin):
    __tablename__ = "exam_versions"
    __table_args__ = (UniqueConstraint("exam_id", "raw_document_id", name="uq_exam_versions_exam_document"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    exam_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("exams.id", name="fk_exam_versions_exam_id"), index=True)
    raw_document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("raw_documents.id", name="fk_exam_versions_raw_document_id"), index=True)
    notice_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("recruitment_notices.id", name="fk_exam_versions_notice_id"))
    version_hash: Mapped[str] = mapped_column(String(255))  # content hash of the notice document
    change_type: Mapped[Optional[str]] = mapped_column(String(200))  # e.g. "REOPENED_WINDOW,DATE_CHANGE", "POST_CHANGE"
    notice_date: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), index=True)
    parser_version: Mapped[Optional[str]] = mapped_column(String(100))
    change_log: Mapped[Optional[str]] = mapped_column(Text)  # one line per change, for people
    details: Mapped[Optional[str]] = mapped_column(Text)  # JSON: stated changes with old/new values, page and quote
