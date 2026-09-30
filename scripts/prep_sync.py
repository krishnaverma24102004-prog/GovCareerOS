"""
Refresh exam preparation data from upsc.gov.in: list every previous year question paper, download papers of
chosen exams, and ingest the syllabus from examination pages' notices.

    $env:PYTHONPATH="C:\\Users\\krishna"
    python govcareeros/scripts/prep_sync.py                                     # list papers only
    python govcareeros/scripts/prep_sync.py --download IES_ISS --limit 10 --max-mb 5
    python govcareeros/scripts/prep_sync.py --exam-page "https://www.upsc.gov.in/examinations/Indian%20Economic%20Service%20-%20Indian%20Statistical%20Service%20Examination%2C%202026"
"""
import argparse
import asyncio
import sys

from govcareeros.backend.database.session import AsyncSessionLocal, engine
from govcareeros.backend.services.exam_prep import run_prep_sync

async def main(args) -> int:
    async with AsyncSessionLocal() as session:
        run = await run_prep_sync(session, download_exam_codes=args.download, download_limit=args.limit,
                                  max_mb=args.max_mb, exam_pages=args.exam_page)
        await session.commit()
    await engine.dispose()
    print(f"UPSC question papers: {run.source_health}; listed {run.papers_listed} (new {run.papers_created}), "
          f"downloaded {run.papers_downloaded}")
    for s in run.syllabi:
        print(f"Syllabus: {s.entries} lines from {len(s.papers)} papers ({', '.join(s.papers)}); warnings {s.warnings}")
    for line in run.skipped + run.errors:
        print(" -", line)
    return 0 if run.source_health in ("HEALTHY", "DEGRADED") else 1

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--download", nargs="*", default=[], help="exam codes whose papers to download, e.g. IES_ISS CSE")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--max-mb", type=float, default=None)
    parser.add_argument("--exam-page", action="append", default=[], help="UPSC examination page URL (repeatable)")
    sys.exit(asyncio.run(main(parser.parse_args())))
