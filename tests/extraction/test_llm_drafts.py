"""
LLM draft -> validator -> review queue -> Vacancy, on a real BSF walk-in notice (scanned, OCR) and the
real output nvidia/nemotron-3-super-120b-a12b produced for it on 30-09-2026 (replayed, not generated).
"""
import json
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from govcareeros.backend.documents.pdf_text import extract_pages, tessdata_dir
from govcareeros.backend.extraction.llm_extractor import extract
from govcareeros.backend.extraction.validator import IST
from govcareeros.backend.llm.provider import LLMResponse
from govcareeros.backend.models import User, Vacancy, VacancyFieldProvenance, VacancyRequirement
from govcareeros.backend.services.extraction_drafts import create_draft
from govcareeros.backend.services.source_discovery import sync_registry
from govcareeros.backend.sources.registry import SOURCES

LLM = Path(__file__).parents[1] / "fixtures" / "llm"
PDF = LLM / "BSF_e0ee6e833f.pdf"
RECORDING = json.loads((LLM / "BSF_e0ee6e833f.nemotron-3-super-120b-a12b.llm.json").read_text(encoding="utf-8"))
URL = "https://rectt.bsf.gov.in/static/bsf/pdf/specialist-doctors-walk-in.pdf"

pytestmark = pytest.mark.skipif(tessdata_dir() is None, reason="the BSF notice is scanned; needs Tesseract")

class ReplayProvider:
    """Returns the model's recorded answer; used so tests don't depend on a remote endpoint."""
    name = "nvidia"
    chunk_chars = 120_000

    def __init__(self, recording):
        self.model = recording["model"]
        self._answers = [c["raw"] for c in recording["chunks"]]

    async def complete_json(self, system, user, schema):
        return LLMResponse(self._answers.pop(0), self.model, 0.0)

@pytest.mark.asyncio
async def test_recorded_output_passes_validation_without_fabrication():
    pages = extract_pages(PDF.read_bytes())
    assert all(p.ocr for p in pages)
    result = await extract(pages, ReplayProvider(RECORDING))
    posts = [v.fields["post_name"].value for v in result.vacancies if v.fields["post_name"].accepted]
    assert posts == ["Specialist Doctors", "General Duty Medical Officer"]
    fabricated = [f for v in result.vacancies for f in v.fields.values() if f.reason == "quote not found in document"]
    assert fabricated == []
    doctors = result.vacancies[0].fields
    assert doctors["age_limit"].value == [{"category": "ALL", "max_age": 67}]
    # "01 Sept' 2025 to 03 Sept' 2025": the later date closes; no time stated -> 23:59 IST
    assert doctors["last_date"].value == datetime(2025, 9, 3, 23, 59, tzinfo=IST)
    assert "time not stated" in doctors["last_date"].reason
    assert doctors["essential_qualification"].quote.startswith("(i) A recognized medical qualification")

async def _draft(db_session):
    sources = await sync_registry(db_session, [s for s in SOURCES if s.name == "BSF"])
    draft = await create_draft(db_session, sources["BSF"], URL, PDF.read_bytes(), ReplayProvider(RECORDING))
    await db_session.commit()
    await db_session.refresh(draft)
    return draft

@pytest.mark.asyncio
async def test_review_queue_needs_a_reviewer_and_creates_verified_vacancies(api, login, db_session):
    draft = await _draft(db_session)
    assert draft.status == "PENDING_REVIEW" and draft.fields_accepted >= 14
    draft_id = draft.id  # plain value: a rejected request rolls the shared session back and expires ORM objects
    h = await login("reviewer@example.com")

    queue = (await api.get("/review/drafts", headers=h)).json()
    assert [d["id"] for d in queue] == [str(draft_id)] and queue[0]["source"] == "BSF" and queue[0]["vacancies"] == 2
    detail = (await api.get(f"/review/drafts/{draft_id}", headers=h)).json()
    assert detail["payload"]["vacancies"][0]["fields"]["post_name"]["value"] == "Specialist Doctors"

    # Not a reviewer yet
    assert (await api.post(f"/review/drafts/{draft_id}/approve", json={}, headers=h)).status_code == 403
    user = (await db_session.execute(select(User).where(User.email == "reviewer@example.com"))).scalar_one()
    user.is_reviewer = True
    await db_session.commit()

    r = await api.post(f"/review/drafts/{draft_id}/approve", json={"vacancy_indexes": [0], "note": "checked against PDF"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "APPROVED" and len(r.json()["created_vacancy_ids"]) == 1
    assert (await api.post(f"/review/drafts/{draft_id}/approve", json={}, headers=h)).status_code == 409

    v = (await db_session.execute(select(Vacancy))).scalar_one()
    assert v.post_name == "Specialist Doctors" and v.organization == "Border Security Force"
    assert v.parser_version == "llm:vacancy_locate/1:nvidia/nemotron-3-super-120b-a12b"
    assert v.application_deadline == datetime(2025, 9, 3, 23, 59, tzinfo=IST)
    prov = {p.field_name: p for p in (await db_session.execute(select(VacancyFieldProvenance))).scalars()}
    assert prov["post_name"].page == 1 and "Specialist Doctors" in prov["post_name"].excerpt
    reqs = {r.req_type: r for r in (await db_session.execute(select(VacancyRequirement))).scalars()}
    assert json.loads(reqs["AGE_MAX"].value) == {"category": "ALL", "max_age": 67}
    assert reqs["EDUCATION"].value.startswith("(i) A recognized medical qualification")

    # The approved vacancy works with the deterministic eligibility engine
    profile = {"personal_info": {"date_of_birth": "1970-01-15", "category": "OBC", "citizenship_status": "INDIAN_CITIZEN",
                                 "is_pwbd": False, "is_ex_serviceman": False, "is_central_govt_employee": False},
               "education": [{"degree": "MBBS", "institution": "AIIMS", "end_date": "1995-06-30"}]}
    vid = (await api.put("/profile", json=profile, headers=h)).json()["version"]["id"]
    res = (await api.post("/eligibility", json={"profile_version_id": vid, "vacancy_id": str(v.id)}, headers=h)).json()
    checks = {e["check_name"]: e for e in res["evidence"]}
    assert checks["AGE"]["outcome"] == "MET" and checks["AGE"]["requirement"] == "max age 67 for ALL"
    assert checks["EDUCATION"]["match_type"] == "EXACT_MATCH"
    assert checks["CRUCIAL_DATE"]["outcome"] == "NOT_EVALUATED"

@pytest.mark.asyncio
async def test_reject_leaves_no_vacancy(api, login, db_session):
    draft = await _draft(db_session)
    draft_id = draft.id
    h = await login("reviewer@example.com")
    user = (await db_session.execute(select(User))).scalar_one()
    user.is_reviewer = True
    await db_session.commit()
    assert (await api.post(f"/review/drafts/{draft_id}/reject", json={"note": "walk-in already held"}, headers=h)).json()["status"] == "REJECTED"
    assert (await db_session.execute(select(Vacancy))).scalars().all() == []
    assert (await api.get("/review/drafts", headers=h)).json() == []
