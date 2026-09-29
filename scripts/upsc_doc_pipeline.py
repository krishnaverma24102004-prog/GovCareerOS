"""
Runs the real UPSC document pipeline against DATABASE_URL:
official listing page -> advertisement PDFs -> RawDocument -> Vacancy + provenance.

    $env:PYTHONPATH="C:\\Users\\krishna"
    python govcareeros/scripts/upsc_doc_pipeline.py
"""
import asyncio
import sys

from sqlalchemy import func, select

from govcareeros.backend.database.session import AsyncSessionLocal, engine
from govcareeros.backend.models import GovernmentSource, RawDocument, Vacancy, VacancyFieldProvenance, VacancyRequirement
from govcareeros.backend.services.document_ingestion import run_upsc_pipeline

async def main() -> int:
    async with AsyncSessionLocal() as session:
        run = await run_upsc_pipeline(session)
        await session.commit()

        print(f"Source health: {run.source_health}")
        for err in run.errors:
            print(f"  ERROR {err}")
        for doc in run.documents:
            print(f"Advertisement {doc.advertisement_number}: raw_document={doc.raw_document_id} "
                  f"(new={doc.raw_document_created}) created={len(doc.created_vacancy_ids)} "
                  f"reparsed={len(doc.reparsed_vacancy_ids)} "
                  f"already_present={len(doc.existing_vacancy_refs)}")
            for w in doc.warnings:
                print(f"  WARNING {w}")

        source = (await session.execute(select(GovernmentSource).where(GovernmentSource.name == "UPSC"))).scalar_one()
        vacancies = (await session.execute(
            select(Vacancy).where(Vacancy.source_id == source.id).order_by(Vacancy.advertisement_number, Vacancy.external_ref)
        )).scalars().all()
        print(f"\nStored UPSC vacancies: {len(vacancies)}")
        for v in vacancies:
            print(f"  [{v.advertisement_number} / {v.external_ref}] {v.post_name} x{v.vacancy_count} "
                  f"deadline={v.application_deadline} confidence={v.confidence}")
        for table in (RawDocument, VacancyRequirement, VacancyFieldProvenance):
            count = (await session.execute(select(func.count()).select_from(table))).scalar()
            print(f"{table.__tablename__}: {count} rows")
    await engine.dispose()
    return 0 if run.source_health == "HEALTHY" else 1

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
