"""Career relevance on the real UPSC vacancies (Advt 11/2026, 12/2026) over the API."""
from pathlib import Path

import pytest
from sqlalchemy import select

from govcareeros.backend.models import Vacancy
from govcareeros.backend.services.document_ingestion import get_or_create_source, ingest_pdf_bytes
from govcareeros.backend.sources import upsc

UPSC = Path(__file__).parents[1] / "fixtures" / "upsc"

LAW_GRAD = {
    "personal_info": {"full_name": "Asha Rao", "date_of_birth": "1995-03-10", "category": "OBC", "is_pwbd": False,
                      "is_ex_serviceman": False, "is_central_govt_employee": False, "citizenship_status": "INDIAN_CITIZEN",
                      "preferred_locations": "Delhi, Mumbai", "career_interests": "Law Officer, legal advisory, litigation"},
    "education": [{"degree": "LL.B.", "institution": "Faculty of Law, University of Delhi", "education_level": "BACHELORS", "end_date": "2021-06-30"}],
    "experience": [{"organization": "Rao & Associates", "designation": "Legal Associate", "domain": "Legal",
                    "start_date": "2022-01-01", "responsibilities": "Drafting pleadings, land acquisition matters, civil suits in district courts"}],
    "skills": [{"skill_name": "Legal drafting", "skill_type": "Domain"}, {"skill_name": "Civil litigation", "skill_type": "Domain"}],
}

async def _ingest(db_session):
    source = await get_or_create_source(db_session, upsc.UPSC_SOURCE)
    for name in ("AdvtNo-12-2026-Engl-250926_0.pdf", "AdvtNo-11-2026-Engl-100926.pdf"):
        await ingest_pdf_bytes(db_session, source, f"https://www.upsc.gov.in/sites/default/files/{name}", (UPSC / name).read_bytes())
    await db_session.commit()
    return {v.external_ref: v for v in (await db_session.execute(select(Vacancy))).scalars()}

@pytest.mark.asyncio
async def test_relevance_components_are_transparent(api, login, db_session):
    vacancies = await _ingest(db_session)
    h = await login("asha@example.com")
    v1 = (await api.put("/profile", json=LAW_GRAD, headers=h)).json()["version"]["id"]

    async def score(ref, **extra):
        r = await api.post("/relevance", json={"profile_version_id": v1, "vacancy_id": str(vacancies[ref].id), **extra}, headers=h)
        assert r.status_code == 201, r.text
        return r.json()

    law = await score("26091203226")          # Law Officer, Land & Building Dept, Delhi
    cardiology = await score("26091204526")   # Specialist Grade II Cardiology, NDMC

    comps = {c["name"]: c for c in law["components"]}
    assert set(comps) == {"education", "skills", "experience", "domain", "location", "role"}
    assert round(sum(c["weight"] for c in law["components"]), 2) == 100.0
    assert all(c["evidence"] for c in law["components"])
    assert comps["education"]["score"] == 100 and "EXACT_MATCH" in comps["education"]["evidence"][0]
    assert comps["experience"]["score"] == 100
    assert comps["location"]["score"] == 100 and "Delhi" in comps["location"]["evidence"][0]
    assert comps["role"]["score"] > 0
    assert law["score"] >= 70

    assert cardiology["score"] < 30
    assert {c["name"]: c for c in cardiology["components"]}["education"]["score"] == 0
    assert law["score"] > cardiology["score"]

    # Custom weights are honoured and reported
    edu_only = await score("26091203226", weights={"education": 1, "skills": 0, "experience": 0, "domain": 0, "location": 0, "role": 0})
    assert edu_only["score"] == 100.0 and edu_only["weights"]["education"] == 1
    bad = await api.post("/relevance", json={"profile_version_id": v1, "vacancy_id": str(vacancies["26091203226"].id),
                                             "weights": {"education": 1}}, headers=h)
    assert bad.status_code == 422

    stored = (await api.get(f"/relevance/{law['id']}", headers=h)).json()
    assert stored["score"] == law["score"] and stored["components"] == law["components"]

@pytest.mark.asyncio
async def test_missing_profile_inputs_are_not_scored_as_zero(api, login, db_session):
    vacancies = await _ingest(db_session)
    h = await login("asha@example.com")
    minimal = {"personal_info": {"date_of_birth": "1995-03-10", "category": "OBC"},
               "education": LAW_GRAD["education"], "experience": LAW_GRAD["experience"]}
    v = (await api.put("/profile", json=minimal, headers=h)).json()["version"]["id"]
    r = (await api.post("/relevance", json={"profile_version_id": v, "vacancy_id": str(vacancies["26091203226"].id)}, headers=h)).json()
    comps = {c["name"]: c for c in r["components"]}
    assert comps["location"]["score"] is None and comps["location"]["weight"] == 0
    assert comps["skills"]["score"] is None and "no skills" in comps["skills"]["evidence"][0]
    assert round(sum(c["weight"] for c in r["components"]), 2) == 100.0

@pytest.mark.asyncio
async def test_matches_rank_eligibility_first_then_relevance(api, login, db_session):
    vacancies = await _ingest(db_session)
    h = await login("asha@example.com")
    v1 = (await api.put("/profile", json=LAW_GRAD, headers=h)).json()["version"]["id"]
    assert (await api.get("/matches", headers=h)).status_code == 422  # profile_version_id is mandatory

    r = await api.get(f"/matches?profile_version_id={v1}&include_closed=true", headers=h)
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) == len(vacancies) == 11
    assert rows[0]["post_name"] == "Law Officer" and rows[0]["eligibility_status"] == "ELIGIBLE"
    order = {"ELIGIBLE": 0, "CONDITIONAL": 1, "MANUAL_REVIEW": 2, "INELIGIBLE": 3}
    keys = [(order[m["eligibility_status"]], -m["relevance_score"]) for m in rows]
    assert keys == sorted(keys)

    ravi = await login("ravi@example.com")
    assert (await api.get(f"/matches?profile_version_id={v1}&include_closed=true", headers=ravi)).status_code == 404
