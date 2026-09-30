"""
What the local dashboard (frontend/) relies on: CORS for exactly the configured origin, the reviewer
flag on /auth/me, and an official document URL on every field's provenance (real UPSC Advt 12/2026).
"""
from pathlib import Path

import pytest

from govcareeros.backend.config.settings import settings
from govcareeros.backend.services.document_ingestion import get_or_create_source, ingest_pdf_bytes
from govcareeros.backend.sources import upsc

UPSC = Path(__file__).parents[1] / "fixtures" / "upsc"

@pytest.mark.asyncio
async def test_cors_allows_only_the_configured_origin(api):
    preflight = {"Access-Control-Request-Method": "PATCH", "Access-Control-Request-Headers": "authorization,content-type"}
    ok = await api.options("/profile", headers={"Origin": settings.FRONTEND_ORIGIN, **preflight})
    assert ok.status_code == 200
    assert ok.headers["access-control-allow-origin"] == settings.FRONTEND_ORIGIN
    assert "authorization" in ok.headers["access-control-allow-headers"].lower()

    for origin in ("http://evil.example", "http://localhost:3001", "http://127.0.0.1:3000"):
        refused = await api.options("/profile", headers={"Origin": origin, **preflight})
        assert refused.status_code == 400
        assert "access-control-allow-origin" not in refused.headers
        simple = await api.get("/health", headers={"Origin": origin})
        assert simple.status_code == 200 and "access-control-allow-origin" not in simple.headers

    same = await api.get("/health", headers={"Origin": settings.FRONTEND_ORIGIN})
    assert same.headers["access-control-allow-origin"] == settings.FRONTEND_ORIGIN

@pytest.mark.asyncio
async def test_me_reports_reviewer_flag_and_provenance_links_official_pdf(api, login, db_session):
    h = await login("asha@example.com")
    assert (await api.get("/auth/me", headers=h)).json()["is_reviewer"] is False

    url = "https://www.upsc.gov.in/sites/default/files/AdvtNo-12-2026-Engl-250926_0.pdf"
    source = await get_or_create_source(db_session, upsc.UPSC_SOURCE)
    await ingest_pdf_bytes(db_session, source, url, (UPSC / "AdvtNo-12-2026-Engl-250926_0.pdf").read_bytes())
    await db_session.commit()

    law_officer = next(v for v in (await api.get("/vacancies?q=Law Officer", headers=h)).json() if v["external_ref"] == "26091203226")
    detail = (await api.get(f"/vacancies/{law_officer['id']}", headers=h)).json()
    assert detail["provenance"] and all(p["document_url"] == url for p in detail["provenance"])
    deadline = next(p for p in detail["provenance"] if p["field_name"] == "application_deadline")
    assert deadline["page"] == 13 and deadline["excerpt"]
