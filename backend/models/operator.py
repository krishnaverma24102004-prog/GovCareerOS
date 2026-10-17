"""
Cyber-cafe / CSC operator console (B2B2C): per-application reports an operator prepares for a client, the
one-time tokens a client uses to claim the account, and the TEST-MODE billing ledger. No real money moves:
every ledger row is written through backend/services/billing.FakeTestProvider and carries test_mode=True.
"""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from govcareeros.backend.models.base import Base, TimestampMixin, UTCDateTime

class OperatorReport(Base, TimestampMixin):
    """One apply-ready report an operator made for one client and one vacancy or exam: the client's Application
    row and links (URL shapes of the apply-ready contract) to the official requirements, pinned to a profile version."""
    __tablename__ = "operator_reports"
    __table_args__ = (UniqueConstraint("operator_id", "idempotency_key", name="uq_operator_reports_operator_key"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    operator_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", name="fk_operator_reports_operator_id"), index=True)
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", name="fk_operator_reports_client_id"), index=True)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id", name="fk_operator_reports_application_id"), index=True)
    # vacancy | exam
    kind: Mapped[str] = mapped_column(String(10))
    vacancy_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("vacancies.id", name="fk_operator_reports_vacancy_id"))
    exam_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("exams.id", name="fk_operator_reports_exam_id"))
    profile_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("profile_versions.id", name="fk_operator_reports_profile_version_id"))
    # The consent the report was made under (purpose OPERATOR_ASSISTED_APPLICATION)
    consent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("consent_records.id", name="fk_operator_reports_consent_id"))
    # Sent by the console with each "create report" click so a double click or retry returns the same report
    idempotency_key: Mapped[str] = mapped_column(String(80))
    # JSON: title, organization, advertisement_number, apply-ready links; the facts themselves stay behind the links
    snapshot: Mapped[str] = mapped_column(Text)

class ClaimToken(Base, TimestampMixin):
    """One-time token a managed client uses to set their own email and password. Only a SHA-256 is stored."""
    __tablename__ = "claim_tokens"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", name="fk_claim_tokens_user_id"), index=True)
    created_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", name="fk_claim_tokens_created_by_id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    # Set when used or when a newer token for the same client replaced it
    used_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime())
    revoked_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime())

class BillingPrice(Base, TimestampMixin):
    """Price of one billable item (REPORT), in paise. operator_id None = the default for every operator; with no row
    at all, settings.OPERATOR_REPORT_PRICE_PAISE applies. The newest row effective at the charge time wins."""
    __tablename__ = "billing_prices"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    item: Mapped[str] = mapped_column(String(20), index=True)
    operator_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", name="fk_billing_prices_operator_id"), index=True)
    amount_paise: Mapped[int] = mapped_column(Integer)
    effective_from: Mapped[datetime] = mapped_column(UTCDateTime())
    set_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", name="fk_billing_prices_set_by_id"))

class LedgerEntry(Base, TimestampMixin):
    """One TEST-MODE charge to an operator. At most one CHARGE per report (the unique constraint makes a retried
    or concurrent charge fail instead of double-billing). Never updated."""
    __tablename__ = "operator_ledger_entries"
    __table_args__ = (UniqueConstraint("report_id", "entry_type", name="uq_operator_ledger_entries_report_type"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    operator_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", name="fk_operator_ledger_entries_operator_id"), index=True)
    report_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("operator_reports.id", name="fk_operator_ledger_entries_report_id"))
    # CHARGE
    entry_type: Mapped[str] = mapped_column(String(20))
    item: Mapped[str] = mapped_column(String(20))
    amount_paise: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    # Always FAKE_TEST: no real payment gateway is wired in
    provider: Mapped[str] = mapped_column(String(30))
    provider_ref: Mapped[str] = mapped_column(String(100))
    test_mode: Mapped[bool] = mapped_column(Boolean, default=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
