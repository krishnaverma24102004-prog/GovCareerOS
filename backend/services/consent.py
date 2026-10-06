"""Consent records (DPDP Act 2023): the one place consent is granted, checked and withdrawn."""
import json
import uuid
from datetime import datetime, timezone
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.models.consent import ConsentRecord

METHODS = ("SELF_WEB", "OPERATOR_ASSISTED")

async def record_consent(session: AsyncSession, user_id: uuid.UUID, purpose: str, data_items: Iterable[str], notice_version: str,
                         method: str = "SELF_WEB", captured_by_id: Optional[uuid.UUID] = None,
                         guardian_name: Optional[str] = None, now: Optional[datetime] = None) -> ConsentRecord:
    if method not in METHODS:
        raise ValueError(f"unknown consent method {method!r}")
    if method == "OPERATOR_ASSISTED" and captured_by_id is None:
        raise ValueError("operator-assisted consent must name the operator")
    row = ConsentRecord(user_id=user_id, purpose=purpose, data_items=json.dumps(sorted(set(data_items))),
                        notice_version=notice_version, method=method, captured_by_id=captured_by_id,
                        guardian_name=guardian_name, granted_at=now or datetime.now(timezone.utc))
    session.add(row)
    await session.flush()
    return row

async def active_consent(session: AsyncSession, user_id: uuid.UUID, purpose: str) -> Optional[ConsentRecord]:
    return (await session.execute(select(ConsentRecord).where(
        ConsentRecord.user_id == user_id, ConsentRecord.purpose == purpose, ConsentRecord.withdrawn_at.is_(None))
        .order_by(ConsentRecord.granted_at.desc()).limit(1))).scalar_one_or_none()

async def withdraw(session: AsyncSession, user_id: uuid.UUID, purpose: str, now: Optional[datetime] = None) -> int:
    rows = (await session.execute(select(ConsentRecord).where(
        ConsentRecord.user_id == user_id, ConsentRecord.purpose == purpose, ConsentRecord.withdrawn_at.is_(None)))).scalars().all()
    for r in rows:
        r.withdrawn_at = now or datetime.now(timezone.utc)
    await session.flush()
    return len(rows)
