"""
Apply-ready pages built from real notices captured from upsc.gov.in and ssc.gov.in:
  - UPSC Advt 12/2026 https://www.upsc.gov.in/sites/default/files/AdvtNo-12-2026-Engl-250926_0.pdf (Law Officer) and Advt 11/2026
  - SSC CHSL 2026 and JE 2026 notices (tests/fixtures/ssc/sources.json)
Every cited quote is checked against the text of the page it cites. Profiles are test-only users made in the test.
"""
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from govcareeros.backend.documents.pdf_text import extract_pages
from govcareeros.backend.extraction.validator import find_quote_on_page
from govcareeros.backend.models import ApplicationRequirementParse, Exam, Vacancy
from govcareeros.backend.parsing.upsc_advertisement import IST
from govcareeros.backend.schemas.attribution import DISCLAIMER
from govcareeros.backend.services import apply_ready
from govcareeros.backend.services.document_ingestion import get_or_create_source, ingest_pdf_bytes
from govcareeros.backend.services.exam_ingestion import ingest_exam_pdf
from govcareeros.backend.sources import ssc, upsc

FIXTURES = Path(__file__).parents[1] / "fixtures"
ADVT12 = FIXTURES / "upsc" / "AdvtNo-12-2026-Engl-250926_0.pdf"
ADVT12_URL = f"https://www.upsc.gov.in/sites/default/files/{ADVT12.name}"
LAW_OFFICER = "26091203226"  # Advt 12/2026: LL.B. and two years' legal experience
SSC_URLS = json.loads((FIXTURES / "ssc" / "sources.json").read_text(encoding="utf-8"))
KEYS = ["dates", "eligibility", "certificates", "fee", "uploads", "photo_id", "documents", "portal"]
BEFORE_CLOSING = datetime(2026, 10, 6, 12, 0, tzinfo=IST)  # CHSL closes 07.10.2026 23:00, Advt 12 on 16.10.2026 18:00

def _sections(page):
    assert [s["key"] for s in page["sections"]] == KEYS
    return {s["key"]: s["items"] for s in page["sections"]}

def _quotes_on_their_pages(page, pdf, url):
    """Each item's quote is the notice's own text on the page it cites (a table row or list item may run on)."""
    pages = extract_pages(pdf.read_bytes(), ocr=False)
    whole = " ".join(re.sub(r"\s+", " ", p.text) for p in pages)
    for s in page["sections"]:
        for it in s["items"]:
            c = it["citation"]
            assert c and c["page"] and c["quote"] and c["document_url"] == url, (s["key"], it)
            # Post-wise rows join a table row with the clauses it relies on (" | p.8: ...") and the table parser notes
            # cells carried down from a merged row ("[merged cell(s) continued ...]"): check each part of the notice's text
            quote = re.sub(r"\s*\[merged cell\(s\) continued from the row above:[^\]]*\]", "", c["quote"])
            for part in re.split(r" \| (?:p\.\d+: )?", quote):
                head = " ".join(part.split()[:10])
                assert find_quote_on_page(head, pages, c["page"]) or head in whole, (s["key"], it["label"], part)

async def _ingest_advt12(db_session):
    source = await get_or_create_source(db_session, upsc.UPSC_SOURCE)
    await ingest_pdf_bytes(db_session, source, ADVT12_URL, ADVT12.read_bytes())
    await db_session.commit()
    return (await db_session.execute(select(Vacancy).where(Vacancy.external_ref == LAW_OFFICER))).scalar_one()

async def _ingest_ssc(db_session, name):
    source = await get_or_create_source(db_session, ssc.SSC_SOURCE)
    exam_id = (await ingest_exam_pdf(db_session, source, SSC_URLS[name]["url"], (FIXTURES / "ssc" / name).read_bytes())).exam_id
    await db_session.commit()
    return await db_session.get(Exam, exam_id)

async def _version(api, headers, personal_info, **extra):
    r = await api.put("/profile", json={"personal_info": personal_info, **extra}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["version"]["id"]

LAW_GRAD = {"education": [{"degree": "LL.B.", "institution": "Faculty of Law, University of Delhi", "education_level": "BACHELORS",
                           "end_date": "2021-06-30"}]}

@pytest.mark.asyncio
async def test_upsc_advt_12_public_page(api, db_session):
    vacancy = await _ingest_advt12(db_session)
    r = await api.get(f"/apply-ready/vacancies/{vacancy.id}")  # no login
    assert r.status_code == 200, r.text
    page = r.json()
    assert (page["kind"], page["id"], page["title"], page["organization"], page["advertisement_number"]) == \
        ("vacancy", str(vacancy.id), "Law Officer", "Union Public Service Commission", "12/2026")
    assert page["document_url"] == ADVT12_URL and page["warnings"] == [] and page["disclaimer"] == DISCLAIMER
    assert datetime.fromisoformat(page["deadline"]) == datetime(2026, 10, 16, 18, 0, tzinfo=IST)
    assert page["status"] == ("OPEN" if datetime.now(timezone.utc) <= datetime(2026, 10, 16, 18, 0, tzinfo=IST) else "CLOSED")
    assert page["attribution"]["document_url"] == ADVT12_URL and page["attribution"]["source_name"]
    s = _sections(page)
    _quotes_on_their_pages(page, ADVT12, ADVT12_URL)
    assert all(it["status"] is None for items in s.values() for it in items)  # public: no personal status

    [end] = s["dates"]
    assert (end["label"], end["detail"], end["citation"]["page"], end["citation"]["quote"]) == (
        "Aavedan ki antim tithi", "16 Oct 2026, 18:00 IST", 13,
        "CLOSING DATE FOR SUBMISSION OF ONLINE RECRUITMENT APPLICATION THROUGH WEBSITE IS 1800 HRS ON 16-10-2026")

    elig = {it["label"]: it for it in s["eligibility"]}
    assert elig["Shaikshik yogyata (education)"]["detail"] == "Degree in Law from a recognized Institute/University."
    assert elig["Aayu seema (age limit)"]["detail"] == "UR: adhiktam 30 saal; OBC: adhiktam 33 saal"
    assert elig["Aayu seema (age limit)"]["citation"]["page"] == 8
    assert elig["Anubhav (experience)"]["detail"].startswith("Two years")
    assert elig["Nagrikta (citizenship)"]["citation"]["page"] == 14
    assert not any(l.startswith(("Duties", "Reservation")) for l in elig)  # not eligibility conditions

    certs = {it["label"]: it for it in s["certificates"]}
    obc = certs["OBC (Non-Creamy Layer) certificate"]
    assert obc["citation"]["page"] == 19 and "is to be treated as crucial date" in obc["citation"]["quote"]
    assert obc["detail"] == "Kis par lagu: OBC ummeedwar; Crucial date: 16 Oct 2026; Format: prescribed format"
    # The advertisement states no date for the SC certificate: said so (what that means for a candidate is certificate_check's)
    assert certs["SC certificate"]["detail"] == "Kis par lagu: SC ummeedwar; Format: prescribed proforma; Validity: notice mein koi tithi nahi di gayi"

    fee = {it["label"]: it for it in s["fee"]}
    assert (fee["Application fee"]["detail"], fee["Application fee"]["citation"]["page"]) == ("₹25", 21)
    exemption = fee["Fee maafi (exemption)"]
    assert exemption["detail"] == "Fee nahi lagegi: Mahilayein, PwBD, SC, ST"
    assert "No fee for SC/ST/PwBD/Women candidates of any community" in exemption["citation"]["quote"]
    assert fee["Fee wapas nahi hogi"]["citation"]["quote"].startswith("Fee once paid shall not be refunded")

    uploads = {it["label"]: it for it in s["uploads"]}
    assert "Un-signed Experience Certificate" in uploads["Ye documents upload na karein"]["detail"]
    assert uploads["Signature"]["citation"]["page"] == 17
    [photo_id] = s["photo_id"]
    assert photo_id["detail"].startswith("Aadhaar Card") and photo_id["citation"]["page"] == 20
    # The advertisement's lettered list (a)..(l), in its order, each on its page
    assert [(d["label"], d["citation"]["page"]) for d in s["documents"]] == [
        ("Janm tithi ka praman", 18), ("Shaikshik yogyata ka certificate", 18), ("Samkaksh (equivalent) qualification ka order", 18),
        ("Anubhav (experience) certificate", 18), ("Jaati / category certificate", 19), ("OBC non-creamy layer declaration", 19),
        ("Divyangta (PwBD) certificate", 19), ("Naam badalne ke documents", 19), ("Aayu chhoot ka certificate", 19),
        ("Sarkari seva ki declaration", 20), ("Anya daavon ke certificates", 20), ("Anya daavon ke praman", 20)]
    [portal] = s["portal"]
    assert (portal["detail"], portal["citation"]["page"]) == ("https://upsconline.nic.in", 12)

@pytest.mark.asyncio
async def test_upsc_advt_12_personalised(api, login, db_session):
    vacancy = await _ingest_advt12(db_session)
    asha = await login("asha@example.com")
    asha_v = await _version(api, asha, {"full_name": "Asha Rao", "date_of_birth": "1995-03-10", "gender": "Male", "category": "OBC",
                                        "is_pwbd": False, "nationality": "Indian", "is_ex_serviceman": False,
                                        "is_central_govt_employee": False}, **LAW_GRAD)
    user_id = uuid.UUID((await api.get("/auth/me", headers=asha)).json()["id"])
    page = await apply_ready.vacancy_page(db_session, vacancy.id, user_id, uuid.UUID(asha_v), now=BEFORE_CLOSING)
    assert page["status"] == "OPEN"
    s = _sections(page)
    elig = {it["label"]: it["status"] for it in s["eligibility"]}
    # LL.B.: education met; no experience in the profile against "Two years' experience": not met
    assert (elig["Shaikshik yogyata (education)"], elig["Anubhav (experience)"]) == ("OK", "FAILS")
    assert (elig["Aayu seema (age limit)"], elig["Nagrikta (citizenship)"]) == ("OK", "OK")
    certs = {it["label"]: it for it in s["certificates"]}
    # OBC claimed, crucial date stated (16.10.2026), none recorded, still before the crucial date: obtain one
    assert certs["OBC (Non-Creamy Layer) certificate"]["status"] == "NEEDS_DOCUMENT"
    assert certs["SC certificate"]["status"] is None and "aap par lagu nahi" in certs["SC certificate"]["detail"]
    docs = {it["label"]: it for it in s["documents"]}
    assert docs["Jaati / category certificate"]["status"] == "NEEDS_DOCUMENT"
    assert docs["OBC non-creamy layer declaration"]["status"] == "NEEDS_DOCUMENT"
    assert docs["Divyangta (PwBD) certificate"]["status"] is None and "zaroori nahi" in docs["Divyangta (PwBD) certificate"]["detail"]
    assert docs["Aayu chhoot ka certificate"]["status"] == "CANNOT_DECIDE"
    fee = {it["label"]: it for it in s["fee"]}
    assert fee["Application fee"]["status"] == "OK" and "aapko fee ₹25 deni hai" in fee["Application fee"]["detail"]
    assert all(it["status"] is None for k in ("dates", "uploads", "photo_id", "portal") for it in s[k])

    # SC woman with no SC certificate recorded: the advertisement states no date for it, so certificate_check takes the
    # closing date (Sakshi Arha para 27) and, before it, she must obtain one; the page shows that status as it is. No fee
    priya = await login("priya@example.com")
    priya_v = await _version(api, priya, {"full_name": "Priya", "date_of_birth": "1996-01-02", "gender": "female", "category": "SC",
                                          "is_pwbd": False, "nationality": "Indian"}, **LAW_GRAD)
    priya_id = uuid.UUID((await api.get("/auth/me", headers=priya)).json()["id"])
    s = _sections(await apply_ready.vacancy_page(db_session, vacancy.id, priya_id, uuid.UUID(priya_v), now=BEFORE_CLOSING))
    sc = next(it for it in s["certificates"] if it["label"] == "SC certificate")
    assert sc["status"] == "NEEDS_DOCUMENT", sc
    assert "no SC certificate is recorded" in sc["detail"] and "obtain one valid on 2026-10-16" in sc["detail"]
    assert "default-cut-off-last-date-of-application" in sc["detail"]
    # Ex-serviceman status not stated in her profile: certificate_check cannot decide, and the page gives its reason
    esm = next(it for it in s["certificates"] if it["label"] == "Ex-Serviceman certificate")
    assert esm["status"] == "CANNOT_DECIDE" and "Aapke liye: the profile does not say whether you claim" in esm["detail"], esm
    assert next(it for it in s["certificates"] if it["label"].startswith("OBC"))["status"] is None
    fee = next(it for it in s["fee"] if it["label"] == "Application fee")
    assert fee["status"] == "OK" and "aapko fee nahi deni (exempt as FEMALE/SC)" in fee["detail"]

    # No date of birth in the profile: the age check names no row ("date of birth missing in profile"), so the age-limit
    # item says the profile lacks it rather than showing no status
    meera = await login("meera@example.com")
    meera_v = await _version(api, meera, {"full_name": "Meera", "gender": "female", "category": "OBC", "is_pwbd": False,
                                          "nationality": "Indian"}, **LAW_GRAD)
    meera_id = uuid.UUID((await api.get("/auth/me", headers=meera)).json()["id"])
    s = _sections(await apply_ready.vacancy_page(db_session, vacancy.id, meera_id, uuid.UUID(meera_v), now=BEFORE_CLOSING))
    elig = {it["label"]: it["status"] for it in s["eligibility"]}
    assert (elig["Aayu seema (age limit)"], elig["Shaikshik yogyata (education)"]) == ("UNKNOWN", "OK")

    # The same through the API: own version only, and only with a token (an error response rolls the shared session back)
    vacancy_id = vacancy.id
    url = f"/apply-ready/vacancies/{vacancy_id}?profile_version_id={asha_v}"
    r = await api.get(url, headers=asha)
    assert r.status_code == 200, r.text
    assert {it["label"]: it["status"] for it in _sections(r.json())["eligibility"]}["Anubhav (experience)"] == "FAILS"
    assert (await api.get(url)).status_code == 401
    assert (await api.get(url, headers={"Authorization": "Bearer not-a-token"})).status_code == 401
    assert (await api.get(url, headers=priya)).status_code == 404
    assert (await api.get(f"/apply-ready/vacancies/{vacancy_id}", headers={"Authorization": "Bearer not-a-token"})).status_code == 401
    assert (await api.get("/apply-ready/vacancies/00000000-0000-0000-0000-000000000000")).status_code == 404

@pytest.mark.asyncio
async def test_printable_page(api, login, db_session):
    vacancy = await _ingest_advt12(db_session)
    r = await api.get(f"/apply-ready/vacancies/{vacancy.id}.html")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    text = r.text
    assert text.startswith("<!doctype html>") and "<script" not in text and "<link" not in text
    assert "<title>Law Officer — apply checklist</title>" in text and "Aavedan ki antim tithi" in text
    assert f'href="{ADVT12_URL}#page=19"' in text and f'href="{ADVT12_URL}#page=13"' in text
    # Quotes in the notice's English, escaped
    assert "No fee for SC/ST/PwBD/Women candidates of any community" in text and "he/she does not belong to the creamy layer" in text
    assert "Official notification hi maanya hai" in text and "the official notification prevails" in text
    assert "Document chahiye" not in text  # no personal status without a profile

    h = await login("asha@example.com")
    v = await _version(api, h, {"full_name": "Asha Rao", "date_of_birth": "1995-03-10", "category": "OBC", "nationality": "Indian"}, **LAW_GRAD)
    r = await api.get(f"/apply-ready/vacancies/{vacancy.id}.html?profile_version_id={v}", headers=h)
    assert r.status_code == 200 and "Yogya nahi" in r.text  # experience
    assert (await api.get(f"/apply-ready/vacancies/{vacancy.id}.html?profile_version_id={v}")).status_code == 401

@pytest.mark.asyncio
async def test_ssc_chsl_2026_page(api, db_session):
    exam = await _ingest_ssc(db_session, "Notice_of_adv_chsle_2026.pdf")
    url = SSC_URLS["Notice_of_adv_chsle_2026.pdf"]["url"]
    r = await api.get(f"/apply-ready/exams/{exam.id}")
    assert r.status_code == 200, r.text
    page = r.json()
    assert (page["kind"], page["title"], page["organization"], page["advertisement_number"], page["document_url"]) == (
        "exam", "Combined Higher Secondary (10+2) Level Examination, 2026", "Staff Selection Commission", "HQ-C1102/5/2026-C-1", url)
    # The notice names no application website: said so, nothing invented
    assert page["warnings"] == ["the application website was not extracted from the notice; see the official notification"]
    s = _sections(page)
    _quotes_on_their_pages(page, FIXTURES / "ssc" / "Notice_of_adv_chsle_2026.pdf", url)
    assert s["portal"] == [] and s["photo_id"] == []

    assert [(d["label"], d["detail"], d["citation"]["page"]) for d in s["dates"]] == [
        ("Aayu is tithi par gini jayegi (crucial date)", "01 Aug 2026", 4), ("Online aavedan shuru", "07 Sep 2026", 1),
        ("Shaikshik yogyata is tithi tak honi chahiye", "07 Oct 2026", 10), ("Aavedan ki antim tithi", "07 Oct 2026, 23:00 IST", 1),
        ("Fee bharne ki antim tithi", "08 Oct 2026, 23:00 IST", 1), ("Correction window khulegi", "14 Oct 2026", 1),
        ("Correction window band", "16 Oct 2026, 23:00 IST", 1)]
    ages = [it["detail"] for it in s["eligibility"] if it["label"] == "Aayu seema (age limit)"]
    assert ages == ["UR: 18-27 saal; EWS: 18-27 saal", "OBC: 18-30 saal", "SC: 18-32 saal; ST: 18-32 saal"]
    certs = {it["label"]: it for it in s["certificates"]}
    assert certs["EWS Income & Asset certificate"]["detail"] == \
        "Kis par lagu: EWS ummeedwar; Financial year (aay): 2025-26; Format: Annexure-XIV"
    assert certs["OBC (Non-Creamy Layer) certificate"]["citation"]["page"] == 7
    fee = {it["label"]: it for it in s["fee"]}
    assert (fee["Application fee"]["detail"], fee["Application fee"]["citation"]["quote"]) == ("₹100", "Fee payable: Rs 100/- (Rs one hundred only).")
    sig = next(it for it in s["uploads"] if it["label"] == "Signature")
    assert (sig["detail"], sig["citation"]["page"]) == ("Format: JPEG/JPG; Size: 10-20 KB; Naap: 6.0 cm x 2.0 cm", 12)
    assert [d["citation"]["quote"].split()[0] for d in s["documents"]] == [f"16.5.{i}" for i in range(1, 11)]

    assert (await apply_ready.exam_page(db_session, exam.id, now=BEFORE_CLOSING))["status"] == "OPEN"
    assert (await apply_ready.exam_page(db_session, exam.id, now=datetime(2026, 10, 7, 23, 1, tzinfo=IST)))["status"] == "CLOSED"
    # Applications open on 07.09.2026: before that the page does not say "open"
    before = await apply_ready.exam_page(db_session, exam.id, now=datetime(2026, 9, 1, 12, 0, tzinfo=IST))
    assert before["status"] == "NOT_YET_OPEN" and datetime.fromisoformat(before["application_start"]) == datetime(2026, 9, 7, tzinfo=IST)
    printable = apply_ready.render_html(before)
    assert "Aavedan abhi shuru nahi hua" in printable and "Aavedan shuru: <strong>07 Sep 2026</strong>" in printable
    assert "Aavedan khula hai" not in printable
    assert (await apply_ready.exam_page(db_session, exam.id, now=datetime(2026, 9, 7, 0, 0, tzinfo=IST)))["status"] == "OPEN"

@pytest.mark.asyncio
async def test_ssc_je_2026_page(api, login, db_session):
    exam = await _ingest_ssc(db_session, "Notice_of_adv_je_2026.pdf")
    # The JE notice has no OBC creamy-layer paragraph; the parser's warning is stored with the document and shown
    stored = (await db_session.execute(select(ApplicationRequirementParse).where(
        ApplicationRequirementParse.raw_document_id == exam.raw_document_id))).scalar_one()
    assert json.loads(stored.warnings) == ["certificate clause not found: obc"]
    page = (await api.get(f"/apply-ready/exams/{exam.id}")).json()
    assert page["warnings"][0] == "certificate clause not found: obc"
    assert page["status"] == ("OPEN" if datetime.now(timezone.utc) <= datetime(2026, 9, 22, 23, 0, tzinfo=IST) else "CLOSED")
    s = _sections(page)
    _quotes_on_their_pages(page, FIXTURES / "ssc" / "Notice_of_adv_je_2026.pdf", SSC_URLS["Notice_of_adv_je_2026.pdf"]["url"])
    # Roman-numbered list (i)..(x), in the notice's order (not "ix" before "v")
    assert [d["citation"]["quote"].split()[0] for d in s["documents"]] == \
        ["(i)", "(ii)", "(iii)", "(iv)", "(v)", "(vi)", "(vii)", "(viii)", "(ix)", "(x)"]
    # Post-wise notice: each post's own age and qualification rows, labelled with the post
    bro = [it for it in s["eligibility"] if it["label"].endswith("JE(C) (Border Roads Organization)")]
    assert [it["label"].split(" — ")[0] for it in bro] == ["Aayu seema (age limit)"] * 3 + ["Shaikshik yogyata (education)"]
    assert bro[-1]["detail"].startswith("Degree in Civil Engineering from a recognized University/Institute") and bro[-1]["citation"]["page"] == 2

    h = await login("vikram@example.com")
    v = await _version(api, h, {"full_name": "Vikram", "date_of_birth": "2000-03-10", "gender": "Male", "category": "OBC",
                                "is_pwbd": False, "nationality": "Indian", "is_ex_serviceman": False, "is_central_govt_employee": False},
                       education=[{"degree": "B.E. (Civil Engineering)", "institution": "MBM Engineering College",
                                   "specialization": "Civil Engineering", "education_level": "BACHELORS", "end_date": "2022-06-30"}])
    user_id = uuid.UUID((await api.get("/auth/me", headers=h)).json()["id"])
    s = _sections(await apply_ready.exam_page(db_session, exam.id, user_id, uuid.UUID(v), now=datetime(2026, 9, 1, tzinfo=IST)))
    bro = {it["detail"]: it["status"] for it in s["eligibility"] if it["label"].endswith("JE(C) (Border Roads Organization)")}
    bro_education = next(it["status"] for it in s["eligibility"] if it["label"] == "Shaikshik yogyata (education) — JE(C) (Border Roads Organization)")
    # 26 on the crucial date 01.08.2026, OBC: the OBC row decides (18-33); the other categories' rows are not his
    assert (bro["OBC: 18-33 saal"], bro["UR: 18-30 saal; EWS: 18-30 saal"]) == ("OK", None)
    assert next(it for it in s["eligibility"] if it["label"] == "Nagrikta (citizenship)")["status"] == "OK"
    obc = next(it for it in s["certificates"] if it["label"].startswith("OBC"))
    assert obc["status"] == "NEEDS_DOCUMENT" and obc["citation"]["page"] == 11

    # No date of birth: every post's own age rows (judged post by post) say the profile lacks it; education is judged as for
    # the same degree with a date of birth
    h = await login("anil@example.com")
    v = await _version(api, h, {"full_name": "Anil", "gender": "Male", "category": "OBC", "is_pwbd": False, "nationality": "Indian",
                                "is_ex_serviceman": False, "is_central_govt_employee": False},
                       education=[{"degree": "B.E. (Civil Engineering)", "institution": "MBM Engineering College",
                                   "specialization": "Civil Engineering", "education_level": "BACHELORS", "end_date": "2022-06-30"}])
    user_id = uuid.UUID((await api.get("/auth/me", headers=h)).json()["id"])
    s = _sections(await apply_ready.exam_page(db_session, exam.id, user_id, uuid.UUID(v), now=datetime(2026, 9, 1, tzinfo=IST)))
    ages = [it["status"] for it in s["eligibility"] if it["label"].startswith("Aayu seema (age limit) — ")]
    assert ages and set(ages) == {"UNKNOWN"}, ages
    bro = {it["label"].split(" — ")[0]: it["status"] for it in s["eligibility"] if it["label"].endswith("JE(C) (Border Roads Organization)")}
    assert bro["Shaikshik yogyata (education)"] == bro_education and bro_education not in (None, "UNKNOWN")

@pytest.mark.asyncio
async def test_closing_date_without_a_key_date_row(api, db_session):
    """Advt 11/2026 states two closing dates (Ladakh posts later): no single KEY_DATE row, so each vacancy's own
    closing date and the advertisement words it was read from are shown."""
    source = await get_or_create_source(db_session, upsc.UPSC_SOURCE)
    name = "AdvtNo-11-2026-Engl-100926.pdf"
    url = f"https://www.upsc.gov.in/sites/default/files/{name}"
    await ingest_pdf_bytes(db_session, source, url, (FIXTURES / "upsc" / name).read_bytes())
    await db_session.commit()
    vacancy = (await db_session.execute(select(Vacancy).where(Vacancy.advertisement_number == "11/2026")
                                        .order_by(Vacancy.external_ref).limit(1))).scalar_one()
    page = (await api.get(f"/apply-ready/vacancies/{vacancy.id}")).json()
    end = next(it for it in _sections(page)["dates"] if it["label"] == "Aavedan ki antim tithi")
    assert datetime.fromisoformat(page["deadline"]) == vacancy.application_deadline
    assert end["detail"] == vacancy.application_deadline.astimezone(IST).strftime("%d %b %Y, %H:%M IST")
    assert end["citation"]["document_url"] == url and end["citation"]["page"] and end["citation"]["quote"]
    pages = extract_pages((FIXTURES / "upsc" / name).read_bytes(), ocr=False)
    assert find_quote_on_page(" ".join(end["citation"]["quote"].split()[:10]), pages, end["citation"]["page"])
