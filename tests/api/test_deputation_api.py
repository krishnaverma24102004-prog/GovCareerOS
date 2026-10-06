"""
Deputation circulars stored, parsed and assessed through the API, on the real circulars captured on 2026-10-05
(tests/fixtures/deputation/meta.json: URL, capture time and sha256 of each file; see
tests/eligibility/test_deputation_circulars.py for what each one states).

CBSE and ICSIL are not in the source registry; their GovernmentSource rows are made here from the publisher named
on the circular and the domain it was fetched from. The DoPT listing title is the row of the captured listing page.
Profiles are inputs to the rules (test-only users), not document facts.
"""
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import pytest
from sqlalchemy import select

from govcareeros.backend.models import DeputationCircular, RecruitmentNotice
from govcareeros.backend.parsing.deputation_circular import PARSER_VERSION, from_json, parse_circular
from govcareeros.backend.documents.pdf_text import extract_pages
from govcareeros.backend.services import deputation_circulars as svc
from govcareeros.backend.services.document_ingestion import get_or_create_source, store_raw_document
from govcareeros.backend.services.source_discovery import sync_registry, url_hash
from govcareeros.backend.sources import upsc
from govcareeros.backend.sources.registry import SOURCES

FIXTURES = Path(__file__).parents[1] / "fixtures"
DEP = FIXTURES / "deputation"
META = json.loads((DEP / "meta.json").read_text(encoding="utf-8"))
CBSE = "Details_Advertisement_Deputation_15012026.pdf"
NATGRID = "NATGRIDVACAN_13062025.pdf"
DOPT = "dopt_despatch_rider_A-12026-1-2023-AD-III.pdf"
ICSIL = "Advertisement_-_Deputation_Posts25_9_2026.pdf"
DOPT_TITLE = "Filling up of 01 post of Despatch Rider (DR) on deputation basis in DoPT, New Delhi-reg."
UPSC_ADVT = "AdvtNo-12-2026-Engl-250926_0.pdf"

def _extra_source(name, organization, url):
    host = urlparse(url).hostname
    return {"name": name, "organization": organization, "category": "AUTONOMOUS_BODY_PSU",
            "official_domain": host[4:] if host.startswith("www.") else host, "recruitment_url": url,
            "source_type": "DOCUMENT", "jurisdiction": "IN"}

async def _store_all(db_session):
    """The four circulars and one UPSC advertisement (not a deputation circular) as stored RawDocuments."""
    registry = await sync_registry(db_session, [s for s in SOURCES if s.name in {"MHA", "DOPT"}])
    sources = {
        CBSE: await get_or_create_source(db_session, _extra_source("CBSE", "Central Board of Secondary Education", META[CBSE]["url"])),
        ICSIL: await get_or_create_source(db_session, _extra_source("ICSIL", "Intelligent Communication Systems India Ltd.", META[ICSIL]["url"])),
        NATGRID: registry["MHA"], DOPT: registry["DOPT"],
    }
    raws = {}
    for name, source in sources.items():
        raws[name], _ = await store_raw_document(db_session, source, META[name]["url"], (DEP / name).read_bytes(), 200, "application/pdf")
    now = datetime.now(timezone.utc)
    listing = META["dopt_vacancy_circulars.html"]["url"]
    db_session.add(RecruitmentNotice(source_id=registry["DOPT"].id, url=META[DOPT]["url"], url_hash=url_hash(META[DOPT]["url"]),
                                     title=DOPT_TITLE, document_type="PDF", notice_kind="VACANCY", found_on_url=listing,
                                     first_seen_at=now, last_seen_at=now, raw_document_id=raws[DOPT].id))
    upsc_source = await get_or_create_source(db_session, upsc.UPSC_SOURCE)
    raws[UPSC_ADVT], _ = await store_raw_document(db_session, upsc_source, f"https://www.upsc.gov.in/sites/default/files/{UPSC_ADVT}",
                                                  (FIXTURES / "upsc" / UPSC_ADVT).read_bytes(), 200, "application/pdf")
    await db_session.flush()
    return raws

@pytest.mark.asyncio
async def test_stored_pdfs_become_circulars(db_session):
    raws = await _store_all(db_session)
    run = await svc.parse_stored(db_session)
    assert set(run.stored) == {raws[n].id for n in (CBSE, NATGRID, DOPT, ICSIL)}
    assert list(run.skipped) == [raws[UPSC_ADVT].id] and "not DEPUTATION" in run.skipped[raws[UPSC_ADVT].id]
    rows = {r.raw_document_id: r for r in (await db_session.execute(select(DeputationCircular))).scalars()}
    cbse = rows[raws[CBSE].id]
    assert (cbse.parser_version, cbse.mode, cbse.closing_kind, cbse.closing_date.isoformat(), cbse.post_count) == (
        PARSER_VERSION, "DEPUTATION", "DATE", "2026-02-13", 3)
    natgrid = rows[raws[NATGRID].id]
    assert (natgrid.closing_kind, natgrid.closing_date, natgrid.closing_days) == ("EMPLOYMENT_NEWS_DAYS", None, 45)
    assert rows[raws[DOPT].id].title == DOPT_TITLE and cbse.title is None
    # The stored terms are the parse of the stored PDF, quotes and pages included
    for name in (CBSE, NATGRID, DOPT, ICSIL):
        row = rows[raws[name].id]
        assert from_json(json.loads(row.terms)) == parse_circular(extract_pages((DEP / name).read_bytes()))
    gs = json.loads(cbse.terms)["govt_service"]
    assert gs["value"] == {"mode": "DEPUTATION", "open_to": []} and gs["page"] and gs["quote"]

    # Already parsed by this parser version: left alone
    again = await svc.parse_stored(db_session, [raws[CBSE].id])
    assert again.unchanged == [raws[CBSE].id] and not again.stored
    # Nothing new to parse (the skipped advertisement is tried again, and skipped again)
    assert (await svc.parse_stored(db_session)).stored == []

async def _parsed(db_session):
    raws = await _store_all(db_session)
    await svc.parse_stored(db_session)
    await db_session.commit()
    rows = {r.raw_document_id: r for r in (await db_session.execute(select(DeputationCircular))).scalars()}
    # Ids only: a request that fails rolls the shared session back and expires loaded rows
    return {name: rows[raw.id].id for name, raw in raws.items() if raw.id in rows}

OFFICER = {"full_name": "Test Officer", "date_of_birth": "1975-03-01", "category": "UR", "nationality": "Indian",
           "is_central_govt_employee": True, "current_pay_level": 11, "current_level_regular_since": "2020-01-01",
           "organisation_cadre": "Indian Audit & Accounts Department", "last_deputation_end": "2024-03-31"}

@pytest.mark.asyncio
async def test_list_and_detail(api, login, db_session):
    circulars = await _parsed(db_session)
    h = await login("officer@example.com")
    assert (await api.get("/deputation/circulars")).status_code == 401
    listed = (await api.get("/deputation/circulars", headers=h)).json()
    # Newest closing date first; the Employment-News circular (date unknown) last
    assert [c["id"] for c in listed] == [str(circulars[n]) for n in (ICSIL, DOPT, CBSE, NATGRID)]
    today = svc.today()
    by_id = {c["id"]: c for c in listed}
    icsil, natgrid, cbse = (by_id[str(circulars[n])] for n in (ICSIL, NATGRID, CBSE))
    assert icsil["closing_date"] == "2026-10-19" and icsil["status"] == ("OPEN" if today.isoformat() <= "2026-10-19" else "CLOSED")
    assert cbse["status"] == "CLOSED" and cbse["closing"]["quote"] == "submitted w.e.f. 15.01.2026 to 13.02.2026"
    assert natgrid["status"] == "CLOSING_DATE_UNKNOWN" and natgrid["closing_days"] == 45 and natgrid["mode"][0] == "DEPUTATION_STC"
    assert [(p["name"]["value"], p["name"]["page"], p["pay_level"]["value"]) for p in cbse["posts"]] == [
        ("ADDITIONAL INTERNAL AUDITOR & FINANCIAL ADVISOR", 5, 12), ("SENIOR ACCOUNTS OFFICER", 6, 11), ("UNDER SECRETARY (LEGAL)", 7, 11)]
    assert cbse["attribution"]["document_url"] == META[CBSE]["url"] and cbse["attribution"]["source_name"] == "CBSE"
    assert cbse["document_content_hash"] == "sha256:" + META[CBSE]["sha256"]
    assert "official notification prevails" in cbse["disclaimer"]
    dopt = by_id[str(circulars[DOPT])]
    assert dopt["title"] == DOPT_TITLE and dopt["attribution"]["official_page_url"] == META["dopt_vacancy_circulars.html"]["url"]

    open_only = (await api.get("/deputation/circulars?open_only=true", headers=h)).json()
    assert [c["id"] for c in open_only] == [c["id"] for c in listed if c["status"] == "OPEN"]

    detail = (await api.get(f"/deputation/circulars/{circulars[CBSE]}", headers=h)).json()
    assert detail["terms"]["max_age"] == {"value": {"years": 56, "on": "CLOSING_DATE"}, "page": 1,
                                          "quote": detail["terms"]["max_age"]["quote"]}
    assert detail["terms"]["max_age"]["quote"].endswith("is 56 years as on the closing date of receipt of application")
    assert detail["terms"]["documents"]["APAR_LAST_5_YEARS"] == {"value": "APAR_LAST_5_YEARS", "page": 3, "quote": "ACRs of last 5 years"}
    assert (await api.get(f"/deputation/circulars/{uuid.uuid4()}", headers=h)).status_code == 404

@pytest.mark.asyncio
async def test_assess_a_serving_officer(api, login, db_session):
    circulars = await _parsed(db_session)
    h = await login("officer@example.com")
    version = (await api.put("/profile", json={"personal_info": OFFICER}, headers=h)).json()["version"]["id"]
    url = f"/deputation/circulars/{circulars[CBSE]}/assess"
    assert (await api.post(url, json={"profile_version_id": version})).status_code == 401

    r = await api.post(url, json={"profile_version_id": version}, headers=h)
    assert r.status_code == 200, r.text
    res = r.json()
    assert (res["closing_date"], res["closing_status"], res["as_of"]) == ("2026-02-13", "CLOSED", svc.today().isoformat())
    iafa = res["posts"][0]
    assert (iafa["post"], iafa["post_page"], iafa["pay_level"]) == ("ADDITIONAL INTERNAL AUDITOR & FINANCIAL ADVISOR", 5, 12)
    checks = {o["check"]: o for o in iafa["outcomes"]}
    assert checks["DEPUTATION_AGE"]["outcome"] == "MET" and checks["DEPUTATION_AGE"]["data"]["age"] == 50
    assert checks["DEPUTATION_SERVICE"]["outcome"] == "MET" and checks["DEPUTATION_SERVICE"]["source_page"] == 5
    assert checks["ANALOGOUS_POST"]["outcome"] == "NEEDS_HUMAN_REVIEW"
    assert checks["ANALOGOUS_POST"]["requirement"] == "Holding analogous post on regular basis"
    assert "Indian Audit & Accounts Department" in checks["ANALOGOUS_POST"]["profile_evidence"]
    assert checks["COOLING_OFF"]["outcome"] == "NEEDS_HUMAN_REVIEW" and checks["COOLING_OFF"]["data"]["cooling_off_ends"] == "2027-03-31"
    assert checks["GOVT_SERVICE"]["outcome"] == "CONDITIONAL"
    # Reviewer items keep every post out of ELIGIBLE
    assert [p["status"] for p in res["posts"]] == ["MANUAL_REVIEW"] * 3
    assert [i["code"] for i in res["forwarding_checklist"]] == ["PROPER_CHANNEL", "APAR_LAST_5_YEARS", "VIGILANCE_CLEARANCE",
                                                                "PENALTY_STATEMENT", "NOC", "CLOSING"]
    assert all(i["page"] and i["quote"] for i in res["forwarding_checklist"])
    assert "official notification prevails" in res["disclaimer"] and res["attribution"]["document_url"] == META[CBSE]["url"]

    one = (await api.post(url, json={"profile_version_id": version, "post_index": 1}, headers=h)).json()
    assert [p["post"] for p in one["posts"]] == ["SENIOR ACCOUNTS OFFICER"]
    assert (await api.post(url, json={"profile_version_id": version, "post_index": 3}, headers=h)).status_code == 404

    # Employment-News closing: the date is unknown, so age is not decided
    natgrid = (await api.post(f"/deputation/circulars/{circulars[NATGRID]}/assess", json={"profile_version_id": version},
                              headers=h)).json()
    assert natgrid["closing_date"] is None and natgrid["closing_status"] == "CLOSING_DATE_UNKNOWN"
    assert {o["outcome"] for p in natgrid["posts"] for o in p["outcomes"] if o["check"] == "DEPUTATION_AGE"} == {"UNKNOWN"}
    # An analogous-post clause is never decided, in any circular
    for name in (CBSE, NATGRID, DOPT, ICSIL):
        res = (await api.post(f"/deputation/circulars/{circulars[name]}/assess", json={"profile_version_id": version},
                              headers=h)).json()
        assert {o["outcome"] for p in res["posts"] for o in p["outcomes"] if o["check"] == "ANALOGOUS_POST"} <= {"NEEDS_HUMAN_REVIEW"}
        assert all(p["status"] != "ELIGIBLE" for p in res["posts"])
    # ICSIL's post table is not parsed into posts; its checklist still comes from the circular
    icsil = (await api.post(f"/deputation/circulars/{circulars[ICSIL]}/assess", json={"profile_version_id": version}, headers=h)).json()
    assert icsil["posts"] == [] and icsil["forwarding_checklist"][0]["quote"] == "through proper channel"

    # Another user's profile version is not found
    other = await login("someone@example.com")
    assert (await api.post(url, json={"profile_version_id": version}, headers=other)).status_code == 404

@pytest.mark.asyncio
async def test_organisation_cadre_length_is_bounded(api, login):
    h = await login("officer@example.com")
    r = await api.put("/profile", json={"personal_info": {**OFFICER, "organisation_cadre": "x" * 256}}, headers=h)
    assert r.status_code == 422
    assert (await api.put("/profile", json={"personal_info": {**OFFICER, "organisation_cadre": "x" * 255}}, headers=h)).status_code == 201
