"""
Public notice check (POST /verify) on real data only:

  fakes      tests/fixtures/fake_notices.json: fake recruitment websites and notice wording debunked by PIB Fact
             Check, ministries or police, each with the news / fact-check page that reported it
  genuine    tests/fixtures/notices_snapshot.json (282 notice links discovered on official sites) and the real
             UPSC advertisements 11/2026 and 12/2026 (tests/fixtures/upsc)
"""
import asyncio
import json
import time
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import event, select

from govcareeros.backend.documents.pdf_text import tessdata_dir
from govcareeros.backend.models import RecruitmentNotice, RecruitmentNoticeTitleToken, Vacancy
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
    # EPFO's registry entry now points at epfo.gov.in, so epfindia.gov.in is official through the gov.in suffix
    ("https://pmvbry.epfindia.gov.in", "gov.in"),
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

# ------------------------------------------------------------------ bare domains

@pytest.mark.parametrize("item_id", sorted(i for i in EXPECTED_DOMAIN if "(dot)" not in ITEMS[i]["value"]))
def test_fake_domains_without_scheme_are_found_in_text(item_id):
    # How fake notices and forwards print them: 'indiapostgds.online', with no https:// or www.
    bare = ITEMS[item_id]["value"].split("://")[-1].removeprefix("www.").rstrip("/")
    links = notice_verifier.urls_in(f"{ITEMS['field-assistant-1736']['value']}, apply at {bare}.")
    assert links == [bare], item_id
    assert check_domain(links[0]).status == {LOOKALIKE: "LOOKALIKE", UNOFFICIAL: "UNOFFICIAL"}[EXPECTED_DOMAIN[item_id]]

@pytest.mark.asyncio
async def test_bare_lookalike_domain_in_pasted_text_is_a_lookalike(db_session):
    await load_official(db_session)
    result = await verify(db_session, text=f"{ITEMS['field-assistant-1736']['value']} {ITEMS['indiapostgds-online']['value']}")
    assert result.verdict == LOOKALIKE
    assert [(c.host, c.official_domain) for c in result.links_in_text] == [("indiapostgds.online", "indiapost.gov.in")]

@pytest.mark.parametrize("path", sorted(FIXTURES.glob("**/*.pdf")), ids=lambda p: p.name)
def test_bare_domain_reading_adds_nothing_from_genuine_documents(path):
    # 'B.Tech' (SSC), a lone 'gov.in' / 'nic.in' (UPSC IES/ISS) and "Telecommunication (DoT) JE" broken over lines
    # (SSC JE) are not domains: every link found in these real notices starts with http(s):// or www.
    text = notice_verifier.pdf_text(path.read_bytes())
    links = notice_verifier.urls_in(text)
    assert all(u.lower().startswith(("http://", "https://", "www.")) for u in links), links

@pytest.mark.parametrize("separator", [".", "-", "(dot)", ".online", "-online.", " (dot) "])
def test_link_scan_is_linear_on_adversarial_input(separator):
    # A paste of MAX_TEXT_CHARS built from a debunked domain's own name: every alternative of the link pattern must
    # start only at a run of domain characters, or this takes minutes
    name = ITEMS["indiapostgds-online"]["value"].split(".")[0]
    text = (name + separator) * (notice_verifier.MAX_TEXT_CHARS // (len(name) + len(separator)) + 1)
    start = time.perf_counter()
    notice_verifier.urls_in(text[:notice_verifier.MAX_TEXT_CHARS])
    assert time.perf_counter() - start < 1.0, separator

# ------------------------------------------------------------------ request and PDF limits

@pytest.mark.asyncio
async def test_api_refuses_a_declared_oversized_body_before_parsing_it(api, db_session, monkeypatch):
    from starlette.formparsers import MultiPartParser

    def no_parse(*args, **kwargs):
        raise AssertionError("the form must not be parsed")
    monkeypatch.setattr(MultiPartParser, "parse", no_parse)
    body = (UPSC_DIR / ADVT_12).read_bytes()
    monkeypatch.setattr(notice_verifier, "MAX_REQUEST_BYTES", len(body) // 2)
    r = await api.post("/verify", files={"file": (ADVT_12, body, "application/pdf")})
    assert r.status_code == 413, r.text

@pytest.mark.asyncio
async def test_api_stops_reading_an_undeclared_oversized_body(api, db_session, monkeypatch):
    body = (UPSC_DIR / ADVT_12).read_bytes()
    limit, chunk = 256 * 1024, 64 * 1024
    monkeypatch.setattr(notice_verifier, "MAX_REQUEST_BYTES", limit)
    sent = 0

    async def chunked():  # no Content-Length: a multipart upload of the PDF ten times over, arriving in pieces
        nonlocal sent
        head = f'--x\r\nContent-Disposition: form-data; name="file"; filename="{ADVT_12}"\r\n' \
               'Content-Type: application/pdf\r\n\r\n'
        yield head.encode()
        for _ in range(10):
            for i in range(0, len(body), chunk):
                sent += len(body[i:i + chunk])
                yield body[i:i + chunk]
        yield b"\r\n--x--\r\n"
    r = await api.post("/verify", content=chunked(), headers={"Content-Type": "multipart/form-data; boundary=x"})
    assert r.status_code == 413, r.text
    assert sent <= limit + chunk

@pytest.mark.asyncio
async def test_pdf_page_and_character_limits(db_session, monkeypatch):
    await load_official(db_session)
    body = (UPSC_DIR / ADVT_11).read_bytes()
    monkeypatch.setattr(notice_verifier, "MAX_PDF_PAGES", 5)
    result = await verify(db_session, pdf=body)
    assert (result.pdf_pages, result.pdf_pages_read) == (52, 5)
    assert any("first 5 of 52 pages" in n for n in result.notes)
    monkeypatch.setattr(notice_verifier, "MAX_PDF_PAGES", 150)
    monkeypatch.setattr(notice_verifier, "MAX_CHECK_CHARS", 20_000)
    result = await verify(db_session, text=ITEMS["field-assistant-1736"]["value"], pdf=body)
    assert result.text_chars <= 20_000 and result.pdf_pages_read < 52
    assert any("20,000 characters" in n for n in result.notes)

def test_pdf_reading_time_limit_stops_after_the_first_page(monkeypatch):
    monkeypatch.setattr(notice_verifier, "PDF_READ_SECONDS", 0)
    read = notice_verifier.read_pdf((UPSC_DIR / ADVT_12).read_bytes())
    assert read.pages_read == 1 and "UNION PUBLIC SERVICE COMMISSION" in read.text
    assert any("reading time limit" in n for n in read.notes)

@pytest.mark.asyncio
async def test_api_pdf_timeout_and_busy(api, db_session, monkeypatch):
    body = (UPSC_DIR / ADVT_12).read_bytes()
    monkeypatch.setattr(notice_verifier, "PDF_TIMEOUT_SECONDS", 0)
    r = await api.post("/verify", files={"file": (ADVT_12, body, "application/pdf")})
    assert r.status_code == 422 and "took longer" in r.json()["detail"]
    # The timed-out read gives its slot back when its thread finishes
    for _ in range(100):
        if notice_verifier._PDF_SLOTS._value == notice_verifier.MAX_CONCURRENT_PDFS:
            break
        await asyncio.sleep(0.05)
    assert notice_verifier._PDF_SLOTS._value == notice_verifier.MAX_CONCURRENT_PDFS

    monkeypatch.setattr(notice_verifier, "PDF_TIMEOUT_SECONDS", 45)
    for _ in range(notice_verifier.MAX_CONCURRENT_PDFS):
        assert notice_verifier._PDF_SLOTS.acquire(blocking=False)
    try:
        r = await api.post("/verify", files={"file": (ADVT_12, body, "application/pdf")})
        assert r.status_code == 503 and r.headers["retry-after"]
    finally:
        for _ in range(notice_verifier.MAX_CONCURRENT_PDFS):
            notice_verifier._PDF_SLOTS.release()

# ------------------------------------------------------------------ SQL prefilter

@pytest.fixture
def loads():
    """How many notices and vacancies the ORM loads from the database."""
    loaded = {"notices": 0, "vacancies": 0}

    def on_notice(*_):
        loaded["notices"] += 1

    def on_vacancy(*_):
        loaded["vacancies"] += 1
    event.listen(RecruitmentNotice, "load", on_notice)
    event.listen(Vacancy, "load", on_vacancy)
    yield loaded
    event.remove(RecruitmentNotice, "load", on_notice)
    event.remove(Vacancy, "load", on_vacancy)

@pytest.mark.asyncio
async def test_only_candidate_notices_and_vacancies_are_loaded(db_session, loads):
    await load_official(db_session)
    db_session.expunge_all()  # loads below come from the database, not the identity map
    # A notice title pasted on its own: a handful of notices share most of its words
    title = "Clarification for the post of Assistant Public Prosecutor in Delhi, vide Advt. No. 11 - 2026"
    result = await verify(db_session, text=title)
    assert result.verdict == FOUND
    assert 1 <= loads["notices"] <= 10, loads
    # Debunked fake wording names no tracked organisation, link or advertisement number: nothing is loaded
    loads.update(notices=0, vacancies=0)
    db_session.expunge_all()
    result = await verify(db_session, text=ITEMS["rsby-posts"]["value"])
    assert result.verdict == NOT_FOUND and loads == {"notices": 0, "vacancies": 0}, loads

@pytest.mark.asyncio
async def test_title_index_follows_inserts_and_title_changes(db_session):
    await load_official(db_session)
    rows = (await db_session.execute(select(RecruitmentNoticeTitleToken))).scalars().all()
    notices = {n.id: n for n in (await db_session.execute(select(RecruitmentNotice))).scalars()}
    indexed = {n["url"] for n in NOTICES if len(notice_verifier._tokens(n["title"])) >= 5}
    assert {notices[r.notice_id].url for r in rows} == indexed
    # A title corrected later is found by its new wording
    first = next(n for n in NOTICES if n["url"] in indexed)
    second = next(n for n in NOTICES if n["url"] in indexed and n["source"] != first["source"])
    notice = next(n for n in notices.values() if n.url == first["url"])
    notice.title = second["title"]
    await db_session.commit()
    result = await verify(db_session, text=second["title"])
    assert {m.official_url for m in result.matches if "title" in m.matched_on} >= {first["url"], second["url"]}

# ------------------------------------------------------------------ scanned PDFs (OCR)

NEEDS_TESSERACT = pytest.mark.skipif(tessdata_dir() is None, reason="needs Tesseract")
ASSTT_NOTICE = UPSC_DIR / "notices" / "AssttPublProseGNCTD-Notice-Engl-170926Rev.pdf"  # one scanned page

@NEEDS_TESSERACT
@pytest.mark.asyncio
async def test_api_scanned_upsc_notice_is_found_through_ocr(api, db_session):
    await load_official(db_session)
    files = {"file": (ASSTT_NOTICE.name, ASSTT_NOTICE.read_bytes(), "application/pdf")}
    out = (await api.post("/verify", files=files)).json()
    assert out["verdict"] == FOUND and out["ocr_pages"] == [1]
    assert out["advertisement_numbers"] == ["11/2026"]
    vacancies = [m for m in out["matches"] if m["kind"] == "VACANCY"]
    assert vacancies and all({"advertisement number", "organisation"} <= set(m["matched_on"]) for m in vacancies)
    assert any("read by OCR" in n for n in out["notes"])

@pytest.mark.asyncio
async def test_api_scanned_notice_without_ocr_says_what_was_not_read(api, db_session):
    await load_official(db_session)
    files = {"file": (ASSTT_NOTICE.name, ASSTT_NOTICE.read_bytes(), "application/pdf")}
    out = (await api.post("/verify", files=files, data={"ocr": "false"})).json()
    assert out["verdict"] == NOT_FOUND and out["text_chars"] == 0 and out["ocr_pages"] == []
    assert any("Page(s) 1 have no text layer" in n and "OCR was not requested" in n for n in out["notes"])

@NEEDS_TESSERACT
def test_ocr_page_limit_on_a_four_page_scanned_circular():
    mha = FIXTURES / "mha"
    recorded = json.loads((mha / "CircularVacancy_03082026.ocr.json").read_text(encoding="utf-8"))
    read = notice_verifier.read_pdf((mha / "CircularVacancy_03082026.pdf").read_bytes(), ocr=True)
    assert (read.pages, read.pages_read, read.ocr_pages) == (4, 4, [1, 2, 3])
    # The same words as the OCR recorded with the fixture
    assert "Office of the Custodian of Enemy Property" in recorded["pages"][0]["text"]
    assert "Office of the Custodian of Enemy Property" in read.text
    assert any("Page(s) 4 have no text layer" in n and f"limited to {notice_verifier.MAX_OCR_PAGES} pages" in n
               for n in read.notes)

@NEEDS_TESSERACT
def test_ocr_skips_a_page_too_large_to_render(monkeypatch):
    # The scanned UPSC notice is A4 (8.7 million pixels at 300 dpi); with the cap below that, it is not rendered
    from govcareeros.backend.documents import pdf_text
    monkeypatch.setattr(pdf_text, "MAX_OCR_PIXELS", 5_000_000)
    read = notice_verifier.read_pdf(ASSTT_NOTICE.read_bytes(), ocr=True)
    assert read.ocr_pages == [] and read.text.strip() == ""
    assert read.notes == ["Page(s) 1 have no text layer and are too large to read by OCR."]
