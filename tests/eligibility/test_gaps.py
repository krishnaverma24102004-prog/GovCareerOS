"""
Gap engine on the real UPSC vacancies (Advt 11/2026 and 12/2026, ingested from the official PDFs) over the API.

Law Officer (Vacancy No. 26091203226, GNCTD): "Two years' experience in Legal work ..." (p.8), OBC upper
age 33 with 10 years' PwBD relaxation (p.8); crucial date 16 Oct 2026.
"""
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select

from govcareeros.backend.gaps.engine import add_years
from govcareeros.backend.models import Vacancy
from govcareeros.backend.services.document_ingestion import get_or_create_source, ingest_pdf_bytes
from govcareeros.backend.sources import upsc

UPSC = Path(__file__).parents[1] / "fixtures" / "upsc"
LAW_OFFICER = "26091203226"

# OBC LL.B. with 1.5 years of legal practice on the crucial date (16 Apr 2025 -> 16 Oct 2026 = 548 days)
JUNIOR_LAWYER = {
    "personal_info": {"full_name": "Asha Rao", "date_of_birth": "1996-03-10", "category": "OBC", "is_pwbd": False,
                      "is_ex_serviceman": False, "is_central_govt_employee": False, "citizenship_status": "INDIAN_CITIZEN"},
    "education": [{"degree": "LL.B.", "institution": "Faculty of Law, University of Delhi", "education_level": "BACHELORS", "end_date": "2024-06-30"}],
    "experience": [{"organization": "Rao & Associates", "designation": "Legal Associate", "domain": "Legal",
                    "start_date": "2025-04-16", "responsibilities": "Drafting pleadings, appearing before civil courts"}],
}

async def _ingest(db_session):
    source = await get_or_create_source(db_session, upsc.UPSC_SOURCE)
    for name in ("AdvtNo-12-2026-Engl-250926_0.pdf", "AdvtNo-11-2026-Engl-100926.pdf"):
        await ingest_pdf_bytes(db_session, source, f"https://www.upsc.gov.in/sites/default/files/{name}", (UPSC / name).read_bytes())
    await db_session.commit()
    return {v.external_ref: v for v in (await db_session.execute(select(Vacancy))).scalars()}

@pytest.mark.asyncio
async def test_experience_gap_gives_earliest_date_and_cites_notification(api, login, db_session):
    vacancies = await _ingest(db_session)
    law = str(vacancies[LAW_OFFICER].id)  # plain id: a 404 rolls the shared session back and expires ORM objects
    h = await login("asha@example.com")
    v1 = (await api.put("/profile", json=JUNIOR_LAWYER, headers=h)).json()["version"]["id"]

    r = await api.get(f"/gaps?profile_version_id={v1}&vacancy_id={law}", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["eligibility_status"] == "INELIGIBLE"
    blocking = [g for g in body["gaps"] if g["blocking"]]
    assert [g["gap_type"] for g in blocking] == ["EXPERIENCE"]
    exp = blocking[0]
    assert exp["reason_code"] == "TOO_FEW_YEARS"
    # 2 years = 730.5 days at the engine's 365.25 days/year: day 731 after 16 Apr 2025
    assert exp["due_date"] == "2027-04-17"
    assert "need 2 years" in exp["next_step"] and "1.5 years" in exp["next_step"]
    assert "earliest date you'd meet it: 17 Apr 2027" in exp["next_step"]
    assert "Legal Associate @ Rao & Associates" in exp["profile_has"]
    assert exp["requirement_page"] == 8 and "Two years" in exp["requirement_quote"] and "Legal work" in exp["requirement_quote"]
    # Age, education, citizenship are met: nothing about them, and no PwBD question for a non-PwBD profile
    assert not any(g["profile_field"] == "disability_percentage" for g in body["gaps"])

    # Stored against the eligibility result and returned unchanged on the next call
    again = (await api.get(f"/gaps?profile_version_id={v1}&vacancy_id={law}", headers=h)).json()
    assert again["eligibility_result_id"] == body["eligibility_result_id"]
    assert [g["id"] for g in again["gaps"]] == [g["id"] for g in body["gaps"]]
    elig = (await api.get(f"/eligibility/{body['eligibility_result_id']}", headers=h)).json()
    assert elig["profile_version_id"] == v1 and elig["status"] == "INELIGIBLE"

    # Other users' profile versions are not found
    ravi = await login("ravi@example.com")
    assert (await api.get(f"/gaps?profile_version_id={v1}&vacancy_id={law}", headers=ravi)).status_code == 404
    assert (await api.get(f"/gaps/summary?profile_version_id={v1}", headers=ravi)).status_code == 404
    assert (await api.get(f"/gaps?profile_version_id={v1}&vacancy_id={law}")).status_code == 401

@pytest.mark.asyncio
async def test_missing_disability_percentage_is_asked_only_when_the_relaxation_matters(api, login, db_session):
    vacancies = await _ingest(db_session)
    law = str(vacancies[LAW_OFFICER].id)
    h = await login("meera@example.com")
    # Age 35 on 16 Oct 2026: over the OBC limit of 33, inside it with the PwBD relaxation of 10 years
    pwbd = {**JUNIOR_LAWYER, "personal_info": {**JUNIOR_LAWYER["personal_info"], "date_of_birth": "1991-05-01", "is_pwbd": True},
            "experience": [{**JUNIOR_LAWYER["experience"][0], "start_date": "2022-01-01"}]}
    v1 = (await api.put("/profile", json=pwbd, headers=h)).json()["version"]["id"]
    body = (await api.get(f"/gaps?profile_version_id={v1}&vacancy_id={law}", headers=h)).json()
    assert body["eligibility_status"] == "MANUAL_REVIEW"
    missing = [g for g in body["gaps"] if g["gap_type"] == "PROFILE_MISSING_INFO"]
    assert [g["profile_field"] for g in missing] == ["disability_percentage"]
    assert missing[0]["blocking"] and "PwBD age relaxation" in missing[0]["next_step"]
    assert missing[0]["requirement_page"] == 8 and "33 years for OBC" in missing[0]["requirement_quote"]

    # Same person, not PwBD: aged out, with the last crucial date the OBC limit still covered
    not_pwbd = (await api.patch("/profile", json={"personal_info": {**pwbd["personal_info"], "is_pwbd": False}}, headers=h)).json()["version"]["id"]
    body = (await api.get(f"/gaps?profile_version_id={not_pwbd}&vacancy_id={law}", headers=h)).json()
    age = [g for g in body["gaps"] if g["gap_type"] == "AGE"]
    assert len(age) == 1 and age[0]["blocking"] and age[0]["reason_code"] == "AGED_OUT"
    assert age[0]["due_date"] == "2025-04-30"  # the day before the 34th birthday
    assert not any(g["gap_type"] == "PROFILE_MISSING_INFO" for g in body["gaps"])

@pytest.mark.asyncio
async def test_summary_ranks_the_most_common_blockers(api, login, db_session):
    vacancies = await _ingest(db_session)
    law = str(vacancies[LAW_OFFICER].id)
    h = await login("asha@example.com")
    v1 = (await api.put("/profile", json=JUNIOR_LAWYER, headers=h)).json()["version"]["id"]
    assert (await api.get("/gaps/summary", headers=h)).status_code == 422

    r = await api.get(f"/gaps/summary?profile_version_id={v1}&include_closed=true", headers=h)
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["vacancies_evaluated"] == len(vacancies) == 11
    counts = [b["vacancies"] for b in s["blockers"]]
    assert counts == sorted(counts, reverse=True)
    # A law graduate is blocked from the medical/technical posts by the qualification itself
    wrong = next(b for b in s["blockers"] if (b["gap_type"], b["reason_code"]) == ("EDUCATION", "WRONG_FAMILY"))
    assert wrong["vacancies"] == counts[0] == 5
    assert any("Cardiology" in p for p in wrong["posts"]) and "Law Officer" not in wrong["posts"]
    exp = next(b for b in s["blockers"] if b["gap_type"] == "EXPERIENCE" and law in b["vacancy_ids"])
    assert exp["earliest_due_date"] == "2027-04-17" and "Law Officer" in exp["posts"]

def test_age_dates_follow_completed_years():
    assert add_years(date(1991, 5, 1), 34) == date(2025, 5, 1)
    assert add_years(date(2000, 2, 29), 25) == date(2025, 3, 1)  # the engine counts age 25 from 1 Mar
