"""
Jurisdiction and reuse policy per source (the real registry), and the attribution + disclaimer on every
response that states a vacancy or exam fact: real UPSC Advt 12/2026, the real SSC CGL 2026 notice and its
re-open corrigendum (tests/fixtures/ssc/sources.json gives each PDF's official URL).
"""
import json
import re
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from govcareeros.backend.models import ReviewAuditLog, User
from govcareeros.backend.schemas.attribution import DISCLAIMER
from govcareeros.backend.services.document_ingestion import get_or_create_source, ingest_pdf_bytes
from govcareeros.backend.services.exam_corrigenda import ingest_corrigendum_pdf
from govcareeros.backend.services.exam_ingestion import ingest_exam_pdf
from govcareeros.backend.services.source_discovery import sync_registry
from govcareeros.backend.sources import ssc, upsc
from govcareeros.backend.sources.registry import SOURCES, STATE_PSC

FIXTURES = Path(__file__).parents[1] / "fixtures"
SSC = FIXTURES / "ssc"
SSC_URLS = json.loads((SSC / "sources.json").read_text(encoding="utf-8"))
SNAPSHOT = json.loads((FIXTURES / "notices_snapshot.json").read_text(encoding="utf-8"))["notices"]
ADVT_12 = "https://www.upsc.gov.in/sites/default/files/AdvtNo-12-2026-Engl-250926_0.pdf"

def test_disclaimer_is_the_official_notification_prevails_text():
    assert DISCLAIMER.startswith("Information from official notifications; the official notification prevails")

def test_every_registry_source_has_a_jurisdiction():
    by_name = {s.name: s.jurisdiction for s in SOURCES}
    assert all(j and re.fullmatch(r"IN(-[A-Z]{2})?", j) for j in by_name.values()), by_name
    # Union bodies are "IN"; only state public service commissions carry a state code
    assert {s.jurisdiction for s in SOURCES if s.category != STATE_PSC} == {"IN"}
    assert {s.name: s.jurisdiction for s in SOURCES if s.category == STATE_PSC} == {
        "UPPSC": "IN-UP", "BPSC": "IN-BR", "MPSC": "IN-MH", "RPSC": "IN-RJ", "TNPSC": "IN-TN", "GPSC": "IN-GJ",
        "HPSC": "IN-HR", "PPSC": "IN-PB", "APPSC": "IN-AP", "UKPSC": "IN-UK", "APSC": "IN-AS", "JKPSC": "IN-JK",
        "JPSC": "IN-JH", "CGPSC": "IN-CG"}
    # Every source that appears in the captured notices snapshot resolves too
    assert {n["source"] for n in SNAPSHOT} <= set(by_name)

@pytest.mark.asyncio
async def test_sync_registry_sets_jurisdiction_and_keeps_reviewer_reuse_policy(db_session):
    sources = await sync_registry(db_session)
    assert sources["HPSC"].jurisdiction == "IN-HR" and sources["UPSC"].jurisdiction == "IN"
    assert {s.reuse_level for s in sources.values()} == {"UNKNOWN"}
    assert all(s.copyright_policy_url is None and s.reuse_checked_at is None for s in sources.values())
    await db_session.commit()

    # An older row without a jurisdiction gets it on the next sync; a reviewer's reuse fields survive the sync
    upsc_row = sources["UPSC"]
    upsc_row.jurisdiction = None
    upsc_row.copyright_policy_url = "https://www.upsc.gov.in/website-policy"
    await db_session.commit()
    again = await sync_registry(db_session)
    assert again["UPSC"].jurisdiction == "IN"
    assert again["UPSC"].copyright_policy_url == "https://www.upsc.gov.in/website-policy"

@pytest.mark.asyncio
async def test_reviewer_records_reuse_policy_without_fetching(api, login, db_session):
    await sync_registry(db_session, [s for s in SOURCES if s.name in {"UPSC", "HPSC"}])
    await db_session.commit()
    h = await login("reviewer@example.com")
    assert (await api.get("/admin/sources", headers=h)).status_code == 403
    (await db_session.execute(select(User).where(User.email == "reviewer@example.com"))).scalar_one().is_reviewer = True
    await db_session.commit()

    rows = (await api.get("/admin/sources", headers=h)).json()
    assert [(r["name"], r["jurisdiction"], r["reuse_level"]) for r in rows] == [("UPSC", "IN", "UNKNOWN"), ("HPSC", "IN-HR", "UNKNOWN")]
    assert [r["name"] for r in (await api.get("/admin/sources", params={"jurisdiction": "IN-HR"}, headers=h)).json()] == ["HPSC"]
    upsc_id = rows[0]["id"]

    # The "Website Policies" link in the footer of the captured UPSC recruitment page
    footer = (FIXTURES / "upsc" / "recruitment-advertisement.html").read_text(encoding="utf-8")
    assert 'href="/website-policy"' in footer
    policy_url = "https://www.upsc.gov.in/website-policy"

    # A level is never stored without the page it was read from
    r = await api.put(f"/admin/sources/{upsc_id}/reuse-policy", json={"reuse_level": "LIBERAL"}, headers=h)
    assert r.status_code == 422
    assert (await api.put(f"/admin/sources/{upsc_id}/reuse-policy", json={"reuse_level": "OPEN"}, headers=h)).status_code == 422
    assert (await api.put(f"/admin/sources/{upsc_id}/reuse-policy",
                          json={"reuse_level": "UNKNOWN", "copyright_policy_url": "javascript:alert(1)"}, headers=h)).status_code == 422

    # Where the policy lives is recorded; the level stays UNKNOWN until someone has read it
    r = await api.put(f"/admin/sources/{upsc_id}/reuse-policy",
                      json={"reuse_level": "UNKNOWN", "copyright_policy_url": policy_url, "note": "footer link"}, headers=h)
    assert r.status_code == 200, r.text
    row = r.json()
    assert row["copyright_policy_url"] == policy_url and row["reuse_level"] == "UNKNOWN" and row["reuse_checked_at"]
    (entry,) = (await db_session.execute(select(ReviewAuditLog).where(ReviewAuditLog.action == "REUSE_POLICY_SET"))).scalars().all()
    assert entry.object_type == "SOURCE" and str(entry.object_id) == upsc_id and entry.note == "footer link"
    assert json.loads(entry.detail)["to"]["copyright_policy_url"] == policy_url
    audit = (await api.get("/admin/audit", params={"object_type": "SOURCE"}, headers=h)).json()
    assert [a["action"] for a in audit["items"]] == ["REUSE_POLICY_SET"]

@pytest.mark.asyncio
async def test_vacancy_responses_carry_attribution(api, login, db_session):
    source = await get_or_create_source(db_session, upsc.UPSC_SOURCE)
    await ingest_pdf_bytes(db_session, source, ADVT_12, (FIXTURES / "upsc" / "AdvtNo-12-2026-Engl-250926_0.pdf").read_bytes(),
                           source_url=upsc.LISTING_URL)
    await db_session.commit()
    h = await login("asha@example.com")

    listing = (await api.get("/vacancies", headers=h)).json()
    assert listing and all(v["disclaimer"] == DISCLAIMER for v in listing)
    law_officer = next(v for v in listing if v["external_ref"] == "26091203226")
    att = law_officer["attribution"]
    assert (att["source_name"], att["organization"], att["jurisdiction"]) == ("UPSC", "Union Public Service Commission", "IN")
    assert att["official_page_url"] == upsc.LISTING_URL and att["document_url"] == ADVT_12
    assert datetime.fromisoformat(att["document_retrieved_at"])

    detail = (await api.get(f"/vacancies/{law_officer['id']}", headers=h)).json()
    assert detail["attribution"] == att and detail["disclaimer"] == DISCLAIMER
    # The page is the one the post name was read from
    post_name = next(p for p in detail["provenance"] if p["field_name"] == "post_name")
    assert att["page"] == post_name["page"] and att["page"] is not None
    # Existing fields are unchanged
    assert detail["post_name"] == law_officer["post_name"] and detail["requirements"]

@pytest.mark.asyncio
async def test_exam_and_exam_version_responses_carry_attribution(api, login, db_session):
    source = await get_or_create_source(db_session, ssc.SSC_SOURCE)
    cgl, reopen = "Notice_of_adv_cgl_2026.pdf", "CGLE_Reopen_23062026.pdf"
    exam_id = (await ingest_exam_pdf(db_session, source, SSC_URLS[cgl]["url"], (SSC / cgl).read_bytes())).exam_id
    await ingest_corrigendum_pdf(db_session, source, SSC_URLS[reopen]["url"], (SSC / reopen).read_bytes(), title=SSC_URLS[reopen]["title"])
    await db_session.commit()
    h = await login("reader@example.com")

    (summary,) = (await api.get("/exams", params={"q": "Combined Graduate"}, headers=h)).json()
    att = summary["attribution"]
    assert (att["source_name"], att["organization"], att["jurisdiction"]) == ("SSC", "Staff Selection Commission", "IN")
    assert att["official_page_url"] == ssc.NOTICE_BOARD_PAGE and att["document_url"] == SSC_URLS[cgl]["url"]
    assert summary["disclaimer"] == DISCLAIMER

    detail = (await api.get(f"/exams/{exam_id}", headers=h)).json()
    name = next(p for p in detail["provenance"] if p["field_name"] == "name")
    assert detail["attribution"] == att and att["page"] == name["page"] and detail["disclaimer"] == DISCLAIMER
    assert detail["posts"] and detail["name"] == summary["name"]

    (version,) = (await api.get(f"/exams/{exam_id}/versions", headers=h)).json()
    vatt = version["attribution"]
    assert vatt["document_url"] == SSC_URLS[reopen]["url"] and vatt["source_name"] == "SSC"
    assert vatt["page"] is None and version["disclaimer"] == DISCLAIMER
    assert version["change_type"] == "REOPENED_WINDOW,DATE_CHANGE"
