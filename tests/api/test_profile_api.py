"""
Profile API, called over HTTP (ASGI) against SQLite and PostgreSQL:
versioning, historical snapshots, facts -> evidence -> RawDocument, and cross-user authorization.
"""
import asyncio
from datetime import datetime, timezone

import pytest
import pytest_asyncio
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
async def test_impossible_date_of_birth_is_rejected(api, login):
    h = await login("asha@example.com")
    for dob in ("2030-01-01", "1900-01-01"):
        bad = {"personal_info": {**V1["personal_info"], "date_of_birth": dob}}
        assert (await api.put("/profile", json=bad, headers=h)).status_code == 422
        assert (await api.patch("/profile", json=bad, headers=h)).status_code == 422
    assert (await api.get("/profile", headers=h)).status_code == 404

@pytest_asyncio.fixture
async def api_per_request_session(db_session, tmp_path):
    """Like `api`, but each request gets its own session and connection, as in production, so requests can race.
    The shared in-memory SQLite has a single connection, so SQLite runs on a file here."""
    import httpx
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from govcareeros.backend.api.deps import get_session
    from govcareeros.backend.api.main import create_app
    from govcareeros.backend.models.base import Base

    engine = db_session.bind
    if engine.dialect.name == "sqlite":
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'concurrent.db'}")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def _session():
        async with sessions() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app = create_app()
    app.dependency_overrides[get_session] = _session
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client
    if engine is not db_session.bind:
        await engine.dispose()

@pytest.mark.asyncio
async def test_concurrent_saves_get_distinct_version_numbers(api_per_request_session):
    api = api_per_request_session
    await api.post("/auth/register", json={"email": "asha@example.com", "password": "correct-horse-battery"})
    token = (await api.post("/auth/login", json={"email": "asha@example.com", "password": "correct-horse-battery"})).json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    assert (await api.put("/profile", json=V1, headers=h)).status_code == 201
    # Five tabs saving at once
    responses = await asyncio.gather(*[
        api.patch("/profile", json={"change_summary": f"tab {i}", "skills": [{"skill_name": f"S{i}", "skill_type": "Technical"}]}, headers=h)
        for i in range(5)])
    assert [r.status_code for r in responses] == [201] * 5, [r.text for r in responses]
    assert sorted(r.json()["version"]["version_number"] for r in responses) == [2, 3, 4, 5, 6]
    for r in responses:  # each version holds its own skills and the carried-over education
        body = r.json()
        assert [s["skill_name"] for s in body["skills"]] == ["S" + body["version"]["change_summary"][-1]]
        assert [e["degree"] for e in body["education"]] == ["BCA"]
    assert [v["version_number"] for v in (await api.get("/profile/versions", headers=h)).json()] == [1, 2, 3, 4, 5, 6]
    assert (await api.get("/profile", headers=h)).json()["version"]["version_number"] == 6

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
