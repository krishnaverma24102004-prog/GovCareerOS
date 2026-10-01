"""
Public notice check (POST /verify) on real data only:

  fakes      tests/fixtures/fake_notices.json: fake recruitment websites and notice wording debunked by PIB Fact
             Check, ministries or police, each with the news / fact-check page that reported it
  genuine    tests/fixtures/notices_snapshot.json (282 notice links discovered on official sites) and the real
             UPSC advertisements 11/2026 and 12/2026 (tests/fixtures/upsc)
"""
import json
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from govcareeros.backend.models import RecruitmentNotice
from govcareeros.backend.services import notice_verifier
from govcareeros.backend.services.document_ingestion import get_or_create_source, ingest_pdf_bytes
from govcareeros.backend.services.notice_verifier import (
    FOUND, LOOKALIKE, NOT_FOUND, UNOFFICIAL, check_domain, red_flags, verify,
)
from govcareeros.backend.services.source_discovery import sync_registry, url_hash
from govcareeros.backend.sources import upsc
from govcareeros.backend.sources.registry import SOURCES

FIXTURES = Path(__file__).parents[1] / "fixtures"
FAKES = json.loads((FIXTURES / "fake_notices.json").read_text(encoding="utf-8"))
ITEMS = {i["id"]: i for i in FAKES["items"]}
NOTICES = json.loads((FIXTURES / "notices_snapshot.json").read_text(encoding="utf-8"))["notices"]
UPSC_DIR = FIXTURES / "upsc"
ADVT_12 = "AdvtNo-12-2026-Engl-250926_0.pdf"
ADVT_11 = "AdvtNo-11-2026-Engl-100926.pdf"

def upsc_url(name: str) -> str:
    return f"https://www.upsc.gov.in/sites/default/files/{name}"

async def load_official(db_session):
    """Every snapshot notice link plus both UPSC advertisements parsed into vacancies (with provenance)."""
    sources = await sync_registry(db_session, [s for s in SOURCES if s.name in {n["source"] for n in NOTICES}])
    for n in NOTICES:
        seen = datetime.fromisoformat(n["first_seen_at"])
        db_session.add(RecruitmentNotice(
            source_id=sources[n["source"]].id, url=n["url"], url_hash=url_hash(n["url"]), title=n["title"],
            document_type=n["document_type"], notice_kind=n["notice_kind"], first_seen_at=seen, last_seen_at=seen,
            review_status=n["review_status"]))
    source = await get_or_create_source(db_session, upsc.UPSC_SOURCE)
    for name in (ADVT_12, ADVT_11):
        await ingest_pdf_bytes(db_session, source, upsc_url(name), (UPSC_DIR / name).read_bytes())
    await db_session.commit()

def test_fixture_is_cited_and_large_enough():
    assert len(FAKES["items"]) >= 10
    for item in FAKES["items"]:
        assert item["debunk_url"].startswith("https://") and item["debunk_date"], item["id"]
        assert len(item["value"].split()) <= 25, item["id"]
    assert sum(i["kind"] == "DOMAIN" for i in FAKES["items"]) >= 10

# ------------------------------------------------------------------ fake domains

# The site each fake imitates is in our registry only for India Post (indiapost.gov.in); the others imitate
# schemes and bodies we do not track, so they are unofficial rather than look-alikes of a tracked domain
EXPECTED_DOMAIN = {
    "rashtriyaunnatikendra": UNOFFICIAL,
    "bsnltowerindia": UNOFFICIAL,
    "viksitbharatrozgaryojana-org": UNOFFICIAL,
    "pmviksitbharatrozgaryojana-com": UNOFFICIAL,
    "rsby-ind-in": UNOFFICIAL,
    "sarvashikshaabhiyan-com": UNOFFICIAL,
    "sarvashiksha-online": UNOFFICIAL,
    "samagra-shikshaabhiyan-co-in": UNOFFICIAL,
    "shikshaabhiyan-org-in": UNOFFICIAL,
    "ssabhiyan-org": UNOFFICIAL,
    "indiapostgds-online": LOOKALIKE,
}

def test_every_fake_domain_in_the_fixture_has_an_expected_verdict():
    assert set(EXPECTED_DOMAIN) == {i["id"] for i in FAKES["items"] if i["kind"] == "DOMAIN"}

@pytest.mark.asyncio
@pytest.mark.parametrize("item_id", sorted(EXPECTED_DOMAIN))
async def test_debunked_fake_domains(db_session, item_id):
    await load_official(db_session)
    item = ITEMS[item_id]
    result = await verify(db_session, url=item["value"])
    assert result.verdict == EXPECTED_DOMAIN[item_id], (item_id, result.url)
    assert result.matches == []
    assert "fake" not in result.message.lower()
    assert "not found on official sources we track" in result.message.lower()

def test_lookalike_names_the_domain_it_imitates():
    check = check_domain(ITEMS["indiapostgds-online"]["value"])
    assert check.status == "LOOKALIKE" and check.official_domain == "indiapost.gov.in"
    assert any("indiapost" in r for r in check.reasons)
    # The debunk's '(dot)' spelling is read as a dot
    assert check_domain(ITEMS["bsnltowerindia"]["value"]).host == "bsnltowerindia.com"

@pytest.mark.parametrize("value, official", [
    ("https://pmvbry.epfindia.gov.in", "epfindia.gov.in"),
    ("https://www.jointerritorialarmy.gov.in/", "gov.in"),
    ("indiapost.gov.in", "indiapost.gov.in"),
])
def test_genuine_sites_named_in_the_debunks_are_official(value, official):
    assert {g["value"] for g in FAKES["genuine_counterparts"]} >= {value}
    check = check_domain(value)
    assert check.status == "OFFICIAL" and check.official_domain == official

@pytest.mark.asyncio
async def test_official_domain_not_yet_indexed_is_found_with_a_caveat(db_session):
    await load_official(db_session)
    result = await verify(db_session, url="https://www.jointerritorialarmy.gov.in/")
    assert result.verdict == FOUND and result.matches == []
    assert "not indexed" in result.message

# ------------------------------------------------------------------ fake notice wording

@pytest.mark.asyncio
@pytest.mark.parametrize("item_id", ["field-assistant-1736", "rsby-posts", "rashtriyaunnatikendra-fee-all-categories",
                                     "rashtriyaunnatikendra-sitting-arrangement"])
async def test_debunked_notice_wording_is_not_found(db_session, item_id):
    await load_official(db_session)
    result = await verify(db_session, text=ITEMS[item_id]["value"])
    assert result.verdict == NOT_FOUND
    assert result.matches == []
    assert "fake" not in result.message.lower()

@pytest.mark.asyncio
async def test_debunked_site_and_its_wording_together(db_session):
    await load_official(db_session)
    result = await verify(db_session, url=ITEMS["rsby-ind-in"]["value"], text=ITEMS["rsby-posts"]["value"])
    assert result.verdict == UNOFFICIAL and result.matches == []

@pytest.mark.parametrize("item_id, code", [
    ("rashtriyaunnatikendra-fee-all-categories", "FEE_FROM_ALL_CATEGORIES"),
    ("rashtriyaunnatikendra-sitting-arrangement", "FEE_FOR_SITTING_ARRANGEMENT"),
])
def test_red_flags_on_debunked_wording(item_id, code):
    flags = red_flags(ITEMS[item_id]["value"])
    assert [f.code for f in flags] == [code]
    assert flags[0].quote and ITEMS[item_id]["debunk_url"] in flags[0].citations

def test_every_red_flag_rule_cites_a_debunk():
    for rule in notice_verifier.RED_FLAG_RULES:
        assert rule.citations and all(c.startswith("https://") for c in rule.citations), rule.code

@pytest.mark.parametrize("name", [ADVT_12, ADVT_11])
def test_genuine_upsc_advertisements_raise_no_red_flags(name):
    # They take the fee by "UPI payment" through the official portal, which is not a payment to a UPI ID
    text = notice_verifier.pdf_text((UPSC_DIR / name).read_bytes())
    assert "upi payment" in text.lower()
    assert red_flags(text) == []

# ------------------------------------------------------------------ genuine notices

@pytest.mark.asyncio
async def test_every_snapshot_link_is_found(db_session):
    await load_official(db_session)
    for url in sorted({n["url"] for n in NOTICES}):
        result = await verify(db_session, url=url)
        assert result.verdict == FOUND, url
        assert any(m.official_url == url and "official link" in m.matched_on for m in result.matches), url

@pytest.mark.asyncio
async def test_snapshot_titles_pasted_as_text_are_found(db_session):
    await load_official(db_session)
    titled = {n["url"]: n["title"] for n in NOTICES if len(notice_verifier._tokens(n["title"])) >= 5}
    assert len(titled) >= 100
    for url, title in sorted(titled.items()):
        result = await verify(db_session, text=title)
        assert result.verdict == FOUND, title
        assert any(m.official_url == url and "title" in m.matched_on for m in result.matches), title

@pytest.mark.asyncio
async def test_upsc_notice_found_by_advertisement_number_and_organisation(db_session):
    await load_official(db_session)
    title = next(n["title"] for n in NOTICES if n["url"].endswith("AssttPublProseGNCTD-Notice-Engl-170926Rev.pdf"))
    assert title == "Clarification for the post of Assistant Public Prosecutor in Delhi, vide Advt. No. 11 - 2026"
    result = await verify(db_session, text=f"Union Public Service Commission. {title}")
    assert result.verdict == FOUND
    assert result.advertisement_numbers == ["11/2026"]
    vacancies = [m for m in result.matches if m.kind == "VACANCY"]
    assert vacancies and all(m.advertisement_number == "11/2026" for m in vacancies)
    assert all(m.official_url == upsc_url(ADVT_11) and m.page and m.quote for m in vacancies)

# ------------------------------------------------------------------ API

@pytest.mark.asyncio
async def test_api_pdf_upload_finds_the_official_advertisement(api, db_session):
    await load_official(db_session)
    body = (UPSC_DIR / ADVT_12).read_bytes()
    r = await api.post("/verify", files={"file": (ADVT_12, body, "application/pdf")})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["verdict"] == FOUND and out["red_flags"] == []
    law = next(m for m in out["matches"] if m["kind"] == "VACANCY" and m["title"] == "Law Officer")
    assert law["official_url"] == upsc_url(ADVT_12) and law["advertisement_number"] == "12/2026"
    assert {"advertisement number", "post name", "organisation", "closing date"} <= set(law["matched_on"])
    assert law["closing_date"] == "2026-10-16"
    assert law["page"] == 6 and "Three vacancies for the post of Law Officer" in law["quote"]

@pytest.mark.asyncio
async def test_api_is_public_and_never_fetches_the_url(api, db_session, monkeypatch):
    await load_official(db_session)

    async def no_network(*args, **kwargs):
        raise AssertionError("the verifier must not fetch anything")
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", no_network)

    r = await api.post("/verify", data={"url": ITEMS["indiapostgds-online"]["value"]})
    assert r.status_code == 200, r.text
    assert r.json()["verdict"] == LOOKALIKE
    assert r.json()["url"]["official_domain"] == "indiapost.gov.in"

@pytest.mark.asyncio
async def test_api_links_inside_pasted_text_are_checked(api, db_session):
    await load_official(db_session)
    item = ITEMS["sarvashikshaabhiyan-com"]
    r = await api.post("/verify", data={"text": f"{ITEMS['field-assistant-1736']['value']} {item['value']}"})
    out = r.json()
    assert out["verdict"] == NOT_FOUND
    assert [(c["host"], c["status"]) for c in out["links_in_text"]] == [("sarvashikshaabhiyan.com", "UNOFFICIAL")]

@pytest.mark.asyncio
async def test_api_rejects_bad_uploads(api, db_session, monkeypatch):
    r = await api.post("/verify", data={})
    assert r.status_code == 422
    html = (UPSC_DIR / "home.html").read_bytes()
    r = await api.post("/verify", files={"file": ("home.html", html, "text/html")})
    assert r.status_code == 415
    body = (UPSC_DIR / ADVT_12).read_bytes()
    monkeypatch.setattr(notice_verifier, "MAX_PDF_BYTES", len(body) - 1)
    r = await api.post("/verify", files={"file": (ADVT_12, body, "application/pdf")})
    assert r.status_code == 413

# ------------------------------------------------------------------ evasions built from the fixture's real values

def test_backslash_before_userinfo_is_read_as_a_browser_reads_it():
    # Browsers open 'http://<fake>\@<official>' on <fake>; the official domain after '@' is only a path there
    fake, genuine = ITEMS["indiapostgds-online"]["value"], "indiapost.gov.in"
    check = check_domain(f"https://{fake}\\@{genuine}")
    assert check.host == fake and check.status == "LOOKALIKE"

@pytest.mark.asyncio
async def test_genuine_link_quoted_next_to_a_lookalike_does_not_vouch_for_it(db_session):
    await load_official(db_session)
    text = f"{ITEMS['field-assistant-1736']['value']} {NOTICES[0]['url']} https://{ITEMS['indiapostgds-online']['value']}"
    result = await verify(db_session, text=text)
    assert any("official link" in m.matched_on for m in result.matches)
    assert result.verdict == LOOKALIKE
    # Submitted with an unofficial link, the genuine link in the text does not make it found either
    result = await verify(db_session, url=ITEMS["rsby-ind-in"]["value"], text=NOTICES[0]["url"])
    assert result.verdict == UNOFFICIAL

@pytest.mark.parametrize("name", [ADVT_12, ADVT_11])
def test_links_printed_in_genuine_upsc_advertisements_are_official(name):
    # They print "https://upsconline.nic.in.”": the closing quote is not part of the host
    text = notice_verifier.pdf_text((UPSC_DIR / name).read_bytes())
    checks = [check_domain(u) for u in notice_verifier.urls_in(text)]
    assert checks and all(c.status == "OFFICIAL" for c in checks), [(c.input, c.status) for c in checks]
