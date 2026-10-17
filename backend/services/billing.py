"""
Operator billing, TEST MODE ONLY. Prices come from billing_prices (or settings.OPERATOR_REPORT_PRICE_PAISE); each
report is charged at most once through a PaymentProvider. The only provider is FakeTestProvider: it moves no money,
calls no gateway and its references say so. Wiring a real gateway is a separate, reviewed change.
"""
import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Protocol, Tuple

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.config.settings import settings
from govcareeros.backend.models.operator import BillingPrice, LedgerEntry, OperatorReport
from govcareeros.backend.models.user import User
from govcareeros.backend.parsing.upsc_advertisement import IST

ITEM_REPORT = "REPORT"
TEST_MODE_NOTE = "TEST MODE: no real payment gateway is connected and no money is collected."

@dataclass(frozen=True)
class ProviderCharge:
    reference: str
    amount_paise: int
    currency: str
    test_mode: bool

class PaymentProvider(Protocol):
    name: str
    test_mode: bool

    def charge(self, idempotency_key: str, amount_paise: int, currency: str, description: str) -> ProviderCharge:
        """Charge once per idempotency_key; the same key returns the first charge, never a second one."""
        ...

class FakeTestProvider:
    """TEST-ONLY provider: records charges in memory, contacts nothing, collects nothing."""
    name = "FAKE_TEST"
    test_mode = True

    def __init__(self) -> None:
        self.charges: Dict[str, ProviderCharge] = {}

    def charge(self, idempotency_key: str, amount_paise: int, currency: str, description: str) -> ProviderCharge:
        if amount_paise < 0:
            raise ValueError("negative charge")
        if idempotency_key not in self.charges:
            ref = "fake_test_" + hashlib.sha256(idempotency_key.encode()).hexdigest()[:24]
            self.charges[idempotency_key] = ProviderCharge(ref, amount_paise, currency, True)
        return self.charges[idempotency_key]

_provider = FakeTestProvider()

def get_provider() -> PaymentProvider:
    return _provider

async def price_for(session: AsyncSession, operator_id: uuid.UUID, item: str = ITEM_REPORT,
                    at: Optional[datetime] = None) -> int:
    """The operator's own price if one is set, else the default row, else settings; the newest effective row wins."""
    at = at or datetime.now(timezone.utc)
    rows = (await session.execute(select(BillingPrice).where(
        BillingPrice.item == item, BillingPrice.effective_from <= at,
        or_(BillingPrice.operator_id == operator_id, BillingPrice.operator_id.is_(None)))
        .order_by(BillingPrice.effective_from.desc()))).scalars().all()
    own = next((r for r in rows if r.operator_id == operator_id), None)
    default = next((r for r in rows if r.operator_id is None), None)
    if own or default:
        return (own or default).amount_paise
    if item == ITEM_REPORT:
        return settings.OPERATOR_REPORT_PRICE_PAISE
    raise ValueError(f"no price for {item!r}")

async def charge_for_report(session: AsyncSession, report_id: uuid.UUID) -> Optional[LedgerEntry]:
    return (await session.execute(select(LedgerEntry).where(
        LedgerEntry.report_id == report_id, LedgerEntry.entry_type == "CHARGE"))).scalar_one_or_none()

async def charge_report(session: AsyncSession, report: OperatorReport, provider: Optional[PaymentProvider] = None,
                        now: Optional[datetime] = None) -> Tuple[LedgerEntry, bool]:
    """(ledger entry, created). A report already charged returns its entry; the provider sees the report id as the
    idempotency key, so even a retry after a lost response cannot charge twice."""
    existing = await charge_for_report(session, report.id)
    if existing is not None:
        return existing, False
    provider = provider or get_provider()
    if not provider.test_mode:
        raise RuntimeError("only test-mode payment providers are allowed")
    now = now or datetime.now(timezone.utc)
    amount = await price_for(session, report.operator_id, ITEM_REPORT, now)
    charge = provider.charge(f"report:{report.id}", amount, "INR", f"Apply-ready report {report.id}")
    entry = LedgerEntry(id=uuid.uuid4(), operator_id=report.operator_id, report_id=report.id, entry_type="CHARGE",
                        item=ITEM_REPORT, amount_paise=charge.amount_paise, currency=charge.currency, provider=provider.name,
                        provider_ref=charge.reference, test_mode=True, at=now)
    try:
        async with session.begin_nested():
            session.add(entry)
    except IntegrityError:
        # A concurrent request charged it first; that row is the one charge
        existing = await charge_for_report(session, report.id)
        if existing is None:
            raise
        return existing, False
    return entry, True

def month_bounds(month: str) -> Tuple[datetime, datetime]:
    """'2026-10' -> [1 Oct 00:00 IST, 1 Nov 00:00 IST) as UTC-aware datetimes."""
    year, mon = (int(x) for x in month.split("-"))
    if not 1 <= mon <= 12:
        raise ValueError("month must be YYYY-MM")
    start = datetime(year, mon, 1, tzinfo=IST)
    end = datetime(year + (mon == 12), 1 if mon == 12 else mon + 1, 1, tzinfo=IST)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)

async def statement(session: AsyncSession, operator_id: uuid.UUID, month: str) -> Dict[str, Any]:
    start, end = month_bounds(month)
    rows = (await session.execute(
        select(LedgerEntry, OperatorReport, User.full_name)
        .join(OperatorReport, OperatorReport.id == LedgerEntry.report_id)
        .join(User, User.id == OperatorReport.client_id)
        .where(LedgerEntry.operator_id == operator_id, and_(LedgerEntry.at >= start, LedgerEntry.at < end))
        .order_by(LedgerEntry.at))).all()
    lines = [{
        "entry_id": str(e.id), "at": e.at.isoformat(), "report_id": str(r.id), "client_id": str(r.client_id),
        "client_name": name, "title": json.loads(r.snapshot).get("title"), "item": e.item, "amount_paise": e.amount_paise,
        "currency": e.currency, "provider": e.provider, "provider_ref": e.provider_ref, "test_mode": e.test_mode,
    } for e, r, name in rows]
    return {"month": month, "operator_id": str(operator_id), "period_start": start.isoformat(),
            "period_end": (end - timedelta(microseconds=1)).isoformat(), "entries": lines, "report_count": len(lines),
            "total_paise": sum(line["amount_paise"] for line in lines), "currency": "INR", "test_mode": True,
            "note": TEST_MODE_NOTE}
