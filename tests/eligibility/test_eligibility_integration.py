"""
Real UPSC vacancies (Advt 11/2026 and 12/2026, ingested from the official PDFs) + a versioned
master profile -> hard eligibility over the API, including historical reproducibility.
"""
from pathlib import Path

import pytest
from sqlalchemy import select

from govcareeros.backend.models import Vacancy
from govcareeros.backend.services.document_ingestion import get_or_create_source, ingest_pdf_bytes
from govcareeros.backend.sources import upsc

UPSC = Path(__file__).parents[1] / "fixtures" / "upsc"

LAW_GRAD = {
    "personal_info": {"full_name": "Asha Rao", "date_of_birth": "1995-03-10", "category": "OBC", "is_pwbd": False, "nationality": "Indian"},
    "education": [{"degree": "LL.B.", "institution": "Faculty of Law, University of Delhi", "education_level": "BACHELORS", "end_date": "2021-06-30"}],
    "experience": [{"organization": "Rao & Associates", "designation": "Legal Associate", "domain": "Legal",
                    "start_date": "2022-01-01", "responsibilities": "Drafting pleadings, appearing before civil courts"}],
}

async def _ingest_real_upsc(db_session):
    source = await get_or_create_source(db_session, upsc.UPSC_SOURCE)
    for name in ("AdvtNo-12-2026-Engl-250926_0.pdf", "AdvtNo-11-2026-Engl-100926.pdf"):
        await ingest_pdf_bytes(db_session, source, f"https://www.upsc.gov.in/sites/default/files/{name}", (UPSC / name).read_bytes())
    await db_session.commit()
    rows = (await db_session.execute(select(Vacancy))).scalars().all()
    return {v.external_ref: v for v in rows}

def _checks(result):
    return {e["check_name"]: e for e in result["evidence"]}

def _fingerprint(result):
    return sorted((e["check_name"], e["outcome"], e["match_type"], e["requirement"], e["profile_evidence"], e["rule"])
                  for e in result["evidence"])

@pytest.mark.asyncio
async def test_real_upsc_eligibility_is_explained_and_reproducible(api, login, db_session):
    vacancies = await _ingest_real_upsc(db_session)
    law_officer = vacancies["26091203226"]      # Law Officer, GNCTD: Degree in Law + 2 yrs legal work; OBC max 33
    jto = vacancies["26091201626"]              # Junior Technical Officer (Sugar Technology): Science/Engineering degree
    alc = vacancies["26091202226"]              # Assistant Legislative Counsel: law degree plus 7-year service clauses

    h = await login("asha@example.com")
    v1 = (await api.put("/profile", json=LAW_GRAD, headers=h)).json()["version"]["id"]

    async def evaluate(version_id, vacancy):
        r = await api.post("/eligibility", json={"profile_version_id": version_id, "vacancy_id": str(vacancy.id)}, headers=h)
        assert r.status_code == 201, r.text
        return r.json()

    # --- Law Officer: every hard check is met, each with evidence and the PDF page
    first = await evaluate(v1, law_officer)
    assert first["status"] == "ELIGIBLE", first
    assert first["engine_version"] == "hard_eligibility/1"
    assert first["profile_version_id"] == v1
    c = _checks(first)
    assert c["AGE"]["outcome"] == "MET" and "age 31 on crucial date 2026-10-16" in c["AGE"]["profile_evidence"]
    assert c["AGE"]["requirement"] == "max age 33 for OBC" and c["AGE"]["source_page"] == "8"
    assert (c["EDUCATION"]["outcome"], c["EDUCATION"]["match_type"]) == ("MET", "EXACT_MATCH")
    assert c["EDUCATION"]["requirement"] == "Degree in Law from a recognized Institute/University."
    assert c["EXPERIENCE"]["outcome"] == "MET" and "Legal Associate @ Rao & Associates" in c["EXPERIENCE"]["profile_evidence"]
    assert c["NATIONALITY"]["outcome"] == "NOT_EVALUATED"

    # --- Science/Engineering post: a law degree is a mismatch, never assumed equivalent
    jto_result = await evaluate(v1, jto)
    assert jto_result["status"] == "INELIGIBLE"
    assert _checks(jto_result)["EDUCATION"]["match_type"] == "MISMATCH"

    # --- Law degree matches, but the requirement adds service/practice clauses the data can't confirm
    alc_result = await evaluate(v1, alc)
    assert alc_result["status"] == "CONDITIONAL"
    assert "unless" in _checks(alc_result)["EDUCATION"]["rule"]

    # --- Change the profile: version 2 swaps the law degree for a BCA
    v2 = (await api.patch("/profile", json={"education": [{"degree": "BCA", "institution": "Uni A", "end_date": "2018-06-30"}]},
                          headers=h)).json()["version"]["id"]
    v2_result = await evaluate(v2, law_officer)
    assert v2_result["status"] == "INELIGIBLE"
    assert _checks(v2_result)["EDUCATION"]["match_type"] == "MISMATCH"

    # --- Re-running version 1 after the change reproduces the original decision exactly
    again = await evaluate(v1, law_officer)
    assert again["status"] == "ELIGIBLE"
    assert again["profile_version_id"] == v1
    assert _fingerprint(again) == _fingerprint(first)

    stored = (await api.get(f"/eligibility/{first['id']}", headers=h)).json()
    assert _fingerprint(stored) == _fingerprint(first)
    history = (await api.get(f"/eligibility?vacancy_id={law_officer.id}", headers=h)).json()
    assert sorted(r["status"] for r in history) == ["ELIGIBLE", "ELIGIBLE", "INELIGIBLE"]

@pytest.mark.asyncio
async def test_eligibility_requires_own_explicit_profile_version(api, login, db_session):
    vacancies = await _ingest_real_upsc(db_session)
    vacancy_id = str(vacancies["26091203226"].id)
    asha = await login("asha@example.com")
    ravi = await login("ravi@example.com")
    v1 = (await api.put("/profile", json=LAW_GRAD, headers=asha)).json()["version"]["id"]
    result = (await api.post("/eligibility", json={"profile_version_id": v1, "vacancy_id": vacancy_id}, headers=asha)).json()

    # No implicit "current profile": profile_version_id is mandatory
    assert (await api.post("/eligibility", json={"vacancy_id": vacancy_id}, headers=asha)).status_code == 422
    # Another user can neither evaluate with Asha's version nor read her result
    assert (await api.post("/eligibility", json={"profile_version_id": v1, "vacancy_id": vacancy_id}, headers=ravi)).status_code == 404
    assert (await api.get(f"/eligibility/{result['id']}", headers=ravi)).status_code == 404
    assert (await api.get("/eligibility", headers=ravi)).json() == []
    # Vacancies are public reference data but still need a logged-in user
    assert (await api.get(f"/vacancies/{vacancy_id}")).status_code == 401
    detail = (await api.get(f"/vacancies/{vacancy_id}", headers=ravi)).json()
    assert detail["post_name"] == "Law Officer"
    assert any(p["field_name"] == "application_deadline" and p["page"] == 13 for p in detail["provenance"])
