"""
Create review drafts for VACANCY PDF notices of sources without a deterministic parser.
Uses LLM_PROVIDER and LLM_MODEL (Ollama) or NVIDIA_MODELS (NVIDIA) from .env; override with
--provider/--model. For NVIDIA, --model takes a comma-separated chain tried in order.

    $env:PYTHONPATH="C:\\Users\\krishna"
    python govcareeros/scripts/draft_notices.py --limit 5
    python govcareeros/scripts/draft_notices.py --sources CRPF DOE --provider nvidia
    python govcareeros/scripts/draft_notices.py --provider nvidia --model nvidia/nemotron-3.5-lightning-30b-a3b
    python govcareeros/scripts/draft_notices.py --limit 5 --requirements   # also draft what each notice asks of applicants
    python govcareeros/scripts/draft_notices.py --retry-failed --limit 20  # redraft notices whose draft failed at the provider

--retry-failed puts DRAFT_FAILED notices whose latest draft failed only because of the provider (5xx/429 that
outlasted the retries, network errors) back to MANUAL_REVIEW and drafts those. A draft the validator rejected is
not a failure and is never retried; REJECTED and PARSED notices are never touched.
"""
import argparse
import asyncio

from govcareeros.backend.database.session import AsyncSessionLocal, engine
from govcareeros.backend.llm.provider import get_provider
from govcareeros.backend.services.extraction_drafts import draft_pending_notices, requeue_failed_drafts
from govcareeros.backend.services.requirement_drafts import draft_requirements_for
from govcareeros.backend.sources.generic_notices import PageFetcher

async def main(args) -> int:
    provider = get_provider(args.provider, args.model)
    async with AsyncSessionLocal() as session:
        notice_ids = None
        if args.retry_failed:
            requeued = await requeue_failed_drafts(session, args.sources)
            await session.commit()
            notice_ids = [n.id for n in requeued]
            print(f"{len(requeued)} notice(s) re-queued after a provider failure")
        async with PageFetcher(allow_browser=False) as fetcher:
            drafts = await draft_pending_notices(session, fetcher, provider, args.limit, args.sources, commit_each=True,
                                                 notice_ids=notice_ids)
        if args.requirements:
            drafts += await draft_requirements_for(session, drafts, provider, commit_each=True)
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
    p.add_argument("--requirements", action="store_true", help="also draft certificates, fee, uploads, documents and dates")
    p.add_argument("--retry-failed", action="store_true",
                   help="re-queue and draft DRAFT_FAILED notices whose latest draft failed at the provider (5xx, 429, network)")
    raise SystemExit(asyncio.run(main(p.parse_args())))
