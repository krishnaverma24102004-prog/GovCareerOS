"""
SSC notice board -> RecruitmentNotice rows -> exam notices (PDF) -> Exam rows.
Notices the deterministic parser cannot read become LLM review drafts only with --llm
(LLM_PROVIDER / LLM_MODEL from .env).

    $env:PYTHONPATH="C:\\Users\\krishna"
    python govcareeros/scripts/ssc_exams.py --pages 5
    python govcareeros/scripts/ssc_exams.py --pages 5 --llm
    python govcareeros/scripts/ssc_exams.py --observe     # list the public JSON calls ssc.gov.in's pages make
"""
import argparse
import asyncio

from govcareeros.backend.database.session import AsyncSessionLocal, engine
from govcareeros.backend.services.exam_ingestion import run_ssc_pipeline
from govcareeros.backend.sources import ssc
from govcareeros.backend.sources.generic_notices import PageFetcher

async def main(args) -> int:
    if args.observe:
        for page in (ssc.HOME_URL, ssc.NOTICE_BOARD_PAGE):
            for e in await ssc.observe_json_endpoints(page):
                print(f"{page}  {e.method} {e.status} {e.url}")
        return 0
    provider = None
    if args.llm:
        from govcareeros.backend.llm.provider import get_provider
        provider = get_provider()
    async with AsyncSessionLocal() as session:
        async with PageFetcher(allow_browser=True) as fetcher:
            run = await run_ssc_pipeline(session, fetcher, pages=args.pages, limit=args.limit, provider=provider)
        await session.commit()
    p = run["probe"]
    print(f"notice board: {p.status} via {p.fetch_mode}, {p.notices_seen} notices, {p.new_notices} new")
    for r in run["exams"]:
        print(f"  exam {r.exam_id or '-'}  created={r.created} reparsed={r.reparsed} {r.skipped_reason or ''}"
              + (f"  warnings: {'; '.join(r.warnings)}" if r.warnings else ""))
    for d in run["drafts"]:
        print(f"  draft {d.id} {d.status} accepted {d.fields_accepted}/{d.fields_offered}")
    for e in run["errors"]:
        print(f"  error: {e[:200]}")
    await engine.dispose()
    return 0

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=3, help="notice-board pages of 10 to read")
    ap.add_argument("--limit", type=int, default=5, help="exam notices to download per run")
    ap.add_argument("--llm", action="store_true")
    ap.add_argument("--observe", action="store_true")
    raise SystemExit(asyncio.run(main(ap.parse_args())))
