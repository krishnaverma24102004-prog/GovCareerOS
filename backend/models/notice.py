import uuid
from datetime import date, datetime
from typing import Optional
from sqlalchemy import Date, Integer, String, Text, ForeignKey, UniqueConstraint, delete, event, inspect
from sqlalchemy.orm import Mapped, Session, mapped_column
from govcareeros.backend.models.base import Base, TimestampMixin, UTCDateTime
from govcareeros.backend.parsing.title_tokens import normalise, token_counts

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
    # Reviewer console: SKIPPED, NOT_REQUIRED, or back to MANUAL_REVIEW (requeue)
    review_status: Mapped[str] = mapped_column(String(30), default="NEW", index=True)
    raw_document_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("raw_documents.id"))
    # Last human decision on the notice itself (bulk skip / not required / requeue); history is in review_audit_log
    reviewer_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", name="fk_recruitment_notices_reviewer_id_users"))
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime())
    review_note: Mapped[Optional[str]] = mapped_column(Text)
    # What the source's listing adapter read from the listing row (backend/sources/adapters); None when the page
    # does not state it. listing_evidence is JSON {field: exact listing text}, read on the page at found_on_url
    published_on: Mapped[Optional[date]] = mapped_column(Date)
    last_date: Mapped[Optional[date]] = mapped_column(Date)  # closing date for applications
    advertisement_number: Mapped[Optional[str]] = mapped_column(Text)
    post_names: Mapped[Optional[str]] = mapped_column(Text)  # JSON list
    kind_hint: Mapped[Optional[str]] = mapped_column(String(30))  # the listing's own label, e.g. CORRIGENDUM
    listing_evidence: Mapped[Optional[str]] = mapped_column(Text)
    # Rows without a document of their own (url is the listing page, e.g. EMPLOYMENT-NEWS): what tells them
    # apart, hashed into url_hash together with the url
    listing_row_key: Mapped[Optional[str]] = mapped_column(Text)

class RecruitmentNoticeTitleToken(Base):
    """One distinct word of a notice title (backend/parsing/title_tokens.py), so the public notice check looks up the
    notices whose titles a pasted text covers instead of loading every notice. Titles under MIN_TITLE_TOKENS words have
    no rows (the check never matches them by wording). Written by the before_flush hook below on every ORM insert or
    title change; the migration that created the table filled it for existing notices."""
    __tablename__ = "recruitment_notice_title_tokens"

    notice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recruitment_notices.id", ondelete="CASCADE"), primary_key=True)
    token: Mapped[str] = mapped_column(Text, primary_key=True, index=True)
    occurrences: Mapped[int] = mapped_column(Integer)
    # The same on every row of a notice: its number of title words (with repeats) and normalised title length
    title_tokens: Mapped[int] = mapped_column(Integer)
    title_chars: Mapped[int] = mapped_column(Integer, index=True)

def title_token_rows(notice_id: uuid.UUID, title: str) -> list:
    counts = token_counts(title or "")
    total, chars = sum(counts.values()), len(normalise(title or ""))
    return [RecruitmentNoticeTitleToken(notice_id=notice_id, token=t, occurrences=n, title_tokens=total, title_chars=chars)
            for t, n in counts.items()]

@event.listens_for(Session, "before_flush")
def _index_notice_titles(session: Session, flush_context, instances) -> None:
    for obj in list(session.new):
        if isinstance(obj, RecruitmentNotice):
            if obj.id is None:
                obj.id = uuid.uuid4()
            session.add_all(title_token_rows(obj.id, obj.title))
    for obj in list(session.dirty):
        if isinstance(obj, RecruitmentNotice) and inspect(obj).attrs.title.history.has_changes():
            session.execute(delete(RecruitmentNoticeTitleToken).where(RecruitmentNoticeTitleToken.notice_id == obj.id))
            session.add_all(title_token_rows(obj.id, obj.title))
