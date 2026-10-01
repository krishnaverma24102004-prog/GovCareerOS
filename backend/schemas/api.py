import re
import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from govcareeros.backend.schemas.attribution import DISCLAIMER, AttributionOut

class RegisterIn(BaseModel):
    email: str
    password: str = Field(min_length=10)
    full_name: Optional[str] = None

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v):
            raise ValueError("invalid email")
        return v

class LoginIn(BaseModel):
    email: str
    password: str

class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: str
    full_name: Optional[str]
    is_reviewer: bool = False

class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime

class RequirementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    req_type: str
    value: str
    is_mandatory: bool
    source_page: Optional[int]
    source_section: Optional[str]

class ProvenanceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    field_name: str
    value: Optional[str]
    raw_document_id: uuid.UUID
    page: Optional[int]
    section: Optional[str]
    excerpt: Optional[str]
    document_url: Optional[str] = None  # official URL the RawDocument was fetched from

class VacancySummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    organization: str
    ministry: Optional[str]
    department: Optional[str]
    post_name: str
    vacancy_count: Optional[int]
    advertisement_number: Optional[str]
    external_ref: Optional[str]
    application_deadline: Optional[datetime]
    notification_url: Optional[str]
    confidence: float
    recruitment_mode: str = "UNKNOWN"
    attribution: Optional[AttributionOut] = None
    disclaimer: str = DISCLAIMER

class VacancyDetail(VacancySummary):
    pay_scale: Optional[str]
    headquarters: Optional[str]
    application_mode: str
    application_start: Optional[datetime]
    eligibility_crucial_date: Optional[datetime]
    application_url: Optional[str]
    raw_document_id: Optional[uuid.UUID]
    requirements: List[RequirementOut] = []
    provenance: List[ProvenanceOut] = []

class EligibilityIn(BaseModel):
    profile_version_id: uuid.UUID
    vacancy_id: uuid.UUID

class EligibilityEvidenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    check_name: str
    outcome: str
    match_type: Optional[str]
    requirement: str
    profile_evidence: str
    rule: str
    requirement_id: Optional[uuid.UUID]
    source_page: Optional[str]

class EligibilityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    profile_version_id: uuid.UUID
    vacancy_id: uuid.UUID
    status: str
    confidence: float
    engine_version: str
    crucial_date: Optional[datetime]
    created_at: datetime
    evidence: List[EligibilityEvidenceOut]
