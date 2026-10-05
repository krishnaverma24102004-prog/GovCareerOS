"""
Certificates held in the profile -> the separate certificate section of vacancy and exam eligibility results.

Requirement rows are written here by hand (no extractor emits CERTIFICATE rows yet); each carries a verbatim clause
of a real captured notice, checked against the PDF text:
  * UPSC Advt 12/2026, page 19, item (f): OBC declaration, closing date treated as crucial date (Law Officer vacancy).
  * SSC Junior Engineer Examination 2026, page 11, para 8.5 (first sentence): EWS Income & Asset certificate on income
    of FY 2025-26; page 1: last date 22.09.2026; para 8.7: crucial date = closing date of the Online Application Form.
"""
import json
from datetime import date, datetime
from pathlib import Path

import pymupdf
import pytest
from sqlalchemy import select

from govcareeros.backend.models import Exam, ExamRequirement, User, Vacancy, VacancyRequirement
from govcareeros.backend.parsing.upsc_advertisement import IST
from govcareeros.backend.services.document_ingestion import get_or_create_source, ingest_pdf_bytes, store_raw_document
from govcareeros.backend.services.exam_ingestion import ingest_exam_pdf
from govcareeros.backend.sources import ssc, upsc

FIXTURES = Path(__file__).parents[1] / "fixtures"
UPSC_OBC = ("A declaration in the prescribed format by candidate seeking reservation as OBC, that he/she does not belong to "
            "the creamy layer on the crucial date, in addition to the community certificate (OBC). Unless specified otherwise, "
            "the prescribed closing date for receipt of Online Recruitment Application for the post is to be treated as crucial date.")
SSC_JE_EWS = ("A person seeking appointment on the basis of reservation to EWS must ensure that he/she possesses the Income & "
              "Asset certificate valid for the financial year 2026-27 issued on the basis of income for the financial year "
              "2025-26 in accordance with the DoP&T OM No. 36039/1/2019-Estt(Res.) dated 31.01.2019.")
SSC_JE_CLOSING = "Last date and time for receipt of Online Application Form 22.09.2026 (23:00 hours)"

def _page_text(path, page):
    return " ".join(pymupdf.open(path)[page - 1].get_text().split())

def test_clauses_are_verbatim():
    assert UPSC_OBC in _page_text(FIXTURES / "upsc" / "AdvtNo-12-2026-Engl-250926_0.pdf", 19)
    je = FIXTURES / "ssc" / "Notice_of_adv_je_2026.pdf"
    assert SSC_JE_EWS in _page_text(je, 11) and SSC_JE_CLOSING in _page_text(je, 1)

PERSON = {"full_name": "Asha Rao", "date_of_birth": "1995-03-10", "category": "OBC", "is_pwbd": False, "nationality": "Indian"}
LAW_GRAD = {
    "personal_info": PERSON,
    "education": [{"degree": "LL.B.", "institution": "Faculty of Law, University of Delhi", "education_level": "BACHELORS", "end_date": "2021-06-30"}],
    "experience": [{"organization": "Rao & Associates", "designation": "Legal Associate", "domain": "Legal",
                    "start_date": "2022-01-01", "responsibilities": "Drafting pleadings, appearing before civil courts"}],
}
OBC_CERT = {"kind": "OBC_NCL", "issue_date": "2026-04-10", "issuing_authority": "Tehsildar, New Delhi", "central_format": True}

async def _law_officer(db_session):
    source = await get_or_create_source(db_session, upsc.UPSC_SOURCE)
    name = "AdvtNo-12-2026-Engl-250926_0.pdf"
    await ingest_pdf_bytes(db_session, source, f"https://www.upsc.gov.in/sites/default/files/{name}", (FIXTURES / "upsc" / name).read_bytes())
    vacancy = (await db_session.execute(select(Vacancy).where(Vacancy.external_ref == "26091203226"))).scalar_one()
    crucial = vacancy.application_deadline.astimezone(IST).date()
    db_session.add(VacancyRequirement(vacancy_id=vacancy.id, req_type="CERTIFICATE", raw_document_id=vacancy.raw_document_id,
                                      value=json.dumps({"kind": "OBC_NCL", "crucial_date": crucial.isoformat()}),
                                      source_page=19, source_section="General Instructions (f)", source_excerpt=UPSC_OBC))
    await db_session.commit()
    return vacancy, crucial

@pytest.mark.asyncio
async def test_vacancy_certificate_section_is_separate_and_follows_review(api, login, db_session):
    vacancy, crucial = await _law_officer(db_session)
    h = await login("asha@example.com")
    profile = (await api.put("/profile", json={**LAW_GRAD, "reservation_certificates": [OBC_CERT]}, headers=h)).json()
    assert profile["reservation_certificates"][0]["kind"] == "OBC_NCL"
    assert profile["reservation_certificates"][0]["issue_date"] == "2026-04-10"
    v1 = profile["version"]["id"]

    # The certificate is a claim: one UNVERIFIED Fact with USER_ATTESTED evidence naming the profile row
    facts = (await api.get("/profile/facts", headers=h)).json()
    cert_fact = next(f for f in facts if f["subject"] == "reservation_certificates")
    assert cert_fact["status"] == "UNVERIFIED" and cert_fact["value"].startswith("OBC_NCL certificate; issued 2026-04-10")
    evidence = (await api.get(f"/profile/facts/{cert_fact['id']}/evidence", headers=h)).json()
    assert [e["evidence_type"] for e in evidence] == ["USER_ATTESTED"]

    async def evaluate(version):
        r = await api.post("/eligibility", json={"profile_version_id": version, "vacancy_id": str(vacancy.id)}, headers=h)
        assert r.status_code == 201, r.text
        return r.json()

    res = await evaluate(v1)
    # The hard verdict is what it was without certificates (test_eligibility_integration: ELIGIBLE)
    assert res["status"] == "ELIGIBLE" and all(e["check_name"] != "CERTIFICATE" for e in res["evidence"])
    [cert] = res["certificates"]
    # No issue window and no cited rule on how recent an OBC-NCL certificate must be: not decided
    assert (cert["outcome"], res["certificate_status"]) == ("CANNOT_DECIDE", "CANNOT_DECIDE")
    assert cert["source_page"] == "19" and cert["requirement"] == f"OBC_NCL: {UPSC_OBC}"
    assert cert["match_type"] == "SELF_REPORTED" and cert["profile_evidence"] == cert_fact["value"]
    assert "issue window" in cert["rule"]
    # Gaps explain the stored result; certificate rows do not make it look stale
    gaps = (await api.get(f"/gaps?profile_version_id={v1}&vacancy_id={vacancy.id}", headers=h)).json()
    assert gaps["eligibility_result_id"] == res["id"]

    # A reviewer verifies the certificate against a stored document
    reviewer = await login("reviewer@example.com")
    (await db_session.execute(select(User).where(User.email == "reviewer@example.com"))).scalar_one().is_reviewer = True
    await db_session.commit()
    raw_id = (await db_session.execute(select(Vacancy.raw_document_id).where(Vacancy.id == vacancy.id))).scalar_one()
    r = await api.post(f"/profile/facts/{cert_fact['id']}/evidence",
                       json={"evidence_type": "DOCUMENT", "raw_document_id": str(raw_id), "source": "OBC certificate"}, headers=h)
    assert r.status_code == 201, r.text
    assert (await api.post(f"/review/facts/{cert_fact['id']}/verify", json={"note": "matches certificate"}, headers=reviewer)).status_code == 200
    verified = (await evaluate(v1))["certificates"][0]
    assert verified["match_type"] == "REVIEWER_VERIFIED" and verified["outcome"] == "CANNOT_DECIDE"

    # A rejected certificate counts as not held: the OBC claim needs a document
    v2 = (await api.patch("/profile", json={"reservation_certificates": [{**OBC_CERT, "issue_date": "2026-04-11"}]},
                          headers=h)).json()["version"]["id"]
    other = next(f for f in (await api.get(f"/profile/facts?version_id={v2}", headers=h)).json() if f["subject"] == "reservation_certificates")
    assert (await api.post(f"/review/facts/{other['id']}/reject", json={"note": "date does not match"}, headers=reviewer)).status_code == 200
    rejected = await evaluate(v2)
    assert rejected["status"] == "ELIGIBLE" and rejected["certificate_status"] == "NEEDS_DOCUMENT"
    assert rejected["certificates"][0]["match_type"] is None and "provisionally" in rejected["certificates"][0]["rule"]
    # Posting the rejected claim again as a fresh (UNVERIFIED) Fact does not undo the reviewer's rejection
    again_claimed = await api.post("/profile/facts", json={"subject": other["subject"], "predicate": other["predicate"],
                                                           "value": other["value"]}, headers=h)
    assert again_claimed.status_code == 201, again_claimed.text
    assert (await evaluate(v2))["certificate_status"] == "NEEDS_DOCUMENT"

    # No reserved-category claim: the certificate is not needed
    v3 = (await api.patch("/profile", json={"personal_info": {**PERSON, "category": "UR"}}, headers=h)).json()["version"]["id"]
    assert (await evaluate(v3))["certificate_status"] == "NOT_APPLICABLE"
    # Re-running version 1 reproduces its section
    again = await evaluate(v1)
    assert [(c["outcome"], c["match_type"]) for c in again["certificates"]] == [("CANNOT_DECIDE", "REVIEWER_VERIFIED")]

@pytest.mark.asyncio
async def test_vacancy_without_certificate_rows_has_no_section(api, login, db_session):
    source = await get_or_create_source(db_session, upsc.UPSC_SOURCE)
    name = "AdvtNo-12-2026-Engl-250926_0.pdf"
    await ingest_pdf_bytes(db_session, source, f"https://www.upsc.gov.in/sites/default/files/{name}", (FIXTURES / "upsc" / name).read_bytes())
    await db_session.commit()
    vacancy = (await db_session.execute(select(Vacancy).where(Vacancy.external_ref == "26091203226"))).scalar_one()
    h = await login("asha@example.com")
    v1 = (await api.put("/profile", json={**LAW_GRAD, "reservation_certificates": [OBC_CERT]}, headers=h)).json()["version"]["id"]
    res = (await api.post("/eligibility", json={"profile_version_id": v1, "vacancy_id": str(vacancy.id)}, headers=h)).json()
    assert res["status"] == "ELIGIBLE" and res["certificates"] == [] and res["certificate_status"] is None

@pytest.mark.asyncio
async def test_exam_ews_certificate_section(api, login, db_session):
    source = await get_or_create_source(db_session, ssc.SSC_SOURCE)
    sources = json.loads((FIXTURES / "ssc" / "sources.json").read_text(encoding="utf-8"))
    name = "Notice_of_adv_je_2026.pdf"
    exam_id = (await ingest_exam_pdf(db_session, source, sources[name]["url"], (FIXTURES / "ssc" / name).read_bytes())).exam_id
    await db_session.commit()
    exam = await db_session.get(Exam, exam_id)
    closing = exam.application_end.astimezone(IST).date()
    assert closing == date(2026, 9, 22)  # SSC_JE_CLOSING

    h = await login("asha@example.com")
    person = {"full_name": "Asha Verma", "date_of_birth": "2000-03-10", "gender": "Female", "category": "EWS", "is_pwbd": False,
              "is_ex_serviceman": False, "is_central_govt_employee": False, "nationality": "Indian"}
    education = [{"degree": "B.E. (Civil Engineering)", "institution": "MBM Engineering College", "specialization": "Civil Engineering",
                  "education_level": "BACHELORS", "end_date": "2022-06-30"}]

    async def evaluate(certs):
        body = {"personal_info": person, "education": education, "reservation_certificates": certs}
        version = (await api.put("/profile", json=body, headers=h)).json()["version"]["id"]
        r = await api.post(f"/exams/{exam_id}/eligibility", json={"profile_version_id": version}, headers=h)
        assert r.status_code == 201, r.text
        return r.json()

    right = [{"kind": "EWS", "issue_date": "2026-05-04", "financial_year": "2025-26", "issuing_authority": "Tehsildar, Jodhpur"}]
    before = await evaluate(right)
    assert before["certificates"] == [] and before["certificate_status"] is None

    db_session.add(ExamRequirement(exam_id=exam_id, req_type="CERTIFICATE", raw_document_id=exam.raw_document_id,
                                   value=json.dumps({"kind": "EWS", "crucial_date": closing.isoformat(), "financial_year": "2025-26"}),
                                   source_page=11, source_section="8.5", source_excerpt=SSC_JE_EWS))
    await db_session.commit()

    ok = await evaluate(right)
    assert (ok["status"], ok["confidence"]) == (before["status"], before["confidence"])
    [cert] = ok["certificates"]
    assert (cert["outcome"], cert["match_type"], cert["source_page"]) == ("OK", "SELF_REPORTED", "11")
    assert "not yet verified" in cert["rule"] and cert["requirement"] == f"EWS: {SSC_JE_EWS}"

    wrong_year = await evaluate([{"kind": "EWS", "issue_date": "2025-05-04", "financial_year": "2024-25"}])
    assert wrong_year["status"] == before["status"]
    # Judged on today's date: once the closing date has passed the wrong year can no longer be replaced
    expected = "FAILS" if datetime.now(IST).date() > closing else "NEEDS_DOCUMENT"
    assert wrong_year["certificate_status"] == expected
    assert "FY 2024-25; FY 2025-26 is required" in wrong_year["certificates"][0]["rule"]

    # Two certificates: the one that satisfies the notice counts
    both = await evaluate(right + [{"kind": "EWS", "issue_date": "2025-05-04", "financial_year": "2024-25"}])
    assert both["certificate_status"] == "OK"
