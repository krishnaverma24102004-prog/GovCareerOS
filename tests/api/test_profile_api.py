"""
Profile API, called over HTTP (ASGI) against SQLite and PostgreSQL:
versioning, historical snapshots, facts -> evidence -> RawDocument, and cross-user authorization.
"""
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from govcareeros.backend.models import AuthToken, GovernmentSource, RawDocument, User

V1 = {
    "personal_info": {"full_name": "Asha Rao", "date_of_birth": "1996-05-01", "category": "OBC", "is_pwbd": False, "nationality": "Indian"},
    "education": [{"degree": "BCA", "institution": "Uni A", "education_level": "BACHELORS", "end_date": "2018-06-30"}],
    "skills": [{"skill_name": "Python", "skill_type": "Technical"}],
}

async def _raw_document(db_session):
    source = GovernmentSource(name="TEST-BOARD", organization="Test Board", category="TEST", official_domain="example.gov.in",
                              recruitment_url="https://example.gov.in/jobs", source_type="DOCUMENT")
    db_session.add(source)
    await db_session.flush()
    doc = RawDocument(source_id=source.id, retrieved_at=datetime.now(timezone.utc), url="https://example.gov.in/bca.pdf",
                      content_hash="sha256:bca-degree", discovery_method="PDF")
    db_session.add(doc)
    await db_session.commit()
    return doc

@pytest.mark.asyncio
async def test_register_login_and_auth_required(api, login, db_session):
    assert (await api.get("/profile")).status_code == 401
    assert (await api.get("/profile", headers={"Authorization": "Bearer nope"})).status_code == 401
    headers = await login("asha@example.com")
    assert (await api.get("/auth/me", headers=headers)).json()["email"] == "asha@example.com"
    assert (await api.post("/auth/register", json={"email": "asha@example.com", "password": "another-password"})).status_code == 409
    assert (await api.post("/auth/login", json={"email": "asha@example.com", "password": "wrong-password"})).status_code == 401

    # Passwords and tokens are never stored in the clear
    user = (await db_session.execute(select(User))).scalar_one()
    assert user.hashed_password.startswith("scrypt$") and "correct-horse" not in user.hashed_password
    token = headers["Authorization"].split()[1]
    assert token not in (await db_session.execute(select(AuthToken.token_hash))).scalars().all()

    assert (await api.post("/auth/logout", headers=headers)).status_code == 204
    assert (await api.get("/auth/me", headers=headers)).status_code == 401

@pytest.mark.asyncio
async def test_profile_versions_are_immutable_snapshots(api, login):
    h = await login("asha@example.com")
    assert (await api.get("/profile", headers=h)).status_code == 404

    r = await api.put("/profile", json=V1, headers=h)
    assert r.status_code == 201, r.text
    v1 = r.json()
    assert v1["version"]["version_number"] == 1
    assert [e["degree"] for e in v1["education"]] == ["BCA"]
    assert v1["personal_info"]["date_of_birth"] == "1996-05-01"

    # PATCH: only skills sent -> education and personal info carried over
    r = await api.patch("/profile", json={"skills": [{"skill_name": "Python", "skill_type": "Technical"},
                                                     {"skill_name": "SQL", "skill_type": "Technical"}],
                                          "change_summary": "added SQL"}, headers=h)
    assert r.status_code == 201, r.text
    v2 = r.json()
    assert v2["version"]["version_number"] == 2
    assert sorted(s["skill_name"] for s in v2["skills"]) == ["Python", "SQL"]
    assert [e["degree"] for e in v2["education"]] == ["BCA"]
    assert v2["personal_info"]["category"] == "OBC"

    # Version 1 is unchanged: BCA + Python, no SQL
    old = (await api.get(f"/profile/versions/{v1['version']['id']}", headers=h)).json()
    assert [e["degree"] for e in old["education"]] == ["BCA"]
    assert [s["skill_name"] for s in old["skills"]] == ["Python"]

    current = (await api.get("/profile", headers=h)).json()
    assert current["version"]["id"] == v2["version"]["id"]
    versions = (await api.get("/profile/versions", headers=h)).json()
    assert [v["version_number"] for v in versions] == [1, 2]
    assert versions[1]["change_summary"] == "added SQL"

    # PUT replaces the whole profile: sections not sent are cleared in the new version
    v3 = (await api.put("/profile", json={"education": V1["education"]}, headers=h)).json()
    assert v3["skills"] == [] and v3["personal_info"] is None

@pytest.mark.asyncio
async def test_unknown_fields_are_rejected(api, login):
    h = await login("asha@example.com")
    r = await api.put("/profile", json={"education": [{"degree": "BCA", "institution": "U", "is_btech": True}]}, headers=h)
    assert r.status_code == 422

@pytest.mark.asyncio
async def test_fact_evidence_raw_document_chain(api, login, db_session):
    h = await login("asha@example.com")
    await api.put("/profile", json=V1, headers=h)
    doc = await _raw_document(db_session)

    r = await api.post("/profile/facts", json={"subject": "education", "predicate": "degree", "value": "BCA"}, headers=h)
    assert r.status_code == 201, r.text
    fact = r.json()
    r = await api.post(f"/profile/facts/{fact['id']}/evidence",
                       json={"evidence_type": "DOCUMENT", "source": "Degree certificate", "raw_document_id": str(doc.id)}, headers=h)
    assert r.status_code == 201, r.text

    assert (await api.get(f"/profile/facts/{fact['id']}", headers=h)).json()["value"] == "BCA"
    evidence = (await api.get(f"/profile/facts/{fact['id']}/evidence", headers=h)).json()
    assert len(evidence) == 1
    assert evidence[0]["artifact_id"] == str(doc.id)
    assert evidence[0]["artifact_content_hash"] == "sha256:bca-degree"
    assert [f["id"] for f in (await api.get("/profile/facts", headers=h)).json()] == [fact["id"]]

    bad = await api.post(f"/profile/facts/{fact['id']}/evidence",
                         json={"evidence_type": "DOCUMENT", "raw_document_id": "00000000-0000-0000-0000-000000000000"}, headers=h)
    assert bad.status_code == 404

@pytest.mark.asyncio
async def test_users_cannot_see_each_others_data(api, login, db_session):
    asha = await login("asha@example.com")
    ravi = await login("ravi@example.com")
    v1 = (await api.put("/profile", json=V1, headers=asha)).json()
    fact = (await api.post("/profile/facts", json={"subject": "education", "predicate": "degree", "value": "BCA"}, headers=asha)).json()
    await api.post(f"/profile/facts/{fact['id']}/evidence", json={"evidence_type": "USER_ATTESTED", "source": "self"}, headers=asha)

    # Ravi has no profile of his own and cannot reach Asha's records by id
    assert (await api.get("/profile", headers=ravi)).status_code == 404
    assert (await api.get("/profile/versions", headers=ravi)).json() == []
    assert (await api.get(f"/profile/versions/{v1['version']['id']}", headers=ravi)).status_code == 404
    assert (await api.get(f"/profile/facts?version_id={v1['version']['id']}", headers=ravi)).status_code == 404
    assert (await api.get(f"/profile/facts/{fact['id']}", headers=ravi)).status_code == 404
    assert (await api.get(f"/profile/facts/{fact['id']}/evidence", headers=ravi)).status_code == 404
    assert (await api.post(f"/profile/facts/{fact['id']}/evidence", json={"evidence_type": "USER_ATTESTED"}, headers=ravi)).status_code == 404

    # Ravi's own writes never touch Asha's versions
    await api.put("/profile", json={"education": [{"degree": "B.Sc.", "institution": "Uni B"}]}, headers=ravi)
    assert (await api.get("/profile", headers=asha)).json()["version"]["version_number"] == 1
    assert (await api.get(f"/profile/facts/{fact['id']}/evidence", headers=asha)).json()[0]["source"] == "self"
