"""
B2B verified-notice API (/api/v1): organisations, their API keys, daily usage, the event log behind the change feed,
and webhooks with their delivery log. See backend/services/api_keys.py, b2b_events.py and webhooks.py.
"""
import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import BigInteger, Boolean, Date, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from govcareeros.backend.models.base import Base, TimestampMixin, UTCDateTime

# SQLite only auto-increments an INTEGER PRIMARY KEY
_SEQ = BigInteger().with_variant(Integer(), "sqlite")

class ApiOrg(Base, TimestampMixin):
    """A business customer of the API. Created by a reviewer; deactivating it stops every key at once."""
    __tablename__ = "api_orgs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    contact_email: Mapped[Optional[str]] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", name="fk_api_orgs_created_by_id"))

class ApiKey(Base, TimestampMixin):
    """An org's key. The key itself is shown once at creation; only its SHA-256 and its public prefix are kept."""
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("api_orgs.id", name="fk_api_keys_org_id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    prefix: Mapped[str] = mapped_column(String(20), unique=True)  # "gco_" + 8 characters, shown in listings and logs
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    scopes: Mapped[str] = mapped_column(Text)  # space-separated, e.g. "notices:read changes:read"
    rate_limit_per_minute: Mapped[Optional[int]] = mapped_column(Integer)  # None: settings.B2B_RATE_LIMIT_PER_MINUTE
    last_used_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime())
    revoked_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime())
    created_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", name="fk_api_keys_created_by_id"))

class ApiUsage(Base):
    """Answered requests per key, per day (UTC) and endpoint, for billing later. Refused requests are not counted."""
    __tablename__ = "api_usage"
    __table_args__ = (UniqueConstraint("key_id", "day", "endpoint", name="uq_api_usage_key_day_endpoint"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    key_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("api_keys.id", name="fk_api_usage_key_id"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    endpoint: Mapped[str] = mapped_column(String(60))  # e.g. "notices.list"
    requests: Mapped[int] = mapped_column(Integer, default=0)

class ApiEvent(Base):
    """One entry of the change feed and of webhook deliveries, numbered in the order it was recorded.

    notice.new      a RecruitmentNotice first seen on an official source page (notice_id)
    notice.changed  an official change to a vacancy or exam (version_id: VacancyVersion / ExamVersion id), with the
                    field-level changes as the change log read them when the event was recorded
    `payload` is that JSON, stored so the feed and every webhook delivery send the same thing."""
    __tablename__ = "api_events"

    seq: Mapped[int] = mapped_column(_SEQ, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    # "notice.new:<notice id>" / "notice.changed:<version id>": each is recorded once
    dedupe_key: Mapped[str] = mapped_column(String(120), unique=True)
    notice_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("recruitment_notices.id", name="fk_api_events_notice_id"), index=True)
    vacancy_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("vacancies.id", name="fk_api_events_vacancy_id"))
    exam_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("exams.id", name="fk_api_events_exam_id"))
    version_id: Mapped[Optional[uuid.UUID]] = mapped_column(index=True)
    payload: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)

class Webhook(Base, TimestampMixin):
    """An org's https endpoint. The signing secret is shown once at registration; it is kept (not hashed) because
    every delivery is signed with it."""
    __tablename__ = "webhooks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("api_orgs.id", name="fk_webhooks_org_id"), index=True)
    url: Mapped[str] = mapped_column(String(2000))
    events: Mapped[str] = mapped_column(Text)  # space-separated event types
    secret: Mapped[str] = mapped_column(String(100))
    # Only events recorded after registration are delivered: the last event seq at that moment
    after_seq: Mapped[int] = mapped_column(_SEQ, default=0)
    disabled_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime())

class WebhookDelivery(Base, TimestampMixin):
    """One event to one webhook: PENDING (attempts left), DELIVERED (2xx), FAILED (out of attempts), REFUSED (the
    URL resolved to a non-public address or is otherwise not allowed; never retried)."""
    __tablename__ = "webhook_deliveries"
    __table_args__ = (UniqueConstraint("webhook_id", "event_seq", name="uq_webhook_deliveries_webhook_event"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    webhook_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("webhooks.id", name="fk_webhook_deliveries_webhook_id"), index=True)
    event_seq: Mapped[int] = mapped_column(ForeignKey("api_events.seq", name="fk_webhook_deliveries_event_seq"))
    status: Mapped[str] = mapped_column(String(20), default="PENDING", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), index=True)
    last_attempt_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime())
    response_status: Mapped[Optional[int]] = mapped_column(Integer)
    error: Mapped[Optional[str]] = mapped_column(Text)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime())
