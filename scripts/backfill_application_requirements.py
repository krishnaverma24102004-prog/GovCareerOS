"""
One-off: extract application requirements (documents, fee, photo ID, portal) from advertisement PDFs that were
ingested before this step existed. Reads the stored copy of each document (RawDocument.storage_path); nothing is
fetched. New ingestions do this automatically.

    $env:PYTHONPATH="C:\\Users\\krishna"
    python govcareeros/scripts/backfill_application_requirements.py
"""
import asyncio
import sys
from pathlib import Path

from sqlalchemy import select

from govcareeros.backend.database.session import AsyncSessionLocal, engine
from govcareeros.backend.documents.pdf_text import extract_pages
from govcareeros.backend.models import GovernmentSource, RawDocument, Vacancy
from govcareeros.backend.services.application_prep import store_application_requirements

async def main() -> int:
    async with AsyncSessionLocal() as session:
        docs = (await session.execute(select(RawDocument).where(RawDocument.parser_version.like("upsc_advertisement/%")))).scalars().all()
        for raw in docs:
            path = Path(raw.storage_path or "")
            if not path.is_file():
                print(f"skip {raw.url}: stored file missing")
                continue
            advt = (await session.execute(select(Vacancy.advertisement_number).where(Vacancy.raw_document_id == raw.id).limit(1))).scalar_one_or_none()
            source = await session.get(GovernmentSource, raw.source_id)
            info = await store_application_requirements(session, source, raw, extract_pages(path.read_bytes()), advt)
            print(f"{raw.url}: Advt {advt}, {len(info.items)} item(s), warnings {info.warnings}")
        await session.commit()
    await engine.dispose()
    return 0

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
