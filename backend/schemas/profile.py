import uuid
from datetime import date, datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

_MAX_AGE_YEARS = 100

class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")

class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)

class PersonalInfoIn(_In):
    full_name: Optional[str] = None
    date_of_birth: Optional[date] = None
    gender: Optional[str] = None
    category: Optional[str] = Field(None, description="UR/GEN, EWS, OBC, SC, ST")
    is_pwbd: Optional[bool] = None
    disability_percentage: Optional[int] = Field(None, ge=0, le=100)
    disability_types: Optional[str] = Field(None, description="notification sub-category codes, e.g. 'OL' or 'LV,HH'")
    citizenship_status: Optional[Literal["INDIAN_CITIZEN", "NEPAL_SUBJECT", "BHUTAN_SUBJECT", "TIBETAN_REFUGEE_PRE_1962", "PIO_MIGRANT", "OTHER"]] = None
    is_ex_serviceman: Optional[bool] = None
    armed_forces_service_months: Optional[int] = Field(None, ge=0)
    is_central_govt_employee: Optional[bool] = None
    central_govt_service_start: Optional[date] = None
    is_state_govt_employee: Optional[bool] = None
    career_interests: Optional[str] = None
    nationality: Optional[str] = None
    contact: Optional[str] = None
    address: Optional[str] = None
    preferred_locations: Optional[str] = None

    @field_validator("date_of_birth")
    @classmethod
    def _plausible_dob(cls, v):
        # A mistyped year would otherwise give a negative or absurd age to the eligibility engine
        today = date.today()
        if v is not None and not date(today.year - _MAX_AGE_YEARS, 1, 1) <= v <= today:
            raise ValueError(f"date of birth must lie between {today.year - _MAX_AGE_YEARS}-01-01 and today")
        return v

class EducationIn(_In):
    degree: str
    institution: str
    university: Optional[str] = None
    specialization: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    percentage: Optional[float] = None
    cgpa: Optional[float] = None
    education_level: Optional[str] = Field(None, description="DIPLOMA, BACHELORS, MASTERS, DOCTORATE")
    subjects: Optional[str] = None
    equivalence: Optional[str] = None

class ExperienceIn(_In):
    organization: str
    designation: str
    employment_type: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    responsibilities: Optional[str] = None
    skills_used: Optional[str] = None
    domain: Optional[str] = None

class ProjectIn(_In):
    project_name: str
    description: Optional[str] = None
    role: Optional[str] = None
    technologies: Optional[str] = None
    domain: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None

class SkillIn(_In):
    skill_name: str
    skill_type: str
    proficiency: Optional[str] = None

class CertificationIn(_In):
    name: str
    issuer: str
    issue_date: Optional[date] = None
    expiry_date: Optional[date] = None
    credential_id: Optional[str] = None
    verification_url: Optional[str] = None

class LanguageIn(_In):
    language: str
    proficiency: str

class AchievementIn(_In):
    title: str
    description: Optional[str] = None
    date: Optional[date] = None
    evidence: Optional[str] = None

class UserDocumentIn(_In):
    document_type: str
    file_path: str

class ReservationCertificateIn(_In):
    """A category/benefit certificate as written on it; it counts for eligibility only once a reviewer verifies its Fact."""
    kind: Literal["OBC_NCL", "EWS", "SC", "ST", "PWBD", "ESM", "DOMICILE"]
    issue_date: Optional[date] = None
    financial_year: Optional[str] = Field(None, pattern=r"^\d{4}-(\d{2}|\d{4})$",
                                          description="EWS: financial year of the income stated, e.g. 2025-26")
    valid_until: Optional[date] = None
    issuing_authority: Optional[str] = None
    central_format: Optional[bool] = Field(None, description="issued in the Central Government format; null = not stated")

class ProfileIn(_In):
    """Sections left as null are carried over on PATCH and cleared on PUT."""
    change_summary: Optional[str] = None
    personal_info: Optional[PersonalInfoIn] = None
    education: Optional[List[EducationIn]] = None
    experience: Optional[List[ExperienceIn]] = None
    projects: Optional[List[ProjectIn]] = None
    skills: Optional[List[SkillIn]] = None
    certifications: Optional[List[CertificationIn]] = None
    languages: Optional[List[LanguageIn]] = None
    achievements: Optional[List[AchievementIn]] = None
    documents: Optional[List[UserDocumentIn]] = None
    reservation_certificates: Optional[List[ReservationCertificateIn]] = None

class PersonalInfoOut(PersonalInfoIn, _Out):
    model_config = ConfigDict(from_attributes=True, extra="ignore")

    @field_validator("date_of_birth", "central_govt_service_start", mode="before")
    @classmethod
    def _dt_to_date(cls, v):
        return v.date() if isinstance(v, datetime) else v

    @field_validator("date_of_birth")
    @classmethod
    def _plausible_dob(cls, v):
        return v  # stored rows are shown as they are; the bound applies to input only

def _out(model):
    class Out(model):
        model_config = ConfigDict(from_attributes=True, extra="ignore")
        id: uuid.UUID

        @field_validator("*", mode="before")
        @classmethod
        def _dt_to_date(cls, v, info):
            ann = cls.model_fields[info.field_name].annotation
            if isinstance(v, datetime) and ann in (date, Optional[date]):
                return v.date()
            return v
    Out.__name__ = model.__name__.replace("In", "Out")
    return Out

EducationOut = _out(EducationIn)
ExperienceOut = _out(ExperienceIn)
ProjectOut = _out(ProjectIn)
SkillOut = _out(SkillIn)
CertificationOut = _out(CertificationIn)
LanguageOut = _out(LanguageIn)
AchievementOut = _out(AchievementIn)
UserDocumentOut = _out(UserDocumentIn)
ReservationCertificateOut = _out(ReservationCertificateIn)

class ProfileVersionSummary(_Out):
    id: uuid.UUID
    version_number: int
    change_summary: Optional[str]
    created_at: datetime

class ProfileOut(BaseModel):
    version: ProfileVersionSummary
    personal_info: Optional[PersonalInfoOut] = None
    education: List[EducationOut] = []
    experience: List[ExperienceOut] = []
    projects: List[ProjectOut] = []
    skills: List[SkillOut] = []
    certifications: List[CertificationOut] = []
    languages: List[LanguageOut] = []
    achievements: List[AchievementOut] = []
    documents: List[UserDocumentOut] = []
    reservation_certificates: List[ReservationCertificateOut] = []

class FactIn(_In):
    subject: str
    predicate: str
    value: str

class FactOut(_Out):
    id: uuid.UUID
    profile_version_id: uuid.UUID
    subject: str
    predicate: str
    value: str
    status: str
    created_at: datetime

class EvidenceIn(_In):
    # APPROVED is not user-settable: approval belongs to a reviewer, never to the candidate
    evidence_type: Literal["DOCUMENT", "USER_ATTESTED"] = Field(description="DOCUMENT or USER_ATTESTED")
    source: Optional[str] = None
    raw_document_id: Optional[uuid.UUID] = None

class EvidenceOut(_Out):
    id: uuid.UUID
    fact_id: uuid.UUID
    evidence_type: str
    source: Optional[str]
    artifact_id: Optional[uuid.UUID]
    artifact_content_hash: Optional[str] = None
    artifact_url: Optional[str] = None
    created_at: datetime
