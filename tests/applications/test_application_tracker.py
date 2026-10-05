"""
Application tracker: user-scoped CRUD with status history, and APPLICATION_UPDATE alerts from the real
UPSC home-page notice "Clarification for the post of Assistant Public Prosecutor in Delhi, vide Advt. No. 11 - 2026"
(captured 29-09-2026, scanned, read via OCR).
"""
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from govcareeros.backend.documents.pdf_text import tessdata_dir
from govcareeros.backend.models import UserAlert, Vacancy
from govcareeros.backend.services.application_tracker import advertisement_pattern
from govcareeros.backend.services.document_ingestion import get_or_create_source, ingest_pdf_bytes
from govcareeros.backend.services.notice_ingestion import ingest_notice_pdf
from govcareeros.backend.services.radar import run_radar
from govcareeros.backend.sources import upsc

UPSC = Path(__file__).parents[1] / "fixtures" / "upsc"
NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)

async def _ingest(db_session, *names):
    source = await get_or_create_source(db_session, upsc.UPSC_SOURCE)
    for name in names:
        await ingest_pdf_bytes(db_session, source, f"https://www.upsc.gov.in/sites/default/files/{name}", (UPSC / name).read_bytes())
    await db_session.commit()
    return source

async def _vacancy_id(db_session, ref) -> str:
    # ids, not ORM rows: a rejected request rolls the shared session back and expires loaded rows
    return str((await db_session.execute(select(Vacancy.id).where(Vacancy.external_ref == ref))).scalar_one())

def test_advertisement_number_matching_on_real_link_titles():
    # Titles and file names as they appear on https://www.upsc.gov.in/ (home.html fixture)
    p11 = advertisement_pattern("11/2026")
    assert p11.search("Clarification for the post of Assistant Public Prosecutor in Delhi, vide Advt. No. 11 - 2026")
    assert p11.search("https://www.upsc.gov.in/sites/default/files/AdvtNo-11-2026-Engl-100926.pdf")
    assert not p11.search("Advt. No. 111 - 2026") and not advertisement_pattern("1/2026").search("Advt. No. 11 - 2026")
    assert not advertisement_pattern("11/2025").search("vide Advt. No. 11 - 2026")
    assert advertisement_pattern("13/2025").search("/sites/default/files/NoticeCancellation-06-Posts-Advt-13-25-Engl-200826.pdf")
    assert advertisement_pattern("12/2026").search("Advertisement No.12 - 2026")

@pytest.mark.asyncio
async def test_tracker_crud_history_and_isolation(api, login, db_session):
    await _ingest(db_session, "AdvtNo-12-2026-Engl-250926_0.pdf")
    law = await _vacancy_id(db_session, "26091203226")
    asha, ravi = await login("asha@example.com"), await login("ravi@example.com")

    r = await api.post("/applications", json={"vacancy_id": law, "notes": "collect experience certificate"}, headers=asha)
    assert r.status_code == 201, r.text
    app = r.json()
    assert app["status"] == "PLANNED" and [(h["from_status"], h["to_status"]) for h in app["history"]] == [(None, "PLANNED")]
    assert (await api.post("/applications", json={"vacancy_id": law}, headers=asha)).status_code == 409
    assert (await api.post("/applications", json={"notes": "no target"}, headers=asha)).status_code == 422
    exam = await api.post("/applications", json={"exam_ref": "UPSC Civil Services (Preliminary) Examination 2027"}, headers=asha)
    assert exam.status_code == 201 and exam.json()["vacancy_id"] is None

    r = await api.patch(f"/applications/{app['id']}", json={
        "status": "APPLIED", "status_note": "submitted on ORA", "application_number": "APP-12-26-0001",
        "applied_at": "2026-10-01T10:30:00+05:30"}, headers=asha)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "APPLIED" and body["application_number"] == "APP-12-26-0001"
    assert [(h["from_status"], h["to_status"], h["note"]) for h in body["history"]] == [
        (None, "PLANNED", "created"), ("PLANNED", "APPLIED", "submitted on ORA")]
    assert (await api.patch(f"/applications/{app['id']}", json={"status": "APPLIED"}, headers=asha)).json()["history"] == body["history"]
    assert (await api.patch(f"/applications/{app['id']}", json={"applied_at": "2026-10-01T10:30:00"}, headers=asha)).status_code == 422
    assert (await api.patch(f"/applications/{app['id']}", json={"status": "SHORTLISTED"}, headers=asha)).status_code == 422

    listed = (await api.get("/applications", headers=asha)).json()
    assert {a["id"] for a in listed} == {app["id"], exam.json()["id"]}
    assert [a["id"] for a in (await api.get("/applications?status=APPLIED", headers=asha)).json()] == [app["id"]]

    # Another user sees nothing and can touch nothing; the same vacancy can be tracked by them separately
    assert (await api.get("/applications", headers=ravi)).json() == []
    assert (await api.get(f"/applications/{app['id']}", headers=ravi)).status_code == 404
    assert (await api.patch(f"/applications/{app['id']}", json={"status": "WITHDRAWN"}, headers=ravi)).status_code == 404
    assert (await api.delete(f"/applications/{app['id']}", headers=ravi)).status_code == 404
    assert (await api.post("/applications", json={"vacancy_id": law}, headers=ravi)).status_code == 201
    asha_version = (await api.put("/profile", json={"personal_info": {"full_name": "Asha"}}, headers=asha)).json()["version"]["id"]
    assert (await api.patch(f"/applications/{app['id']}", json={"profile_version_id": asha_version}, headers=asha)).status_code == 200
    assert (await api.post("/applications", json={"exam_ref": "x", "profile_version_id": asha_version}, headers=ravi)).status_code == 404
    assert (await api.get("/applications")).status_code == 401

    assert (await api.delete(f"/applications/{app['id']}", headers=asha)).status_code == 204
    assert (await api.get(f"/applications/{app['id']}", headers=asha)).status_code == 404

@pytest.mark.asyncio
@pytest.mark.skipif(tessdata_dir() is None, reason="the notice is scanned; needs Tesseract")
async def test_result_or_corrigendum_notice_alerts_tracked_application(api, login, db_session):
    source = await _ingest(db_session, "AdvtNo-11-2026-Engl-100926.pdf", "AdvtNo-12-2026-Engl-250926_0.pdf")
    app_post = await _vacancy_id(db_session, "26091106212")  # Assistant Public Prosecutor, Advt 11/2026
    law = await _vacancy_id(db_session, "26091203226")  # Advt 12/2026
    asha = await login("asha@example.com")
    tracked = (await api.post("/applications", json={"vacancy_id": app_post, "status": "APPLIED"}, headers=asha)).json()
    await api.post("/applications", json={"vacancy_id": law}, headers=asha)

    for name, title in [
        ("AssttPublProseGNCTD-Notice-Engl-170926Rev.pdf", "Clarification for the post of Assistant Public Prosecutor in Delhi, vide Advt. No. 11 - 2026"),
        ("NoticeCancellation-06-Posts-Advt-13-25-Engl-200826.pdf", "Notice regarding change in recruitment process for six (06) posts advertised vide Advt. No. 13-2025"),
    ]:
        await ingest_notice_pdf(db_session, source, f"https://www.upsc.gov.in/sites/default/files/{name}",
                                (UPSC / "notices" / name).read_bytes(), title)
    await db_session.commit()

    run = await run_radar(db_session, now=NOW)
    await db_session.commit()
    # The title names the advertisement (APPLICATION_UPDATE); the parsed notice's conditions are a field-level change
    # to the tracked vacancy (VACANCY_UPDATED, services/change_log.py)
    assert run.alerts_created == {"APPLICATION_UPDATE": 1, "VACANCY_UPDATED": 1}
    alerts = [a for a in (await api.get("/alerts", headers=asha)).json() if a["kind"] == "APPLICATION_UPDATE"]
    assert len(alerts) == 1 and alerts[0]["vacancy_id"] == app_post
    assert "Advt. No. 11 - 2026" in alerts[0]["body"] and "AssttPublProseGNCTD-Notice" in alerts[0]["body"]

    assert (await run_radar(db_session, now=NOW)).alerts_created == {}
    # Another user tracking nothing gets nothing
    ravi = await login("ravi@example.com")
    await run_radar(db_session, now=NOW)
    await db_session.commit()
    assert (await api.get("/alerts", headers=ravi)).json() == []
    rows = (await db_session.execute(select(UserAlert).where(UserAlert.kind == "APPLICATION_UPDATE"))).scalars().all()
    assert [r.dedupe_key.split(":")[1] for r in rows] == [tracked["id"]]
