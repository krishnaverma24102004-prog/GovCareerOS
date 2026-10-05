"""
One-off: extract application requirements (documents, certificates, uploads, fee, photo ID, portal, key dates) from
UPSC advertisement and SSC exam notice PDFs that were ingested before this step existed, or parsed by an older
requirements parser (a newer parser version replaces a document's rows). Reads the stored copy of each document
(RawDocument.storage_path); nothing is fetched. New ingestions do this automatically.

    $env:PYTHONPATH="C:\\Users\\krishna"
    python govcareeros/scripts/backfill_application_requirements.py
"""
import asyncio
import sys
from pathlib import Path

from sqlalchemy import select

from govcareeros.backend.database.session import AsyncSessionLocal, engine
from govcareeros.backend.documents.pdf_text import extract_pages
from govcareeros.backend.models import Exam, GovernmentSource, RawDocument, Vacancy
from govcareeros.backend.parsing.ssc_application import parse_ssc_application_info
from govcareeros.backend.parsing.upsc_application import parse_application_info
from govcareeros.backend.services.application_prep import store_application_requirements

# (RawDocument.parser_version pattern, requirements parser, query for the document's advertisement / file number)
KINDS = [
    ("upsc_advertisement/%", parse_application_info, lambda raw: select(Vacancy.advertisement_number).where(Vacancy.raw_document_id == raw.id)),
    ("ssc_exam_notice/%", parse_ssc_application_info, lambda raw: select(Exam.external_ref).where(Exam.raw_document_id == raw.id)),
]

async def main() -> int:
    async with AsyncSessionLocal() as session:
        for pattern, parse, ref_query in KINDS:
            docs = (await session.execute(select(RawDocument).where(RawDocument.parser_version.like(pattern)))).scalars().all()
            for raw in docs:
                path = Path(raw.storage_path or "")
                if not path.is_file():
                    print(f"skip {raw.url}: stored file missing")
                    continue
                ref = (await session.execute(ref_query(raw).limit(1))).scalar_one_or_none()
                if ref is None and parse is parse_ssc_application_info:
                    print(f"skip {raw.url}: no exam stored from it")
                    continue
                source = await session.get(GovernmentSource, raw.source_id)
                info = await store_application_requirements(session, source, raw, extract_pages(path.read_bytes()), ref, parse=parse)
                print(f"{raw.url}: {ref}, {len(info.items)} item(s), warnings {info.warnings}")
        await session.commit()
    await engine.dispose()
    return 0

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
