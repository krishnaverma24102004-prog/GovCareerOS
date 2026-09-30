import uuid
from typing import List, Optional
from datetime import datetime
from sqlalchemy import String, Text, ForeignKey, Float, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship
from govcareeros.backend.models.base import Base, TimestampMixin, UTCDateTime

class EligibilityResult(Base, TimestampMixin):
    __tablename__ = 'eligibility_results'
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('users.id'), index=True)
    vacancy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('vacancies.id'), index=True)
    # Decisions are always made against one immutable profile snapshot, never "the current profile"
    profile_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('profile_versions.id', name='fk_eligibility_results_profile_version_id'), index=True)
    engine_version: Mapped[str] = mapped_column(String(100))
    crucial_date: Mapped[Optional[datetime]] = mapped_column(UTCDateTime())
    status: Mapped[str] = mapped_column(String(100))  # ELIGIBLE, INELIGIBLE, CONDITIONAL, MANUAL_REVIEW
    confidence: Mapped[float] = mapped_column(Float)
    # Hash of the vacancy inputs the decision used (active requirement ids, crucial date, engine version);
    # a different value means the vacancy changed since and the result is stale
    input_fingerprint: Mapped[Optional[str]] = mapped_column(String(64))

    evidence: Mapped[List['EligibilityEvidence']] = relationship(back_populates='result')

class EligibilityEvidence(Base, TimestampMixin):
    __tablename__ = 'eligibility_evidence'
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    result_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('eligibility_results.id'), index=True)
    requirement_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('vacancy_requirements.id', name='fk_eligibility_evidence_requirement_id'))
    check_name: Mapped[str] = mapped_column(String(100), default='', server_default='')  # AGE, EDUCATION, EXPERIENCE, ...
    match_type: Mapped[Optional[str]] = mapped_column(String(100))  # EXACT_MATCH, EXPLICIT_EQUIVALENCE, ...
    requirement: Mapped[str] = mapped_column(Text)
    profile_evidence: Mapped[str] = mapped_column(Text)
    rule: Mapped[str] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(String(100))
    source_page: Mapped[Optional[str]] = mapped_column(String(500))
    
    result: Mapped['EligibilityResult'] = relationship(back_populates='evidence')

class MatchScore(Base, TimestampMixin):
    __tablename__ = 'match_scores'
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('users.id'), index=True)
    vacancy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('vacancies.id'), index=True)
    profile_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('profile_versions.id', name='fk_match_scores_profile_version_id'), index=True)
    engine_version: Mapped[Optional[str]] = mapped_column(String(100))
    score: Mapped[float] = mapped_column(Float)  # 0-100
    # JSON: [{"name", "score", "weight", "evidence": [...]}, ...] plus the configured weights
    explanation: Mapped[Optional[str]] = mapped_column(Text)

class SkillGap(Base, TimestampMixin):
    __tablename__ = 'skill_gaps'
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('users.id'), index=True)
    vacancy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('vacancies.id'), index=True)
    gap_type: Mapped[str] = mapped_column(String(100))
    requirement: Mapped[str] = mapped_column(Text)
    suggested_resource: Mapped[Optional[str]] = mapped_column(Text)
