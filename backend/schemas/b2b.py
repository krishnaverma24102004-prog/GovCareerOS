"""Response shapes of the B2B API (/api/v1) and its admin endpoints. Every notice fact carries its attribution."""
import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel

from govcareeros.backend.schemas.attribution import DISCLAIMER, AttributionOut

class RequirementOut(BaseModel):
    """A requirement as the official document states it: its page and the quoted words, where the parser kept them."""
    req_type: str
    value: str
    is_mandatory: bool
    post: Optional[str] = None  # the post it applies to, for an exam rule stated for one post only
    page: Optional[int] = None
    section: Optional[str] = None
    quote: Optional[str] = None

class ParsedVacancyOut(BaseModel):
    id: uuid.UUID
    post_name: str
    organization: str
    advertisement_number: Optional[str]
    vacancy_count: Optional[int]
    status: str  # ACTIVE, CANCELLED (set only from an official notice)
    recruitment_mode: str
    application_deadline: Optional[datetime]
    attribution: Optional[AttributionOut]
    requirements: List[RequirementOut]

class ParsedExamOut(BaseModel):
    id: uuid.UUID
    name: str
    conducting_body: str
    status: str  # NOTIFIED, CANCELLED (set only from an official notice)
    application_start: Optional[datetime]
    application_end: Optional[datetime]
    fee_amount: Optional[int]
    attribution: Optional[AttributionOut]
    requirements: List[RequirementOut]

class NoticeOut(BaseModel):
    id: uuid.UUID
    source: str
    organization: str
    jurisdiction: Optional[str]
    kind: str  # VACANCY, CORRIGENDUM, EXAM_NOTICE, RESULT, ... (backend/sources/notice_classifier.py)
    title: str
    url: str  # the official link as found on the source page
    document_type: str
    published_on: Optional[date]
    last_date: Optional[date]  # closing date, as the source's listing states it
    # open: last_date is today or later (IST); closed: before today; unknown: the listing states no closing date
    status: Literal["open", "closed", "unknown"]
    advertisement_number: Optional[str]
    post_names: List[str]
    listing_evidence: Dict[str, str]  # field -> the listing's exact words, read on attribution.official_page_url
    first_seen_at: datetime
    last_seen_at: datetime
    updated_at: datetime
    attribution: Optional[AttributionOut]
    disclaimer: str = DISCLAIMER

class NoticeDetailOut(NoticeOut):
    """The notice and the records parsed from its document (none until a parser or a reviewer has read it)."""
    vacancies: List[ParsedVacancyOut] = []
    exams: List[ParsedExamOut] = []

class NoticePage(BaseModel):
    items: List[NoticeOut]
    next_cursor: Optional[str]  # None on the last page

class ChangeEventOut(BaseModel):
    cursor: str  # pass as ?since= to read what came after this event
    type: Literal["notice.new", "notice.changed"]
    created_at: datetime
    notice_id: Optional[uuid.UUID]
    vacancy_id: Optional[uuid.UUID]
    exam_id: Optional[uuid.UUID]
    version_id: Optional[uuid.UUID]
    data: Dict[str, Any]  # notice.new: {"notice": ...}; notice.changed: field-level changes with page and quote

class ChangePage(BaseModel):
    items: List[ChangeEventOut]
    next_cursor: str  # always set: poll again with it; equal to `since` when nothing new arrived
    disclaimer: str = DISCLAIMER

class WebhookIn(BaseModel):
    url: str
    events: List[Literal["notice.new", "notice.changed"]] = ["notice.new", "notice.changed"]

class WebhookOut(BaseModel):
    id: uuid.UUID
    url: str
    events: List[str]
    created_at: datetime
    disabled_at: Optional[datetime]

class WebhookCreatedOut(WebhookOut):
    secret: str  # shown once: sign-check every delivery with it

class DeliveryOut(BaseModel):
    id: uuid.UUID
    event_seq: int
    event_type: str
    status: str
    attempts: int
    next_attempt_at: Optional[datetime]
    last_attempt_at: Optional[datetime]
    response_status: Optional[int]
    error: Optional[str]
    delivered_at: Optional[datetime]

# ---------- admin ----------

class OrgIn(BaseModel):
    name: str
    contact_email: Optional[str] = None

class OrgOut(BaseModel):
    id: uuid.UUID
    name: str
    contact_email: Optional[str]
    is_active: bool
    created_at: datetime

class KeyIn(BaseModel):
    name: str
    scopes: List[str]
    rate_limit_per_minute: Optional[int] = None

class KeyOut(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    prefix: str
    scopes: List[str]
    rate_limit_per_minute: int
    created_at: datetime
    last_used_at: Optional[datetime]
    revoked_at: Optional[datetime]

class KeyCreatedOut(KeyOut):
    key: str  # shown once; only its hash and prefix are kept

class UsageOut(BaseModel):
    key_id: uuid.UUID
    prefix: str
    day: date
    endpoint: str
    requests: int
