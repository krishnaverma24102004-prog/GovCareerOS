"""
Eligibility for one post of a post-wise SSC notice uses that post's own rows (real CGL 2026 / JE 2026 notices,
captured 2026-10-01 from ssc.gov.in; see tests/fixtures/ssc/sources.json).

CGL 2026, page 3: "Junior Statistical Officer | Ministry of Statistics & Programme Implementation. | Group "B" | 18-32 years";
page 3: "Auditor | Offices under CGDA | Group "C" | 18-27 years"; page 6, para 5.1: posts with limit 18-30 years:
"Candidate must have been born not earlier than 02-08-1996 and not later than 01-08-2008."; page 7-8: relaxation
rows 10-13 apply to Group 'C' posts only (widows: "Up to 35 years of age").
"""
import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from govcareeros.backend.models import ExamPost, ExamRequirement
from govcareeros.backend.services.document_ingestion import get_or_create_source
from govcareeros.backend.services.exam_corrigenda import ingest_corrigendum_pdf
from govcareeros.backend.services.exam_ingestion import ingest_exam_pdf
from govcareeros.backend.sources import ssc

SSC = Path(__file__).parents[1] / "fixtures" / "ssc"
SOURCES = json.loads((SSC / "sources.json").read_text(encoding="utf-8"))

def _profile(dob, category="UR", gender="Male", education=None):
    return {
        "personal_info": {"full_name": "Asha Verma", "date_of_birth": dob, "gender": gender, "category": category,
                          "is_pwbd": False, "is_ex_serviceman": False, "is_central_govt_employee": False, "nationality": "Indian"},
        "education": education or [{"degree": "Bachelor of Commerce (B.Com)", "institution": "University of Rajasthan",
                                     "education_level": "BACHELORS", "end_date": "2018-05-31"}],
    }

async def _ingest(db_session, name):
    source = await get_or_create_source(db_session, ssc.SSC_SOURCE)
    result = await ingest_exam_pdf(db_session, source, SOURCES[name]["url"], (SSC / name).read_bytes())
    await db_session.commit()
    return str(result.exam_id)

async def _post_id(db_session, name, department):
    return str((await db_session.execute(select(ExamPost.id).where(ExamPost.post_name == name, ExamPost.department == department))).scalar_one())

def _checks(result):
    return {e["check_name"]: e for e in result["evidence"]}

@pytest.mark.asyncio
async def test_cgl_post_uses_its_own_age_limit_and_birth_window(api, login, db_session):
    exam_id = await _ingest(db_session, "Notice_of_adv_cgl_2026.pdf")
    h = await login("asha@example.com")
    jso = await _post_id(db_session, "Junior Statistical Officer", "Ministry of Statistics & Programme Implementation.")
    auditor = await _post_id(db_session, "Auditor", "Offices under CGDA")
    aso_css = await _post_id(db_session, "Assistant Section Officer", "Central Secretariat Service")
    aao = await _post_id(db_session, "Assistant Audit Officer (Central Cadre)",
                         "Indian Audit & Accounts Department under O/o Comptroller and Auditor General of India (C&AG)")

    async def evaluate(profile, post_id=None):
        version = (await api.put("/profile", json=profile, headers=h)).json()["version"]["id"]
        r = await api.post(f"/exams/{exam_id}/eligibility", json={"profile_version_id": version, "post_id": post_id}, headers=h)
        assert r.status_code == 201, r.text
        return r.json()

    # 31 on 01-08-2026 (born 10-03-1995): within 18-32 for JSO, over 18-27 for Auditor
    res = await evaluate(_profile("1995-03-10"), jso)
    age = _checks(res)["AGE"]
    assert age["outcome"] == "MET" and age["source_page"] == "3"
    assert age["requirement"] == "born 1994-08-02 to 2008-08-01 for UR (age limit 18-32)"
    assert res["post_id"] == jso
    res = await evaluate(_profile("1995-03-10"), auditor)
    assert res["status"] == "INELIGIBLE" and _checks(res)["AGE"]["outcome"] == "NOT_MET"
    assert _checks(res)["AGE"]["rule"].startswith("born before 1999-08-02 (limit 27 reached)")

    # The notice's window, not completed years: born 01-08-1996 is exactly 30 on 01-08-2026, which the 18-30/20-30
    # windows ("born not earlier than 02-08-1996") exclude; one day younger is inside
    assert _checks(await evaluate(_profile("1996-08-01"), aso_css))["AGE"]["outcome"] == "NOT_MET"
    assert _checks(await evaluate(_profile("1996-08-02"), aso_css))["AGE"]["outcome"] == "MET"
    # ... and the lower end: 20-30 for ASO in CSS means born not later than 01-08-2006
    young = await evaluate(_profile("2006-08-02"), aso_css)
    assert _checks(young)["AGE"]["outcome"] == "NOT_MET" and "below the minimum age" in _checks(young)["AGE"]["rule"]

    # OBC: 27 + 3 for Auditor, window moved back three years (born not earlier than 02-08-1996)
    obc = await evaluate(_profile("1996-09-15", category="OBC"), auditor)
    assert _checks(obc)["AGE"]["outcome"] == "MET" and _checks(obc)["AGE"]["requirement"] == "born 1996-08-02 to 2008-08-01 for OBC (age limit 18-30)"

    # Widow/divorced relaxation ("Up to 35 years of age") is stated for Group 'C' posts only:
    # a woman of 31 may qualify for Auditor (Group "C") but not for Assistant Audit Officer (Group "B", 18-30)
    woman = _profile("1995-03-10", gender="Female")
    res = await evaluate(woman, auditor)
    assert res["status"] == "MANUAL_REVIEW" and "WIDOW_DIVORCED_SEPARATED" in _checks(res)["AGE"]["profile_evidence"]
    res = await evaluate(woman, aao)
    assert res["status"] == "INELIGIBLE" and "WIDOW_DIVORCED_SEPARATED" not in _checks(res)["AGE"]["profile_evidence"]

    # No post chosen: the best post decides and the result says which, and how the others came out
    res = await evaluate(_profile("1995-03-10"))
    post = _checks(res)["POST"]
    assert post["outcome"] == "NOT_EVALUATED" and post["requirement"].startswith("Junior Statistical Officer")
    assert _checks(res)["AGE"]["outcome"] == "MET" and "INELIGIBLE: 39 posts" in post["profile_evidence"]

@pytest.mark.asyncio
async def test_je_post_age_and_corrected_qualification(api, login, db_session):
    exam_id = await _ingest(db_session, "Notice_of_adv_je_2026.pdf")
    h = await login("ravi@example.com")
    cpwd = await _post_id(db_session, "JE (C)", "Central Public Works Department (CPWD)")
    bro = await _post_id(db_session, "JE(C)", "Border Roads Organization")
    sa = await _post_id(db_session, "Scientific Assistant", "India Meteorological Department")
    diploma = [{"degree": "Diploma in Civil Engineering", "institution": "Govt. Polytechnic, Kota", "education_level": "DIPLOMA",
                "specialization": "Civil Engineering", "end_date": "2016-06-30"}]

    async def evaluate(profile, post_id):
        version = (await api.put("/profile", json=profile, headers=h)).json()["version"]["id"]
        r = await api.post(f"/exams/{exam_id}/eligibility", json={"profile_version_id": version, "post_id": post_id}, headers=h)
        assert r.status_code == 201, r.text
        return r.json()

    # 31 on 01-08-2026: "Up to 32 years" for CPWD, "Up to 30 years" for BRO
    res = await evaluate(_profile("1995-05-05", education=diploma), cpwd)
    assert _checks(res)["AGE"]["outcome"] == "MET" and _checks(res)["AGE"]["source_page"] == "2"
    assert _checks(res)["EDUCATION"]["requirement"].startswith("Diploma in Civil Engineering from a recognized University or Institute.")
    assert _checks(await evaluate(_profile("1995-05-05", education=diploma), bro))["AGE"]["outcome"] == "NOT_MET"

    # After the addendum of 10.09.2026 the Scientific Assistant post is judged on the qualification "read as" stated there
    source = await get_or_create_source(db_session, ssc.SSC_SOURCE)
    await ingest_corrigendum_pdf(db_session, source, SOURCES["Addendum_JE_10092026.pdf"]["url"], (SSC / "Addendum_JE_10092026.pdf").read_bytes())
    await db_session.commit()
    sa_row = await db_session.get(ExamPost, uuid.UUID(sa))
    current = (await db_session.execute(select(ExamRequirement).where(
        ExamRequirement.req_type == "EDUCATION", ExamRequirement.superseded_at.is_(None), ExamRequirement.post_id == sa_row.id))).scalar_one()
    res = await evaluate(_profile("2000-01-01", education=diploma), sa)
    edu = _checks(res)["EDUCATION"]
    assert edu["requirement_id"] == str(current.id) and edu["source_page"] == "1"
    assert edu["requirement"].startswith("(1) Bachelor’s Degree in Science with Physics") and edu["outcome"] != "MET"
