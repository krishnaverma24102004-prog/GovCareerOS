import pytest
from datetime import datetime, timezone
from govcareeros.backend.models.source import GovernmentSource
from govcareeros.backend.models.artifact import RawDocument
from govcareeros.backend.services.profile_service import ProfileService
from govcareeros.backend.repositories.profile_repository import ProfileRepository
from govcareeros.backend.repositories.fact_repository import FactRepository

@pytest.mark.asyncio
async def test_profile_versioning_and_facts(db_session):
    # Runs on SQLite and PostgreSQL (see tests/conftest.py); foreign keys are enforced on both
    session = db_session
    profile_service = ProfileService(session)
    profile_repo = ProfileRepository(session)
    fact_repo = FactRepository(session)
    
    user = await profile_repo.create_user("test@example.com", "hash", "Test User")
    
    v1 = await profile_service.create_initial_profile(user.id, {
        "education": [{"degree": "BCA", "institution": "Uni A"}],
        "skills": [{"skill_name": "Python", "skill_type": "Technical"}]
    })
    
    source = GovernmentSource(
        name="Test Board", organization="Test Board", category="TEST",
        official_domain="example.gov.in", recruitment_url="https://example.gov.in/jobs",
        source_type="DOCUMENT",
    )
    session.add(source)
    await session.flush()
    raw_doc = RawDocument(
        source_id=source.id, retrieved_at=datetime.now(timezone.utc),
        url="https://example.gov.in/degree.pdf", content_hash="sha256:test-degree-cert",
        discovery_method="PDF",
    )
    session.add(raw_doc)
    await session.flush()

    fact = await fact_repo.create_fact(v1.id, "education", "degree", "BCA")
    await fact_repo.add_evidence(fact.id, "DOCUMENT", raw_doc.id, "Degree Cert")
    
    v2 = await profile_service.create_new_version(user.id, v1.id, {
        "skills": [{"skill_name": "SQL", "skill_type": "Technical"}]
    })
    
    v1_snapshot = await profile_repo.get_profile_snapshot(v1.id)
    assert "BCA" in v1_snapshot["education"]
    assert "Python" in v1_snapshot["skills"]
    assert "SQL" not in v1_snapshot["skills"]
    
    v2_snapshot = await profile_repo.get_profile_snapshot(v2.id)
    assert "SQL" in v2_snapshot["skills"]
    assert "Python" in v2_snapshot["skills"]
    
    chain = await fact_repo.get_fact_chain(fact.id)
    assert chain["fact"] == "BCA"
    assert "Degree Cert" in chain["evidence"]
    assert chain["artifacts"][0]["raw_document_id"] == raw_doc.id
    assert chain["artifacts"][0]["content_hash"] == "sha256:test-degree-cert"

    assert (v1.version_number, v2.version_number) == (1, 2)
    v3 = await profile_service.create_new_version(user.id, v2.id, {})
    assert v3.version_number == 3
    assert (await profile_repo.get_profile_snapshot(v3.id))["skills"] == v2_snapshot["skills"]
    
    print("Historical Versioning and Fact Chain VERIFIED.")
