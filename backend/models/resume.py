"""
Truth-locked resumes. A Resume is a list of ResumeClaims; every claim is one profile value quoted verbatim
and points at the Fact (and through it the Evidence) it came from. Claims for "Meets requirement" also carry
the notification requirement they satisfy, quoted with its page.
"""
import uuid
from typing import List, Optional
from sqlalchemy import String, Text, Boolean, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship
from govcareeros.backend.models.base import Base, TimestampMixin

class Resume(Base, TimestampMixin):
    __tablename__ = "resumes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", name="fk_resumes_user_id"), index=True)
    profile_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profile_versions.id", name="fk_resumes_profile_version_id"), index=True)
    vacancy_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("vacancies.id", name="fk_resumes_vacancy_id"))
    eligibility_result_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("eligibility_results.id", name="fk_resumes_eligibility_result_id"))
    builder_version: Mapped[str] = mapped_column(String(50))
    html_path: Mapped[Optional[str]] = mapped_column(String(1000))
    docx_path: Mapped[Optional[str]] = mapped_column(String(1000))

    claims: Mapped[List["ResumeClaim"]] = relationship(back_populates="resume", order_by="ResumeClaim.position")

class ResumeClaim(Base, TimestampMixin):
    __tablename__ = "resume_claims"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    resume_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resumes.id", name="fk_resume_claims_resume_id"), index=True)
    fact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profile_facts.id", name="fk_resume_claims_fact_id"), index=True)
    section: Mapped[str] = mapped_column(String(50))
    position: Mapped[int] = mapped_column(Integer)
    # Groups the claims of one profile row, e.g. "experience:<row id>"; `field` is the profile column quoted
    item_ref: Mapped[str] = mapped_column(String(100))
    field: Mapped[str] = mapped_column(String(100))
    text: Mapped[str] = mapped_column(Text)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    # "Meets requirement" claims only: the notification requirement quoted verbatim, its page and the engine outcome
    requirement_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("vacancy_requirements.id", name="fk_resume_claims_requirement_id"))
    requirement_quote: Mapped[Optional[str]] = mapped_column(Text)
    requirement_page: Mapped[Optional[int]] = mapped_column(Integer)
    requirement_outcome: Mapped[Optional[str]] = mapped_column(String(50))

    resume: Mapped["Resume"] = relationship(back_populates="claims")
    fact: Mapped["Fact"] = relationship()
