"""
Exam draft path without an LLM call: no model output has been recorded for an exam notice yet, so the located
fields below are HAND-BUILT, with quotes copied verbatim from the real SSC CHSL 2026 notice. Everything after that
(validator verdicts, draft payload, reviewer approval, Exam rows) runs through the production code.
"""
import json
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from govcareeros.backend.documents.pdf_text import extract_pages
from govcareeros.backend.extraction.exam_extractor import ExamExtractionResult, verify_exam_field
from govcareeros.backend.models import Exam, ExamFieldProvenance, ExamRequirement, ExtractionDraft, User
from govcareeros.backend.parsing.upsc_advertisement import IST
from govcareeros.backend.services.document_ingestion import get_or_create_source, store_raw_document
from govcareeros.backend.services.exam_drafts import serialize
from govcareeros.backend.sources import ssc

PDF = Path(__file__).parents[1] / "fixtures" / "ssc" / "Notice_of_adv_chsle_2026.pdf"
URL = "https://ssc.gov.in/api/attachment/uploads/masterData/NoticeBoards/Notice_of_adv_chsle_2026.pdf"

LOCATED = {
    "exam_name": {"value": "Combined Higher Secondary (10+2) Level Examination, 2026", "page": 1,
                  "quote": "NOTICE Combined Higher Secondary (10+2) Level Examination, 2026"},
    "last_date": {"value": "07.10.2026", "page": 1,
                  "quote": "Last date and time for receipt of online applications 07.10.2026 (23:00 Hours)"},
    "age_crucial_date": {"value": "01-08-2026", "page": 4,
                         "quote": "The crucial date for age reckoning is fixed as 01-08-2026"},
    "fee": {"value": "100", "page": 13, "quote": "Fee payable: Rs 100/- (Rs one hundred only)."},
    "age_limit": {"value": "18-27 years", "page": 4, "quote": "Age limit for the posts is 18-27 years"},
}

@pytest.fixture(scope="module")
def pages():
    return extract_pages(PDF.read_bytes())

def test_exam_fields_are_verified_against_the_notice(pages):
    v = {name: verify_exam_field(name, item, pages) for name, item in LOCATED.items()}
    assert all(x.accepted for x in v.values()), {k: x.reason for k, x in v.items()}
    assert v["last_date"].value == datetime(2026, 10, 7, 23, 0, tzinfo=IST)
    # A reckoning date is a calendar day, not a closing time
    assert v["age_crucial_date"].value == datetime(2026, 8, 1, tzinfo=IST)
    assert v["fee"].value == 100 and v["age_limit"].value == [{"category": "ALL", "min_age": 18, "max_age": 27}]

    wrong_fee = verify_exam_field("fee", {**LOCATED["fee"], "value": "200"}, pages)
    assert not wrong_fee.accepted and "not a rupee amount" in wrong_fee.reason
    not_an_exam = verify_exam_field("exam_name", {"value": "Data Entry Operator", "page": 1,
                                                  "quote": "Data Entry Operator (DEO): Pay Level-4(Rs. 25,500-81,100)"}, pages)
    assert not not_an_exam.accepted
    invented = verify_exam_field("fee", {"value": "100", "page": 13, "quote": "Fee payable: Rs 100/- for all candidates"}, pages)
    assert invented.reason == "quote not found in document"

@pytest.mark.asyncio
async def test_reviewer_approval_turns_the_draft_into_an_exam(api, login, db_session, pages):
    source = await get_or_create_source(db_session, ssc.SSC_SOURCE)
    raw, _ = await store_raw_document(db_session, source, URL, PDF.read_bytes(), 200, "application/pdf")
    result = ExamExtractionResult("hand-built", "exam_locate/1", {n: verify_exam_field(n, i, pages) for n, i in LOCATED.items()}, [])
    draft = ExtractionDraft(source_id=source.id, raw_document_id=raw.id, provider="none", model="hand-built", prompt_version="exam_locate/1",
                            payload=json.dumps(serialize(result)), fields_offered=5, fields_accepted=5)
    db_session.add(draft)
    await db_session.commit()
    draft_id = draft.id  # the refused request rolls the shared session back, expiring `draft`

    h = await login("reviewer@example.com")
    assert (await api.post(f"/review/drafts/{draft_id}/approve", json={}, headers=h)).status_code == 403
    (await db_session.execute(select(User).where(User.email == "reviewer@example.com"))).scalar_one().is_reviewer = True
    await db_session.commit()
    r = await api.post(f"/review/drafts/{draft_id}/approve", json={"note": "checked against p.1, 4, 13"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "APPROVED" and r.json()["payload"]["exam_decision"]["status"] == "APPROVED"

    exam = (await db_session.execute(select(Exam))).scalar_one()
    assert (exam.name, exam.exam_code, exam.fee_amount) == ("Combined Higher Secondary (10+2) Level Examination, 2026", "CHSL", 100)
    assert exam.application_end == datetime(2026, 10, 7, 23, 0, tzinfo=IST)
    prov = {p.field_name: p for p in (await db_session.execute(select(ExamFieldProvenance))).scalars()}
    assert prov["fee"].page == 13 and prov["fee"].excerpt == "Fee payable: Rs 100/- (Rs one hundred only)."
    age = (await db_session.execute(select(ExamRequirement).where(ExamRequirement.req_type == "AGE_MAX"))).scalar_one()
    assert json.loads(age.value) == {"category": "ALL", "min_age": 18, "max_age": 27} and age.source_page == 4
