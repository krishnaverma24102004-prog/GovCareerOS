"""
Create review drafts for VACANCY PDF notices of sources without a deterministic parser.
Uses LLM_PROVIDER / LLM_MODEL from .env (override with --provider/--model).

    $env:PYTHONPATH="C:\\Users\\krishna"
    python govcareeros/scripts/draft_notices.py --limit 5
    python govcareeros/scripts/draft_notices.py --sources CRPF DOE --provider nvidia --model nvidia/nemotron-3-super-120b-a12b
"""
import argparse
import asyncio

from govcareeros.backend.database.session import AsyncSessionLocal, engine
from govcareeros.backend.llm.provider import get_provider
from govcareeros.backend.services.extraction_drafts import draft_pending_notices
from govcareeros.backend.sources.generic_notices import PageFetcher

async def main(args) -> int:
    provider = get_provider(args.provider, args.model)
    async with AsyncSessionLocal() as session:
        async with PageFetcher(allow_browser=False) as fetcher:
            drafts = await draft_pending_notices(session, fetcher, provider, args.limit, args.sources)
        await session.commit()
        print(f"{len(drafts)} draft(s) with {provider.name}:{provider.model}")
        for d in drafts:
            print(f"  {d.id} {d.status:15} accepted {d.fields_accepted}/{d.fields_offered} fields, {d.llm_seconds}s"
                  + (f"  error: {d.error[:120]}" if d.error else ""))
    await engine.dispose()
    return 0

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--sources", nargs="*")
    p.add_argument("--provider")
    p.add_argument("--model")
    raise SystemExit(asyncio.run(main(p.parse_args())))
