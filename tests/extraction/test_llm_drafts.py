"""
LLM draft -> validator -> review queue -> Vacancy, on a real BSF walk-in notice (scanned, OCR) and the
real output nvidia/nemotron-3-super-120b-a12b produced for it on 30-09-2026 (replayed, not generated).
"""
import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from govcareeros.backend.documents.pdf_text import extract_pages, tessdata_dir
from govcareeros.backend.extraction.llm_extractor import extract
from govcareeros.backend.extraction.validator import IST
from govcareeros.backend.llm import provider as provider_module
from govcareeros.backend.llm.provider import LLMResponse, NvidiaProvider
from govcareeros.backend.models import ExtractionDraft, RecruitmentNotice, User, UserAlert, Vacancy, VacancyFieldProvenance, VacancyRequirement
from govcareeros.backend.services.extraction_drafts import create_draft, draft_pending_notices, requeue_failed_drafts
from govcareeros.backend.services.radar import run_radar
from govcareeros.backend.services.source_discovery import sync_registry, url_hash
from govcareeros.backend.sources.generic_notices import PageFetcher
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
        self.prompt_version = recording["prompt_version"]  # the prompt the recorded answer was produced with
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
    assert all(doctors[f].accepted for f in ("post_name", "last_date", "age_limit", "organization", "pay_level",
                                             "essential_qualification", "experience", "mode_of_selection", "how_to_apply"))
    assert doctors["age_limit"].value == [{"category": "ALL", "max_age": 67}]
    # "01 Sept' 2025 to 03 Sept' 2025": the later date closes; no time stated -> 23:59 IST
    assert doctors["last_date"].value == datetime(2025, 9, 3, 23, 59, tzinfo=IST)
    assert "time not stated" in doctors["last_date"].reason
    assert doctors["essential_qualification"].quote.startswith("(i) A recognized medical qualification")

@pytest.mark.asyncio
async def test_tampered_recording_is_rejected():
    """The real answer with invented text put in: the validator must refuse exactly the tampered fields."""
    recording = copy.deepcopy(RECORDING)
    doctors, gdmo = recording["chunks"][0]["raw"]["vacancies"]
    # Not in the PDF at all
    doctors["essential_qualification"]["quote"] = "MBBS degree from a university recognised by the National Medical Commission"
    # One digit changed: an OCR near match still finds "Not more than 67 years", but 62 is not what the notice says
    doctors["age_limit"].update(value="62 years", quote="Not more than 62 years")
    # A post the notice does not name, quoted from a line that exists
    gdmo["post_name"]["value"] = "Chief Medical Officer"
    # The interview date as the value of a date quote that does not contain it
    doctors["last_date"]["value"] = "12 Sept 2025"
    result = await extract(extract_pages(PDF.read_bytes()), ReplayProvider(recording))
    fields = result.vacancies[0].fields
    assert fields["essential_qualification"].reason == "quote not found in document"
    assert not fields["age_limit"].accepted and "disagrees" in fields["age_limit"].reason
    assert not fields["last_date"].accepted and "not one of the dates" in fields["last_date"].reason
    assert [v.fields["post_name"].value for v in result.vacancies if v.fields["post_name"].accepted] == ["Specialist Doctors"]
    assert result.vacancies[1].fields["post_name"].reason == "post name is not whole words of its quote"
    # Untouched fields are still accepted
    assert fields["experience"].accepted and fields["organization"].accepted

async def _draft(db_session):
    sources = await sync_registry(db_session, [s for s in SOURCES if s.name == "BSF"])
    now = datetime.now(timezone.utc)
    notice = RecruitmentNotice(source_id=sources["BSF"].id, url=URL, url_hash=url_hash(URL), title="Walk-in: Specialist Doctors and GDMOs",
                               document_type="PDF", notice_kind="VACANCY", first_seen_at=now, last_seen_at=now, review_status="MANUAL_REVIEW")
    db_session.add(notice)
    await db_session.flush()
    draft = await create_draft(db_session, sources["BSF"], URL, PDF.read_bytes(), ReplayProvider(RECORDING), notice)
    await db_session.commit()
    await db_session.refresh(draft)
    return draft

async def _notice_status(db_session):
    return (await db_session.execute(select(RecruitmentNotice.review_status).execution_options(populate_existing=True))).scalar_one()

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

    approve = lambda body: api.post(f"/review/drafts/{draft_id}/approve", json=body, headers=h)
    # Nothing selected, or the same vacancy twice, changes nothing
    assert (await approve({"vacancy_indexes": []})).status_code == 409
    assert (await approve({"vacancy_indexes": [0, 0]})).status_code == 409
    assert (await db_session.execute(select(Vacancy))).scalars().all() == []

    r = await approve({"vacancy_indexes": [0], "note": "checked against PDF"})
    assert r.status_code == 200, r.text
    # One of two approved: the draft stays open for the other one
    assert r.json()["status"] == "PENDING_REVIEW" and len(r.json()["created_vacancy_ids"]) == 1
    assert r.json()["payload"]["vacancies"][0]["decision"]["status"] == "APPROVED"
    assert [d["id"] for d in (await api.get("/review/drafts", headers=h)).json()] == [str(draft_id)]
    assert (await approve({"vacancy_indexes": [0]})).status_code == 409
    assert await _notice_status(db_session) == "DRAFTED"

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

    # The remaining vacancy closes the draft
    r = await approve({"vacancy_indexes": [1]})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "APPROVED" and len(r.json()["created_vacancy_ids"]) == 2
    assert sorted(v.post_name for v in (await db_session.execute(select(Vacancy))).scalars()) == \
        ["General Duty Medical Officer", "Specialist Doctors"]
    assert (await approve({})).status_code == 409
    assert (await api.get("/review/drafts", headers=h)).json() == []
    assert await _notice_status(db_session) == "PARSED"

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
    assert await _notice_status(db_session) == "REJECTED"

@pytest.mark.asyncio
async def test_reject_after_partial_approval_keeps_the_approved_vacancy(api, login, db_session):
    draft = await _draft(db_session)
    draft_id = draft.id
    h = await login("reviewer@example.com")
    user = (await db_session.execute(select(User))).scalar_one()
    user.is_reviewer = True
    await db_session.commit()
    assert (await api.post(f"/review/drafts/{draft_id}/approve", json={"vacancy_indexes": [1]}, headers=h)).status_code == 200
    r = (await api.post(f"/review/drafts/{draft_id}/reject", json={"note": "no specialist posts at my centre"}, headers=h)).json()
    assert r["status"] == "APPROVED" and [x["decision"]["status"] for x in r["payload"]["vacancies"]] == ["REJECTED", "APPROVED"]
    assert [v.post_name for v in (await db_session.execute(select(Vacancy))).scalars()] == ["General Duty Medical Officer"]
    assert await _notice_status(db_session) == "PARSED"

@pytest.mark.asyncio
async def test_single_all_category_limit_drives_age_headroom(api, login, db_session):
    # BSF states one limit for everyone ("Not more than 67 years"), stored as category ALL
    draft = await _draft(db_session)
    draft_id = draft.id
    h = await login("reviewer@example.com")
    (await db_session.execute(select(User).where(User.email == "reviewer@example.com"))).scalar_one().is_reviewer = True
    await db_session.commit()
    assert (await api.post(f"/review/drafts/{draft_id}/approve", json={"vacancy_indexes": [0]}, headers=h)).status_code == 200

    # 66 on the closing date 03-09-2025: one year of headroom under 67
    profile = {"personal_info": {"date_of_birth": "1959-01-15", "category": "OBC", "citizenship_status": "INDIAN_CITIZEN",
                                 "is_pwbd": False, "is_ex_serviceman": False, "is_central_govt_employee": False},
               "education": [{"degree": "MBBS", "institution": "AIIMS", "end_date": "1983-06-30"}]}
    await api.put("/profile", json=profile, headers=h)
    run = await run_radar(db_session, now=datetime(2025, 9, 1, 6, 0, tzinfo=timezone.utc))
    await db_session.commit()
    # The OCR'd experience clause ("1% years") keeps the verdict at MANUAL_REVIEW; the age limit still binds
    assert run.alerts_created.get("AGE_HEADROOM") == 1
    alert = (await db_session.execute(select(UserAlert).where(UserAlert.kind == "AGE_HEADROOM"))).scalar_one()
    assert "You are 66" in alert.body and "the ALL limit for this post is 67" in alert.body and "from 15 Jan 2027" in alert.body

# ------------------------------------------------------------------ --retry-failed (scripts/draft_notices.py)
# The body integrate.api.nvidia.com returned for every attempt during the 06-10-2026 drafting run
OVERLOADED = {"error": {"message": "Service temporarily overloaded", "type": "Service Unavailable", "code": 503}}

def _nvidia(status_for_every_request):
    def handler(request):
        return httpx.Response(status_for_every_request, json=OVERLOADED if status_for_every_request == 503 else None)
    return NvidiaProvider(["nvidia/nemotron-3-ultra-550b-a55b", "nvidia/nemotron-3.5-lightning-30b-a3b"], "test-key",
                          "https://integrate.api.nvidia.com/v1", 30, requests_per_minute=0, transport=httpx.MockTransport(handler))

@pytest.fixture
def no_waits(monkeypatch):
    async def fake_sleep(seconds):
        pass
    monkeypatch.setattr(provider_module.asyncio, "sleep", fake_sleep)

async def _failed_notice(db_session, provider):
    sources = await sync_registry(db_session, [s for s in SOURCES if s.name == "BSF"])
    now = datetime.now(timezone.utc)
    notice = RecruitmentNotice(source_id=sources["BSF"].id, url=URL, url_hash=url_hash(URL), title="Walk-in: Specialist Doctors and GDMOs",
                               document_type="PDF", notice_kind="VACANCY", first_seen_at=now, last_seen_at=now, review_status="MANUAL_REVIEW")
    db_session.add(notice)
    await db_session.flush()
    draft = await create_draft(db_session, sources["BSF"], URL, PDF.read_bytes(), provider, notice)
    await db_session.commit()
    assert draft.status == "FAILED" and notice.review_status == "DRAFT_FAILED"
    return notice, draft

def _serving_the_pdf():
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        assert str(request.url) == URL
        return httpx.Response(200, content=PDF.read_bytes(), headers={"content-type": "application/pdf"})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)

@pytest.mark.asyncio
async def test_retry_failed_redrafts_a_notice_the_provider_failed(db_session, no_waits):
    notice, failed = await _failed_notice(db_session, _nvidia(503))
    assert "Service temporarily overloaded" in failed.error and "lightning" in failed.error  # both models were asked
    notice_id = notice.id

    # A reviewer's decision is never undone, even when the last draft failed at the provider
    for status in ("REJECTED", "PARSED"):
        notice.review_status = status
        await db_session.commit()
        assert await requeue_failed_drafts(db_session) == []
        assert await _notice_status(db_session) == status
    notice.review_status = "DRAFT_FAILED"
    await db_session.commit()

    assert [n.id for n in await requeue_failed_drafts(db_session, ["BSF"])] == [notice_id]
    assert await _notice_status(db_session) == "MANUAL_REVIEW"
    async with PageFetcher(allow_browser=False, client=_serving_the_pdf()) as fetcher:
        [draft] = await draft_pending_notices(db_session, fetcher, ReplayProvider(RECORDING), notice_ids=[notice_id])
    await db_session.commit()
    assert draft.status == "PENDING_REVIEW" and draft.fields_accepted >= 14
    assert await _notice_status(db_session) == "DRAFTED"
    # The failed draft stays as the record of the outage
    assert (await db_session.get(ExtractionDraft, failed.id)).status == "FAILED"

@pytest.mark.asyncio
async def test_retry_failed_leaves_a_draft_that_did_not_fail_at_the_provider(db_session, no_waits):
    # 400 is a request the API refused, not an outage: retrying the same request would fail the same way
    notice, failed = await _failed_notice(db_session, _nvidia(400))
    assert "NVIDIA API 400" in failed.error
    assert await requeue_failed_drafts(db_session) == []
    assert await _notice_status(db_session) == "DRAFT_FAILED"
