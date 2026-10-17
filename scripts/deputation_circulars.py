"""
Stored official PDFs -> DeputationCircular rows (backend/services/deputation_circulars.py). Reads only documents
already stored under storage/raw_documents; fetches nothing. Documents not read as DEPUTATION are listed as skipped.

    $env:PYTHONPATH="C:\\Users\\krishna"
    python govcareeros/scripts/deputation_circulars.py                 # every stored PDF not yet parsed by this parser
    python govcareeros/scripts/deputation_circulars.py --source DOPT   # one source's documents
    python govcareeros/scripts/deputation_circulars.py --raw-document-id <uuid> [--raw-document-id <uuid> ...]
"""
import argparse
import asyncio
import uuid

from sqlalchemy import select

from govcareeros.backend.database.session import AsyncSessionLocal, engine
from govcareeros.backend.models.source import GovernmentSource
from govcareeros.backend.services.deputation_circulars import parse_stored

async def main(args) -> int:
    async with AsyncSessionLocal() as session:
        source_id = None
        if args.source:
            source_id = (await session.execute(select(GovernmentSource.id).where(GovernmentSource.name == args.source))).scalar_one_or_none()
            if source_id is None:
                print(f"no source named {args.source}")
                return 1
        run = await parse_stored(session, [uuid.UUID(i) for i in args.raw_document_id or []], source_id, args.limit)
        await session.commit()
    print(f"stored {len(run.stored)}, already parsed {len(run.unchanged)}, skipped {len(run.skipped)}")
    for raw_id in run.stored:
        print(f"  circular from raw document {raw_id}")
    if args.verbose:
        for raw_id, why in run.skipped.items():
            print(f"  skipped {raw_id}: {why}")
    await engine.dispose()
    return 0

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-document-id", action="append")
    ap.add_argument("--source", help="GovernmentSource name, e.g. DOPT or MHA")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--verbose", action="store_true", help="also list skipped documents and why")
    raise SystemExit(asyncio.run(main(ap.parse_args())))
