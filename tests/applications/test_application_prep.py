"""
Application preparation from the real UPSC advertisements captured from upsc.gov.in
(Advt 12/2026: https://www.upsc.gov.in/sites/default/files/AdvtNo-12-2026-Engl-250926_0.pdf, Advt 11/2026).
"""
from pathlib import Path

import pytest
from sqlalchemy import func, select

from govcareeros.backend.documents.pdf_text import extract_pages
from govcareeros.backend.models import ApplicationRequirement, Vacancy
from govcareeros.backend.parsing.upsc_application import parse_application_info
from govcareeros.backend.services.document_ingestion import get_or_create_source, ingest_pdf_bytes
from govcareeros.backend.sources import upsc

UPSC = Path(__file__).parents[1] / "fixtures" / "upsc"
ADVT12 = UPSC / "AdvtNo-12-2026-Engl-250926_0.pdf"
LAW_OFFICER = "26091203226"  # Advt 12/2026, experience required

def test_general_instructions_of_advt_12():
    info = parse_application_info(extract_pages(ADVT12.read_bytes(), ocr=False))
    assert info.warnings == []

    exemption = info.get("FEE_EXEMPTION")
    assert exemption.provenance.page == 21
    assert exemption.value["exempt"] == ["FEMALE", "PWBD", "SC", "ST"]
    assert "No fee for SC/ST/PwBD/Women candidates of any community" in exemption.provenance.excerpt
    assert info.get("FEE").value == {"amount": 25, "currency": "INR"}
    assert {"UPI", "INTERNET_BANKING", "RUPAY"} <= set(info.get("PAYMENT_MODE").value["modes"])
    assert info.get("APPLICATION_PORTAL").value["url"] == "https://upsconline.nic.in"
    photo_id = info.get("PHOTO_ID")
    assert photo_id.provenance.page == 20 and photo_id.value["accepted"][0] == "Aadhaar Card" and photo_id.value["aadhaar_advised"]

    docs = {i.value["item"]: i for i in info.items if i.req_type == "DOCUMENT" and "item" in i.value}
    assert list(docs) == list("abcdefghijkl")  # the roman "i) Ex-Servicemen" inside item (i) is not a new item
    pages = {d.code: d.provenance.page for d in docs.values()}
    assert pages == {
        "DATE_OF_BIRTH_PROOF": 18, "EDUCATION_CERTIFICATE": 18, "EQUIVALENCE_ORDER": 18, "EXPERIENCE_CERTIFICATE": 18,
        "CASTE_CERTIFICATE": 19, "OBC_NON_CREAMY_LAYER_DECLARATION": 19, "DISABILITY_CERTIFICATE": 19,
        "NAME_CHANGE_DOCUMENTS": 19, "AGE_RELAXATION_CERTIFICATE": 19, "GOVT_SERVICE_DECLARATION": 20,
        "OTHER_CLAIM_CERTIFICATES": 20, "OTHER_CLAIMS": 20,
    }
    assert "does not belong to the creamy layer on the crucial date" in docs["f"].value["text"]
    assert "Un-signed Experience Certificate" in info.get("UPLOAD_RULE").value["documents"]

def test_advt_11_has_the_same_instructions_on_its_own_pages():
    info = parse_application_info(extract_pages((UPSC / "AdvtNo-11-2026-Engl-100926.pdf").read_bytes(), ocr=False))
    assert info.warnings == []
    assert info.get("FEE_EXEMPTION").provenance.page == 31
    assert info.get("DOCUMENT", "CASTE_CERTIFICATE").provenance.page == 28

async def _ingest(db_session):
    source = await get_or_create_source(db_session, upsc.UPSC_SOURCE)
    url = f"https://www.upsc.gov.in/sites/default/files/{ADVT12.name}"
    await ingest_pdf_bytes(db_session, source, url, ADVT12.read_bytes())
    await db_session.commit()
    return source, url

async def _version(api, headers, personal_info, **extra):
    r = await api.put("/profile", json={"personal_info": personal_info, **extra}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["version"]["id"]

@pytest.mark.asyncio
async def test_prep_checklist_is_personalised_and_cited(api, login, db_session):
    source, url = await _ingest(db_session)
    count = (await db_session.execute(select(func.count()).select_from(ApplicationRequirement))).scalar_one()
    await _ingest(db_session)  # re-ingesting the same document adds nothing
    assert (await db_session.execute(select(func.count()).select_from(ApplicationRequirement))).scalar_one() == count
    law_id = (await db_session.execute(select(Vacancy.id).where(Vacancy.external_ref == LAW_OFFICER))).scalar_one()

    asha = await login("asha@example.com")
    asha_v = await _version(api, asha, {"full_name": "Asha Rao", "gender": "Male", "category": "OBC", "is_pwbd": False,
                                        "is_ex_serviceman": False, "is_central_govt_employee": False})
    r = await api.get(f"/applications/prep?vacancy_id={law_id}&profile_version_id={asha_v}", headers=asha)
    assert r.status_code == 200, r.text
    prep = r.json()
    assert prep["advertisement_number"] == "12/2026" and prep["warnings"] == []
    assert prep["fee"]["status"] == "PAY" and prep["fee"]["amount"] == 25
    assert {c["page"] for c in prep["fee"]["citations"]} == {21}
    assert prep["portal"]["url"] == "https://upsconline.nic.in" and prep["portal"]["citation"]["document_url"] == url
    docs = {d["code"]: d for d in prep["documents"]}
    assert docs["CASTE_CERTIFICATE"]["status"] == "REQUIRED"
    ncl = docs["OBC_NON_CREAMY_LAYER_DECLARATION"]
    assert ncl["status"] == "REQUIRED" and "16 Oct 2026 18:00 IST" in ncl["reason"] and ncl["citation"]["page"] == 19
    assert docs["DISABILITY_CERTIFICATE"]["status"] == "NOT_REQUIRED"
    assert docs["EXPERIENCE_CERTIFICATE"]["status"] == "REQUIRED"  # Law Officer's EQ includes experience
    assert docs["AGE_RELAXATION_CERTIFICATE"]["status"] == "IF_APPLICABLE"
    assert all(d["citation"]["excerpt"] and d["citation"]["page"] for d in prep["documents"])

    # Woman, SC, PwBD: exempt from the fee; disability certificate needed, OBC declaration not
    priya = await login("priya@example.com")
    priya_v = await _version(api, priya, {"full_name": "Priya", "gender": "female", "category": "SC", "is_pwbd": True})
    prep = (await api.get(f"/applications/prep?vacancy_id={law_id}&profile_version_id={priya_v}", headers=priya)).json()
    assert prep["fee"]["status"] == "EXEMPT" and prep["fee"]["reason"] == "exempt as FEMALE/PWBD/SC"
    docs = {d["code"]: d for d in prep["documents"]}
    assert docs["DISABILITY_CERTIFICATE"]["status"] == "REQUIRED"
    assert docs["OBC_NON_CREAMY_LAYER_DECLARATION"]["status"] == "NOT_REQUIRED"
    assert docs["AGE_RELAXATION_CERTIFICATE"]["status"] == "UNKNOWN"

    # UR without gender: fee cannot be decided
    ravi = await login("ravi@example.com")
    ravi_v = await _version(api, ravi, {"full_name": "Ravi", "category": "UR", "is_pwbd": False})
    prep = (await api.get(f"/applications/prep?vacancy_id={law_id}&profile_version_id={ravi_v}", headers=ravi)).json()
    assert prep["fee"]["status"] == "UNKNOWN" and "gender" in prep["fee"]["reason"]

    # Another user's profile version is not visible
    assert (await api.get(f"/applications/prep?vacancy_id={law_id}&profile_version_id={asha_v}", headers=ravi)).status_code == 404
    assert (await api.get(f"/applications/prep?vacancy_id={law_id}&profile_version_id={asha_v}")).status_code == 401
