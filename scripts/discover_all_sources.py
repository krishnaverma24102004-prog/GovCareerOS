"""
Sweep every registered official source for recruitment notices, then run the
vacancy-level pipelines for sources that have a parser (currently UPSC).

    $env:PYTHONPATH="C:\\Users\\krishna"
    python govcareeros/scripts/discover_all_sources.py            # all sources
    python govcareeros/scripts/discover_all_sources.py MHA ISRO   # only these
"""
import asyncio
import sys
from collections import Counter

from sqlalchemy import func, select

from govcareeros.backend.database.session import AsyncSessionLocal, engine
from govcareeros.backend.models import RecruitmentNotice, Vacancy
from govcareeros.backend.services.document_ingestion import run_upsc_pipeline
from govcareeros.backend.services.source_discovery import discover_all
from govcareeros.backend.sources.generic_notices import PageFetcher
from govcareeros.backend.sources.registry import SOURCES

async def main(names) -> int:
    specs = [s for s in SOURCES if not names or s.name in names]
    async with AsyncSessionLocal() as session:
        async with PageFetcher() as fetcher:
            summaries = await discover_all(session, fetcher, specs)
        await session.commit()

        if not names or "UPSC" in names:
            upsc_run = await run_upsc_pipeline(session)
            await session.commit()
        else:
            upsc_run = None

        print(f"{'SOURCE':18} {'STATUS':22} {'MODE':7} {'SEEN':>4} {'NEW':>4} {'NEW JOBS':>8}  PAGE / DETAIL")
        for s in sorted(summaries, key=lambda s: (s.status != "OK", s.status, s.name)):
            where = s.page_url if s.status == "OK" else (s.detail or s.page_url or "")
            print(f"{s.name:18} {s.status:22} {s.fetch_mode:7} {s.notices_seen:4} {s.new_notices:4} {s.new_vacancy_notices:8}  {where}"[:200])
        print("\nSources by status:", dict(Counter(s.status for s in summaries)))

        kinds = (await session.execute(
            select(RecruitmentNotice.notice_kind, func.count()).group_by(RecruitmentNotice.notice_kind)
        )).all()
        print("Stored notices by kind:", dict(kinds))
        if upsc_run:
            print(f"UPSC vacancy pipeline: {upsc_run.source_health}; "
                  f"vacancies stored: {(await session.execute(select(func.count()).select_from(Vacancy))).scalar()}")
    await engine.dispose()
    return 0

if __name__ == "__main__":
    sys.exit(asyncio.run(main(set(sys.argv[1:]))))
