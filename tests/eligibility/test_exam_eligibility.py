"""
Exam eligibility on the real SSC CHSL 2026 notice
(https://ssc.gov.in/api/attachment/uploads/masterData/NoticeBoards/Notice_of_adv_chsle_2026.pdf, captured 2026-09-30).

Page 4, para 5.1, verbatim: "Age limit for the posts is 18-27 years i.e. Candidates born not before 02-08-1999 and
not later than 01-08-2008 are eligible to apply." The relaxation table under para 5.2 gives OBC 3 years, SC/ST 5 years,
and "Up to 35 years of age" for widows/divorced women. Para 8.2 (page 10): "Candidates must have passed 12th Standard
or equivalent examination from a recognized Board or University."
"""
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from govcareeros.backend.models import Exam, ExamPost, ExamRequirement
from govcareeros.backend.services.document_ingestion import get_or_create_source
from govcareeros.backend.services.exam_ingestion import ingest_exam_pdf
from govcareeros.backend.sources import ssc

PDF = Path(__file__).parents[1] / "fixtures" / "ssc" / "Notice_of_adv_chsle_2026.pdf"
URL = "https://ssc.gov.in/api/attachment/uploads/masterData/NoticeBoards/Notice_of_adv_chsle_2026.pdf"
AGE_QUOTE = ("Age limit for the posts is 18-27 years i.e. Candidates born not before 02-08-1999 and not later than "
             "01-08-2008 are eligible to apply.")

def _profile(category, gender="Male", stream="Commerce"):
    return {
        "personal_info": {"full_name": "Ravi Kumar", "date_of_birth": "1997-05-10", "gender": gender, "category": category,
                          "is_pwbd": False, "is_ex_serviceman": False, "is_central_govt_employee": False, "nationality": "Indian"},
        "education": [{"degree": "Class XII (Senior Secondary)", "institution": "Govt. Sr. Sec. School, Jaipur",
                       "specialization": stream, "end_date": "2015-05-31"}],
    }

async def _ingest(db_session):
    source = await get_or_create_source(db_session, ssc.SSC_SOURCE)
    result = await ingest_exam_pdf(db_session, source, URL, PDF.read_bytes())
    await db_session.commit()
    return result

def _checks(result):
    return {e["check_name"]: e for e in result["evidence"]}

@pytest.mark.asyncio
async def test_requirements_cite_the_notice_and_reingestion_is_idempotent(db_session):
    first = await _ingest(db_session)
    again = await _ingest(db_session)
    assert first.created and first.warnings == []
    assert again.exam_id == first.exam_id and again.skipped_reason == "already parsed with this parser version"

    reqs = (await db_session.execute(select(ExamRequirement).where(ExamRequirement.req_type == "AGE_MAX"))).scalars().all()
    by_cat = {json.loads(r.value)["category"]: r for r in reqs}
    assert set(by_cat) == {"UR", "EWS", "OBC", "SC", "ST"}
    assert by_cat["UR"].source_excerpt == AGE_QUOTE and by_cat["UR"].source_page == 4
    # OBC limit = stated limit + the table's OBC row; the excerpt quotes both
    assert by_cat["OBC"].source_excerpt == f"{AGE_QUOTE} | OBC 3 years"
    assert '"max_age": 30' in by_cat["OBC"].value and '"max_age": 32' in by_cat["SC"].value
    edu = (await db_session.execute(select(ExamRequirement).where(ExamRequirement.req_type == "EDUCATION"))).scalars().all()
    assert [r.source_section for r in edu] == ["Essential Educational Qualifications 8.1", "Essential Educational Qualifications 8.2"]
    assert "Candidates must have passed 12th Standard or equivalent examination from a recognized Board or University." in edu[1].value

@pytest.mark.asyncio
async def test_exam_eligibility_over_the_api(api, login, db_session):
    exam_id = str((await _ingest(db_session)).exam_id)
    h = await login("ravi@example.com")

    async def evaluate(profile, headers=h, **body):
        version = (await api.put("/profile", json=profile, headers=headers)).json()["version"]["id"]
        r = await api.post(f"/exams/{exam_id}/eligibility", json={"profile_version_id": version, **body}, headers=headers)
        return r, version

    listing = (await api.get("/exams", params={"q": "CHSL"}, headers=h)).json()
    assert [e["id"] for e in listing] == [exam_id]
    detail = (await api.get(f"/exams/{exam_id}", headers=h)).json()
    assert detail["fee_amount"] == 100 and len(detail["posts"]) == 3
    # Each stored value links back to the official notice it was read from
    assert detail["provenance"] and {p["document_url"] for p in detail["provenance"]} == {URL}

    # OBC, 29 on 01-08-2026: within 27 + 3; 12th pass meets para 8.2 (8.1 wants Science with Maths: reported, not counted)
    r, v_obc = await evaluate(_profile("OBC"))
    assert r.status_code == 201, r.text
    res = r.json()
    assert res["status"] == "ELIGIBLE", res
    c = _checks(res)
    assert c["AGE"]["outcome"] == "MET" and c["AGE"]["requirement"] == "max age 30 for OBC" and c["AGE"]["source_page"] == "4"
    assert "age 29 on crucial date 2026-08-01" in c["AGE"]["profile_evidence"]
    assert (c["EDUCATION"]["outcome"], c["EDUCATION"]["match_type"]) == ("MET", "EXACT_MATCH")
    assert c["EDUCATION"]["requirement"].startswith("For LDC/JSA and DEO/DEO Grade")
    assert c["EDUCATION_ALTERNATIVE"]["outcome"] == "NOT_EVALUATED" and c["EDUCATION_ALTERNATIVE"]["rule"].startswith("NOT_MET: stream 'Commerce'")
    assert c["CITIZENSHIP"]["outcome"] == "MET"

    # Same person as UR: 29 > 27, and every relaxation in the table is ruled out by the profile
    r, _ = await evaluate(_profile("UR"))
    res = r.json()
    assert res["status"] == "INELIGIBLE" and _checks(res)["AGE"]["outcome"] == "NOT_MET"
    assert "WIDOW_DIVORCED_SEPARATED" in _checks(res)["AGE"]["profile_evidence"]

    # A woman of 29 could be within "Up to 35 years of age" for widows/divorced women; marital status is not recorded
    r, _ = await evaluate(_profile("UR", gender="Female"))
    res = r.json()
    assert res["status"] == "MANUAL_REVIEW" and _checks(res)["AGE"]["outcome"] == "UNKNOWN"

    # One post of the exam; a post id from nowhere is refused
    deo = next(p for p in detail["posts"] if p["post_name"] == "Data Entry Operator (DEO)")
    r = await api.post(f"/exams/{exam_id}/eligibility", json={"profile_version_id": v_obc, "post_id": deo["id"]}, headers=h)
    assert r.status_code == 201 and r.json()["post_id"] == deo["id"]
    r = await api.post(f"/exams/{exam_id}/eligibility", json={"profile_version_id": v_obc, "post_id": exam_id}, headers=h)
    assert r.status_code == 404

    # Another user can neither use this profile version nor read these results
    other = await login("someone@example.com")
    r = await api.post(f"/exams/{exam_id}/eligibility", json={"profile_version_id": v_obc}, headers=other)
    assert r.status_code == 404
    assert (await api.get(f"/exams/{exam_id}/eligibility", headers=other)).json() == []
    mine = (await api.get(f"/exams/{exam_id}/eligibility", headers=h)).json()
    assert len(mine) == 4
    assert (await api.get(f"/exams/{exam_id}/eligibility/{mine[0]['id']}", headers=other)).status_code == 404
    assert (await api.get(f"/exams/{exam_id}/eligibility/{mine[0]['id']}", headers=h)).status_code == 200
    assert (await api.get("/exams")).status_code == 401
