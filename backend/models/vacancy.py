from datetime import datetime
import uuid
from typing import List, Optional
from sqlalchemy import String, Text, Boolean, ForeignKey, Integer, DateTime, Float, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from govcareeros.backend.models.base import Base, TimestampMixin, UTCDateTime

class Vacancy(Base, TimestampMixin):
    __tablename__ = 'vacancies'
    # A source's own vacancy identifier (e.g. UPSC "Vacancy No. 26091201626") makes re-ingestion idempotent
    __table_args__ = (UniqueConstraint('source_id', 'external_ref', name='uq_vacancies_source_external_ref'),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    organization: Mapped[str] = mapped_column(String(255), index=True)
    ministry: Mapped[Optional[str]] = mapped_column(String(255))
    department: Mapped[Optional[str]] = mapped_column(String(255))
    post_name: Mapped[str] = mapped_column(String(255), index=True)
    vacancy_count: Mapped[Optional[int]] = mapped_column(Integer)
    advertisement_number: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    external_ref: Mapped[Optional[str]] = mapped_column(String(100))
    pay_scale: Mapped[Optional[str]] = mapped_column(Text)
    headquarters: Mapped[Optional[str]] = mapped_column(Text)
    eligibility_crucial_date: Mapped[Optional[datetime]] = mapped_column(UTCDateTime())
    
    application_mode: Mapped[str] = mapped_column(String(100))
    application_start: Mapped[Optional[datetime]] = mapped_column(UTCDateTime())
    application_deadline: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), index=True)
    correction_deadline: Mapped[Optional[datetime]] = mapped_column(UTCDateTime())
    
    notification_url: Mapped[Optional[str]] = mapped_column(String(1000))
    application_url: Mapped[Optional[str]] = mapped_column(String(1000))
    source_url: Mapped[Optional[str]] = mapped_column(String(1000))
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('government_sources.id'))
    raw_document_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('raw_documents.id'))
    parser_version: Mapped[Optional[str]] = mapped_column(String(100))

    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    # ACTIVE, CANCELLED (set only from an official notice, recorded in vacancy_versions)
    status: Mapped[str] = mapped_column(String(30), default="ACTIVE", server_default="ACTIVE", index=True)

    requirements: Mapped[List['VacancyRequirement']] = relationship(back_populates='vacancy')
    provenance: Mapped[List['VacancyFieldProvenance']] = relationship(back_populates='vacancy')
    versions: Mapped[List['VacancyVersion']] = relationship(back_populates='vacancy')

class VacancyRequirement(Base, TimestampMixin):
    __tablename__ = 'vacancy_requirements'
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    vacancy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('vacancies.id'), index=True)
    req_type: Mapped[str] = mapped_column(String(100))
    value: Mapped[str] = mapped_column(Text)
    is_mandatory: Mapped[bool] = mapped_column(Boolean, default=True)
    raw_document_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('raw_documents.id', name='fk_vacancy_requirements_raw_document_id'))
    source_page: Mapped[Optional[int]] = mapped_column(Integer)
    source_section: Mapped[Optional[str]] = mapped_column(String(255))
    source_excerpt: Mapped[Optional[str]] = mapped_column(Text)
    # Set when a re-parse replaces this row; kept because eligibility evidence may reference it
    superseded_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), index=True)

    vacancy: Mapped['Vacancy'] = relationship(back_populates='requirements')

class VacancyFieldProvenance(Base, TimestampMixin):
    """Where a single Vacancy field value came from: document, page, section, exact excerpt."""
    __tablename__ = 'vacancy_field_provenance'
    __table_args__ = (UniqueConstraint('vacancy_id', 'field_name', name='uq_vacancy_field_provenance_field'),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    vacancy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('vacancies.id'), index=True)
    field_name: Mapped[str] = mapped_column(String(100))
    value: Mapped[Optional[str]] = mapped_column(Text)
    raw_document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('raw_documents.id'), index=True)
    page: Mapped[Optional[int]] = mapped_column(Integer)
    section: Mapped[Optional[str]] = mapped_column(String(255))
    excerpt: Mapped[Optional[str]] = mapped_column(Text)

    vacancy: Mapped['Vacancy'] = relationship(back_populates='provenance')

class VacancyVersion(Base, TimestampMixin):
    __tablename__ = 'vacancy_versions'
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    vacancy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('vacancies.id'), index=True)
    version_hash: Mapped[str] = mapped_column(String(255))
    change_log: Mapped[Optional[str]] = mapped_column(Text)
    
    vacancy: Mapped['Vacancy'] = relationship(back_populates='versions')
