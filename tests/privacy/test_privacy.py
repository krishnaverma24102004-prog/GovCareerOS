"""
DPDP Act 2023: consent notice, consent-gated storage and eligibility processing, the under-18 guardian gate, export,
erasure (soft then hard) and field-level encryption at rest.

Users and profile values here are test-only accounts created inside each test. The vacancies are the real UPSC
advertisements in tests/fixtures/upsc, ingested from the official PDFs.
"""
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import bindparam, select, text

from govcareeros.backend.config.settings import settings
from govcareeros.backend.eligibility import engine as eligibility_engine
from govcareeros.backend.models import Base, Vacancy
from govcareeros.backend.models.consent import ConsentRecord
from govcareeros.backend.models.user import PersonalInfo, ProfileVersion, ReservationCertificate, User
from govcareeros.backend.privacy import crypto, enforcement, notice, rights
from govcareeros.backend.services.document_ingestion import get_or_create_source, ingest_pdf_bytes
from govcareeros.backend.sources import upsc

UPSC = Path(__file__).parents[1] / "fixtures" / "upsc"

PROFILE = {
    "personal_info": {"full_name": "Test Candidate", "date_of_birth": "1995-03-10", "category": "OBC", "is_pwbd": True,
                      "disability_percentage": 45, "disability_types": "OL", "nationality": "Indian"},
    "education": [{"degree": "LL.B.", "institution": "Test Law College", "education_level": "BACHELORS", "end_date": "2021-06-30"}],
    "reservation_certificates": [{"kind": "OBC_NCL", "issue_date": "2026-04-01", "financial_year": "2025-26"}],
}

def _years_ago(years: int, days: int = 0) -> str:
    t = date.today()
    return (t.replace(year=t.year - years) - timedelta(days=days)).isoformat()

async def _consent(api, h, purpose="ELIGIBILITY_CHECK", **extra):
    return await api.post("/privacy/consents", headers=h, json={"purpose": purpose, "notice_version": notice.NOTICE_VERSION, **extra})

async def _law_officer(db_session):
    """The real Law Officer vacancy (Advt 12/2026). Its id is read here: a refused request rolls the shared session back
    and expires loaded objects."""
    source = await get_or_create_source(db_session, upsc.UPSC_SOURCE)
    name = "AdvtNo-12-2026-Engl-250926_0.pdf"
    await ingest_pdf_bytes(db_session, source, f"https://www.upsc.gov.in/sites/default/files/{name}", (UPSC / name).read_bytes())
    await db_session.commit()
    vacancy = (await db_session.execute(select(Vacancy).where(Vacancy.external_ref == "26091203226"))).scalar_one()
    return vacancy.id

# ---------- notice and consents ----------

@pytest.mark.asyncio
async def test_notice_lists_purposes_items_withdrawal_and_grievance_in_english_and_hindi(api, monkeypatch):
    r = await api.get("/privacy/notice")  # public: readable before signup
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == notice.NOTICE_VERSION
    by = {p["purpose"]: p for p in body["purposes"]}
    assert set(by) == {"ELIGIBILITY_CHECK", "ALERTS", "RESUME", "OPERATOR_ASSISTED_APPLICATION"}
    for p in by.values():
        assert p["en"] and p["hi"] and p["data_items"]
        assert all(i["en"] and i["hi"] for i in p["data_items"])
    elig = {i["key"] for i in by["ELIGIBILITY_CHECK"]["data_items"]}
    assert {"category", "date_of_birth", "disability", "certificates", "service_details"} <= elig
    assert "official notification prevails" in by["ELIGIBILITY_CHECK"]["en"]
    assert "/privacy/consents" in body["withdraw"]["en"] and body["withdraw"]["hi"]
    assert body["minors"]["en"] and body["minors"]["hi"]
    assert body["grievance"]["contact"] == notice.GRIEVANCE_NOT_CONFIGURED

    monkeypatch.setattr(settings, "PRIVACY_GRIEVANCE_CONTACT", "Grievance Officer (test), grievance@test.invalid")
    g = (await api.get("/privacy/notice")).json()["grievance"]
    assert g["contact"] == "Grievance Officer (test), grievance@test.invalid"
    assert "grievance@test.invalid" in g["en"] and "grievance@test.invalid" in g["hi"]

@pytest.mark.asyncio
async def test_give_list_and_withdraw_consent_keeps_history(api, login):
    h = await login("c1@test.invalid", consent=False)
    assert (await _consent(api, h, notice_version="2000-01-01")).status_code == 409   # stale notice
    r = await api.post("/privacy/consents", headers=h, json={"purpose": "ELIGIBILITY_CHECK", "notice_version": "2000-01-01"})
    assert r.status_code == 409 and notice.NOTICE_VERSION in r.text
    assert (await _consent(api, h, purpose="MARKETING")).status_code == 404
    assert (await api.delete("/privacy/consents/MARKETING", headers=h)).status_code == 404

    r = await _consent(api, h)
    assert r.status_code == 201, r.text
    given = r.json()
    assert given["method"] == "SELF_WEB" and given["notice_version"] == notice.NOTICE_VERSION
    assert given["data_items"] == sorted(notice.data_items("ELIGIBILITY_CHECK"))   # from the notice, not the client
    assert (await _consent(api, h, purpose="ALERTS")).status_code == 201

    listed = (await api.get("/privacy/consents", headers=h)).json()
    assert listed["active"] == ["ALERTS", "ELIGIBILITY_CHECK"] and listed["outdated"] == []

    r = await api.delete("/privacy/consents/ELIGIBILITY_CHECK", headers=h)
    assert r.status_code == 200 and r.json() == {"purpose": "ELIGIBILITY_CHECK", "withdrawn": 1}
    listed = (await api.get("/privacy/consents", headers=h)).json()
    assert listed["active"] == ["ALERTS"]
    # The withdrawn record is still there, with its withdrawal time
    rec = next(x for x in listed["records"] if x["purpose"] == "ELIGIBILITY_CHECK")
    assert rec["id"] == given["id"] and rec["withdrawn_at"] is not None

# ---------- enforcement ----------

@pytest.mark.asyncio
async def test_profile_refuses_eligibility_data_without_consent(api, login):
    h = await login("c2@test.invalid", consent=False)
    r = await api.put("/profile", json=PROFILE, headers=h)
    assert r.status_code == 403, r.text
    d = r.json()["detail"]
    assert d["code"] == "CONSENT_REQUIRED" and d["purpose"] == "ELIGIBILITY_CHECK"
    assert {"category", "date_of_birth", "disability", "certificates"} <= set(d["data_items"])
    assert "POST /privacy/consents" in d["action"]
    assert (await api.get("/profile", headers=h)).status_code == 404   # nothing was stored

    for field, value in (("organisation_cadre", "Test Cadre"), ("current_pay_level", 7), ("is_ex_serviceman", False)):
        r = await api.put("/profile", json={"personal_info": {"full_name": "T", field: value}}, headers=h)
        assert r.status_code == 403 and r.json()["detail"]["data_items"] in (["service_details"], ["ex_serviceman"])
    # Gender and citizenship / nationality are ELIGIBILITY_CHECK items of the notice too
    for field, value, item in (("gender", "FEMALE", "gender"), ("citizenship_status", "INDIAN_CITIZEN", "citizenship_status"),
                               ("nationality", "Indian", "citizenship_status")):
        assert item in notice.data_items("ELIGIBILITY_CHECK")
        r = await api.put("/profile", json={"personal_info": {"full_name": "T", field: value}}, headers=h)
        assert r.status_code == 403 and r.json()["detail"]["data_items"] == [item], (field, r.text)

    # Data outside the purpose needs no ELIGIBILITY_CHECK consent
    ok = await api.put("/profile", json={"personal_info": {"full_name": "T"}, "skills": [{"skill_name": "Typing", "skill_type": "TECHNICAL"}]}, headers=h)
    assert ok.status_code == 201, ok.text

    assert (await _consent(api, h)).status_code == 201
    r = await api.put("/profile", json=PROFILE, headers=h)
    assert r.status_code == 201, r.text
    assert r.json()["personal_info"]["category"] == "OBC"

@pytest.mark.asyncio
async def test_free_form_claims_of_eligibility_data_need_consent(api, login):
    h = await login("c7@test.invalid", consent=False)
    assert (await api.put("/profile", json={"personal_info": {"full_name": "T"}}, headers=h)).status_code == 201
    for subject, predicate, value in (("personal_info", "category", "SC"), ("personal_info", "Caste", "SC"),
                                      ("personal_info", "is_pwbd", "yes"), ("reservation_certificates", "certificate", "SC certificate")):
        r = await api.post("/profile/facts", json={"subject": subject, "predicate": predicate, "value": value}, headers=h)
        assert r.status_code == 403 and r.json()["detail"]["code"] == "CONSENT_REQUIRED", (predicate, r.text)
    assert (await api.get("/profile/facts", headers=h)).json() == []
    # A claim outside the purpose needs no ELIGIBILITY_CHECK consent
    r = await api.post("/profile/facts", json={"subject": "education", "predicate": "degree", "value": "LL.B."}, headers=h)
    assert r.status_code == 201, r.text

    assert (await _consent(api, h)).status_code == 201
    r = await api.post("/profile/facts", json={"subject": "personal_info", "predicate": "category", "value": "SC"}, headers=h)
    assert r.status_code == 201, r.text
    fact_id = r.json()["id"]
    await api.delete("/privacy/consents/ELIGIBILITY_CHECK", headers=h)
    # Evidence for that claim is that data too: refused after withdrawal
    r = await api.post(f"/profile/facts/{fact_id}/evidence", json={"evidence_type": "DOCUMENT", "source": "caste certificate"}, headers=h)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "CONSENT_REQUIRED"

@pytest.mark.asyncio
async def test_withdrawal_stops_processing_without_deleting_history(api, login, db_session):
    vacancy_id = await _law_officer(db_session)
    h = await login("c3@test.invalid")
    v1 = (await api.put("/profile", json=PROFILE, headers=h)).json()["version"]["id"]
    r = await api.post("/eligibility", json={"profile_version_id": v1, "vacancy_id": str(vacancy_id)}, headers=h)
    assert r.status_code == 201, r.text
    result_id = r.json()["id"]

    assert (await api.delete("/privacy/consents/ELIGIBILITY_CHECK", headers=h)).json()["withdrawn"] == 1

    # Eligibility runs refuse, with what to do
    r = await api.post("/eligibility", json={"profile_version_id": v1, "vacancy_id": str(vacancy_id)}, headers=h)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "CONSENT_REQUIRED"
    # A PATCH would carry the category/disability over into a new version: also processing, also refused
    r = await api.patch("/profile", json={"skills": [{"skill_name": "Drafting", "skill_type": "DOMAIN"}]}, headers=h)
    assert r.status_code == 403
    # History is not silently deleted: the profile, its versions and the earlier result are all still readable
    assert (await api.get("/profile", headers=h)).json()["personal_info"]["category"] == "OBC"
    assert [v["id"] for v in (await api.get("/profile/versions", headers=h)).json()] == [v1]
    assert (await api.get(f"/eligibility/{result_id}", headers=h)).status_code == 200
    # The person can still remove the data without consent (clearing never needs it)
    r = await api.patch("/profile", json={"personal_info": {"full_name": "Test Candidate"}, "reservation_certificates": []}, headers=h)
    assert r.status_code == 201, r.text
    assert r.json()["personal_info"]["category"] is None

@pytest.mark.asyncio
async def test_consent_under_an_older_notice_version_is_not_carried_over(api, login, db_session, monkeypatch):
    vacancy_id = await _law_officer(db_session)
    h = await login("c5@test.invalid")
    v1 = (await api.put("/profile", json=PROFILE, headers=h)).json()["version"]["id"]
    user_id = (await db_session.execute(select(User.id).where(User.email == "c5@test.invalid"))).scalar_one()

    # The notice changes (a new version): the consent given under the old one is listed as outdated ...
    monkeypatch.setattr(notice, "NOTICE_VERSION", "2099-01-01.1")
    listed = (await api.get("/privacy/consents", headers=h)).json()
    assert listed["outdated"] == ["ELIGIBILITY_CHECK"]
    # ... and no longer allows storing or processing eligibility data
    r = await api.patch("/profile", json={"personal_info": {"full_name": "Test Candidate", "category": "SC"}}, headers=h)
    assert r.status_code == 403, r.text
    d = r.json()["detail"]
    assert d["code"] == "CONSENT_OUTDATED" and d["notice_version"] == "2099-01-01.1" and "POST /privacy/consents" in d["action"]
    r = await api.post("/eligibility", json={"profile_version_id": v1, "vacancy_id": str(vacancy_id)}, headers=h)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "CONSENT_OUTDATED"
    assert not await enforcement.has_consent(db_session, user_id)
    # Consenting again under the new version restores it
    assert (await _consent(api, h)).status_code == 201
    r = await api.post("/eligibility", json={"profile_version_id": v1, "vacancy_id": str(vacancy_id)}, headers=h)
    assert r.status_code == 201, r.text

@pytest.mark.asyncio
async def test_consent_that_does_not_list_a_data_item_does_not_cover_it(api, login, db_session):
    h = await login("c6@test.invalid", consent=False)
    user_id = (await db_session.execute(select(User.id).where(User.email == "c6@test.invalid"))).scalar_one()
    from govcareeros.backend.services.consent import record_consent
    # A record (current version) whose items leave out certificates, e.g. written by an older build of the notice
    await record_consent(db_session, user_id, "ELIGIBILITY_CHECK", ["category", "date_of_birth"], notice.NOTICE_VERSION)
    await db_session.commit()
    assert (await api.put("/profile", json={"personal_info": {"full_name": "T", "category": "OBC"}}, headers=h)).status_code == 201
    r = await api.put("/profile", json={"personal_info": {"full_name": "T"},
                                        "reservation_certificates": [{"kind": "OBC_NCL", "issue_date": "2026-04-01"}]}, headers=h)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "CONSENT_OUTDATED"
    assert r.json()["detail"]["data_items"] == ["certificates"]

@pytest.mark.asyncio
async def test_radar_and_digest_skip_a_user_who_withdrew(api, login, db_session):
    from govcareeros.backend.services import digest, radar
    vacancy_id = await _law_officer(db_session)
    h = await login("c4@test.invalid")
    v1 = (await api.put("/profile", json=PROFILE, headers=h)).json()["version"]["id"]
    user = (await db_session.execute(select(User).where(User.email == "c4@test.invalid"))).scalar_one()
    await api.delete("/privacy/consents/ELIGIBILITY_CHECK", headers=h)

    run = await radar.run_radar(db_session, user_ids=[user.id])
    assert run.users == 0 and run.evaluations == 0
    d = await digest.build_digest(db_session, user.id)
    assert d.profile_version_id == v1 and d.items() == []
    with pytest.raises(enforcement.ConsentRequired):
        await eligibility_engine.evaluate(db_session, user.id, uuid.UUID(v1), vacancy_id)

@pytest.mark.asyncio
async def test_change_watch_stops_using_verdicts_after_withdrawal(api, login, db_session):
    """Official changes are alerted for vacancies the person is ELIGIBLE / CONDITIONAL for; after withdrawal those
    verdicts (derived from category, DOB, disability) are no longer used to pick or explain what to watch."""
    from govcareeros.backend.services import change_log
    vacancy_id = await _law_officer(db_session)
    h = await login("c8@test.invalid")
    profile = {**PROFILE, "personal_info": {**PROFILE["personal_info"], "date_of_birth": "1993-06-01"},
               "experience": [{"organization": "Test Chambers", "designation": "Legal Associate", "domain": "Legal",
                               "start_date": "2017-01-01"}]}
    v1 = (await api.put("/profile", json=profile, headers=h)).json()["version"]["id"]
    r = await api.post("/eligibility", json={"profile_version_id": v1, "vacancy_id": str(vacancy_id)}, headers=h)
    assert r.json()["status"] in ("ELIGIBLE", "CONDITIONAL"), r.text
    user_id = (await db_session.execute(select(User.id).where(User.email == "c8@test.invalid"))).scalar_one()
    _, watched = await change_log.watched_targets(db_session, user_id)
    assert watched == {vacancy_id: f"profile v1 is {r.json()['status']} for it"}

    await api.delete("/privacy/consents/ELIGIBILITY_CHECK", headers=h)
    assert await change_log.watched_targets(db_session, user_id) == ({}, {})
    assert await change_log.change_alerts(db_session, user_id) == []

@pytest.mark.asyncio
async def test_reviewers_stop_seeing_claims_after_withdrawal_or_erasure(api, login, db_session):
    candidate = await login("c9@test.invalid", consent=False)
    await _consent(api, candidate)
    reviewer = await login("rev9@test.invalid")
    (await db_session.execute(select(User).where(User.email == "rev9@test.invalid"))).scalar_one().is_reviewer = True
    await db_session.commit()
    assert (await api.put("/profile", json=PROFILE, headers=candidate)).status_code == 201   # its certificate is a claim
    degree = (await api.post("/profile/facts", json={"subject": "education", "predicate": "degree", "value": "LL.B."},
                             headers=candidate)).json()

    async def listed():
        page = (await api.get("/review/facts", headers=reviewer)).json()
        return {f["predicate"]: f["id"] for f in page["items"]}, page["total"]

    claims, total = await listed()
    assert set(claims) == {"certificate", "degree"} and total == 2
    certificate = claims["certificate"]

    # ELIGIBILITY_CHECK withdrawn: the certificate claim (and, with no other purpose, the degree) is out of review
    await api.delete("/privacy/consents/ELIGIBILITY_CHECK", headers=candidate)
    assert await listed() == ({}, 0)
    for fact_id in (certificate, degree["id"]):
        r = await api.post(f"/review/facts/{fact_id}/reject", json={"note": "x"}, headers=reviewer)
        assert r.status_code == 404, r.text
    # RESUME consent covers the degree claim again, never the certificate
    await _consent(api, candidate, purpose="RESUME")
    assert await listed() == ({"degree": degree["id"]}, 1)

    # Erasure requested: nothing of theirs is reviewed during the grace period
    assert (await api.post("/privacy/erase", json={"confirm": "ERASE"}, headers=candidate)).status_code == 202
    assert await listed() == ({}, 0)
    assert (await api.post(f"/review/facts/{degree['id']}/reject", json={"note": "x"}, headers=reviewer)).status_code == 404

@pytest.mark.asyncio
async def test_reviewers_see_a_minors_claims_only_under_guardian_consent(api, login, db_session):
    from govcareeros.backend.services.consent import record_consent
    minor = await login("minor9@test.invalid", consent=False)
    await _consent(api, minor, guardian_name="Test Guardian")
    reviewer = await login("rev10@test.invalid")
    (await db_session.execute(select(User).where(User.email == "rev10@test.invalid"))).scalar_one().is_reviewer = True
    await db_session.commit()
    profile = {"personal_info": {"full_name": "Minor", "date_of_birth": _years_ago(17), "category": "SC"},
               "reservation_certificates": [{"kind": "SC", "issue_date": "2026-04-01"}]}
    assert (await api.put("/profile", json=profile, headers=minor)).status_code == 201
    assert (await api.get("/review/facts", headers=reviewer)).json()["total"] == 1

    # Only a consent without a guardian's name left (e.g. recorded before the DOB was entered): not reviewable
    await api.delete("/privacy/consents/ELIGIBILITY_CHECK", headers=minor)
    uid = (await db_session.execute(select(User.id).where(User.email == "minor9@test.invalid"))).scalar_one()
    await record_consent(db_session, uid, "ELIGIBILITY_CHECK", notice.data_items("ELIGIBILITY_CHECK"), notice.NOTICE_VERSION)
    await db_session.commit()
    assert (await api.get("/review/facts", headers=reviewer)).json()["total"] == 0

LAW_PROFILE_AT_LIMIT = {**PROFILE, "personal_info": {**PROFILE["personal_info"], "date_of_birth": "1993-06-01"},
                        "experience": [{"organization": "Test Chambers", "designation": "Legal Associate", "domain": "Legal",
                                        "start_date": "2017-01-01"}]}
BEFORE_CLOSING = datetime(2026, 10, 1, tzinfo=timezone.utc)   # Advt 12/2026 closes 16 Oct 2026

@pytest.mark.asyncio
async def test_alerts_and_digests_need_alerts_consent(api, login, db_session, monkeypatch):
    from govcareeros.backend.services import digest, mailer, radar
    await _law_officer(db_session)
    h = await login("c10@test.invalid", consent=["ELIGIBILITY_CHECK"])
    assert (await api.put("/profile", json=LAW_PROFILE_AT_LIMIT, headers=h)).status_code == 201
    user_id = (await db_session.execute(select(User.id).where(User.email == "c10@test.invalid"))).scalar_one()
    sent = []
    monkeypatch.setattr(mailer, "send", lambda to, *a, **k: sent.append(to))   # no real email

    # No ALERTS consent: results are still kept current, but nothing is alerted, written as a digest or emailed
    run = await radar.run_radar(db_session, now=BEFORE_CLOSING, user_ids=[user_id])
    assert run.evaluations >= 1 and run.alerts_created == {}
    assert (await api.get("/alerts", headers=h)).json() == []
    assert await digest.run_all(db_session, now=BEFORE_CLOSING, send=True) == [] and sent == []
    r = await api.post("/digest/generate", headers=h)
    assert r.status_code == 403 and r.json()["detail"]["purpose"] == "ALERTS"

    # With ALERTS consent: alerted, and the digest is written and sent
    assert (await _consent(api, h, purpose="ALERTS")).status_code == 201
    run = await radar.run_radar(db_session, now=BEFORE_CLOSING, user_ids=[user_id])
    assert run.alerts_created.get("NEW_MATCH", 0) >= 1
    runs = await digest.run_all(db_session, now=BEFORE_CLOSING, send=True)
    assert [x.user_id for x in runs] == [user_id] and sent == ["c10@test.invalid"]

    # Withdrawn: no further digest or email, not even for a digest written earlier
    assert (await api.delete("/privacy/consents/ALERTS", headers=h)).json()["withdrawn"] == 1
    assert await digest.run_all(db_session, now=BEFORE_CLOSING, send=True) == [] and sent == ["c10@test.invalid"]
    with pytest.raises(enforcement.ConsentRequired):
        await digest.send_run(db_session, runs[0], "c10@test.invalid")
    assert sent == ["c10@test.invalid"]

@pytest.mark.asyncio
async def test_resume_needs_resume_consent_and_quotes_only_resume_items(api, login, db_session):
    from govcareeros.backend.models.fact import Fact
    from govcareeros.backend.models.resume import Resume
    h = await login("c11@test.invalid", consent=["ELIGIBILITY_CHECK"])
    v1 = (await api.put("/profile", json=PROFILE, headers=h)).json()["version"]["id"]
    facts_before = len((await api.get("/profile/facts", headers=h)).json())

    r = await api.post("/resume", json={"profile_version_id": v1}, headers=h)
    assert r.status_code == 403 and r.json()["detail"]["purpose"] == "RESUME" and r.json()["detail"]["code"] == "CONSENT_REQUIRED"
    assert (await db_session.execute(select(Resume))).first() is None
    assert len((await api.get("/profile/facts", headers=h)).json()) == facts_before   # nothing read into new claims

    # RESUME consent, ELIGIBILITY_CHECK withdrawn: the resume quotes name/contact, education, experience, skills only
    assert (await _consent(api, h, purpose="RESUME")).status_code == 201
    await api.delete("/privacy/consents/ELIGIBILITY_CHECK", headers=h)
    r = await api.post("/resume", json={"profile_version_id": v1}, headers=h)
    assert r.status_code == 201, r.text
    claims = r.json()["claims"]
    assert {c["field"] for c in claims} == {"full_name", "degree", "institution", "end_date"}
    assert not {"1995-03-10", "Indian"} & {c["text"] for c in claims}
    predicates = (await db_session.execute(select(Fact.predicate).where(Fact.profile_version_id == uuid.UUID(v1)))).scalars().all()
    assert "date_of_birth" not in predicates and "nationality" not in predicates
    assert {"date_of_birth", "citizenship_status"}.isdisjoint(notice.data_items("RESUME"))

    await api.delete("/privacy/consents/RESUME", headers=h)
    assert (await api.post("/resume", json={"profile_version_id": v1}, headers=h)).status_code == 403

# ---------- age gate ----------

@pytest.mark.asyncio
async def test_under_18_needs_guardian_consent(api, login, db_session):
    vacancy_id = await _law_officer(db_session)
    h = await login("minor@test.invalid")   # consent given without a guardian name
    minor = {"personal_info": {"full_name": "Minor", "date_of_birth": _years_ago(17), "category": "SC"}}
    r = await api.put("/profile", json=minor, headers=h)
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "GUARDIAN_CONSENT_REQUIRED" and "guardian_name" in r.json()["detail"]["action"]

    # Turning 18 today is not a minor
    assert (await api.put("/profile", json={"personal_info": {"date_of_birth": _years_ago(18)}}, headers=h)).status_code == 201
    # The day before the 18th birthday is
    assert (await api.put("/profile", json={"personal_info": {"date_of_birth": _years_ago(18, days=-1)}}, headers=h)).status_code == 403

    r = await _consent(api, h, guardian_name="  ")
    assert r.status_code == 201   # profile on file is 18 today: no gate; blank guardian name is stored as none
    assert r.json()["guardian_name"] is None
    r = await _consent(api, h, guardian_name="Test Guardian")
    assert r.status_code == 201 and r.json()["guardian_name"] == "Test Guardian"
    r = await api.put("/profile", json=minor, headers=h)
    assert r.status_code == 201, r.text
    v = r.json()["version"]["id"]
    assert (await api.post("/eligibility", json={"profile_version_id": v, "vacancy_id": str(vacancy_id)}, headers=h)).status_code == 201

    # Once the profile says under 18, consent without a guardian is refused at the door
    await api.delete("/privacy/consents/ELIGIBILITY_CHECK", headers=h)
    r = await _consent(api, h)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "GUARDIAN_CONSENT_REQUIRED"
    # ... and an eligibility run under a guardian-less consent (e.g. recorded before the DOB was entered) refuses
    user = (await db_session.execute(select(User).where(User.email == "minor@test.invalid"))).scalar_one()
    from govcareeros.backend.services.consent import record_consent
    await record_consent(db_session, user.id, "ELIGIBILITY_CHECK", notice.data_items("ELIGIBILITY_CHECK"), notice.NOTICE_VERSION)
    await db_session.commit()
    r = await api.post("/eligibility", json={"profile_version_id": v, "vacancy_id": str(vacancy_id)}, headers=h)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "GUARDIAN_CONSENT_REQUIRED"

def test_age_on_birthdays():
    assert enforcement.age_on(date(2008, 10, 6), date(2026, 10, 6)) == 18
    assert enforcement.age_on(date(2008, 10, 7), date(2026, 10, 6)) == 17
    assert enforcement.is_minor(datetime(2008, 10, 7), date(2026, 10, 6))
    assert not enforcement.is_minor(None)

# ---------- encryption at rest ----------

@pytest.mark.asyncio
async def test_category_and_disability_are_ciphertext_in_the_database_and_plaintext_through_the_api(api, login, db_session):
    h = await login("enc@test.invalid")
    r = await api.put("/profile", json=PROFILE, headers=h)
    assert r.status_code == 201, r.text
    pi = r.json()["personal_info"]
    assert (pi["category"], pi["is_pwbd"], pi["disability_percentage"], pi["disability_types"]) == ("OBC", True, 45, "OL")
    assert r.json()["reservation_certificates"][0]["kind"] == "OBC_NCL"

    raw = (await db_session.execute(text(
        "SELECT category, is_pwbd, disability_percentage, disability_types, date_of_birth FROM personal_info"))).one()
    for stored in raw[:4]:
        assert isinstance(stored, str) and stored.startswith(crypto.PREFIX)
    assert raw[0] != crypto.encrypt_value("OBC")          # random nonce: equal values do not give equal ciphertext
    kind = (await db_session.execute(text("SELECT kind FROM reservation_certificates"))).scalar_one()
    assert kind.startswith(crypto.PREFIX) and "OBC" not in kind
    assert crypto.decrypt_value(raw[0]) == "OBC" and crypto.decrypt_value(raw[1]) is True and crypto.decrypt_value(raw[2]) == 45

    # Eligibility still reads plaintext through the ORM (the decrypted category drives the OBC age limit)
    vacancy_id = await _law_officer(db_session)
    v = r.json()["version"]["id"]
    res = (await api.post("/eligibility", json={"profile_version_id": v, "vacancy_id": str(vacancy_id)}, headers=h)).json()
    age = next(e for e in res["evidence"] if e["check_name"] == "AGE")
    assert age["requirement"] == "max age 33 for OBC"

# The person acting on other people's records (reviewer, operator), not data about them
_ACTOR_COLUMNS = {"reviewer_id", "captured_by_id", "actor_id"}

async def _owned_raw_rows(db_session, email: str) -> dict:
    """Every row reachable from the person's users row through foreign keys, exactly as the database holds it (raw
    SQL, so nothing is decrypted): table -> [row dicts]."""
    uid = (await db_session.execute(text("SELECT id FROM users WHERE email = :e"), {"e": email})).scalar_one()
    owned, rows, grew = {"users": {uid}}, {"users": [dict((await db_session.execute(
        text("SELECT * FROM users WHERE id = :i"), {"i": uid})).mappings().one())]}, True
    while grew:
        grew = False
        for table in Base.metadata.sorted_tables:
            if "id" not in table.c:
                continue
            for fk in table.foreign_keys:
                parent, col = fk.column.table.name, fk.parent.name
                if parent not in owned or col in _ACTOR_COLUMNS or fk.column.name != "id":
                    continue
                stmt = text(f"SELECT * FROM {table.name} WHERE {col} IN :ids").bindparams(bindparam("ids", expanding=True))
                for r in (await db_session.execute(stmt, {"ids": list(owned[parent])})).mappings():
                    if r["id"] not in owned.setdefault(table.name, set()):
                        owned[table.name].add(r["id"])
                        rows.setdefault(table.name, []).append(dict(r))
                        grew = True
    return rows

@pytest.mark.asyncio
async def test_no_stored_text_holds_category_disability_or_certificate_kind_in_plaintext(api, login, db_session):
    """Profile claims, eligibility evidence, skill gaps and alerts quote category, PwBD % and certificate kind; a
    database dump without the key must not show them anywhere in the person's rows."""
    from govcareeros.backend.services import radar
    vacancy_id = await _law_officer(db_session)
    experience = [{"organization": "Test Chambers", "designation": "Legal Associate", "domain": "Legal", "start_date": "2017-01-01"}]
    # A: over the OBC limit of 33, within it with the PwBD relaxation (+10). B: 33 on the crucial date, at the limit.
    people = {"atrest-a@test.invalid": "1990-01-01", "atrest-b@test.invalid": "1993-06-01"}
    for email, dob in people.items():
        h = await login(email)
        profile = {**PROFILE, "personal_info": {**PROFILE["personal_info"], "date_of_birth": dob}, "experience": experience}
        v = (await api.put("/profile", json=profile, headers=h)).json()["version"]["id"]
        assert (await api.post("/eligibility", json={"profile_version_id": v, "vacancy_id": str(vacancy_id)}, headers=h)).status_code == 201
        assert (await api.get(f"/gaps?profile_version_id={v}&vacancy_id={vacancy_id}", headers=h)).status_code == 200
    users = (await db_session.execute(select(User.id).where(User.email.in_(list(people))))).scalars().all()
    await radar.run_radar(db_session, now=datetime(2026, 10, 1, tzinfo=timezone.utc), user_ids=list(users))   # before the closing date
    await db_session.commit()

    decrypted = []
    for email in people:
        rows = await _owned_raw_rows(db_session, email)
        for table in ("profile_facts", "eligibility_evidence", "skill_gaps", "user_alerts"):
            assert rows.get(table), (email, table)
        leaks = [(t, k, v) for t, rs in rows.items() for r in rs for k, v in r.items()
                 if isinstance(v, str) and not crypto.is_ciphertext(v) and any(m in v for m in ("OBC", "PwBD", "45%"))]
        assert leaks == [], leaks
        decrypted += [crypto.decrypt_value(r[k]) for t, k in (("profile_facts", "value"), ("eligibility_evidence", "profile_evidence"),
                                                              ("eligibility_evidence", "requirement"), ("skill_gaps", "profile_has"),
                                                              ("user_alerts", "body")) for r in rows[t]]
    # ... while the texts themselves do say it (so the check above looked at the right places)
    assert any(d.startswith("OBC_NCL certificate;") for d in decrypted)
    assert any("category OBC" in d and "PwBD 45%" in d for d in decrypted)
    assert any("the OBC limit for this post is 33" in d for d in decrypted)      # B's age-headroom alert
    assert any(d.startswith("max age 33 for OBC") for d in decrypted)

def test_crypto_refuses_missing_wrong_or_tampered_keys(monkeypatch, field_encryption_key):
    token = crypto.encrypt_value({"x": 1})
    assert crypto.decrypt_value(token) == {"x": 1}
    blob = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    with pytest.raises(crypto.FieldEncryptionError, match="failed authentication"):
        crypto.decrypt_value(blob)
    with pytest.raises(crypto.FieldEncryptionError, match="not ciphertext"):
        crypto.decrypt_value("OBC")
    other = "A" * 43 + "="
    with pytest.raises(crypto.FieldEncryptionError) as e:
        crypto.decrypt_value(token, key=other)
    assert other not in str(e.value) and field_encryption_key not in str(e.value)   # the key never appears in errors
    from pydantic import SecretStr
    monkeypatch.setattr(settings, "FIELD_ENCRYPTION_KEY", SecretStr(""))
    with pytest.raises(crypto.FieldEncryptionError, match="not configured"):
        crypto.encrypt_value("OBC")
    monkeypatch.setattr(settings, "FIELD_ENCRYPTION_KEY", SecretStr("c2hvcnQ="))
    with pytest.raises(crypto.FieldEncryptionError, match="32 bytes"):
        crypto.encrypt_value("OBC")
    assert "c2hvcnQ=" not in repr(settings) and "c2hvcnQ=" not in str(settings.FIELD_ENCRYPTION_KEY)

# ---------- export and erasure ----------

@pytest.mark.asyncio
async def test_export_returns_all_the_users_data_decrypted(api, login, db_session):
    vacancy_id = await _law_officer(db_session)
    h = await login("exp@test.invalid", consent=["ELIGIBILITY_CHECK"])
    v1 = (await api.put("/profile", json=PROFILE, headers=h)).json()["version"]["id"]
    await api.post("/eligibility", json={"profile_version_id": v1, "vacancy_id": str(vacancy_id)}, headers=h)
    other = await login("other@test.invalid")
    await api.put("/profile", json={"personal_info": {"full_name": "Someone Else", "category": "SC"}}, headers=other)

    r = await api.get("/privacy/export", headers=h)
    assert r.status_code == 200
    data = r.json()
    assert data["account"]["email"] == "exp@test.invalid" and "hashed_password" not in data["account"]
    assert [c["purpose"] for c in data["consents"]] == ["ELIGIBILITY_CHECK"]
    [version] = data["profile_versions"]
    assert version["id"] == v1
    pi = version["sections"]["personal_info"]
    assert (pi["category"], pi["is_pwbd"], pi["disability_percentage"]) == ("OBC", True, 45)
    assert version["sections"]["reservation_certificates"][0]["kind"] == "OBC_NCL"
    assert version["facts"] and version["facts"][0]["evidence"]
    assert len(data["eligibility_results"]) == 1 and data["eligibility_results"][0]["items"]
    assert data["login_sessions"] and all("token_hash" not in t for t in data["login_sessions"])
    assert "Someone Else" not in json.dumps(data)

@pytest.mark.asyncio
async def test_erase_closes_the_account_then_the_due_erase_deletes_the_data(api, login, db_session):
    vacancy_id = await _law_officer(db_session)
    h = await login("erase@test.invalid", consent=["ELIGIBILITY_CHECK"])
    await _consent(api, h, purpose="ALERTS")
    v1 = (await api.put("/profile", json=PROFILE, headers=h)).json()["version"]["id"]
    await api.post("/eligibility", json={"profile_version_id": v1, "vacancy_id": str(vacancy_id)}, headers=h)
    keep = await login("keep@test.invalid")
    kv = (await api.put("/profile", json=PROFILE, headers=keep)).json()["version"]["id"]
    uid = (await db_session.execute(select(User.id).where(User.email == "erase@test.invalid"))).scalar_one()
    folder = settings.storage_dir / "digests" / str(uid)
    folder.mkdir(parents=True)
    (folder / "2026-10-06.html").write_text("digest", encoding="utf-8")

    assert (await api.post("/privacy/erase", json={"confirm": "yes"}, headers=h)).status_code == 422
    r = await api.post("/privacy/erase", json={"confirm": "ERASE"}, headers=h)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["grace_days"] == settings.ERASURE_GRACE_DAYS
    due = datetime.fromisoformat(body["erasure_due_at"])
    # Soft delete: logged out everywhere, cannot log in, every consent withdrawn; data still there
    assert (await api.get("/profile", headers=h)).status_code == 401
    assert (await api.post("/auth/login", json={"email": "erase@test.invalid", "password": "correct-horse-battery"})).status_code == 401
    user = await db_session.get(User, uid)
    consents = (await db_session.execute(select(ConsentRecord).where(ConsentRecord.user_id == uid))).scalars().all()
    assert len(consents) == 2 and all(c.withdrawn_at is not None for c in consents)
    assert await db_session.get(ProfileVersion, uuid.UUID(v1)) is not None

    # Not yet due: nothing is erased
    assert (await rights.erase_due(db_session, now=due - timedelta(minutes=1)))["erased"] == []
    # A legal hold blocks the erase even when due
    user.legal_hold_reason = "test hold"
    await db_session.flush()
    assert await rights.erase_due(db_session, now=due) == {"erased": [], "held": [str(user.id)]}
    user.legal_hold_reason = None
    report = await rights.erase_due(db_session, now=due)
    assert report == {"erased": [str(user.id)], "held": []}
    await db_session.commit()

    # Every table that holds this person's data has nothing left for them; the stub row and consent proof remain
    stub = await db_session.get(User, user.id)
    assert stub.erased_at is not None and stub.email == f"erased-{user.id}@erased.invalid" and stub.full_name is None
    for model in rights.OWNED_BY_USER:
        assert (await db_session.execute(select(model).where(model.user_id == user.id))).first() is None, model
    assert len((await db_session.execute(select(PersonalInfo))).scalars().all()) == 1   # only keep@'s
    assert len((await db_session.execute(select(ReservationCertificate))).scalars().all()) == 1
    assert len((await db_session.execute(select(ConsentRecord).where(ConsentRecord.user_id == user.id))).scalars().all()) == 2
    assert not folder.exists()
    # The other user's data is untouched
    assert (await api.get("/profile", headers=keep)).json()["version"]["id"] == kv
    # Running again does nothing
    assert (await rights.erase_due(db_session, now=due + timedelta(days=1)))["erased"] == []

def test_every_table_referencing_users_is_erased_or_documented_as_retained():
    """A new table keyed by users.id or profile_versions.id must be added to the erase (and export) or to RETAINED."""
    erased = {m.__tablename__ for m in rights.OWNED_BY_USER}
    handled_children = {"resume_claims", "eligibility_evidence", "exam_eligibility_evidence", "application_status_history",
                        "study_plan_items", "profile_evidence", "profile_facts"}
    from govcareeros.backend.repositories.profile_repository import VERSIONED_ENTITIES
    handled_children |= {m.__tablename__ for m in VERSIONED_ENTITIES}
    retained_actor_columns = {("profile_facts", "reviewer_id"), ("extraction_drafts", "reviewer_id"),
                              ("recruitment_notices", "reviewer_id"), ("consent_records", "captured_by_id"),
                              ("consent_records", "user_id"), ("review_audit_log", "actor_id")}
    for table in Base.metadata.sorted_tables:
        for fk in table.foreign_keys:
            if fk.column.table.name in ("users", "profile_versions"):
                col = fk.parent.name
                assert (table.name in erased or table.name in handled_children or (table.name, col) in retained_actor_columns), \
                    f"{table.name}.{col} references {fk.column.table.name}: erase it in privacy/rights.py or document it in RETAINED"
    for key in ("consent_records", "review_audit_log"):
        assert key in rights.RETAINED
