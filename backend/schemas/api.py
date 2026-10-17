import re
import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

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
    email: Optional[str]  # None only for an unclaimed operator-managed client, who cannot log in
    full_name: Optional[str]
    is_reviewer: bool = False
    is_operator: bool = False

class ClaimIn(RegisterIn):
    """A client an operator created takes over the account: the one-time code from the operator, own email and password."""
    token: str = Field(min_length=10, max_length=200)
    full_name: Optional[str] = None

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

_CERTIFICATE_CHECK = "CERTIFICATE"
_CERTIFICATE_SEVERITY = ("FAILS", "NEEDS_DOCUMENT", "CANNOT_DECIDE", "OK", "NOT_APPLICABLE")
# eligibility.esm: check names ESM_STATUS, ESM_RELEASE, ESM_AGE, ESM_RESERVATION_REUSE, ESM_AGNIVEER
_ESM_PREFIX = "ESM_"
_ESM_SEVERITY = ("FAILS", "NEEDS_HUMAN_REVIEW", "CANNOT_DECIDE", "OK", "NOT_APPLICABLE")

def _check_name(e) -> str:
    return e.get("check_name") if isinstance(e, dict) else e.check_name

class EligibilitySectionsOut(BaseModel):
    """`evidence` holds the hard checks behind `status`; `certificates` is the separate certificate-validity section
    (eligibility.certificate_check) and `esm` the ex-servicemen / Agniveer section (eligibility.esm), both stored as
    evidence rows but never counted in `status` or `confidence`."""
    model_config = ConfigDict(from_attributes=True)
    evidence: List[EligibilityEvidenceOut]
    certificates: List[EligibilityEvidenceOut] = Field([], validation_alias="evidence")
    esm: List[EligibilityEvidenceOut] = Field([], validation_alias="evidence")

    @field_validator("evidence", mode="before")
    @classmethod
    def _hard_checks(cls, v):
        return [e for e in v if _check_name(e) != _CERTIFICATE_CHECK and not _check_name(e).startswith(_ESM_PREFIX)]

    @field_validator("esm", mode="before")
    @classmethod
    def _esm_checks(cls, v):
        return [e for e in v if _check_name(e).startswith(_ESM_PREFIX)]

    @computed_field
    @property
    def esm_status(self) -> Optional[str]:
        """Worst ex-servicemen row; null when the profile claims neither ex-serviceman nor Agniveer service."""
        present = {c.outcome for c in self.esm}
        return next((s for s in _ESM_SEVERITY if s in present), None)

    @field_validator("certificates", mode="before")
    @classmethod
    def _certificate_checks(cls, v):
        return [e for e in v if _check_name(e) == _CERTIFICATE_CHECK]

    @computed_field
    @property
    def certificate_status(self) -> Optional[str]:
        """Worst certificate outcome; null when the notice has no extracted certificate requirement."""
        present = {c.outcome for c in self.certificates}
        return next((s for s in _CERTIFICATE_SEVERITY if s in present), None)

class EligibilityOut(EligibilitySectionsOut):
    id: uuid.UUID
    profile_version_id: uuid.UUID
    vacancy_id: uuid.UUID
    status: str
    confidence: float
    engine_version: str
    crucial_date: Optional[datetime]
    created_at: datetime
