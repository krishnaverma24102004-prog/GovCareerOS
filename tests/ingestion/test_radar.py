"""
Eligibility radar over the real UPSC data captured on 29-09-2026 (Advt 11/2026, 12/2026 and the
scanned notices). "now" is pinned to that date so deadline windows stay meaningful.
"""
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from govcareeros.backend.documents.pdf_text import tessdata_dir
from govcareeros.backend.models import UserAlert, Vacancy
from govcareeros.backend.services.document_ingestion import run_upsc_pipeline
from govcareeros.backend.services.radar import age_out_date, run_radar
from govcareeros.backend.sources import upsc

UPSC = Path(__file__).parents[1] / "fixtures" / "upsc"
NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)

pytestmark = pytest.mark.skipif(tessdata_dir() is None, reason="scanned notices need Tesseract")

def _portal():
    files = {
        "AdvtNo-12-2026-Engl-250926_0.pdf": UPSC / "AdvtNo-12-2026-Engl-250926_0.pdf",
        "AdvtNo-11-2026-Engl-100926.pdf": UPSC / "AdvtNo-11-2026-Engl-100926.pdf",
        "AssttPublProseGNCTD-Notice-Engl-170926Rev.pdf": UPSC / "notices" / "AssttPublProseGNCTD-Notice-Engl-170926Rev.pdf",
        "NoticeCancellation-06-Posts-Advt-13-25-Engl-200826.pdf": UPSC / "notices" / "NoticeCancellation-06-Posts-Advt-13-25-Engl-200826.pdf",
    }

    def handler(request):
        url = str(request.url)
        if url == upsc.LISTING_URL:
            return httpx.Response(200, text=(UPSC / "recruitment-advertisement.html").read_text(encoding="utf-8"))
        if url == upsc.HOME_URL:
            return httpx.Response(200, text=(UPSC / "home.html").read_text(encoding="utf-8"))
        name = url.rsplit("/", 1)[-1]
        if name in files:
            return httpx.Response(200, content=files[name].read_bytes(), headers={"content-type": "application/pdf"})
        return httpx.Response(404)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))

def law_profile(dob):
    return {
        "personal_info": {"full_name": "Asha Rao", "date_of_birth": dob, "category": "OBC", "citizenship_status": "INDIAN_CITIZEN",
                          "is_pwbd": False, "is_ex_serviceman": False, "is_central_govt_employee": False},
        "education": [{"degree": "LL.B.", "institution": "Faculty of Law, University of Delhi", "end_date": "2016-06-30"}],
        "experience": [{"organization": "Rao & Associates", "designation": "Legal Associate", "domain": "Legal", "start_date": "2017-01-01"}],
    }

def test_age_out_date():
    assert age_out_date(datetime(1993, 6, 1).date(), 33).isoformat() == "2027-06-01"
    # Born 29 Feb: in a non-leap year the birthday is taken as 1 Mar (consistent with rules.age_on)
    assert age_out_date(datetime(1996, 2, 29).date(), 32).isoformat() == "2029-03-01"
    assert age_out_date(datetime(1996, 2, 29).date(), 31).isoformat() == "2028-02-29"

@pytest.mark.asyncio
async def test_radar_alerts_from_real_upsc_data(api, login, db_session):
    async with _portal() as client:
        await run_upsc_pipeline(db_session, client)
        await db_session.commit()
    refs = {v.external_ref: v for v in (await db_session.execute(select(Vacancy))).scalars()}

    asha = await login("asha@example.com")
    # 33 on 16-10-2026: exactly at the OBC limit (33) for Law Officer, Advt 12/2026
    await api.put("/profile", json=law_profile("1993-06-01"), headers=asha)

    first = await run_radar(db_session, now=NOW)
    await db_session.commit()
    assert first.users == 1 and first.evaluations == 11
    alerts = (await api.get("/alerts", headers=asha)).json()
    kinds = {}
    for a in alerts:
        kinds.setdefault(a["kind"], []).append(a)

    law = str(refs["26091203226"].id)
    assert any(a["vacancy_id"] == law for a in kinds["NEW_MATCH"])
    age = [a for a in kinds["AGE_HEADROOM"] if a["vacancy_id"] == law]
    assert age and "limit for this post is 33 (p.8)" in age[0]["body"] and "01 Jun 2027" in age[0]["body"]

    # Advt 11/2026 closes 02-10-2026 18:00 IST: within 7 days of 29-09-2026; Advt 12/2026 (16-10) is not
    advt11 = {str(v.id) for v in refs.values() if v.advertisement_number == "11/2026"}
    assert kinds.get("DEADLINE_SOON") and all(a["vacancy_id"] in advt11 for a in kinds["DEADLINE_SOON"])
    assert all("02 Oct 2026 18:00 IST" in a["body"] for a in kinds["DEADLINE_SOON"])

    # The scanned GNCTD clarification reaches the user who was assessed for that vacancy
    app = str(refs["26091106212"].id)
    notice = [a for a in kinds["VACANCY_UPDATED"] if a["vacancy_id"] == app]
    assert notice and "CLARIFICATION" in notice[0]["body"] and "15 Sep 2026" in notice[0]["body"]

    # Re-running creates nothing new
    again = await run_radar(db_session, now=NOW)
    await db_session.commit()
    assert again.evaluations == 0 and again.alerts_created == {}
    assert len((await api.get("/alerts", headers=asha)).json()) == len(alerts)

    # Read state is per user and private
    target = alerts[0]["id"]
    assert (await api.post(f"/alerts/{target}/read", headers=asha)).json()["read_at"] is not None
    assert all(a["id"] != target for a in (await api.get("/alerts?unread_only=true", headers=asha)).json())
    ravi = await login("ravi@example.com")
    assert (await api.post(f"/alerts/{target}/read", headers=ravi)).status_code == 404
    assert (await api.get("/alerts", headers=ravi)).json() == []

@pytest.mark.asyncio
async def test_new_profile_version_is_rechecked(api, login, db_session):
    async with _portal() as client:
        await run_upsc_pipeline(db_session, client)
        await db_session.commit()
    h = await login("asha@example.com")
    await api.put("/profile", json=law_profile("1995-03-10"), headers=h)
    await run_radar(db_session, now=NOW)
    await db_session.commit()

    # A new version (PATCH) is a new profile: the radar evaluates it again and alerts are keyed per version
    await api.patch("/profile", json={"skills": [{"skill_name": "Legal drafting", "skill_type": "Domain"}]}, headers=h)
    second = await run_radar(db_session, now=NOW)
    await db_session.commit()
    assert second.evaluations == 11
    assert second.alerts_created.get("NEW_MATCH", 0) >= 1
    assert "DEADLINE_SOON" not in second.alerts_created  # deadline alerts are per vacancy, not repeated per version
    rows = (await db_session.execute(select(UserAlert).where(UserAlert.kind == "NEW_MATCH"))).scalars().all()
    assert len({r.profile_version_id for r in rows}) == 2
