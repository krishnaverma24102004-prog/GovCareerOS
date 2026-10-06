import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from govcareeros.backend.models.base import Base, TimestampMixin, UTCDateTime

class ConsentRecord(Base, TimestampMixin):
    """One consent a person gave (DPDP Act 2023 s.6): for one purpose, over the listed data items, under one version
    of the consent notice. Never updated in place except to set withdrawn_at; a new grant is a new row."""
    __tablename__ = "consent_records"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", name="fk_consent_records_user_id"), index=True)
    # e.g. ELIGIBILITY_CHECK, ALERTS, OPERATOR_ASSISTED_APPLICATION
    purpose: Mapped[str] = mapped_column(String(60), index=True)
    data_items: Mapped[str] = mapped_column(Text)  # JSON list, e.g. ["date_of_birth", "category", "certificates"]
    notice_version: Mapped[str] = mapped_column(String(40))
    # SELF_WEB (the person on their own account) or OPERATOR_ASSISTED (an operator recorded it with the person present)
    method: Mapped[str] = mapped_column(String(30))
    granted_at: Mapped[datetime] = mapped_column(UTCDateTime())
    withdrawn_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime())
    captured_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", name="fk_consent_records_captured_by_id"))
    # Under-18s: verifiable consent of a parent / lawful guardian (DPDP s.9)
    guardian_name: Mapped[Optional[str]] = mapped_column(String(255))
