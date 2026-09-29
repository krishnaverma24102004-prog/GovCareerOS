"""
End-to-end ingestion (listing -> PDF -> RawDocument -> Vacancy + provenance) against real UPSC
fixtures served through an httpx MockTransport, persisted to SQLite and PostgreSQL.
"""
import json
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

from govcareeros.backend.models import GovernmentSource, RawDocument, Vacancy, VacancyFieldProvenance, VacancyRequirement
from govcareeros.backend.parsing.upsc_advertisement import IST
from govcareeros.backend.services.document_ingestion import run_upsc_pipeline
from govcareeros.backend.sources import upsc

UPSC = Path(__file__).parents[1] / "fixtures" / "upsc"
PDFS = {
    "https://www.upsc.gov.in/sites/default/files/AdvtNo-12-2026-Engl-250926_0.pdf": UPSC / "AdvtNo-12-2026-Engl-250926_0.pdf",
    "https://www.upsc.gov.in/sites/default/files/AdvtNo-11-2026-Engl-100926.pdf": UPSC / "AdvtNo-11-2026-Engl-100926.pdf",
}

def _portal(status_override=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if status_override:
            return httpx.Response(status_override, text="Access Denied")
        url = str(request.url)
        if url == upsc.LISTING_URL:
            return httpx.Response(200, text=(UPSC / "recruitment-advertisement.html").read_text(encoding="utf-8"))
        if url in PDFS:
            return httpx.Response(200, content=PDFS[url].read_bytes(), headers={"content-type": "application/pdf"})
        return httpx.Response(404)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))

async def _count(session, model):
    return (await session.execute(select(func.count()).select_from(model))).scalar()

@pytest.mark.asyncio
async def test_pipeline_persists_vacancies_with_provenance(db_session):
    async with _portal() as client:
        run = await run_upsc_pipeline(db_session, client)
    await db_session.commit()

    assert run.source_health == "HEALTHY", run.errors
    assert run.errors == []
    assert [d.advertisement_number for d in run.documents] == ["12/2026", "11/2026"]
    assert all(d.warnings == [] for d in run.documents)
    assert await _count(db_session, RawDocument) == 2
    assert await _count(db_session, Vacancy) == 11

    raw = (await db_session.execute(select(RawDocument).where(RawDocument.url.like("%AdvtNo-12%")))).scalar_one()
    assert raw.content_hash.startswith("sha256:") and Path(raw.storage_path).read_bytes().startswith(b"%PDF")

    v = (await db_session.execute(select(Vacancy).where(Vacancy.external_ref == "26091202226"))).scalar_one()
    assert v.post_name == "Assistant Legislative Counsel (Grade IV of Indian Legal Service)"
    assert v.vacancy_count == 8
    assert v.organization == "Union Public Service Commission"
    assert v.advertisement_number == "12/2026"
    assert v.application_mode == "ONLINE"
    # 18:00 IST on the closing date, identical on SQLite and PostgreSQL
    assert v.application_deadline == datetime(2026, 10, 16, 18, 0, tzinfo=IST)
    assert v.application_deadline.utcoffset() is not None
    assert v.eligibility_crucial_date == v.application_deadline
    assert v.raw_document_id == raw.id
    assert v.confidence == 1.0

    # Field-level provenance: post_name and deadline trace to the RawDocument page + excerpt
    prov = {p.field_name: p for p in (await db_session.execute(
        select(VacancyFieldProvenance).where(VacancyFieldProvenance.vacancy_id == v.id))).scalars()}
    assert prov["post_name"].raw_document_id == raw.id and prov["post_name"].page == 3
    assert "Assistant Legislative Counsel" in prov["post_name"].excerpt
    assert prov["application_deadline"].page == 13
    assert "16-10-2026" in prov["application_deadline"].excerpt

    ages = [json.loads(r.value) for r in (await db_session.execute(
        select(VacancyRequirement).where(VacancyRequirement.vacancy_id == v.id, VacancyRequirement.req_type == "AGE_MAX")
    )).scalars()]
    assert {"category": "SC", "max_age": 45} in ages

    source = (await db_session.execute(select(GovernmentSource).where(GovernmentSource.name == "UPSC"))).scalar_one()
    assert source.health_status == "HEALTHY" and source.last_success is not None

@pytest.mark.asyncio
async def test_pipeline_is_idempotent(db_session):
    async with _portal() as client:
        await run_upsc_pipeline(db_session, client)
        await db_session.commit()
        counts = [await _count(db_session, m) for m in (RawDocument, Vacancy, VacancyRequirement, VacancyFieldProvenance)]
        second = await run_upsc_pipeline(db_session, client)
        await db_session.commit()

    assert [await _count(db_session, m) for m in (RawDocument, Vacancy, VacancyRequirement, VacancyFieldProvenance)] == counts
    assert all(not d.raw_document_created and not d.created_vacancy_ids and not d.reparsed_vacancy_ids for d in second.documents)

@pytest.mark.asyncio
async def test_newer_parser_reparses_same_document(db_session):
    async with _portal() as client:
        await run_upsc_pipeline(db_session, client)
        await db_session.commit()
        v = (await db_session.execute(select(Vacancy).where(Vacancy.external_ref == "26091102112"))).scalar_one()
        original_id = v.id
        v.parser_version, v.vacancy_count = "upsc_advertisement/0", None  # simulate a row from an older parser
        await db_session.commit()

        await run_upsc_pipeline(db_session, client)
        await db_session.commit()

    v = (await db_session.execute(select(Vacancy).where(Vacancy.external_ref == "26091102112"))).scalar_one()
    await db_session.refresh(v)
    assert v.id == original_id and v.vacancy_count == 60
    assert await _count(db_session, Vacancy) == 11

@pytest.mark.asyncio
async def test_access_denied_is_reported_not_bypassed(db_session):
    async with _portal(status_override=403) as client:
        run = await run_upsc_pipeline(db_session, client)
    await db_session.commit()

    assert run.source_health == "ACCESS_RESTRICTED"
    assert await _count(db_session, Vacancy) == 0
    source = (await db_session.execute(select(GovernmentSource).where(GovernmentSource.name == "UPSC"))).scalar_one()
    assert source.health_status == "ACCESS_RESTRICTED" and source.last_failure is not None
