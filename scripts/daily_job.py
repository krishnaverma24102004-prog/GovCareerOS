"""
The daily job: one command, run by Windows Task Scheduler (scripts/install_schedule.ps1).

Steps, in this order; each runs in its own session and transaction, so a failing step is rolled back,
logged, and the next step still runs:
  discover  every registered official source (services.source_discovery.discover_all, PageFetcher)
  upsc      UPSC advertisements + notices (document_ingestion.run_upsc_pipeline)
  triage    fixed-rule backlog triage of every notice (services.triage): QUEUED / STALE / NOT_REQUIRED + priority,
            so drafts take the highest-priority fresh vacancies first; never deletes or edits a notice
  drafts    LLM review drafts for pending VACANCY notices - only when an LLM is configured
            (LLM_PROVIDER=nvidia with NVIDIA_API_KEY, or a reachable Ollama); drafts still need review
  radar     eligibility radar for every user (services.radar.run_radar)
  digest    weekly digest, only if backend.services.digest exists (imported lazily)

A file lock in storage/ keeps two runs from overlapping (stale after 6h). Every run, including one that
found the lock held, appends one JSON line to storage/logs/daily_job.jsonl (see services.run_log).

    $env:PYTHONPATH="C:\\Users\\krishna"
    python govcareeros/scripts/daily_job.py
    python govcareeros/scripts/daily_job.py --only radar
    python govcareeros/scripts/daily_job.py --skip discover --skip drafts --limit-drafts 3

Exit code: 0 all steps ok or skipped, 1 a step failed, 2 another run holds the lock.
"""
import argparse
import asyncio
import importlib
import importlib.util
import inspect
import sys
import time
import traceback
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

if __package__ in (None, ""):
    # Run as a file (Task Scheduler): make "govcareeros" importable without relying on PYTHONPATH
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx

from govcareeros.backend.config.settings import settings
from govcareeros.backend.services import run_log

STEPS = ("discover", "upsc", "triage", "drafts", "radar", "digest")
_DIGEST_MODULE = "govcareeros.backend.services.digest"
# backend.services.digest.run_all writes digest files only; emailing stays an explicit send_digests.py --send
_DIGEST_FUNCS = ("run_all", "run_weekly_digest", "generate_weekly_digests", "run_digest")

class Skipped(Exception):
    """Raised by a step that decided not to run; the message is logged as the reason."""

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

async def llm_configured() -> Optional[str]:
    """Why drafts cannot run, or None when an LLM is configured."""
    name = settings.LLM_PROVIDER.lower()
    if name == "nvidia":
        return None if settings.NVIDIA_API_KEY.strip() else "no LLM configured (LLM_PROVIDER=nvidia, NVIDIA_API_KEY empty)"
    if name == "ollama":
        try:
            async with httpx.AsyncClient(timeout=3) as c:
                (await c.get(f"{settings.OLLAMA_URL.rstrip('/')}/api/tags")).raise_for_status()
            return None
        except httpx.HTTPError:
            return f"no LLM configured (Ollama not reachable at {settings.OLLAMA_URL})"
    return f"no LLM configured (LLM_PROVIDER={name!r} not supported)"

class DailyJob:
    def __init__(self, session_factory, *, fetcher_factory=None, upsc_client: Optional[httpx.AsyncClient] = None,
                 provider=None, now: Optional[datetime] = None, limit_drafts: int = 5, stale_days: Optional[int] = None):
        self.session_factory = session_factory
        self.fetcher_factory = fetcher_factory
        self.upsc_client = upsc_client
        self.provider = provider
        self.now = now
        self.limit_drafts = limit_drafts
        self.stale_days = stale_days
        self.source_health: Dict[str, Any] = {}

    def _fetcher(self, **kw):
        if self.fetcher_factory:
            return self.fetcher_factory(**kw)
        from govcareeros.backend.sources.generic_notices import PageFetcher
        return PageFetcher(**kw)

    async def discover(self, session) -> Dict[str, Any]:
        from govcareeros.backend.services.source_discovery import discover_all
        async with self._fetcher() as fetcher:
            summaries = await discover_all(session, fetcher)
        self.source_health["discovery"] = dict(Counter(s.status for s in summaries))
        self.source_health["failing_sources"] = sorted(s.name for s in summaries if s.status != "OK")
        return {"sources": len(summaries), "notices_seen": sum(s.notices_seen for s in summaries),
                "new_notices": sum(s.new_notices for s in summaries),
                "new_vacancy_notices": sum(s.new_vacancy_notices for s in summaries)}

    async def upsc(self, session) -> Dict[str, Any]:
        from govcareeros.backend.services.document_ingestion import run_upsc_pipeline
        run = await run_upsc_pipeline(session, self.upsc_client)
        self.source_health["UPSC"] = run.source_health
        return {"health": run.source_health, "advertisements": len(run.documents), "notices": len(run.notices),
                "vacancies_created": sum(len(d.created_vacancy_ids) for d in run.documents),
                "vacancies_reparsed": sum(len(d.reparsed_vacancy_ids) for d in run.documents),
                "pipeline_errors": run.errors}

    async def triage(self, session) -> Dict[str, Any]:
        from govcareeros.backend.services.triage import DEFAULT_STALE_DAYS, triage_notices
        run = await triage_notices(session, now=self.now, stale_days=self.stale_days or DEFAULT_STALE_DAYS, apply=True)
        return run.summary()

    async def drafts(self, session) -> Dict[str, Any]:
        from govcareeros.backend.services.extraction_drafts import draft_pending_notices
        provider = self.provider
        if provider is None:
            reason = await llm_configured()
            if reason:
                raise Skipped(reason)
            from govcareeros.backend.llm.provider import get_provider
            provider = get_provider()
        async with self._fetcher(allow_browser=False) as fetcher:
            drafts = await draft_pending_notices(session, fetcher, provider, self.limit_drafts, commit_each=True)
        return {"provider": f"{provider.name}:{provider.model}", "drafts": len(drafts),
                "by_status": dict(Counter(d.status for d in drafts))}

    async def radar(self, session) -> Dict[str, Any]:
        from govcareeros.backend.services.radar import run_radar
        run = await run_radar(session, now=self.now)
        return {"users": run.users, "evaluations": run.evaluations, "alerts_created": run.alerts_created}

    async def digest(self, session) -> Dict[str, Any]:
        # Absent module -> skipped; a module that exists but fails to import is a real failure
        if importlib.util.find_spec(_DIGEST_MODULE) is None:
            raise Skipped("backend.services.digest not present")
        digest = importlib.import_module(_DIGEST_MODULE)
        func = next((getattr(digest, n) for n in _DIGEST_FUNCS if callable(getattr(digest, n, None))), None)
        if func is None:
            raise Skipped(f"backend.services.digest has none of {', '.join(_DIGEST_FUNCS)}")
        kwargs = {"now": self.now} if self.now and "now" in inspect.signature(func).parameters else {}
        result = func(session, **kwargs)
        if inspect.isawaitable(result):
            result = await result
        return {"function": func.__name__, "result": result if isinstance(result, (int, dict, list, str)) else repr(result)}

    async def run_step(self, name: str) -> Dict[str, Any]:
        step: Dict[str, Any] = {"name": name, "started_at": _now()}
        started = time.monotonic()
        async with self.session_factory() as session:
            try:
                step["counts"] = await getattr(self, name)(session)
                await session.commit()
                step["status"] = "ok"
            except Skipped as e:
                await session.rollback()
                step.update(status="skipped", reason=str(e))
            except Exception as e:  # isolated: log it and let the next step run
                await session.rollback()
                step.update(status="error", error=f"{type(e).__name__}: {e}",
                            traceback=traceback.format_exc(limit=8)[-4000:])
        step["seconds"] = round(time.monotonic() - started, 1)
        return step

def select_steps(only: Optional[List[str]], skip: Optional[List[str]]) -> List[str]:
    chosen = [s for s in STEPS if not only or s in only]
    return [s for s in chosen if s not in (skip or [])]

def parse_args(argv: Optional[List[str]]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="GovCareerOS daily job")
    p.add_argument("--only", action="append", choices=STEPS, metavar="STEP", help=f"run only this step (repeatable): {', '.join(STEPS)}")
    p.add_argument("--skip", action="append", choices=STEPS, metavar="STEP", help="skip this step (repeatable)")
    p.add_argument("--limit-drafts", type=int, default=5, help="at most N LLM drafts per run (default 5)")
    p.add_argument("--stale-days", type=int, default=None, help="triage: VACANCY/CORRIGENDUM notices older than this are STALE (default 120)")
    return p.parse_args(argv)

async def main(argv: Optional[List[str]] = None, *, session_factory=None, log_path: Optional[Path] = None,
               lock_path: Optional[Path] = None, **job_kwargs) -> int:
    args = parse_args(argv)
    steps = select_steps(args.only, args.skip)
    record: Dict[str, Any] = {"run_id": str(uuid.uuid4()), "started_at": _now(), "argv": list(argv or []), "steps": []}
    lock = run_log.RunLock(lock_path)
    try:
        lock.acquire()
    except run_log.LockHeld as e:
        record.update(finished_at=_now(), status="LOCKED", error=str(e))
        run_log.append_run(record, log_path)
        print(f"daily_job: another run is active ({e})", file=sys.stderr)
        return 2

    owns_engine = session_factory is None
    if owns_engine:
        from govcareeros.backend.database.session import AsyncSessionLocal
        session_factory = AsyncSessionLocal
    try:
        if lock.took_over:
            record["took_over_stale_lock"] = lock.took_over
        job = DailyJob(session_factory, limit_drafts=args.limit_drafts, stale_days=args.stale_days, **job_kwargs)
        for name in STEPS:
            if name not in steps:
                record["steps"].append({"name": name, "status": "skipped", "reason": "not selected"})
                continue
            step = await job.run_step(name)
            record["steps"].append(step)
            detail = step.get("counts") or step.get("reason") or step.get("error")
            print(f"[{step['status']:7}] {name:8} {step['seconds']:7.1f}s  {detail}")
        record["source_health"] = job.source_health
    except BaseException as e:  # outside any step (e.g. Ctrl+C): still log the run, then re-raise
        record["error"] = f"{type(e).__name__}: {e}"
        raise
    finally:
        failed = [s["name"] for s in record["steps"] if s["status"] == "error"]
        status = "CRASHED" if "error" in record else "FAILED" if failed else "OK"
        record.update(finished_at=_now(), status=status, failed_steps=failed)
        run_log.append_run(record, log_path)
        lock.release()
        if owns_engine:
            from govcareeros.backend.database.session import engine
            await engine.dispose()
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
