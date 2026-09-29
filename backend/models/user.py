import uuid
from datetime import datetime
from typing import List, Optional
from sqlalchemy import String, Text, Boolean, ForeignKey, DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship
from govcareeros.backend.models.base import Base, TimestampMixin

class User(Base, TimestampMixin):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[Optional[str]] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    profile_versions: Mapped[List["ProfileVersion"]] = relationship(back_populates="user")

class ProfileVersion(Base, TimestampMixin):
    __tablename__ = "profile_versions"
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
    facts: Mapped[List["Fact"]] = relationship(back_populates="profile_version")

class PersonalInfo(Base, TimestampMixin):
    __tablename__ = "personal_info"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    profile_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profile_versions.id"), unique=True)
    full_name: Mapped[Optional[str]] = mapped_column(String(255))
    date_of_birth: Mapped[Optional[datetime]] = mapped_column(DateTime)
    gender: Mapped[Optional[str]] = mapped_column(String(50))
    category: Mapped[Optional[str]] = mapped_column(String(100))
    # None = not stated; eligibility then cannot apply or rule out PwBD relaxation
    is_pwbd: Mapped[Optional[bool]] = mapped_column(Boolean)
    disability_percentage: Mapped[Optional[int]] = mapped_column(Integer)
    # Comma-separated sub-category codes as used in notifications, e.g. "OL" or "LV,HH"
    disability_types: Mapped[Optional[str]] = mapped_column(String(100))
    # INDIAN_CITIZEN, NEPAL_SUBJECT, BHUTAN_SUBJECT, TIBETAN_REFUGEE_PRE_1962, PIO_MIGRANT, OTHER
    citizenship_status: Mapped[Optional[str]] = mapped_column(String(50))
    is_ex_serviceman: Mapped[Optional[bool]] = mapped_column(Boolean)
    armed_forces_service_months: Mapped[Optional[int]] = mapped_column(Integer)
    is_central_govt_employee: Mapped[Optional[bool]] = mapped_column(Boolean)
    central_govt_service_start: Mapped[Optional[datetime]] = mapped_column(DateTime)
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
