"""
Truth-locked resume over the API, tailored to the real UPSC Law Officer vacancy (Advt 12/2026, official PDF fixture).

The profile is candidate input, as in the other API tests. Its degree Fact carries DOCUMENT evidence pointing at a
real stored RawDocument: the captured Advt 12/2026 PDF stands in as the carrier (no candidate certificate exists in
the fixtures); what is tested is the claim -> fact -> evidence -> RawDocument chain, not the certificate's content.
"""
import io
from pathlib import Path

import pytest
from docx import Document
from sqlalchemy import select

from govcareeros.backend.models import RawDocument, Vacancy
from govcareeros.backend.services.document_ingestion import get_or_create_source, ingest_pdf_bytes
from govcareeros.backend.sources import upsc

UPSC = Path(__file__).parents[1] / "fixtures" / "upsc"
ADVT_12 = "AdvtNo-12-2026-Engl-250926_0.pdf"

PROFILE = {
    "personal_info": {"full_name": "Asha Rao", "date_of_birth": "1995-03-10", "category": "OBC", "is_pwbd": False,
                      "nationality": "Indian", "citizenship_status": "INDIAN_CITIZEN"},
    "education": [{"degree": "LL.B.", "institution": "Faculty of Law, University of Delhi", "education_level": "BACHELORS", "end_date": "2021-06-30"}],
    "experience": [
        {"organization": "Infoway Systems", "designation": "Software Developer", "domain": "Software", "start_date": "2024-01-01",
         "responsibilities": "Building web applications"},
        {"organization": "Rao & Associates", "designation": "Legal Associate", "domain": "Legal", "start_date": "2021-07-01",
         "end_date": "2023-12-31", "responsibilities": "Drafting pleadings, land acquisition matters, civil suits in district courts"},
    ],
    "skills": [{"skill_name": "Legal drafting", "skill_type": "Domain"}],
}

async def _setup(api, login, db_session):
    source = await get_or_create_source(db_session, upsc.UPSC_SOURCE)
    url = f"https://www.upsc.gov.in/sites/default/files/{ADVT_12}"
    await ingest_pdf_bytes(db_session, source, url, (UPSC / ADVT_12).read_bytes())
    await db_session.commit()
    law_officer = (await db_session.execute(select(Vacancy).where(Vacancy.external_ref == "26091203226"))).scalar_one()
    doc = (await db_session.execute(select(RawDocument).where(RawDocument.url == url))).scalar_one()

    h = await login("asha@example.com")
    version = (await api.put("/profile", json=PROFILE, headers=h)).json()["version"]["id"]
    fact = (await api.post("/profile/facts", json={"subject": "education", "predicate": "degree", "value": "LL.B."}, headers=h)).json()
    r = await api.post(f"/profile/facts/{fact['id']}/evidence",
                       json={"evidence_type": "DOCUMENT", "source": "LL.B. degree", "raw_document_id": str(doc.id)}, headers=h)
    assert r.status_code == 201, r.text
    return h, version, fact["id"], law_officer, doc

def _heads(claims, section):
    seen, out = set(), []
    for c in claims:
        if c["section"] == section and c["item_ref"] not in seen:
            seen.add(c["item_ref"])
            out.append(c["text"])
    return out

@pytest.mark.asyncio
async def test_every_claim_is_a_fact_and_unverified_claims_are_marked(api, login, db_session):
    h, version, degree_fact, _, doc = await _setup(api, login, db_session)
    r = await api.post("/resume", json={"profile_version_id": version}, headers=h)
    assert r.status_code == 201, r.text
    resume = r.json()
    claims = resume["claims"]
    assert claims and resume["vacancy_id"] is None

    # Every line is one profile value, verbatim, backed by a Fact of the same subject/predicate with evidence
    for c in claims:
        assert c["text"] == c["fact"]["value"]
        assert (c["fact"]["subject"], c["fact"]["predicate"]) == (c["section"], c["field"])
        assert c["fact"]["evidence"], c
    degree = next(c for c in claims if c["field"] == "degree")
    assert degree["fact"]["id"] == degree_fact and degree["verified"] is True
    assert degree["fact"]["evidence"][0]["artifact_url"] == doc.url
    others = [c for c in claims if c["fact"]["id"] != degree_fact]
    assert others and not any(c["verified"] for c in others)
    assert all(c["fact"]["status"] == "UNVERIFIED" and c["fact"]["evidence"][0]["evidence_type"] == "USER_ATTESTED"
               and c["fact"]["evidence"][0]["source"].startswith("profile row ") for c in others)
    # Nothing beyond the profile: e.g. no text the candidate did not write
    profile_values = {"Asha Rao", "1995-03-10", "Indian", "LL.B.", "Faculty of Law, University of Delhi", "2021-06-30",
                      "Software Developer", "Infoway Systems", "Software", "2024-01-01", "Building web applications",
                      "Legal Associate", "Rao & Associates", "Legal", "2021-07-01", "2023-12-31",
                      PROFILE["experience"][1]["responsibilities"], "Legal drafting"}
    assert {c["text"] for c in claims} <= profile_values
    # Untailored: the ongoing role comes first
    assert _heads(claims, "experience") == ["Software Developer", "Legal Associate"]

    html = (await api.get(f"/resume/{resume['id']}/download?format=html", headers=h)).text
    assert html.count("[unverified]") == len(others) + 1  # plus the explanatory note
    assert "<strong>LL.B.</strong></div>" in html          # the documented degree carries no mark
    r = await api.get(f"/resume/{resume['id']}/download?format=docx", headers=h)
    assert r.status_code == 200
    text = "\n".join(p.text for p in Document(io.BytesIO(r.content)).paragraphs)
    assert "LL.B.\n" in text and "Organization: Rao & Associates [unverified]" in text
    assert (await api.get(f"/resume/{resume['id']}/download?format=pdf", headers=h)).status_code == 422

    # A second build reuses the facts created the first time instead of duplicating them
    again = (await api.post("/resume", json={"profile_version_id": version}, headers=h)).json()
    assert [c["fact"]["id"] for c in again["claims"]] == [c["fact"]["id"] for c in claims]

    # Another user reaches nothing
    ravi = await login("ravi@example.com")
    assert (await api.get(f"/resume/{resume['id']}", headers=ravi)).status_code == 404
    assert (await api.get(f"/resume/{resume['id']}/download?format=html", headers=ravi)).status_code == 404
    assert (await api.post("/resume", json={"profile_version_id": version}, headers=ravi)).status_code == 404

@pytest.mark.asyncio
async def test_tailored_to_law_officer_puts_legal_first_and_quotes_requirements(api, login, db_session):
    h, version, degree_fact, law_officer, _ = await _setup(api, login, db_session)
    r = await api.post("/resume", json={"profile_version_id": version, "vacancy_id": str(law_officer.id)}, headers=h)
    assert r.status_code == 201, r.text
    resume = r.json()
    claims = resume["claims"]

    sections = list(dict.fromkeys(c["section"] for c in claims))
    assert sections[:3] == ["personal_info", "meets_requirement", "experience"]
    assert _heads(claims, "experience") == ["Legal Associate", "Software Developer"]

    meets = [c for c in claims if c["section"] == "meets_requirement"]
    degree = next(c for c in meets if c["field"] == "degree")
    assert degree["requirement_quote"] == "Degree in Law from a recognized Institute/University."
    assert degree["requirement_page"] == 8 and degree["requirement_outcome"] == "MET"
    assert degree["text"] == "LL.B." and degree["fact"]["id"] == degree_fact and degree["verified"]
    experience = next(c for c in meets if c["field"] == "designation")
    assert experience["text"] == "Legal Associate" and experience["requirement_page"] == 8
    assert experience["requirement_quote"].startswith("Two years") and not experience["verified"]

    elig = (await api.get(f"/eligibility/{resume['eligibility_result_id']}", headers=h)).json()
    assert elig["status"] == "ELIGIBLE"
    stored = (await api.get(f"/resume/{resume['id']}", headers=h)).json()
    assert stored["claims"] == claims

    html = (await api.get(f"/resume/{resume['id']}/download?format=html", headers=h)).text
    assert "Degree in Law from a recognized Institute/University.” (notification page 8)" in html
    assert html.index("Legal Associate") < html.index("Software Developer")
