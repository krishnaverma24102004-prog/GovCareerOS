"""
Backlog triage over every discovered notice (backend/services/triage.py). Dry run by default: prints what
would be decided and writes nothing; --apply stores the decisions (notice_triage + triage_decisions log).
Notices are never deleted or edited, and review_status is never touched.

    $env:PYTHONPATH="C:\\Users\\krishna"
    python govcareeros/scripts/triage_notices.py
    python govcareeros/scripts/triage_notices.py --stale-days 90 --top 30
    python govcareeros/scripts/triage_notices.py --apply
"""
import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from govcareeros.backend.services.triage import DEFAULT_STALE_DAYS, TriageRun, triage_notices

def report(run: TriageRun, top: int, out: Callable[[str], None] = print) -> None:
    out(f"{'APPLIED' if run.applied else 'DRY RUN (nothing written; use --apply)'}: {len(run.rows)} notices, "
        f"stale after {run.stale_days} days, as of {run.now.date().isoformat()}")
    out("decisions: " + ", ".join(f"{k} {v}" for k, v in sorted(run.counts().items())))
    out("rules:     " + ", ".join(f"{k} {v}" for k, v in sorted(run.rule_counts().items())))
    out("dates:     " + ", ".join(f"{k} {v}" for k, v in sorted(run.date_rule_counts().items())))
    out(f"changed since last applied run: {run.changed()}")
    rows = run.top(top)
    out(f"top {len(rows)} QUEUED notices awaiting review, by priority:")
    for r in rows:
        d = r.decision
        stated = d.notice_date.date.isoformat() if d.notice_date else "not stated"
        out(f"  {d.priority:4d}  {r.source:14.14} {r.notice_kind:11} {stated:10}  {r.title[:60]!s:60}  {r.url}")

async def main(argv: Optional[List[str]] = None, *, session_factory=None, now: Optional[datetime] = None,
               out: Callable[[str], None] = print) -> int:
    p = argparse.ArgumentParser(description="Deterministic backlog triage of discovered notices")
    p.add_argument("--apply", action="store_true", help="write the decisions (default: dry run)")
    p.add_argument("--stale-days", type=int, default=DEFAULT_STALE_DAYS, help=f"staleness window (default {DEFAULT_STALE_DAYS})")
    p.add_argument("--top", type=int, default=20, help="how many top-priority notices to list (default 20)")
    args = p.parse_args(argv)

    owns_engine = session_factory is None
    if owns_engine:
        from govcareeros.backend.database.session import AsyncSessionLocal
        session_factory = AsyncSessionLocal
    try:
        async with session_factory() as session:
            run = await triage_notices(session, now=now, stale_days=args.stale_days, apply=args.apply)
            if args.apply:
                await session.commit()
            else:
                await session.rollback()
        report(run, args.top, out)
    finally:
        if owns_engine:
            from govcareeros.backend.database.session import engine
            await engine.dispose()
    return 0

if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
