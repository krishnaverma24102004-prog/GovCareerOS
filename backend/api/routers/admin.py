"""Operations views for reviewers: the daily job's run log (storage/logs/daily_job.jsonl)."""
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query, status

from govcareeros.backend.api.deps import current_user
from govcareeros.backend.models.user import User
from govcareeros.backend.services import run_log

router = APIRouter(prefix="/admin", tags=["admin"])

@router.get("/runs")
async def latest_runs(limit: int = Query(20, ge=1, le=200), user: User = Depends(current_user)) -> List[Dict[str, Any]]:
    """Latest daily-job runs, newest first. Tracebacks stay in the log file, not in the API."""
    if not user.is_reviewer:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "reviewer role required")
    runs = run_log.read_runs(limit)
    for run in runs:
        for step in run.get("steps", []):
            step.pop("traceback", None)
    return runs
