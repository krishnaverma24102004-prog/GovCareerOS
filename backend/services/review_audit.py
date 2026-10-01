"""Append-only audit trail of reviewer decisions (backend/models/audit.py). Rows are only ever added."""
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.models.audit import ReviewAuditLog
from govcareeros.backend.models.user import User

def record(session: AsyncSession, actor: User, action: str, object_type: str, object_id: uuid.UUID,
           note: Optional[str] = None, detail: Optional[Dict[str, Any]] = None,
           at: Optional[datetime] = None) -> ReviewAuditLog:
    entry = ReviewAuditLog(
        id=uuid.uuid4(), actor_id=actor.id, action=action, object_type=object_type, object_id=object_id,
        note=note, detail=json.dumps(detail, ensure_ascii=False, default=str) if detail else None,
        at=at or datetime.now(timezone.utc),
    )
    session.add(entry)
    return entry
