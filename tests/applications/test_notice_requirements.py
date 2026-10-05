"""
What a notice asks of an applicant (certificates, upload rules, fee, documents, key dates), read by the deterministic
parsers from real notices captured from ssc.gov.in and upsc.gov.in:
  - SSC CGL 2026 https://ssc.gov.in/api/attachment/uploads/masterData/NoticeBoards/Notice_of_adv_cgl_2026.pdf (and CHSL, JE 2026)
  - UPSC Advt 12/2026 https://www.upsc.gov.in/sites/default/files/AdvtNo-12-2026-Engl-250926_0.pdf
  - UPSC IES/ISS Examination 2026 notice (tests/fixtures/upsc/prep/SOURCES.txt)
"""
import json
import re
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from govcareeros.backend.documents.pdf_text import extract_pages
from govcareeros.backend.eligibility.certificate_types import CertificateKind
from govcareeros.backend.extraction.validator import find_quote_on_page
from govcareeros.backend.models import ApplicationRequirement, Exam
from govcareeros.backend.parsing.application_rules import certificate_from_value
from govcareeros.backend.parsing.ssc_application import parse_ssc_application_info
from govcareeros.backend.parsing.upsc_advertisement import IST
from govcareeros.backend.parsing.upsc_application import parse_application_info, parse_exam_application_info
from govcareeros.backend.services.document_ingestion import get_or_create_source
from govcareeros.backend.services.exam_ingestion import ingest_exam_pdf
from govcareeros.backend.sources import ssc

FIXTURES = Path(__file__).parents[1] / "fixtures"
CGL = FIXTURES / "ssc" / "Notice_of_adv_cgl_2026.pdf"
CGL_URL = "https://ssc.gov.in/api/attachment/uploads/masterData/NoticeBoards/Notice_of_adv_cgl_2026.pdf"
IES = FIXTURES / "upsc" / "prep" / "Exam_Notification_IES_ISS_Eng_11022026.pdf"
CRUCIAL_SSC = ("Crucial date for claim of SC/ST/OBC/PwBD/ESM status or any other benefit, viz., fee concession, reservation, "
               "age-relaxation, etc., where not specified otherwise, will be the closing date for receipt of online applications.")
PRESCRIBED_SSC = ("Candidates who wish to be considered against reserved vacancies or seek age-relaxation must submit requisite "
                  "certificate from the competent authority, in the prescribed format")

def _pages(path):
    return extract_pages(path.read_bytes(), ocr=False)

def _cert(info, kind):
    return info.get("CERTIFICATE", kind)

def _all_quotes_on_their_pages(info, pages):
    """Every stored quote (each clause of a certificate) is the notice's own text, starting on the page it cites
    (a list item or clause may run on to the next page, e.g. JE para 10.7 from page 15 to 16)."""
    whole = " ".join(re.sub(r"\s+", " ", p.text) for p in pages)
    for item in info.items:
        clauses = item.value["clauses"] if item.req_type == "CERTIFICATE" else [{"page": item.provenance.page, "quote": item.provenance.excerpt}]
        for c in clauses:
            head = " ".join(c["quote"].split()[:12])
            assert find_quote_on_page(head, pages, c["page"]), (item.req_type, item.code, c)
            if len(c["quote"].split()) <= 12:
                continue
            tail = " ".join(c["quote"].split()[-6:])
            assert tail in whole, (item.req_type, item.code, c)

@pytest.fixture(scope="module")
def cgl_pages():
    return _pages(CGL)

def test_ssc_cgl_2026_certificates(cgl_pages):
    info = parse_ssc_application_info(cgl_pages)
    assert info.warnings == []
    _all_quotes_on_their_pages(info, cgl_pages)

    obc = _cert(info, "OBC_NCL")
    assert obc.applies_when == "CATEGORY_OBC" and obc.provenance.page == 11
    req = certificate_from_value(obc.value)
    assert req.kind == CertificateKind.OBC_NCL
    # "on the crucial date i.e. the closing date of application"; closing date 22.06.2026 (23:00 hours), page 1
    assert (str(req.crucial_date), req.issued_on_or_after, req.issued_on_or_before, req.financial_year, req.format_note) == \
        ("2026-06-22", None, None, None, "Annexure-X")
    assert req.source_page == 11 and req.source_quote.startswith(
        "A person seeking appointment on the basis of reservation to OBC must ensure that he does not fall in creamy layer "
        "on the crucial date i.e. the closing date of application.")
    assert req.source_quote.endswith("shall invariably submit the requisite certificate as per the format (Annexure-X).")

    ews = certificate_from_value(_cert(info, "EWS").value)
    assert (ews.crucial_date, ews.financial_year, ews.format_note, ews.source_page) == (None, "2025-26", "Annexure-XI", 11)
    assert [c["quote"] for c in _cert(info, "EWS").value["clauses"]] == [
        "a person, seeking appointment on the basis of reservation to EWS Category, must ensure that he possesses the Income & Asset "
        "certificate valid for the Financial Year 2026-2027 issued on the basis of Income for the Financial Year 2025-2026",
        "applicants seeking reservation of EWS category shall invariably submit the requisite certificate as per Format (Annexure-XI)"]

    pwbd = _cert(info, "PWBD")
    assert pwbd.value["format_note"] == "Annexure-I, Annexure-IA and Annexure-XII to XIII whichever is applicable"
    assert pwbd.value["crucial_date"] == "2026-06-22"
    # Main clause (para 9.7, page 19), then the crucial-date clause it took the date from (para 6.4, page 11)
    assert [(c["page"], c["quote"]) for c in pwbd.value["clauses"]] == [
        (19, "PwBD and PwD candidates willing to avail the benefit of reservation or scribe or both are required to upload Certificate "
             "of Disability as per Annexure- I, Annexure-IA and Annexure-XII to XIII whichever is applicable, at the time of filling "
             "online application form."), (11, CRUCIAL_SSC)]

    for kind in ("SC", "ST", "ESM"):
        c = _cert(info, kind)
        assert (c.value["crucial_date"], c.value["format_note"], c.provenance.page) == ("2026-06-22", "prescribed format", 11)
        assert [(x["page"], x["quote"]) for x in c.value["clauses"]] == [(11, CRUCIAL_SSC), (10, PRESCRIBED_SSC)]
    assert {i.code: i.applies_when for i in info.items if i.req_type == "CERTIFICATE"} == {
        "OBC_NCL": "CATEGORY_OBC", "EWS": "CATEGORY_EWS", "PWBD": "PWBD", "SC": "CATEGORY_SC", "ST": "CATEGORY_ST", "ESM": "ESM"}

def test_ssc_cgl_2026_uploads_fee_documents_dates(cgl_pages):
    info = parse_ssc_application_info(cgl_pages)
    sig = info.get("UPLOAD_RULE", "SIGNATURE")
    assert sig.provenance.page == 18 and sig.provenance.excerpt == (
        "Candidates are required to upload the scanned signature in JPEG/JPG format (10 to 20 KB). Image dimension of the "
        "signature should be about 6.0 cm (width) x 2.0 cm (height).")
    assert {k: sig.value[k] for k in ("formats", "min_kb", "max_kb", "width_cm", "height_cm", "background", "date_on_photo")} == \
        {"formats": ["JPEG", "JPG"], "min_kb": 10, "max_kb": 20, "width_cm": 6.0, "height_cm": 2.0, "background": None, "date_on_photo": None}
    photo = info.get("UPLOAD_RULE", "PHOTOGRAPH")
    # The photograph is captured live by the application module: no file size or format is stated, only how to take it
    assert photo.provenance.page == 18 and (photo.value["live_capture"], photo.value["background"], photo.value["max_kb"]) == (True, "plain", None)
    assert photo.provenance.excerpt.endswith("(i) Find a place with good light and plain background.")

    fee = info.get("FEE")
    assert fee.value == {"amount": 100, "currency": "INR"} and fee.provenance.page == 19
    assert fee.provenance.excerpt == "Fee payable: ₹100/- (Rs one hundred only)."
    ex = info.get("FEE_EXEMPTION")
    assert ex.value["exempt"] == ["ESM", "FEMALE", "PWBD", "SC", "ST"] and ex.provenance.page == 19
    assert ex.provenance.excerpt == ("Women candidates and candidates belonging to Scheduled Castes (SC), Scheduled Tribes (ST), Persons "
                                     "with Benchmark Disabilities (PwBD) and Ex-servicemen (ESM) eligible for reservation are exempted from payment of fee.")
    pay = info.get("PAYMENT_MODE")
    assert pay.provenance.page == 20 and set(pay.value["modes"]) == {"UPI", "INTERNET_BANKING", "VISA", "MASTERCARD", "MAESTRO", "RUPAY", "DEBIT_CARD"}
    assert info.get("FEE_RULE", "NO_REFUND").provenance.excerpt == (
        "Fee once paid shall not be refunded under any circumstances nor will it be adjusted against any other examination or selection.")

    docs = [(i.code, i.applies_when, i.value["item"], i.provenance.page) for i in info.items if i.req_type == "DOCUMENT"]
    assert docs == [
        ("MATRICULATION_CERTIFICATE", "ALWAYS", "1", 40), ("EDUCATION_CERTIFICATE", "ALWAYS", "2", 40),
        ("CASTE_CERTIFICATE", "CATEGORY_RESERVED", "3", 40), ("DISABILITY_CERTIFICATE", "PWBD", "4", 40), ("ESM_DOCUMENTS", "ESM", "5", 40),
        ("AGE_RELAXATION_CERTIFICATE", "AGE_RELAXATION", "6", 40), ("CENTRAL_GOVT_EMPLOYEE_CERTIFICATE", "CENTRAL_GOVT_EMPLOYEE", "7", 40),
        ("NO_OBJECTION_CERTIFICATE", "GOVT_SERVICE", "8", 40), ("NAME_CHANGE_DOCUMENTS", "NAME_CHANGE", "9", 40)]
    assert info.get("DOCUMENT", "ESM_DOCUMENTS").value["text"].startswith(
        "For Ex-Servicemen (ESM): 15.11.5.1 Serving Defence Personnel Certificate as per Annexure-VII, if applicable.")

    dates = {i.code: (i.value["at"], i.provenance.page, i.provenance.excerpt) for i in info.items if i.req_type == "KEY_DATE"}
    assert dates["APPLICATION_END"] == ("2026-06-22T23:00:00+05:30", 1, "Last date and time for receipt of online applications 22.06.2026 (23:00 hours)")
    assert dates["FEE_PAYMENT_END"][0] == "2026-06-23T23:00:00+05:30"
    assert (dates["CORRECTION_START"][0], dates["CORRECTION_END"][0]) == ("2026-06-29T00:00:00+05:30", "2026-07-01T23:00:00+05:30")

@pytest.mark.parametrize("pdf,closing,ews_format,pwbd_page,fee_page,warnings", [
    ("Notice_of_adv_chsle_2026.pdf", "2026-10-07", "Annexure-XIV", 12, 13, []),
    # The JE notice has no OBC creamy-layer paragraph; its OBC row comes from the crucial-date clause (para 8.7) alone
    ("Notice_of_adv_je_2026.pdf", "2026-09-22", "Annexure-IX", 15, 16, ["certificate clause not found: obc"]),
])
def test_other_ssc_notices_share_the_layout(pdf, closing, ews_format, pwbd_page, fee_page, warnings):
    pages = _pages(FIXTURES / "ssc" / pdf)
    info = parse_ssc_application_info(pages)
    assert info.warnings == warnings
    _all_quotes_on_their_pages(info, pages)
    assert _cert(info, "OBC_NCL").value["crucial_date"] == closing
    assert (_cert(info, "EWS").value["financial_year"], _cert(info, "EWS").value["format_note"]) == ("2025-26", ews_format)
    assert _cert(info, "PWBD").provenance.page == pwbd_page
    sig = info.get("UPLOAD_RULE", "SIGNATURE").value
    assert (sig["min_kb"], sig["max_kb"], sig["width_cm"], sig["height_cm"]) == (10, 20, 6.0, 2.0)
    assert info.get("FEE").value["amount"] == 100 and info.get("FEE").provenance.page == fee_page
    assert info.get("FEE_EXEMPTION").value["exempt"] == ["ESM", "FEMALE", "PWBD", "SC", "ST"]

def test_je_obc_row_without_creamy_layer_paragraph():
    pages = _pages(FIXTURES / "ssc" / "Notice_of_adv_je_2026.pdf")
    obc = _cert(parse_ssc_application_info(pages), "OBC_NCL")
    assert obc.provenance.page == 11 and obc.value["clauses"][0]["quote"] == (
        "The crucial date for the claim of SC/ ST/ OBC/ PwBD/ ESM status or any other benefit, viz., fee concession, reservation, "
        "age-relaxation, etc., will be the closing date of the Online Application Form.")
    docs = [i.code for i in parse_ssc_application_info(pages).items if i.req_type == "DOCUMENT"]
    # (iii) "Experience Certificate, if applicable."; the closing "(xi) It is reiterated ..." note is not a document
    assert docs[:3] == ["MATRICULATION_CERTIFICATE", "EDUCATION_CERTIFICATE", "EXPERIENCE_CERTIFICATE"] and len(docs) == 10

def test_upsc_advt_12_certificates_and_closing_date():
    pages = _pages(FIXTURES / "upsc" / "AdvtNo-12-2026-Engl-250926_0.pdf")
    info = parse_application_info(pages)
    assert info.warnings == []
    _all_quotes_on_their_pages(info, pages)
    end = info.get("KEY_DATE", "APPLICATION_END")
    assert end.value == {"at": "2026-10-16T18:00:00+05:30"} and end.provenance.page == 13
    assert end.provenance.excerpt == "CLOSING DATE FOR SUBMISSION OF ONLINE RECRUITMENT APPLICATION THROUGH WEBSITE IS 1800 HRS ON 16-10-2026"
    obc = _cert(info, "OBC_NCL")
    # "Unless specified otherwise, the prescribed closing date for receipt of Online Recruitment Application for the post is to be treated as crucial date."
    assert (obc.value["crucial_date"], obc.value["format_note"], obc.provenance.page) == ("2026-10-16", "prescribed format", 19)
    assert "is to be treated as crucial date" in obc.value["source_quote"]
    for kind in ("SC", "ST", "PWBD"):
        assert (_cert(info, kind).value["format_note"], _cert(info, kind).value["crucial_date"], _cert(info, kind).provenance.page) == \
            ("prescribed proforma", None, 19)
    esm = _cert(info, "ESM")
    assert esm.value["source_quote"] == "Ex-Servicemen and Commissioned Officers including ECOs/SSCOs in prescribed proforma from competent authority."

def test_upsc_ies_iss_2026_exam_notice():
    pages = _pages(IES)
    info = parse_exam_application_info(pages)
    assert info.warnings == []
    _all_quotes_on_their_pages(info, pages)

    obc = certificate_from_value(_cert(info, "OBC_NCL").value)
    # "issued on/after 01.04.2025 (after completion of FY 2024-25) but not later than the closing date of the application";
    # based on income of three years (2024-25, 2023-24 and 2022-23), so no single financial year is stored
    assert (str(obc.issued_on_or_after), str(obc.issued_on_or_before), str(obc.crucial_date), obc.financial_year, obc.format_note) == \
        ("2025-04-01", "2026-03-03", "2026-03-03", None, "prescribed format")
    assert [c["page"] for c in _cert(info, "OBC_NCL").value["clauses"]] == [5, 6]
    ews = certificate_from_value(_cert(info, "EWS").value)
    assert (str(ews.issued_on_or_after), str(ews.issued_on_or_before), ews.financial_year, ews.source_page) == ("2025-04-01", "2026-03-03", "2024-25", 6)
    assert "Income & Asset Certificate based on the income for Financial Year (FY) 2024-2025 and issued on/after 01.04.2025" in ews.source_quote
    for kind in ("SC", "ST", "PWBD", "ESM"):
        c = certificate_from_value(_cert(info, kind).value)
        assert (str(c.crucial_date), c.issued_on_or_after, c.format_note, c.source_page) == ("2026-03-03", None, "prescribed format", 6)

    photo, sig = info.get("UPLOAD_RULE", "PHOTOGRAPH"), info.get("UPLOAD_RULE", "SIGNATURE")
    assert (photo.value["formats"], photo.value["min_kb"], photo.value["max_kb"]) == (["JPG"], 20, 200)
    assert (sig.value["formats"], sig.value["min_kb"], sig.value["max_kb"]) == (["JPG"], 20, 100)
    assert photo.provenance.page == 26 and photo.provenance.excerpt == (
        "Before filling up Online Application Form, a candidate must have his photograph and signature duly scanned in the .jpg format "
        "in such a manner that photograph file should not exceed 200 KB and must not be less than 20 KB in size and signature file "
        "should not exceed 100 KB and must not be less than 20 KB in size.")

    assert info.get("FEE").value == {"amount": 200, "currency": "INR"} and info.get("FEE").provenance.page == 9
    assert info.get("FEE_EXEMPTION").value == {"exempt": ["FEMALE", "PWBD", "SC", "ST"],
                                               "text": "Female/SC/ST/Persons with Benchmark Disability (PwBD)"}
    dates = {i.code: i.value["at"] for i in info.items if i.req_type == "KEY_DATE"}
    assert dates == {"APPLICATION_START": "2026-02-11T00:00:00+05:30", "APPLICATION_END": "2026-03-03T18:00:00+05:30"}
    assert info.get("KEY_DATE", "APPLICATION_END").provenance.excerpt == \
        "The Online Application can be filled from 11th February, 2026 to 03rd March, 2026 till 18:00 hours"

async def _version(api, headers, personal_info):
    r = await api.put("/profile", json={"personal_info": personal_info}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["version"]["id"]

@pytest.mark.asyncio
async def test_ssc_exam_prep_checklist_is_cited(api, login, db_session):
    source = await get_or_create_source(db_session, ssc.SSC_SOURCE)
    result = await ingest_exam_pdf(db_session, source, CGL_URL, CGL.read_bytes())
    assert result.requirement_warnings == []
    await db_session.commit()
    exam = await db_session.get(Exam, result.exam_id)
    rows = (await db_session.execute(select(ApplicationRequirement).where(ApplicationRequirement.raw_document_id == exam.raw_document_id))).scalars().all()
    assert {r.parser_version for r in rows} == {"ssc_application/1"} and {r.advertisement_number for r in rows} == {exam.external_ref}
    assert json.loads(next(r for r in rows if r.code == "EWS").value)["financial_year"] == "2025-26"

    # OBC woman, not PwBD, not ex-serviceman: OBC certificate needed, fee exempt as a woman
    h = await login("meera@example.com")
    v = await _version(api, h, {"full_name": "Meera", "gender": "Female", "category": "OBC", "is_pwbd": False, "is_ex_serviceman": False})
    r = await api.get(f"/applications/exam-prep?exam_id={exam.id}&profile_version_id={v}", headers=h)
    assert r.status_code == 200, r.text
    prep = r.json()
    assert prep["warnings"] == [] and prep["exam_name"] == "Combined Graduate Level Examination, 2026"
    assert prep["fee"]["status"] == "EXEMPT" and prep["fee"]["reason"] == "exempt as FEMALE" and prep["fee"]["amount"] == 100
    assert {c["page"] for c in prep["fee"]["citations"]} == {19}
    certs = {c["kind"]: c for c in prep["certificates"]}
    assert certs["OBC_NCL"]["status"] == "REQUIRED" and "(2026-06-22)" in certs["OBC_NCL"]["reason"]
    assert certs["OBC_NCL"]["format_note"] == "Annexure-X" and certs["OBC_NCL"]["citation"]["page"] == 11
    assert certs["OBC_NCL"]["citation"]["document_url"] == CGL_URL
    assert certs["EWS"]["status"] == "NOT_REQUIRED" and certs["EWS"]["financial_year"] == "2025-26"
    assert (certs["PWBD"]["status"], certs["ESM"]["status"], certs["SC"]["status"]) == ("NOT_REQUIRED", "NOT_REQUIRED", "NOT_REQUIRED")
    assert [c["page"] for c in certs["PWBD"]["clauses"]] == [19, 11]
    sig = next(u for u in prep["upload_rules"] if u["code"] == "SIGNATURE")
    assert (sig["min_kb"], sig["max_kb"], sig["citation"]["page"]) == (10, 20, 18)
    docs = {d["code"]: d["status"] for d in prep["documents"]}
    assert docs["CASTE_CERTIFICATE"] == "REQUIRED" and docs["ESM_DOCUMENTS"] == "NOT_REQUIRED" and docs["MATRICULATION_CERTIFICATE"] == "REQUIRED"
    assert [d["code"] for d in prep["documents"]][:2] == ["MATRICULATION_CERTIFICATE", "EDUCATION_CERTIFICATE"]
    assert [d["code"] for d in prep["key_dates"]][:2] == ["APPLICATION_START", "APPLICATION_END"]
    assert prep["key_dates"][1]["at"] == datetime(2026, 6, 22, 23, 0, tzinfo=IST).isoformat()

    # Male ex-serviceman with no category stated: fee exempt as ESM; category certificates cannot be decided
    h2 = await login("arjun@example.com")
    v2 = await _version(api, h2, {"full_name": "Arjun", "gender": "Male", "is_pwbd": False, "is_ex_serviceman": True})
    prep = (await api.get(f"/applications/exam-prep?exam_id={exam.id}&profile_version_id={v2}", headers=h2)).json()
    assert prep["fee"]["status"] == "EXEMPT" and prep["fee"]["reason"] == "exempt as ESM"
    certs = {c["kind"]: c["status"] for c in prep["certificates"]}
    assert certs["ESM"] == "REQUIRED" and certs["OBC_NCL"] == "UNKNOWN" and certs["EWS"] == "UNKNOWN"
    assert (await api.get(f"/applications/exam-prep?exam_id={exam.id}&profile_version_id={v}", headers=h2)).status_code == 404
