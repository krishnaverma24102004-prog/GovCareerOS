import json
import uuid
from datetime import datetime
from typing import Dict, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.api.deps import current_user, get_session
from govcareeros.backend.models.digest import DigestRun
from govcareeros.backend.models.user import User
from govcareeros.backend.services import digest as digest_service

router = APIRouter(prefix="/digest", tags=["digest"])

class DigestRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    profile_version_id: Optional[uuid.UUID]
    period_start: datetime
    period_end: datetime
    item_count: int
    item_counts: Dict[str, int]
    omitted_count: int
    sent_at: Optional[datetime]

def _out(run: DigestRun) -> DigestRunOut:
    return DigestRunOut(id=run.id, profile_version_id=run.profile_version_id, period_start=run.period_start, period_end=run.period_end,
                        item_count=run.item_count, item_counts=json.loads(run.item_counts or "{}"), omitted_count=run.omitted_count,
                        sent_at=run.sent_at)

@router.post("/generate", response_model=DigestRunOut, status_code=status.HTTP_201_CREATED)
async def generate(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    """Build and store the caller's digest since their previous one. Never emails; delivery is scripts/send_digests.py --send."""
    _, run = await digest_service.generate(session, user)
    return _out(run)

@router.get("/latest")
async def latest(format: Literal["html", "json"] = "html", user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    run = (await session.execute(select(DigestRun).where(DigestRun.user_id == user.id)
                                 .order_by(DigestRun.period_end.desc(), DigestRun.created_at.desc()).limit(1))).scalar_one_or_none()
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no digest yet")
    path = digest_service.run_file(run, ".json" if format == "json" else ".html")
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "digest file missing")
    if format == "json":
        return JSONResponse({"run": _out(run).model_dump(mode="json"), "digest": json.loads(path.read_text(encoding="utf-8"))})
    return HTMLResponse(path.read_text(encoding="utf-8"))
