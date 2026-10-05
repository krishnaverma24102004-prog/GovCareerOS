"""
Create review drafts for VACANCY PDF notices of sources without a deterministic parser.
Uses LLM_PROVIDER and LLM_MODEL (Ollama) or NVIDIA_MODELS (NVIDIA) from .env; override with
--provider/--model. For NVIDIA, --model takes a comma-separated chain tried in order.

    $env:PYTHONPATH="C:\\Users\\krishna"
    python govcareeros/scripts/draft_notices.py --limit 5
    python govcareeros/scripts/draft_notices.py --sources CRPF DOE --provider nvidia
    python govcareeros/scripts/draft_notices.py --provider nvidia --model nvidia/nemotron-3.5-lightning-30b-a3b
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
            drafts = await draft_pending_notices(session, fetcher, provider, args.limit, args.sources, commit_each=True)
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
    p.add_argument("--model", help="model; for NVIDIA a comma-separated fallback chain")
    raise SystemExit(asyncio.run(main(p.parse_args())))
