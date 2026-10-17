import uuid
from datetime import datetime
from typing import List, Optional
from sqlalchemy import String, Text, Boolean, ForeignKey, DateTime, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from govcareeros.backend.models.base import Base, TimestampMixin, UTCDateTime
from govcareeros.backend.privacy.crypto import EncryptedJSON

class User(Base, TimestampMixin):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # None only for an operator-managed client who has not claimed the account yet (cannot log in)
    email: Mapped[Optional[str]] = mapped_column(String(255), unique=True, index=True)
    # "" = no password set (unclaimed operator-managed client); verify_password never accepts it
    hashed_password: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[Optional[str]] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # May approve LLM extraction drafts into vacancies
    is_reviewer: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Cyber-cafe / CSC operator: may create and serve client accounts (granted by a reviewer only)
    is_operator: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Set on a client account an operator created; the operator sees only clients with their own id here
    managed_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", name="fk_users_managed_by_id"), index=True)
    # When a managed client set their own email and password with a claim token
    claimed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime())
    # DPDP erasure (backend/privacy/erasure.py): requested -> account deactivated; due -> hard erase by the cron script;
    # erased -> personal data deleted, this row kept as an anonymised stub so audit/consent records stay attributable
    erasure_requested_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime())
    erasure_due_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime())
    erased_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime())
    # Set by an admin when the law requires keeping the data (court order, pending dispute); the hard erase skips it
    legal_hold_reason: Mapped[Optional[str]] = mapped_column(Text)
    profile_versions: Mapped[List["ProfileVersion"]] = relationship(back_populates="user")

class ProfileVersion(Base, TimestampMixin):
    __tablename__ = "profile_versions"
    __table_args__ = (UniqueConstraint("user_id", "version_number", name="uq_profile_versions_user_version"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    version_number: Mapped[int] = mapped_column(Integer, index=True)
    change_summary: Mapped[Optional[str]] = mapped_column(Text)
    
    user: Mapped["User"] = relationship(back_populates="profile_versions")
    personal_info: Mapped[Optional["PersonalInfo"]] = relationship(back_populates="profile_version")
    education: Mapped[List["Education"]] = relationship(back_populates="profile_version")
    experience: Mapped[List["Experience"]] = relationship(back_populates="profile_version")
    projects: Mapped[List["Project"]] = relationship(back_populates="profile_version")
    skills: Mapped[List["Skill"]] = relationship(back_populates="profile_version")
    certifications: Mapped[List["Certification"]] = relationship(back_populates="profile_version")
    languages: Mapped[List["Language"]] = relationship(back_populates="profile_version")
    achievements: Mapped[List["Achievement"]] = relationship(back_populates="profile_version")
    documents: Mapped[List["UserDocument"]] = relationship(back_populates="profile_version")
    reservation_certificates: Mapped[List["ReservationCertificate"]] = relationship(back_populates="profile_version")
    facts: Mapped[List["Fact"]] = relationship(back_populates="profile_version")

class PersonalInfo(Base, TimestampMixin):
    __tablename__ = "personal_info"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    profile_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profile_versions.id"), unique=True)
    full_name: Mapped[Optional[str]] = mapped_column(String(255))
    date_of_birth: Mapped[Optional[datetime]] = mapped_column(DateTime)
    gender: Mapped[Optional[str]] = mapped_column(String(50))
    # Category/caste and disability columns are encrypted at rest (backend/privacy/crypto.py)
    category: Mapped[Optional[str]] = mapped_column(EncryptedJSON(str))
    # None = not stated; eligibility then cannot apply or rule out PwBD relaxation
    is_pwbd: Mapped[Optional[bool]] = mapped_column(EncryptedJSON(bool))
    disability_percentage: Mapped[Optional[int]] = mapped_column(EncryptedJSON(int))
    # Comma-separated sub-category codes as used in notifications, e.g. "OL" or "LV,HH"
    disability_types: Mapped[Optional[str]] = mapped_column(EncryptedJSON(str))
    # INDIAN_CITIZEN, NEPAL_SUBJECT, BHUTAN_SUBJECT, TIBETAN_REFUGEE_PRE_1962, PIO_MIGRANT, OTHER
    citizenship_status: Mapped[Optional[str]] = mapped_column(String(50))
    is_ex_serviceman: Mapped[Optional[bool]] = mapped_column(Boolean)
    armed_forces_service_months: Mapped[Optional[int]] = mapped_column(Integer)
    # Ex-servicemen (eligibility/esm.py); None = not stated. Branch: ARMY, NAVY, AIR_FORCE, TERRITORIAL_ARMY.
    # Release date is the actual one, or the expected end of the term of engagement while still serving.
    esm_service_branch: Mapped[Optional[str]] = mapped_column(String(30))
    esm_enrolment_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    esm_release_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    # schemas.profile.ESM_RELEASE_TYPES, e.g. RETIRED_WITH_PENSION, COMPLETED_ENGAGEMENT_GRATUITY, STILL_SERVING
    esm_release_type: Mapped[Optional[str]] = mapped_column(String(40))
    esm_rank: Mapped[Optional[str]] = mapped_column(String(100))
    # Since release: has secured a regular civil post under a Government, whether or not still held (the notices' "have
    # already secured"); under which Government (CENTRAL / STATE); whether that post was secured through the ESM
    # reservation, its Group (A-D), and whether the self-declaration of earlier applications (DoP&T OM 36034/1/2014,
    # 14.08.2014) was given to that employer on joining
    esm_civil_post_secured: Mapped[Optional[bool]] = mapped_column(Boolean)
    esm_civil_post_government: Mapped[Optional[str]] = mapped_column(String(10))
    esm_reservation_used: Mapped[Optional[bool]] = mapped_column(Boolean)
    esm_civil_post_group: Mapped[Optional[str]] = mapped_column(String(1))
    esm_undertaking_given: Mapped[Optional[bool]] = mapped_column(Boolean)
    # Served as an Agniveer (Agnipath scheme); no captured notice states a provision for them yet
    is_agniveer: Mapped[Optional[bool]] = mapped_column(Boolean)
    is_central_govt_employee: Mapped[Optional[bool]] = mapped_column(Boolean)
    central_govt_service_start: Mapped[Optional[datetime]] = mapped_column(DateTime)
    # Deputation posts are open only to serving government officers; None = not stated
    is_state_govt_employee: Mapped[Optional[bool]] = mapped_column(Boolean)
    # Serving officers, for deputation: 7th CPC pay-matrix level held on regular basis (not an ACP/MACP level),
    # the date regular service in that level began, organisation/cadre, and return date from the last deputation
    current_pay_level: Mapped[Optional[int]] = mapped_column(Integer)
    current_level_regular_since: Mapped[Optional[datetime]] = mapped_column(DateTime)
    organisation_cadre: Mapped[Optional[str]] = mapped_column(String(255))
    last_deputation_end: Mapped[Optional[datetime]] = mapped_column(DateTime)
    # Free text the candidate writes: posts/departments/fields they want (used only for relevance, never eligibility)
    career_interests: Mapped[Optional[str]] = mapped_column(Text)
    nationality: Mapped[Optional[str]] = mapped_column(String(100))
    contact: Mapped[Optional[str]] = mapped_column(String(255))
    address: Mapped[Optional[str]] = mapped_column(Text)
    preferred_locations: Mapped[Optional[str]] = mapped_column(Text)
    profile_version: Mapped["ProfileVersion"] = relationship(back_populates="personal_info")

class Education(Base, TimestampMixin):
    __tablename__ = "education"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    profile_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profile_versions.id"), index=True)
    degree: Mapped[str] = mapped_column(String(255))
    institution: Mapped[str] = mapped_column(String(255))
    university: Mapped[Optional[str]] = mapped_column(String(255))
    specialization: Mapped[Optional[str]] = mapped_column(String(255))
    start_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    end_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    percentage: Mapped[Optional[float]] = mapped_column()
    cgpa: Mapped[Optional[float]] = mapped_column()
    education_level: Mapped[Optional[str]] = mapped_column(String(100))
    subjects: Mapped[Optional[str]] = mapped_column(Text)
    equivalence: Mapped[Optional[str]] = mapped_column(Text)
    profile_version: Mapped["ProfileVersion"] = relationship(back_populates="education")

class Experience(Base, TimestampMixin):
    __tablename__ = "experience"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    profile_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profile_versions.id"), index=True)
    organization: Mapped[str] = mapped_column(String(255))
    designation: Mapped[str] = mapped_column(String(255))
    employment_type: Mapped[Optional[str]] = mapped_column(String(100))
    start_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    end_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    responsibilities: Mapped[Optional[str]] = mapped_column(Text)
    skills_used: Mapped[Optional[str]] = mapped_column(Text)
    domain: Mapped[Optional[str]] = mapped_column(String(255))
    profile_version: Mapped["ProfileVersion"] = relationship(back_populates="experience")

class Project(Base, TimestampMixin):
    __tablename__ = "projects"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    profile_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profile_versions.id"), index=True)
    project_name: Mapped[str] = mapped_column(String(255))
    description: Mapped[Optional[str]] = mapped_column(Text)
    role: Mapped[Optional[str]] = mapped_column(String(255))
    technologies: Mapped[Optional[str]] = mapped_column(Text)
    domain: Mapped[Optional[str]] = mapped_column(String(255))
    start_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    end_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    profile_version: Mapped["ProfileVersion"] = relationship(back_populates="projects")

class Skill(Base, TimestampMixin):
    __tablename__ = "skills"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    profile_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profile_versions.id"), index=True)
    skill_name: Mapped[str] = mapped_column(String(255))
    skill_type: Mapped[str] = mapped_column(String(100))
    proficiency: Mapped[Optional[str]] = mapped_column(String(100))
    profile_version: Mapped["ProfileVersion"] = relationship(back_populates="skills")

class Certification(Base, TimestampMixin):
    __tablename__ = "certifications"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    profile_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profile_versions.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    issuer: Mapped[str] = mapped_column(String(255))
    issue_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    expiry_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    credential_id: Mapped[Optional[str]] = mapped_column(String(255))
    verification_url: Mapped[Optional[str]] = mapped_column(String(1000))
    profile_version: Mapped["ProfileVersion"] = relationship(back_populates="certifications")

class ReservationCertificate(Base, TimestampMixin):
    """A category/benefit certificate the candidate holds (eligibility.certificate_types.HeldCertificate).
    Counts as verified only when its Fact (subject "reservation_certificates") is VERIFIED by a reviewer."""
    __tablename__ = "reservation_certificates"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    profile_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profile_versions.id"), index=True)
    # CertificateKind: OBC_NCL, EWS, SC, ST, PWBD, ESM, DOMICILE. Reveals caste/disability: encrypted at rest
    kind: Mapped[str] = mapped_column(EncryptedJSON(str))
    issue_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    # EWS: the financial year whose income the certificate states, e.g. "2025-26"
    financial_year: Mapped[Optional[str]] = mapped_column(String(9))
    valid_until: Mapped[Optional[datetime]] = mapped_column(DateTime)
    issuing_authority: Mapped[Optional[str]] = mapped_column(String(255))
    # None = not stated
    central_format: Mapped[Optional[bool]] = mapped_column(Boolean)
    profile_version: Mapped["ProfileVersion"] = relationship(back_populates="reservation_certificates")

class Language(Base, TimestampMixin):
    __tablename__ = "languages"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    profile_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profile_versions.id"), index=True)
    language: Mapped[str] = mapped_column(String(100))
    proficiency: Mapped[str] = mapped_column(String(100))
    profile_version: Mapped["ProfileVersion"] = relationship(back_populates="languages")

class Achievement(Base, TimestampMixin):
    __tablename__ = "achievements"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    profile_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profile_versions.id"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[Optional[str]] = mapped_column(Text)
    date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    evidence: Mapped[Optional[str]] = mapped_column(Text)
    profile_version: Mapped["ProfileVersion"] = relationship(back_populates="achievements")

class UserDocument(Base, TimestampMixin):
    __tablename__ = "user_documents"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    profile_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profile_versions.id"), index=True)
    document_type: Mapped[str] = mapped_column(String(100))
    file_path: Mapped[str] = mapped_column(String(1000))
    verification_status: Mapped[str] = mapped_column(String(100), default="UNVERIFIED")
    profile_version: Mapped["ProfileVersion"] = relationship(back_populates="documents")
