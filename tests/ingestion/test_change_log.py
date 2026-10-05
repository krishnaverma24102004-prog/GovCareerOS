"""
Field-level change log, change alerts and the digest's changes section, on real official documents
(tests/fixtures/ssc/sources.json, captured 2026-10-01; UPSC notices captured 29-09-2026):

  Notice_of_adv_cgl_2026.pdf p.1   "Last date and time for receipt of online applications 22.06.2026 (23:00 hours)",
                                   "Last date and time for making online fee payment 23.06.2026 (23:00 hours)",
                                   Tier-I "August-September, 2026"
  CGLE_Reopen_23062026.pdf p.1     closing 25.06.2026 (23:00), fee 26.06.2026 (23:00), correction 01.07.2026 to 03.07.2026
  Important_Notice_2026_cgle_2026_12092026.pdf p.1   Tier-I (CBE) "30th September, 2026 to 30th October, 2026"
  Notice_of_adv_je_2026.pdf p.3 / Addendum_JE_10092026.pdf p.1   Scientific Assistant (IMD) qualification "shall be read as"
  AssttPublProseGNCTD-Notice-Engl-170926Rev.pdf (scanned)        conditions for Assistant Public Prosecutor, Advt 11/2026
"""
import json
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from govcareeros.backend.config.settings import settings
from govcareeros.backend.documents.pdf_text import tessdata_dir
from govcareeros.backend.models import Exam, ExamPost, User, UserAlert, Vacancy, VacancyVersion
from govcareeros.backend.parsing.upsc_advertisement import IST
from govcareeros.backend.services import change_log
from govcareeros.backend.services import digest as digest_service
from govcareeros.backend.services.document_ingestion import get_or_create_source, ingest_pdf_bytes
from govcareeros.backend.services.exam_corrigenda import ingest_corrigendum_pdf
from govcareeros.backend.services.exam_ingestion import ingest_exam_pdf
from govcareeros.backend.services.notice_ingestion import ingest_notice_pdf
from govcareeros.backend.services.radar import run_radar
from govcareeros.backend.sources import ssc, upsc

FIXTURES = Path(__file__).parents[1] / "fixtures"
SSC = FIXTURES / "ssc"
SOURCES = json.loads((SSC / "sources.json").read_text(encoding="utf-8"))
CGL, JE = "Notice_of_adv_cgl_2026.pdf", "Notice_of_adv_je_2026.pdf"
REOPEN, SCHEDULE, JE_ADDENDUM = "CGLE_Reopen_23062026.pdf", "Important_Notice_2026_cgle_2026_12092026.pdf", "Addendum_JE_10092026.pdf"
REOPEN_QUOTE = ("re-open the window for submission of online applications for the Combined Graduate Level Examination, 2026 for "
                "two days i.e. from 23.06.2026 (23:00 hours) to 25.06.2026 (23:00 hours)")

async def _exam(db_session, name, corrigenda=()):
    source = await get_or_create_source(db_session, ssc.SSC_SOURCE)
    result = await ingest_exam_pdf(db_session, source, SOURCES[name]["url"], (SSC / name).read_bytes())
    for c in corrigenda:
        await ingest_corrigendum_pdf(db_session, source, SOURCES[c]["url"], (SSC / c).read_bytes(), title=SOURCES[c]["title"])
    await db_session.commit()
    return await db_session.get(Exam, result.exam_id)

def _ev(e):
    return (e.url, e.page, e.quote) if e else None

@pytest.mark.asyncio
@pytest.mark.parametrize("order", [(REOPEN, SCHEDULE), (SCHEDULE, REOPEN)])
async def test_cgl_change_log_cites_the_value_replaced_and_the_new_one(db_session, order):
    exam = await _exam(db_session, CGL, order)
    log = await change_log.exam_change_log(db_session, exam)
    # Oldest notice first, whatever order the corrigenda were applied in
    assert [(v.change_type, v.notice_date, v.url) for v in log] == [
        ("REOPENED_WINDOW,DATE_CHANGE", "2026-06-23", SOURCES[REOPEN]["url"]), ("SCHEDULE", "2026-09-12", SOURCES[SCHEDULE]["url"])]
    reopen, schedule = log
    notice = SOURCES[CGL]["url"]
    by_field = {c.field: c for c in reopen.changes}
    assert list(by_field) == ["LAST_DATE", "FEE_DATE", "CORRECTION_START", "CORRECTION_END"]
    last = by_field["LAST_DATE"]
    assert (last.old, last.new) == ("2026-06-22T23:00:00+05:30", "2026-06-25T23:00:00+05:30")
    assert _ev(last.old_evidence) == (notice, 1, "Last date and time for receipt of online applications 22.06.2026 (23:00 hours)")
    assert _ev(last.new_evidence) == (SOURCES[REOPEN]["url"], 1, REOPEN_QUOTE) and last.new_evidence.notice_date == "2026-06-23"
    fee = by_field["FEE_DATE"]
    assert _ev(fee.old_evidence) == (notice, 1, "Last date and time for making online fee payment 23.06.2026 (23:00 hours)")
    assert _ev(fee.new_evidence) == (SOURCES[REOPEN]["url"], 1, "last date for making the online fee payment will be 26.06.2026 (23:00 hours)")
    assert (by_field["CORRECTION_END"].old, by_field["CORRECTION_END"].new) == ("2026-07-01T23:00:00+05:30", "2026-07-03T23:00:00+05:30")

    (tier1,) = schedule.changes
    assert (tier1.field, tier1.subject, tier1.old, tier1.new) == (
        "EXAM_DATE", "Tier-I (Computer Based Examination)", "August-September, 2026", "30th September, 2026 to 30th October, 2026")
    # The notice's own excerpt narrowed to the stage's words; the schedule notice's table row
    assert _ev(tier1.old_evidence) == (notice, 1, "Tier-I (Computer Based Examination) August-September, 2026")
    assert _ev(tier1.new_evidence) == (SOURCES[SCHEDULE]["url"], 1,
                                       "1. | Combined Graduate Level Examination, 2026 | Tier-I (CBE) | 30th September, 2026 to 30th October, 2026")
    assert change_log.change_lines(reopen)[0] == "Last date for online applications: 22 Jun 2026 23:00 IST (p.1) -> 25 Jun 2026 23:00 IST (p.1)"

    # What the exam's last date stands on now: the re-open notice, not the notice's superseded line
    value, ev = await change_log.exam_current_evidence(db_session, exam, "application_end")
    assert value == "2026-06-25T23:00:00+05:30" and _ev(ev) == (SOURCES[REOPEN]["url"], 1, REOPEN_QUOTE)

@pytest.mark.asyncio
async def test_je_qualification_change_cites_the_superseded_row(db_session):
    exam = await _exam(db_session, JE, [JE_ADDENDUM])
    (version,) = await change_log.exam_change_log(db_session, exam)
    (change,) = version.changes
    assert (change.field, change.label, change.subject) == ("ELIGIBILITY_TEXT", "Essential qualification",
                                                            "Scientific Assistant (India Meteorological Department)")
    assert change.old_evidence.url == SOURCES[JE]["url"] and change.old_evidence.page == 3
    assert change.old.startswith("Bachelor’s Degree in Science with Physics") and change.old_evidence.quote == change.old
    assert change.new_evidence.url == SOURCES[JE_ADDENDUM]["url"] and change.new_evidence.page == 1
    assert change.new.startswith("(1) Bachelor’s Degree in Science with Physics")
    assert change.new.endswith("Physics and Mathematics as one of the subjects.") and change.applied

def _profile(dob, category="UR", central=False):
    return {"personal_info": {"full_name": "Asha Verma", "date_of_birth": dob, "gender": "Male", "category": category, "is_pwbd": False,
                              "is_ex_serviceman": False, "is_central_govt_employee": central, "nationality": "Indian"},
            "education": [{"degree": "Bachelor of Commerce (B.Com)", "institution": "University of Rajasthan",
                           "education_level": "BACHELORS", "end_date": "2018-05-31"}]}

async def _aso_css(db_session):
    return str((await db_session.execute(select(ExamPost.id).where(
        ExamPost.post_name == "Assistant Section Officer", ExamPost.department == "Central Secretariat Service"))).scalar_one())

@pytest.mark.asyncio
async def test_exam_changes_alert_applicants_and_eligible_users_once_and_reach_the_digest(api, login, db_session):
    exam = await _exam(db_session, CGL, [REOPEN, SCHEDULE])
    post = await _aso_css(db_session)
    exam_id = str(exam.id)  # the 409 below rolls the shared session back and expires loaded rows

    ravi = await login("ravi@example.com")  # tracks an application for the exam; no verdict
    await api.put("/profile", json=_profile("1990-01-01"), headers=ravi)
    r = await api.post("/applications", json={"exam_id": exam_id, "status": "APPLIED"}, headers=ravi)
    assert r.status_code == 201 and r.json()["exam_id"] == exam_id and r.json()["exam_ref"] == "Combined Graduate Level Examination, 2026"
    assert (await api.post("/applications", json={"exam_id": exam_id}, headers=ravi)).status_code == 409

    async def verdict(headers, dob):
        version = (await api.put("/profile", json=_profile(dob), headers=headers)).json()["version"]["id"]
        r = await api.post(f"/exams/{exam_id}/eligibility", json={"profile_version_id": version, "post_id": post}, headers=headers)
        assert r.status_code == 201, r.text
        return r.json()["status"]

    asha = await login("asha@example.com")
    assert await verdict(asha, "1998-01-01") in ("ELIGIBLE", "CONDITIONAL")
    mohan = await login("mohan@example.com")  # born 1980: over 30 for ASO (CSS), page 3
    assert await verdict(mohan, "1980-01-01") == "INELIGIBLE"
    await db_session.commit()

    run = await run_radar(db_session)
    await db_session.commit()
    assert run.alerts_created == {"EXAM_UPDATED": 4}  # two notices x (applicant, eligible user)
    assert (await run_radar(db_session)).alerts_created == {}
    assert [a for a in (await api.get("/alerts", headers=mohan)).json()] == []

    for headers, why in [(ravi, "(you track an application for it (status APPLIED))"), (asha, " for it)")]:
        alerts = (await api.get("/alerts", headers=headers)).json()
        assert len(alerts) == 2 and {a["exam_id"] for a in alerts} == {exam_id} and {a["kind"] for a in alerts} == {"EXAM_UPDATED"}
        reopen = next(a for a in alerts if a["body"].startswith("REOPENED_WINDOW,DATE_CHANGE notice dated 23 Jun 2026"))
        assert why in reopen["body"].split("\n")[0] and reopen["exam_version_id"]
        assert "Last date for online applications: 22 Jun 2026 23:00 IST (p.1) -> 25 Jun 2026 23:00 IST (p.1)" in reopen["body"]
        assert "Last date for online fee payment: 23 Jun 2026 23:00 IST (p.1) -> 26 Jun 2026 23:00 IST (p.1)" in reopen["body"]
    keys = (await db_session.execute(select(UserAlert.dedupe_key).where(UserAlert.kind == "EXAM_UPDATED"))).scalars().all()
    assert len(keys) == 4 and len(set(keys)) == 2 and all(k.startswith("exam_notice:") for k in keys)

    # The digest shows each change with the earlier value's citation and the new value's
    asha_user = (await db_session.execute(select(User).where(User.email == "asha@example.com"))).scalar_one()
    d, _ = await digest_service.generate(db_session, asha_user)
    await db_session.commit()
    assert {i.headline for i in d.changes} == {"Official change: Combined Graduate Level Examination, 2026"} and len(d.changes) == 2
    item = next(i for i in d.changes if i.details[0].startswith("REOPENED_WINDOW"))
    assert item.exam_id == exam_id and item.vacancy_id is None
    assert "Last date for online applications: 22 Jun 2026 23:00 IST (p.1) -> 25 Jun 2026 23:00 IST (p.1)" in item.details
    cited = {(c.url, c.page, c.quote) for c in item.citations}
    assert (SOURCES[CGL]["url"], 1, "Last date and time for receipt of online applications 22.06.2026 (23:00 hours)") in cited
    assert (SOURCES[REOPEN]["url"], 1, REOPEN_QUOTE) in cited
    assert "Official changes to what you applied for or are eligible for (2)" in digest_service.render_text(d)
    # Reported once: the next digest has nothing new
    again, _ = await digest_service.generate(db_session, asha_user)
    assert again.changes == []

@pytest.mark.asyncio
async def test_changes_and_deadline_endpoints(api, login, db_session, monkeypatch):
    exam = await _exam(db_session, CGL, [REOPEN])
    h = await login("ravi@example.com")
    (v,) = (await api.get(f"/exams/{exam.id}/changes", headers=h)).json()
    assert v["lines"][0] == "Last date for online applications: 22 Jun 2026 23:00 IST (p.1) -> 25 Jun 2026 23:00 IST (p.1)"
    assert v["changes"][0]["old_evidence"]["quote"] == "Last date and time for receipt of online applications 22.06.2026 (23:00 hours)"
    assert (await api.get(f"/exams/{exam.id}/changes")).status_code == 401

    # Not in service, no buffer configured: the official date alone, citing the notice that set it
    await api.put("/profile", json=_profile("1998-01-01"), headers=h)
    d = (await api.get(f"/exams/{exam.id}/deadline", headers=h)).json()
    assert datetime.fromisoformat(d["official"]["date"]) == datetime(2026, 6, 25, 23, 0, tzinfo=IST)
    assert (d["official"]["evidence"]["url"], d["official"]["evidence"]["quote"]) == (SOURCES[REOPEN]["url"], REOPEN_QUOTE)
    assert d["forwarding_estimate"] is None and d["relative_to_employment_news"] is None
    monkeypatch.setattr(settings, "IN_SERVICE_FORWARDING_BUFFER_DAYS", 7)
    assert (await api.get(f"/exams/{exam.id}/deadline", headers=h)).json()["forwarding_estimate"] is None  # not in service

    # In service with a buffer: a separate estimate, labelled; the official date is unchanged
    await api.put("/profile", json=_profile("1998-01-01", central=True), headers=h)
    d = (await api.get(f"/exams/{exam.id}/deadline", headers=h)).json()
    est = d["forwarding_estimate"]
    assert datetime.fromisoformat(d["official"]["date"]) == datetime(2026, 6, 25, 23, 0, tzinfo=IST)
    assert est["is_official"] is False and est["buffer_days"] == 7 and est["label"].startswith("Estimate, not an official date")
    assert datetime.fromisoformat(est["estimate"]) == datetime(2026, 6, 18, 23, 0, tzinfo=IST)
    assert datetime.fromisoformat(est["official_deadline"]) == datetime(2026, 6, 25, 23, 0, tzinfo=IST)
    exam_row = await db_session.get(Exam, exam.id)
    await db_session.refresh(exam_row)
    assert exam_row.application_end == datetime(2026, 6, 25, 23, 0, tzinfo=IST)  # nothing stored from the estimate

UPSC = FIXTURES / "upsc"
GNCTD = "AssttPublProseGNCTD-Notice-Engl-170926Rev.pdf"

@pytest.mark.asyncio
@pytest.mark.skipif(tessdata_dir() is None, reason="the notice is scanned; needs Tesseract")
async def test_vacancy_notice_alerts_the_applicant_with_its_cited_conditions(api, login, db_session):
    source = await get_or_create_source(db_session, upsc.UPSC_SOURCE)
    name = "AdvtNo-11-2026-Engl-100926.pdf"
    await ingest_pdf_bytes(db_session, source, f"https://www.upsc.gov.in/sites/default/files/{name}", (UPSC / name).read_bytes())
    await ingest_notice_pdf(db_session, source, f"https://www.upsc.gov.in/sites/default/files/{GNCTD}", (UPSC / "notices" / GNCTD).read_bytes(),
                            "Clarification for the post of Assistant Public Prosecutor in Delhi, vide Advt. No. 11 - 2026")
    await db_session.commit()
    vacancy = (await db_session.execute(select(Vacancy).where(Vacancy.external_ref == "26091106212"))).scalar_one()
    vv = (await db_session.execute(select(VacancyVersion).where(VacancyVersion.vacancy_id == vacancy.id))).scalar_one()

    (vc,) = await change_log.vacancy_change_log(db_session, vacancy)
    assert vc.notice_date == "2026-09-15" and vc.changes and {c.field for c in vc.changes} == {"CONDITION"}
    assert any("certificates issued by GNCTD" in c.new for c in vc.changes)
    # A condition the notice adds: nothing stood before; the new value cites the notice's page and words
    assert all(c.old is None and c.old_evidence is None and c.new_evidence.page and c.new_evidence.quote == c.new
               and c.new_evidence.url.endswith(GNCTD) for c in vc.changes)

    ravi = await login("ravi@example.com")
    await api.put("/profile", json=_profile("1990-01-01"), headers=ravi)
    await api.post("/applications", json={"vacancy_id": str(vacancy.id), "status": "APPLIED"}, headers=ravi)
    await db_session.commit()
    run = await run_radar(db_session, now=datetime(2026, 9, 29, 12, 0, tzinfo=IST))
    await db_session.commit()
    assert run.alerts_created.get("VACANCY_UPDATED") == 1
    (alert,) = [a for a in (await api.get("/alerts", headers=ravi)).json() if a["kind"] == "VACANCY_UPDATED"]
    assert alert["vacancy_version_id"] == str(vv.id) and "(you track an application for it (status APPLIED))" in alert["body"]
    assert "Condition stated by the notice: not stated" in alert["body"]
    assert (await run_radar(db_session, now=datetime(2026, 9, 29, 12, 0, tzinfo=IST))).alerts_created.get("VACANCY_UPDATED") is None
    keys = (await db_session.execute(select(UserAlert.dedupe_key).where(UserAlert.kind == "VACANCY_UPDATED"))).scalars().all()
    assert keys == [f"notice:{vv.id}"]
