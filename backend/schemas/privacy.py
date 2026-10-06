import json
import uuid
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

class ConsentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purpose: str
    notice_version: str = Field(description="the notice version the person was shown (GET /privacy/notice)")
    guardian_name: Optional[str] = Field(None, max_length=255, description="required when the person is under 18")

class ConsentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    purpose: str
    data_items: List[str]
    notice_version: str
    method: str
    granted_at: datetime
    withdrawn_at: Optional[datetime]
    guardian_name: Optional[str]

    @field_validator("data_items", mode="before")
    @classmethod
    def _json(cls, v):
        return json.loads(v) if isinstance(v, str) else v

class ConsentsOut(BaseModel):
    notice_version: str
    active: List[str] = Field(description="purposes with an active consent")
    outdated: List[str] = Field(description="active consents given under an older notice version; ask again")
    records: List[ConsentOut]

class WithdrawOut(BaseModel):
    purpose: str
    withdrawn: int

class EraseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: Literal["ERASE"]

class EraseOut(BaseModel):
    erasure_requested_at: datetime
    erasure_due_at: datetime
    grace_days: int
    message: str
