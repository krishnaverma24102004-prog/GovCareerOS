"""
The real SSC notice "Sub-Inspector in Delhi Police and Central Armed Police Forces Examination, 2026" (69 pages,
text layer), fetched live from ssc.gov.in on 2026-10-06 (URL, sha256 in tests/fixtures/ssc/capf_2026/meta.json).

Its layout differs from CGL/CHSL/JE: the title wraps over two lines, times read "(23:00 hrs)", the posts are stated in
running text ("1.1 Sub-Inspector (GD) in CAPFs: The post carries pay scale of Level-6 ..."), age limits and
qualifications are post-wise sentences, and relaxation rows 12-13 and 17-19 apply to the Delhi Police post only.
Expected values are read off the PDF by eye, e.g. page 4, para 5.2: "Age limit for SI(FIRE) in CISF is 18-30 years".
"""
import hashlib
import json
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from govcareeros.backend.documents.pdf_text import extract_pages
from govcareeros.backend.models import Exam, ExamPost, ExamRequirement
from govcareeros.backend.parsing.ssc_application import parse_ssc_application_info
from govcareeros.backend.parsing.ssc_exam_notice import exam_code_for
from govcareeros.backend.parsing.ssc_tables import parse_exam_pdf
from govcareeros.backend.parsing.upsc_advertisement import IST
from govcareeros.backend.services.document_ingestion import get_or_create_source
from govcareeros.backend.services.exam_ingestion import ingest_exam_pdf
from govcareeros.backend.sources import ssc

FIXTURE = Path(__file__).parents[1] / "fixtures" / "ssc" / "capf_2026"
META = json.loads((FIXTURE / "meta.json").read_text(encoding="utf-8"))
BODY = (FIXTURE / META["file"]).read_bytes()
GD, DP, FIRE = "sub inspector gd in capfs", "sub inspector executive male female in delhi police", "sub inspector fire male in cisf"

@pytest.fixture(scope="module")
def pages():
    return extract_pages(BODY, ocr=False)

@pytest.fixture(scope="module")
def parsed(pages):
    return parse_exam_pdf(BODY, pages)

def _age(post, category):
    return next(r for r in post.requirements if r.req_type == "AGE_MAX" and r.value["category"] == category)

def test_fixture_is_the_captured_document():
    assert hashlib.sha256(BODY).hexdigest() == META["sha256"] and len(BODY) == META["bytes"]

def test_front_page_dates_and_title(parsed):
    assert parsed.warnings == []
    assert parsed.value("name") == "Sub-Inspector in Delhi Police and Central Armed Police Forces Examination, 2026"
    assert parsed.fields["name"].provenance.page == 1 and exam_code_for(parsed.value("name")) == "CPO"
    assert parsed.value("exam_year") == 2026
    f = parsed.fields["file_number"]
    assert (f.value, f.provenance.page, f.provenance.excerpt) == ("E/37/2026-C-2", 1, "E/37/2026-C-2 SECTION:")
    assert parsed.value("application_start") == datetime(2026, 9, 10, tzinfo=IST)
    end = parsed.fields["application_end"]
    assert end.value == datetime(2026, 9, 30, 23, 0, tzinfo=IST)
    assert end.provenance.excerpt == "Last date and time for receipt of online applications 30.09.2026 (23:00 hrs)"
    fee_end = parsed.fields["fee_payment_end"]
    assert fee_end.value == datetime(2026, 10, 1, 23, 0, tzinfo=IST)
    assert fee_end.provenance.excerpt == "Last date and time for online fee payment 01.10.2026 (23:00 hrs)"
    assert parsed.value("correction_start") == datetime(2026, 10, 8, tzinfo=IST)
    assert parsed.value("correction_end") == datetime(2026, 10, 10, 23, 0, tzinfo=IST)
    assert parsed.value("exam_dates") == [{"stage": "Computer Based Examination", "schedule": "To be notified later"}]
    assert parsed.fields["exam_dates"].provenance.excerpt == "Schedule of Computer Based Examination To be notified later"
    crucial = parsed.fields["age_crucial_date"]
    assert crucial.value == datetime(2026, 8, 1, tzinfo=IST) and crucial.provenance.page == 4
    # "Essential Educational Qualification (as on closing date for receipt of online applications)": the parsed closing date
    cutoff = parsed.fields["education_cutoff_date"]
    assert cutoff.value == datetime(2026, 9, 30, tzinfo=IST) and cutoff.provenance.page == 8
    assert cutoff.provenance.excerpt == ("Essential Educational Qualification (as on closing date for receipt of online applications): | "
                                         "p.1: Last date and time for receipt of online applications 30.09.2026 (23:00 hrs)")
    fee = parsed.fields["fee_amount"]
    assert (fee.value, fee.provenance.page, fee.provenance.excerpt) == (100, 11, "Fee payable: Rs.100/- (Rupees One Hundred only).")

def test_posts_with_pay_level_and_classification(parsed):
    posts = {p.post_key: p for p in parsed.posts}
    assert list(posts) == [GD, DP, FIRE]
    gd, dp, fire = posts[GD], posts[DP], posts[FIRE]
    assert (gd.post_name, gd.pay_level, gd.pay_scale, gd.classification) == (
        "Sub-Inspector (GD) in CAPFs", "Level-6", "Level-6 (Rs.35,400-Rs.1,12,400/-)", "Group ‘B’ (Non-Gazetted), Non-Ministerial")
    assert gd.provenance.page == 2 and gd.provenance.excerpt == (
        "1.1 Sub-Inspector (GD) in CAPFs: The post carries pay scale of Level-6 (Rs.35,400- Rs.1,12,400/-) and is classified as "
        "Group ‘B’ (Non-Gazetted), Non-Ministerial.")
    assert (dp.post_name, dp.pay_level, dp.classification) == ("Sub-Inspector (Executive) - (Male/Female) in Delhi Police", "Level-6",
                                                              "Group ‘C’ by Delhi Police")
    assert (fire.post_name, fire.pay_level, fire.pay_scale) == ("Sub-Inspector (FIRE)- (Male) in CISF", "Level-6", "Level-6 (Rs. 35,400-Rs.1,12,400/-)")

def test_post_wise_age_limits_with_birth_windows_and_category_relaxation(parsed):
    posts = {p.post_key: p for p in parsed.posts}
    assert [(posts[k].age_min, posts[k].age_max) for k in (GD, DP, FIRE)] == [(20, 25), (20, 25), (18, 30)]
    ur = _age(posts[GD], "UR")
    assert ur.value == {"category": "UR", "min_age": 20, "max_age": 25, "born_not_earlier_than": "2001-08-02", "born_not_later_than": "2006-08-01"}
    assert ur.provenance.page == 4 and ur.provenance.excerpt == (
        "Age limit for all posts except SI(FIRE) in CISF is 20-25 years; i.e. Candidate must have been born not earlier than "
        "02.08.2001 and not later than 01.08.2006")
    # SC 25 + 5: the window moves back five years; the excerpt quotes the limit sentence and the table row
    sc = _age(posts[DP], "SC")
    assert sc.value["max_age"] == 30 and sc.value["base_max_age"] == 25 and sc.value["relaxation_years"] == 5
    assert sc.value["born_not_earlier_than"] == "1996-08-02" and sc.provenance.excerpt.endswith(" | p.4: SC/ST 5 years")
    fire_obc = _age(posts[FIRE], "OBC")
    assert fire_obc.value == {"category": "OBC", "min_age": 18, "max_age": 33, "base_max_age": 30, "relaxation_years": 3,
                              "born_not_earlier_than": "1993-08-02", "born_not_later_than": "2008-08-01"}
    assert fire_obc.provenance.excerpt == ("Age limit for SI(FIRE) in CISF is 18-30 years; i.e. Candidate must have been born not earlier "
                                           "than 02.08.1996 and not later than 01.08.2008 | p.4: OBC 3 years")
    assert not any(r.req_type == "AGE_MAX" for r in parsed.requirements)

def test_relaxation_rows_including_delhi_police_departmental_candidates(parsed):
    rules = [r for r in parsed.requirements if r.req_type == "AGE_RELAXATION_RULE"]
    esm = rules[0]
    assert esm.value == {"basis": "EX_SERVICEMEN_SERVICE_DEDUCTED", "years": 3} and esm.provenance.page == 4
    assert esm.provenance.excerpt == ("Ex-Servicemen (ESM) 3 years after deduction of the military service rendered from the actual age "
                                      "as on the closing date for receipt of online applications.")
    widows = [r for r in rules if r.value["basis"] == "WIDOW_DIVORCED_SEPARATED"]
    assert [(r.value["max_age"], r.value["categories"], r.value["post_keys"]) for r in widows] == [
        (35, ["UR", "EWS", "OBC"], [DP]), (40, ["SC", "ST"], [DP])]
    # Row 13's category cell is cut by the page break ("not re-" | p.5 "married. (SC/ST)")
    assert widows[1].provenance.excerpt == (
        "Widows, divorced women and women judicially separated from their husbands and who are not re- married. (SC/ST) Up to 40 years "
        "of age [For the post of SI in Delhi Police (Category Codes-12 and 13)]")
    dept = [r for r in rules if r.value["basis"] == "DEPARTMENTAL_CANDIDATE"]
    assert [(r.value["max_age"], r.value["categories"], r.value["min_service_years"], r.provenance.page) for r in dept] == [
        (30, ["UR", "EWS"], 3, 5), (33, ["OBC"], 3, 5), (35, ["SC", "ST"], 3, 5)]
    assert all(r.value["post_keys"] == [DP] for r in dept)
    assert dept[1].provenance.excerpt == (
        "Departmental candidates (OBC) who have rendered not less than 3 years of regular and continuous service as on closing date for "
        "receipt of online applications. Up to 33 years of age [For Departmental candidates of Delhi Police against the vacancies of "
        "Delhi Police only (Category Codes-17, 18 and 19)]")
    assert len(rules) == 6

def test_post_wise_qualifications(parsed):
    posts = {p.post_key: p for p in parsed.posts}
    for key in (GD, DP):
        edu = next(r for r in posts[key].requirements if r.req_type == "EDUCATION")
        assert edu.value == "Bachelor’s degree from a recognized university or equivalent." and edu.provenance.page == 8
        assert edu.provenance.excerpt == ("Educational Qualification for Sub Inspector in Delhi Police and Sub Inspector (GD) in CAPFs is "
                                          "Bachelor’s degree from a recognized university or equivalent.")
    fire = next(r for r in posts[FIRE].requirements if r.req_type == "EDUCATION")
    assert fire.value.startswith("Bachelor’s degree in science with physics, Chemistry and Mathematics of a recognized University or "
                                 "equivalent; OR Matriculation or equivalent and three years diploma in Mechanical, Civil, Electrical,")
    assert fire.provenance.excerpt.startswith("Educational Qualification for Sub Inspector (Fire) is Bachelor’s degree in science")
    assert not any(r.req_type == "EDUCATION" for r in parsed.requirements)

def test_application_requirements(pages):
    info = parse_ssc_application_info(pages)
    # The notice has no PwBD clause: the posts are not identified suitable for PwBD
    assert info.warnings == ["certificate clause not found: pwbd"]
    items = {(i.req_type, i.code): i for i in info.items}
    assert items[("KEY_DATE", "APPLICATION_END")].value == {"at": "2026-09-30T23:00:00+05:30"}
    assert items[("KEY_DATE", "FEE_PAYMENT_END")].value == {"at": "2026-10-01T23:00:00+05:30"}
    obc = items[("CERTIFICATE", "OBC_NCL")]
    assert (obc.value["crucial_date"], obc.value["format_note"], obc.provenance.page) == ("2026-09-30", "Annexure-VI", 7)
    assert obc.provenance.excerpt.startswith("A person seeking appointment on the basis of reservation to OBCs must ensure that he/she "
                                             "possesses the caste/community certificate and does not fall in creamy layer on the crucial date")
    ews = items[("CERTIFICATE", "EWS")]
    assert (ews.value["financial_year"], ews.value["format_note"]) == ("2025-26", "Annexure-VII")
    sig = items[("UPLOAD_RULE", "SIGNATURE")]
    assert (sig.value["formats"], sig.value["min_kb"], sig.value["max_kb"], sig.provenance.page) == (["JPEG"], 10, 20, 11)
    assert items[("UPLOAD_RULE", "PHOTOGRAPH")].value["live_capture"] is True
    assert items[("FEE", "APPLICATION_FEE")].value == {"amount": 100, "currency": "INR"}
    assert items[("FEE_EXEMPTION", "FEE_EXEMPT")].value["exempt"] == ["ESM", "FEMALE", "SC", "ST"]
    docs = [(i.code, i.value["item"]) for i in info.items if i.req_type == "DOCUMENT"]
    assert docs == [("MATRICULATION_CERTIFICATE", "i"), ("EDUCATION_CERTIFICATE", "ii"), ("EQUIVALENCE_ORDER", "iii"),
                    ("CASTE_CERTIFICATE", "iv"), ("DRIVING_LICENCE", "v"), ("ESM_DOCUMENTS", "vi"), ("AGE_RELAXATION_CERTIFICATE", "vii"),
                    ("DEPARTMENTAL_CANDIDATE_CERTIFICATE", "viii"), ("NO_OBJECTION_CERTIFICATE", "ix"), ("NAME_CHANGE_DOCUMENTS", "x"),
                    ("DOCUMENT_XI", "xi")]
    dl = items[("DOCUMENT", "DRIVING_LICENCE")]
    assert dl.provenance.page == 24 and dl.provenance.section == "Document Verification: 15.3 (v)"
    assert dl.value["text"] == ("Driving License for Motor Cycle and Car (issued before the date of PST/PET) for the candidates who have "
                                "given preference for Delhi Police (applicable for male candidates only).")

def _profile(dob, category="UR", gender="Male"):
    return {
        "personal_info": {"full_name": "Test Candidate", "date_of_birth": dob, "gender": gender, "category": category,
                          "is_pwbd": False, "is_ex_serviceman": False, "is_central_govt_employee": False, "nationality": "Indian"},
        "education": [{"degree": "Bachelor of Science (B.Sc)", "specialization": "Physics, Chemistry, Mathematics",
                       "institution": "Test University", "education_level": "BACHELORS", "end_date": "2020-05-31"}],
    }

@pytest.mark.asyncio
async def test_ingested_exam_and_post_scoped_relaxations(api, login, db_session):
    source = await get_or_create_source(db_session, ssc.SSC_SOURCE)
    result = await ingest_exam_pdf(db_session, source, META["url"], BODY)
    await db_session.commit()
    assert result.created and result.warnings == [] and result.requirement_warnings == ["certificate clause not found: pwbd"]
    exam = await db_session.get(Exam, result.exam_id)
    assert (exam.exam_code, exam.external_ref, exam.application_end) == ("CPO", "E/37/2026-C-2", datetime(2026, 9, 30, 23, 0, tzinfo=IST))
    posts = {p.post_key: p for p in (await db_session.execute(select(ExamPost).where(ExamPost.exam_id == exam.id))).scalars()}
    assert set(posts) == {GD, DP, FIRE} and (posts[FIRE].age_min, posts[FIRE].age_max, posts[FIRE].source_page) == (18, 30, 2)
    rows = (await db_session.execute(select(ExamRequirement).where(ExamRequirement.post_id == posts[DP].id))).scalars().all()
    assert sorted(r.req_type for r in rows) == ["AGE_MAX"] * 5 + ["EDUCATION"]

    h = await login("capf-test@example.com")
    version = (await api.put("/profile", json=_profile("1999-03-10"), headers=h)).json()["version"]["id"]  # 27 on 01-08-2026

    async def age_check(post_key):
        r = await api.post(f"/exams/{exam.id}/eligibility", json={"profile_version_id": version, "post_id": str(posts[post_key].id)}, headers=h)
        assert r.status_code == 201, r.text
        return next(e for e in r.json()["evidence"] if e["check_name"] == "AGE")

    assert (await age_check(FIRE))["outcome"] == "MET"  # 18-30
    gd = await age_check(GD)  # 20-25; the Delhi Police-only rows do not reach the CAPF post
    assert gd["outcome"] == "NOT_MET" and "DEPARTMENTAL_CANDIDATE" not in gd["profile_evidence"]
    # Delhi Police: a departmental candidate may be up to 30 (UR); the profile does not record that service
    dp = await age_check(DP)
    assert dp["outcome"] == "UNKNOWN" and "DEPARTMENTAL_CANDIDATE" in dp["profile_evidence"]
