"""
Ex-servicemen (ESM) and Agniveer section, on real captured notices (rows parsed from the PDFs, never typed in):

  * SSC CHSL 2026 (Notice_of_adv_chsle_2026.pdf): relaxation table row "Ex-Servicemen (ESM) 3 years after deduction of
    the military service rendered from the actual age as on 01.08.2026"; para 5.4 (re-use bar, DoP&T OM 36034/1/2014),
    5.6 (release within one year from the closing date), 5.8 (definition), 6.4 (crucial date = closing date).
  * SSC CGL 2026 paras 5.4, 5.6, 5.7, 6.4; SSC JE 2026 paras 7.4, 7.6 (also excludes ESM status acquired "by way of
    dismissal or discharge on account of misconduct or inefficiency"), 7.7, 8.7.
  * UPSC Advt 12/2026 p.21 para 5(a): relaxed by five years with "not less than six months after attestation"; p.22 NOTE:
    ESM already in a regular Central Govt civil post keep the age relaxation but not the reservation.

None of these notices (nor any other captured fixture) mentions Agniveers: a former Agniveer gets CANNOT_DECIDE.
"""
import json
import re
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import List

import pymupdf
import pytest
from sqlalchemy import select

from govcareeros.backend.documents.pdf_text import extract_pages
from govcareeros.backend.eligibility import esm, exam_engine, rules
from govcareeros.backend.models import Exam, Vacancy
from govcareeros.backend.parsing.ssc_exam_notice import parse_exam_notice
from govcareeros.backend.parsing.upsc_advertisement import IST, parse_advertisement
from govcareeros.backend.services.document_ingestion import get_or_create_source, ingest_pdf_bytes
from govcareeros.backend.services.exam_ingestion import ingest_exam_pdf
from govcareeros.backend.sources import ssc, upsc

FIXTURES = Path(__file__).parents[1] / "fixtures"
CHSL, CGL, JE = "Notice_of_adv_chsle_2026.pdf", "Notice_of_adv_cgl_2026.pdf", "Notice_of_adv_je_2026.pdf"
UPSC_12 = "AdvtNo-12-2026-Engl-250926_0.pdf"
UPSC_NOTE = ("Ex Servicemen who have already secured regular employment under the Central Govt. in a Civil Post are permitted the "
             "benefit of age relaxation as admissible for Ex- Servicemen for securing another employment in any higher post or "
             "service under the Central Govt. However, such candidates will not be eligible for the benefit of reservation, if any "
             "for Ex-Servicemen in Central Govt. jobs.")

def _collapse(text: str) -> str:
    return " ".join(text.split())

@lru_cache(maxsize=None)
def _ssc(name: str):
    return parse_exam_notice(extract_pages((FIXTURES / "ssc" / name).read_bytes(), ocr=False))

@lru_cache(maxsize=None)
def _upsc12():
    return parse_advertisement(extract_pages((FIXTURES / "upsc" / UPSC_12).read_bytes(), ocr=False))

def _rows(parsed) -> List[rules.Requirement]:
    return [rules.Requirement(f"{r.req_type}-{i}", r.req_type, r.value, r.provenance.page, r.provenance.excerpt) for i, r in enumerate(parsed)]

def _ssc_rows(name: str) -> List[rules.Requirement]:
    return _rows(_ssc(name).requirements)

def _law_officer_rows() -> List[rules.Requirement]:
    parsed = _upsc12()
    vacancy = next(v for v in parsed.vacancies if v.external_ref == "26091203226")
    return _rows(vacancy.requirements + parsed.general_requirements)

def _day(parsed, name):
    return parsed.value(name).astimezone(IST).date()

def _person(**kw) -> rules.ProfileSnapshot:
    base = dict(profile_version_id="pv", date_of_birth=date(1986, 5, 10), category="UR", is_pwbd=False, nationality="Indian",
                is_ex_serviceman=True, is_central_govt_employee=False)
    return rules.ProfileSnapshot(**{**base, **kw})

def _section(profile, name=CHSL):
    parsed = _ssc(name)
    return {o.check: o for o in esm.check_esm(profile, _ssc_rows(name), _day(parsed, "application_end"),
                                              _day(parsed, "age_crucial_date") if parsed.value("age_crucial_date") else None)}

# ---------- the clauses as the notices state them ----------

@pytest.mark.parametrize("name,pages,dismissal_excluded", [
    (CGL, {"RESERVATION_REUSE_BAR": 8, "RELEASE_WITHIN_ONE_YEAR": 8, "DEFINITION": 8, "STATUS_CRUCIAL_DATE": 11}, False),
    (CHSL, {"RESERVATION_REUSE_BAR": 5, "RELEASE_WITHIN_ONE_YEAR": 5, "DEFINITION": 5, "STATUS_CRUCIAL_DATE": 7}, False),
    (JE, {"RESERVATION_REUSE_BAR": 8, "RELEASE_WITHIN_ONE_YEAR": 9, "DEFINITION": 9, "STATUS_CRUCIAL_DATE": 11}, True),
])
def test_ssc_notices_state_the_esm_clauses_verbatim(name, pages, dismissal_excluded):
    parsed = _ssc(name)
    rows = {r.value["kind"]: r for r in parsed.requirements if r.req_type == "ESM_RULE"}
    assert {k: r.provenance.page for k, r in rows.items()} == pages
    doc = pymupdf.open(FIXTURES / "ssc" / name)
    for r in rows.values():
        # The quote is the notice's own words (a clause may run onto the next page, past its "Page n of N" header)
        span = " ".join(re.sub(r"\A\s*Page\s+\d+\s+of\s+\d+\s*\n", "", doc[p - 1].get_text(), count=1)
                        for p in (r.provenance.page, r.provenance.page + 1))
        assert r.provenance.excerpt in _collapse(span)
    reuse = rows["RESERVATION_REUSE_BAR"].value
    assert reuse == {"kind": "RESERVATION_REUSE_BAR", "trigger": "RESERVATION_AVAILED", "civil_post_groups": ["C", "D"],
                     "undertaking_exception": True, "bars": ["RESERVATION", "FEE_CONCESSION"], "om": "36034/1/2014-Estt (Res) dated 14.08.2014"}
    assert "OM No" in rows["RESERVATION_REUSE_BAR"].provenance.excerpt and "36034/1/2014-Estt (Res)" in rows["RESERVATION_REUSE_BAR"].provenance.excerpt
    assert rows["RELEASE_WITHIN_ONE_YEAR"].value == {"kind": "RELEASE_WITHIN_ONE_YEAR", "months_after_closing": 12,
                                                     "excludes_dismissal_or_misconduct": dismissal_excluded}
    assert rows["DEFINITION"].value["release_types"] == ["RETIRED_WITH_PENSION", "MEDICAL_DISABILITY_PENSION", "REDUCTION_IN_ESTABLISHMENT",
                                                         "COMPLETED_ENGAGEMENT_GRATUITY", "RECRUIT_BOARDED_OUT_MEDICAL"]
    assert not [w for w in parsed.warnings if "ex-servicemen" in w]

def test_upsc_advertisement_note_bars_reservation_but_keeps_age_relaxation():
    rows = [r for r in _upsc12().general_requirements if r.req_type == "ESM_RULE"]
    assert [(r.value, r.provenance.page) for r in rows] == [({
        "kind": "RESERVATION_REUSE_BAR", "trigger": "CIVIL_POST_SECURED", "civil_post_groups": None, "undertaking_exception": False,
        "bars": ["RESERVATION"], "age_relaxation_kept": True}, 22)]
    assert rows[0].provenance.excerpt == UPSC_NOTE
    assert UPSC_NOTE in _collapse(pymupdf.open(FIXTURES / "upsc" / UPSC_12)[21].get_text())

def test_no_captured_notice_mentions_agniveers():
    for path in (FIXTURES / "ssc" / CHSL, FIXTURES / "ssc" / CGL, FIXTURES / "ssc" / JE, FIXTURES / "upsc" / UPSC_12):
        assert not any(re.search(r"agniveer|agnipath", p.get_text(), re.I) for p in pymupdf.open(path))

# ---------- status, release and crucial date ----------

def test_release_route_is_judged_against_the_definition():
    retired = _section(_person(esm_release_type="RETIRED_WITH_PENSION", esm_release_date=date(2024, 1, 31), esm_service_branch="ARMY"))
    assert retired["ESM_STATUS"].outcome == "OK" and retired["ESM_STATUS"].source_page == 5
    assert retired["ESM_STATUS"].requirement.startswith("Explanation: An „ex-serviceman‟ means a person:")
    assert "release/discharge certificate" in retired["ESM_STATUS"].note
    assert (retired["ESM_RELEASE"].outcome, retired["ESM_RELEASE"].source_page) == ("OK", 7)
    assert "closing date 2026-10-07" in retired["ESM_RELEASE"].rule

    assert _section(_person(esm_release_type=None))["ESM_STATUS"].outcome == "CANNOT_DECIDE"
    assert _section(_person(esm_release_type="OWN_REQUEST_WITHOUT_PENSION", esm_release_date=date(2020, 1, 1)))["ESM_STATUS"].outcome == \
        "NEEDS_HUMAN_REVIEW"
    # Dismissal: only JE 2026 excludes it in so many words; CGL's definition leaves other routes open to a reviewer
    dismissed = _person(esm_release_type="DISMISSED", esm_release_date=date(2022, 3, 1))
    je = _section(dismissed, JE)["ESM_STATUS"]
    assert (je.outcome, je.source_page) == ("FAILS", 9) and "dismissal" in je.requirement
    assert _section(dismissed, CGL)["ESM_STATUS"].outcome == "NEEDS_HUMAN_REVIEW"

def test_serving_personnel_within_one_year_of_the_closing_date():
    closing = date(2026, 10, 7)
    soon = _section(_person(esm_release_type="STILL_SERVING", esm_release_date=closing + timedelta(days=300)))
    assert soon["ESM_STATUS"].outcome == "NOT_APPLICABLE"
    assert (soon["ESM_RELEASE"].outcome, soon["ESM_RELEASE"].source_page) == ("OK", 5)
    assert "by 2027-10-07" in soon["ESM_RELEASE"].rule and "documentary evidence" in soon["ESM_RELEASE"].note
    assert _section(_person(esm_release_type="STILL_SERVING", esm_release_date=date(2027, 10, 7)))["ESM_RELEASE"].outcome == "OK"
    late = _section(_person(esm_release_type="STILL_SERVING", esm_release_date=date(2027, 10, 8)))["ESM_RELEASE"]
    assert late.outcome == "FAILS" and "last allowed 2027-10-07" in late.rule
    assert _section(_person(esm_release_type="STILL_SERVING"))["ESM_RELEASE"].outcome == "CANNOT_DECIDE"

def test_upsc_advertisement_states_no_release_window_or_definition():
    parsed = _upsc12()
    closing = parsed.fields["application_deadline"].value.astimezone(IST).date()
    serving = {o.check: o for o in esm.check_esm(_person(esm_release_type="STILL_SERVING", esm_release_date=closing + timedelta(days=30)),
                                                 _law_officer_rows(), closing, closing)}
    assert serving["ESM_RELEASE"].outcome == "CANNOT_DECIDE" and "does not say whether serving personnel" in serving["ESM_RELEASE"].rule
    retired = {o.check: o for o in esm.check_esm(_person(esm_release_type="RETIRED_WITH_PENSION", esm_release_date=date(2020, 1, 1)),
                                                 _law_officer_rows(), closing, closing)}
    assert retired["ESM_STATUS"].outcome == "CANNOT_DECIDE" and retired["ESM_RELEASE"].outcome == "OK"

# ---------- age: the notice's own formula ----------

def test_ssc_age_is_actual_age_minus_military_service_plus_three_years():
    parsed = _ssc(CHSL)
    reqs, on, closing = _ssc_rows(CHSL), _day(parsed, "age_crucial_date"), _day(parsed, "application_end")
    assert on == date(2026, 8, 1)
    # Age 40 on 01.08.2026; 17 years' service from the enrolment and release dates -> 23 <= UR limit 27 + 3
    long = _person(esm_release_type="RETIRED_WITH_PENSION", esm_enrolment_date=date(2004, 6, 1), esm_release_date=date(2021, 6, 30))
    assert rules.military_service_months(long, on) == 204
    age = _section(long)["ESM_AGE"]
    assert age.outcome == "OK" and "actual age 40 minus 17 years 0 months military service = 23.0" in age.rule
    assert "27 + 3 = 30" in age.rule and age.source_page == 4
    assert "3 years after deduction of the military service rendered from the actual age as on 01.08.2026" in age.requirement
    # The hard AGE check reads the same row the same way (and is what the verdict counts)
    hard = exam_engine.check_exam_age(long, reqs, on, closing)[0]
    assert hard.outcome == "CONDITIONAL" and "EX_SERVICEMEN_SERVICE_DEDUCTED" in hard.rule

    short = _person(esm_release_type="RETIRED_WITH_PENSION", armed_forces_service_months=60)
    assert _section(short)["ESM_AGE"].outcome == "FAILS"
    assert exam_engine.check_exam_age(short, reqs, on, closing)[0].outcome == "NOT_MET"
    assert _section(_person(esm_release_type="RETIRED_WITH_PENSION"))["ESM_AGE"].outcome == "CANNOT_DECIDE"
    assert _section(_person(esm_release_type="RETIRED_WITH_PENSION", date_of_birth=date(2003, 1, 1)))["ESM_AGE"].outcome == "NOT_APPLICABLE"

def test_missing_release_date_is_not_counted_up_to_the_crucial_date():
    """Only a candidate still serving has service running on to the crucial date; a retired candidate who leaves the
    release date empty has an unknown length of service, so neither the hard AGE check nor ESM_AGE may decide on it."""
    parsed = _ssc(CHSL)
    reqs, on, closing = _ssc_rows(CHSL), _day(parsed, "age_crucial_date"), _day(parsed, "application_end")
    retired = _person(esm_release_type="RETIRED_WITH_PENSION", esm_enrolment_date=date(2004, 6, 1))
    assert rules.military_service_months(retired, on) is None
    hard = exam_engine.check_exam_age(retired, reqs, on, closing)[0]
    assert hard.outcome == "UNKNOWN" and "EX_SERVICEMEN_SERVICE_DEDUCTED" in hard.profile_evidence
    section = _section(retired)
    assert section["ESM_AGE"].outcome == "CANNOT_DECIDE" and "length of military service not stated" in section["ESM_AGE"].rule
    assert section["ESM_RELEASE"].outcome == "CANNOT_DECIDE"
    for release_type in (None, "MEDICAL_DISABILITY_PENSION", "OTHER"):
        assert rules.military_service_months(_person(esm_release_type=release_type, esm_enrolment_date=date(2004, 6, 1)), on) is None
    # Still serving: 01.06.2004 to the crucial date 01.08.2026 = 22 years 2 months
    serving = _person(esm_release_type="STILL_SERVING", esm_enrolment_date=date(2004, 6, 1))
    assert rules.military_service_months(serving, on) == 266
    # A release after the crucial date counts only up to it
    assert rules.military_service_months(_person(esm_release_type="STILL_SERVING", esm_enrolment_date=date(2004, 6, 1),
                                                 esm_release_date=date(2027, 3, 31)), on) == 266

def test_upsc_age_relaxed_by_five_years_with_six_months_service():
    reqs = _law_officer_rows()
    closing = _upsc12().fields["application_deadline"].value.astimezone(IST).date()
    dob = date(1992, 10, 1)  # 34 on 16.10.2026; Law Officer UR limit 30
    ok = _person(date_of_birth=dob, esm_release_type="RETIRED_WITH_PENSION", esm_enrolment_date=date(2012, 1, 2), esm_release_date=date(2024, 1, 31))
    age = {o.check: o for o in esm.check_esm(ok, reqs, closing, closing)}["ESM_AGE"]
    assert (age.outcome, age.source_page) == ("OK", 21) and "30 + 5 = 35" in age.rule
    assert rules.check_age(ok, reqs, closing)[0].outcome == "CONDITIONAL"
    brief = _person(date_of_birth=dob, esm_release_type="MEDICAL_DISABILITY_PENSION", armed_forces_service_months=4)
    assert {o.check: o for o in esm.check_esm(brief, reqs, closing, closing)}["ESM_AGE"].outcome == "FAILS"
    assert rules.check_age(brief, reqs, closing)[0].outcome == "NOT_MET"

# ---------- reservation already used for a civil post ----------

def test_ssc_reuse_bar_follows_the_2014_om_as_the_notice_quotes_it():
    def reuse(**kw):
        return _section(_person(esm_release_type="RETIRED_WITH_PENSION", esm_release_date=date(2015, 1, 1), **kw))["ESM_RESERVATION_REUSE"]
    barred = reuse(esm_civil_post_secured=True, esm_reservation_used=True, esm_civil_post_group="C", esm_undertaking_given=False)
    assert (barred.outcome, barred.source_page) == ("FAILS", 5) and "reservation and fee concession" in barred.rule
    assert "36034/1/2014-Estt (Res)" in barred.requirement
    assert reuse(esm_civil_post_secured=True, esm_reservation_used=True, esm_civil_post_group="D", esm_undertaking_given=True).outcome == \
        "NEEDS_HUMAN_REVIEW"
    assert reuse(esm_civil_post_secured=True, esm_reservation_used=True, esm_civil_post_group="C").outcome == "CANNOT_DECIDE"
    assert reuse(esm_civil_post_secured=True, esm_reservation_used=True).outcome == "CANNOT_DECIDE"
    assert reuse(esm_civil_post_secured=True, esm_reservation_used=True, esm_civil_post_group="B").outcome == "NEEDS_HUMAN_REVIEW"
    assert reuse(esm_civil_post_secured=True, esm_reservation_used=False).outcome == "OK"
    assert reuse(esm_civil_post_secured=False).outcome == "OK"
    assert reuse().outcome == "CANNOT_DECIDE"

def test_upsc_reuse_bar_and_a_notice_without_one():
    parsed = _upsc12()
    closing = parsed.fields["application_deadline"].value.astimezone(IST).date()
    held = _person(esm_release_type="RETIRED_WITH_PENSION", esm_release_date=date(2015, 1, 1), esm_civil_post_secured=True)
    out = {o.check: o for o in esm.check_esm(held, _law_officer_rows(), closing, closing)}["ESM_RESERVATION_REUSE"]
    assert (out.outcome, out.source_page, out.requirement) == ("FAILS", 22, UPSC_NOTE)
    assert out.note == "the same note keeps the ex-servicemen age relaxation"
    free = _person(esm_release_type="RETIRED_WITH_PENSION", esm_civil_post_secured=False)
    assert {o.check: o for o in esm.check_esm(free, _law_officer_rows(), closing, closing)}["ESM_RESERVATION_REUSE"].outcome == "OK"

    # The same CHSL rows without the re-use clause: the 2014 OM is not a document held here -> a reviewer decides
    without = [r for r in _ssc_rows(CHSL) if not (r.req_type == "ESM_RULE" and r.value["kind"] == "RESERVATION_REUSE_BAR")]
    used = _person(esm_release_type="RETIRED_WITH_PENSION", esm_civil_post_secured=True, esm_reservation_used=True)
    res = {o.check: o for o in esm.check_esm(used, without, date(2026, 10, 7), date(2026, 8, 1))}["ESM_RESERVATION_REUSE"]
    assert res.outcome == "NEEDS_HUMAN_REVIEW" and res.requirement == "-"
    none_held = {o.check: o for o in esm.check_esm(free, without, date(2026, 10, 7), date(2026, 8, 1))}
    assert none_held["ESM_RESERVATION_REUSE"].outcome == "NOT_APPLICABLE"

# ---------- Agniveer and the section's presence ----------

def test_agniveer_cannot_be_decided_and_section_needs_a_claim():
    agniveer = _section(_person(is_ex_serviceman=False, is_agniveer=True))
    assert list(agniveer) == ["ESM_AGNIVEER"] and agniveer["ESM_AGNIVEER"].outcome == "CANNOT_DECIDE"
    assert _section(_person(is_ex_serviceman=False)) == {}
    assert _section(_person(is_ex_serviceman=None)) == {}
    both = _section(_person(esm_release_type="COMPLETED_ENGAGEMENT_GRATUITY", esm_release_date=date(2025, 1, 1), is_agniveer=True))
    assert list(both) == ["ESM_STATUS", "ESM_RELEASE", "ESM_AGE", "ESM_RESERVATION_REUSE", "ESM_AGNIVEER"]

# ---------- through the API ----------

ESM_PERSON = {"full_name": "Hav. Ravi Singh (test user)", "date_of_birth": "1986-05-10", "gender": "Male", "category": "UR", "is_pwbd": False,
              "is_ex_serviceman": True, "is_central_govt_employee": False, "nationality": "Indian",
              "esm_service_branch": "ARMY", "esm_rank": "Havildar", "esm_enrolment_date": "2004-06-01", "esm_release_date": "2021-06-30",
              "esm_release_type": "RETIRED_WITH_PENSION", "esm_civil_post_secured": True, "esm_reservation_used": True,
              "esm_civil_post_group": "C", "esm_undertaking_given": False, "is_agniveer": False}
TWELFTH = [{"degree": "Class XII (Senior Secondary)", "institution": "Army School, Ambala", "specialization": "Commerce", "end_date": "2003-05-31"}]

@pytest.mark.asyncio
async def test_exam_result_has_a_separate_esm_section(api, login, db_session):
    source = await get_or_create_source(db_session, ssc.SSC_SOURCE)
    sources = json.loads((FIXTURES / "ssc" / "sources.json").read_text(encoding="utf-8"))
    exam_id = (await ingest_exam_pdf(db_session, source, sources[CHSL]["url"], (FIXTURES / "ssc" / CHSL).read_bytes())).exam_id
    await db_session.commit()
    assert (await db_session.get(Exam, exam_id)).application_end.astimezone(IST).date() == date(2026, 10, 7)

    h = await login("esm-test@example.com")
    r = await api.put("/profile", json={"personal_info": {**ESM_PERSON, "esm_release_date": "2004-01-01"}, "education": TWELFTH}, headers=h)
    assert r.status_code == 422  # released before enrolment
    profile = (await api.put("/profile", json={"personal_info": ESM_PERSON, "education": TWELFTH}, headers=h)).json()
    info = profile["personal_info"]
    assert (info["esm_release_date"], info["esm_release_type"], info["esm_civil_post_group"]) == ("2021-06-30", "RETIRED_WITH_PENSION", "C")
    r = await api.post(f"/exams/{exam_id}/eligibility", json={"profile_version_id": profile["version"]["id"]}, headers=h)
    assert r.status_code == 201, r.text
    res = r.json()
    assert all(not e["check_name"].startswith("ESM_") for e in res["evidence"])
    age = next(e for e in res["evidence"] if e["check_name"] == "AGE")
    assert age["outcome"] == "CONDITIONAL" and "EX_SERVICEMEN_SERVICE_DEDUCTED" in age["rule"]
    section = {e["check_name"]: e for e in res["esm"]}
    assert [e["check_name"] for e in res["esm"]] == ["ESM_STATUS", "ESM_RELEASE", "ESM_AGE", "ESM_RESERVATION_REUSE"]
    assert {k: (e["outcome"], e["source_page"]) for k, e in section.items()} == {
        "ESM_STATUS": ("OK", "5"), "ESM_RELEASE": ("OK", "7"), "ESM_AGE": ("OK", "4"), "ESM_RESERVATION_REUSE": ("FAILS", "5")}
    assert res["esm_status"] == "FAILS" and section["ESM_AGE"]["requirement_id"] is not None
    # The ESM section never moves the verdict
    assert res["status"] == "CONDITIONAL"

@pytest.mark.asyncio
async def test_vacancy_result_has_a_separate_esm_section_and_gaps_stay_current(api, login, db_session):
    source = await get_or_create_source(db_session, upsc.UPSC_SOURCE)
    await ingest_pdf_bytes(db_session, source, f"https://www.upsc.gov.in/sites/default/files/{UPSC_12}", (FIXTURES / "upsc" / UPSC_12).read_bytes())
    await db_session.commit()
    vacancy = (await db_session.execute(select(Vacancy).where(Vacancy.external_ref == "26091203226"))).scalar_one()
    h = await login("esm-law@example.com")
    person = {**ESM_PERSON, "date_of_birth": "1992-10-01", "esm_enrolment_date": "2012-01-02", "esm_release_date": "2024-01-31"}
    law = {"personal_info": person,
           "education": [{"degree": "LL.B.", "institution": "Faculty of Law, University of Delhi", "education_level": "BACHELORS", "end_date": "2011-06-30"}],
           "experience": [{"organization": "Judge Advocate General's Branch", "designation": "Legal Assistant", "domain": "Legal",
                           "start_date": "2012-01-02", "end_date": "2024-01-31", "responsibilities": "Drafting legal opinions"}]}
    version = (await api.put("/profile", json=law, headers=h)).json()["version"]["id"]
    res = (await api.post("/eligibility", json={"profile_version_id": version, "vacancy_id": str(vacancy.id)}, headers=h)).json()
    section = {e["check_name"]: e for e in res["esm"]}
    assert section["ESM_RESERVATION_REUSE"]["outcome"] == "FAILS" and section["ESM_RESERVATION_REUSE"]["source_page"] == "22"
    assert section["ESM_AGE"]["outcome"] == "OK" and section["ESM_STATUS"]["outcome"] == "CANNOT_DECIDE"
    assert res["esm_status"] == "FAILS" and all(not e["check_name"].startswith("ESM_") for e in res["evidence"])
    gaps = (await api.get(f"/gaps?profile_version_id={version}&vacancy_id={vacancy.id}", headers=h)).json()
    assert gaps["eligibility_result_id"] == res["id"]
