"""
Weekly digest over the real UPSC data captured on 29-09-2026 (Advt 11/2026, Advt 12/2026 and the scanned
GNCTD clarification). "now" is pinned to that date, as in the radar tests, so the 14-day window is meaningful.
Every quote the digest prints from a text PDF is checked against the text of the page it cites.
"""
import json
import re
import smtplib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from govcareeros.backend.config.settings import settings
from govcareeros.backend.documents.pdf_text import extract_pages, tessdata_dir
from govcareeros.backend.models import DigestRun, User
from govcareeros.backend.services import digest as digest_service
from govcareeros.backend.services.document_ingestion import get_or_create_source, ingest_pdf_bytes
from govcareeros.backend.services.notice_ingestion import ingest_notice_pdf
from govcareeros.backend.sources import upsc

UPSC = Path(__file__).parents[1] / "fixtures" / "upsc"
BASE = "https://www.upsc.gov.in/sites/default/files/"
ADVTS = ["AdvtNo-11-2026-Engl-100926.pdf", "AdvtNo-12-2026-Engl-250926_0.pdf"]
GNCTD = "AssttPublProseGNCTD-Notice-Engl-170926Rev.pdf"
NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)

def law_profile(dob):
    return {
        "personal_info": {"full_name": "आशा राव", "date_of_birth": dob, "category": "OBC", "citizenship_status": "INDIAN_CITIZEN",
                          "is_pwbd": False, "is_ex_serviceman": False, "is_central_govt_employee": False},
        "education": [{"degree": "LL.B.", "institution": "Faculty of Law, University of Delhi", "end_date": "2016-06-30"}],
        "experience": [{"organization": "Rao & Associates", "designation": "Legal Associate", "domain": "Legal", "start_date": "2017-01-01"}],
    }

@pytest.fixture
def no_smtp(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("tests must never open an SMTP connection")
    monkeypatch.setattr(smtplib, "SMTP", refuse)
    monkeypatch.setattr(smtplib, "SMTP_SSL", refuse)

async def _ingest_advts(session):
    source = await get_or_create_source(session, upsc.UPSC_SOURCE)
    for name in ADVTS:
        await ingest_pdf_bytes(session, source, BASE + name, (UPSC / name).read_bytes())
    await session.commit()
    return source

_page_text = {}

def _norm(text):
    return re.sub(r"\s+", " ", text).strip().lower()

def assert_quote_on_page(c):
    """The citation's quote is really on the cited page of the official document."""
    assert c.url.startswith(BASE) and c.page >= 1 and c.quote, c
    name = c.url.rsplit("/", 1)[-1]
    if name not in _page_text:
        _page_text[name] = {p.page_number: _norm(p.text) for p in extract_pages((UPSC / name).read_bytes())}
    pages = _page_text[name]
    # A clause may run on past the page break (e.g. Advt 11/2026 "AGE: ..." on p.8 continues on p.9,
    # whose text starts with its page number)
    following = re.sub(r"^\d+ ", "", pages.get(c.page + 1, ""))
    assert _norm(c.quote.rstrip("…")) in f"{pages[c.page]} {following}", (c.page, c.quote)
    assert _norm(c.quote)[:40] in pages[c.page], (c.page, c.quote)

def all_citations(d):
    for item in d.items():
        assert item.citations, item.headline
        yield from item.citations
        for chk in item.open_checks:
            yield chk.citation

@pytest.mark.asyncio
async def test_digest_from_real_upsc_advertisements(api, login, db_session, no_smtp):
    await _ingest_advts(db_session)
    h = await login("asha@example.com")
    # 33 on 16-10-2026: exactly at the OBC limit (33) for Law Officer, Advt 12/2026
    assert (await api.put("/profile", json=law_profile("1993-06-01"), headers=h)).status_code == 201
    user = (await db_session.execute(select(User))).scalar_one()

    d, run = await digest_service.generate(db_session, user, now=NOW)
    await db_session.commit()
    for c in all_citations(d):
        assert_quote_on_page(c)

    posts = {i.post: i for i in d.matches}
    law = posts["Law Officer"]
    assert law.change == "NEW" and law.advertisement == "12/2026"
    assert "Closes 16 Oct 2026 18:00 IST" in law.details
    # "Three years' experience at the bar" is not machine-checkable: the open check names the page it is on
    app = posts["Assistant Public Prosecutor"]
    assert app.status == "MANUAL_REVIEW"
    assert any(c.label.startswith("check experience relevance — p.") for c in app.open_checks)

    # Advt 11/2026 closes 02-10-2026 18:00 IST, within 14 days; Advt 12/2026 (16-10) is 17 days out
    assert d.deadlines and {i.advertisement for i in d.deadlines} == {"11/2026"}
    assert all("02 Oct 2026 18:00 IST" in i.details[0] for i in d.deadlines)
    [age] = [i for i in d.age_headroom if i.post == "Law Officer"]
    assert "limit for this post is 33 (p.8)" in age.details[0] and "01 Jun 2027" in age.details[1]
    assert d.notices == []

    path = settings.storage_dir / "digests" / str(user.id) / "2026-09-29.html"
    assert run.file_path == f"digests/{user.id}/2026-09-29.html" and path.is_file()
    page = path.read_text(encoding="utf-8")
    assert '<meta charset="utf-8">' in page and "आशा राव" in page
    assert "16 Oct 2026 18:00 IST" in page and "Law Officer" in page
    assert f'href="{BASE}AdvtNo-12-2026-Engl-250926_0.pdf"' in page
    assert "16 Oct 2026 18:00 IST" in path.with_suffix(".txt").read_text(encoding="utf-8")
    assert run.item_count == len(d.items()) and json.loads(run.item_counts)["deadlines"] == len(d.deadlines)

    # Nothing is emailed unless asked: run_all without send writes a new run and leaves sent_at empty
    runs = await digest_service.run_all(db_session, now=NOW + timedelta(days=7), send=False)
    await db_session.commit()
    assert len(runs) == 1 and all(r.sent_at is None for r in (await db_session.execute(select(DigestRun))).scalars())
    # The second digest starts where the first ended: the verdicts are not new any more
    assert runs[0].period_start == NOW and json.loads(runs[0].item_counts)["matches"] == 0

@pytest.mark.asyncio
@pytest.mark.skipif(tessdata_dir() is None, reason="the scanned notice needs Tesseract")
async def test_digest_cites_official_notice(api, login, db_session, no_smtp):
    source = await _ingest_advts(db_session)
    h = await login("asha@example.com")
    await api.put("/profile", json=law_profile("1995-03-10"), headers=h)
    user = (await db_session.execute(select(User))).scalar_one()
    await digest_service.generate(db_session, user, now=NOW - timedelta(days=1))
    await db_session.commit()

    # The real GNCTD clarification adds OBC conditions to the Assistant Public Prosecutor vacancy
    await ingest_notice_pdf(db_session, source, BASE + GNCTD, (UPSC / "notices" / GNCTD).read_bytes(),
                            "Clarification for the post of Assistant Public Prosecutor in Delhi, vide Advt. No. 11 - 2026")
    await db_session.commit()
    d, _ = await digest_service.generate(db_session, user, now=NOW)
    for c in all_citations(d):
        if c.url.endswith(GNCTD):
            # Scanned notice: the quote is OCR text, so only its document, page and content are checked
            assert c.page >= 1 and "OBC" in c.quote
        else:
            assert_quote_on_page(c)

    [notice] = d.notices
    assert notice.post == "Assistant Public Prosecutor" and "CLARIFICATION" in notice.details[0] and "15 Sep 2026" in notice.details[0]
    assert notice.citations and all(c.url == BASE + GNCTD for c in notice.citations)
    # Re-evaluated on the notice: the match is UPDATED and its conditional check cites the notice, not the advertisement
    [app] = [i for i in d.matches if i.post == "Assistant Public Prosecutor"]
    assert app.change == "UPDATED"
    assert any(c.citation.url == BASE + GNCTD for c in app.open_checks if c.label.startswith("check notice condition"))

@pytest.mark.asyncio
async def test_digest_api_is_per_user_and_never_emails(api, login, db_session, no_smtp):
    await _ingest_advts(db_session)
    asha = await login("asha@example.com")
    await api.put("/profile", json=law_profile("1993-06-01"), headers=asha)
    assert (await api.get("/digest/latest", headers=asha)).status_code == 404

    r = await api.post("/digest/generate", headers=asha)
    assert r.status_code == 201, r.text
    assert r.json()["sent_at"] is None and set(r.json()["item_counts"]) == {"matches", "deadlines", "age_headroom", "notices"}

    page = await api.get("/digest/latest", headers=asha)
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html") and "आशा राव" in page.text
    body = (await api.get("/digest/latest?format=json", headers=asha)).json()
    assert body["run"]["id"] == r.json()["id"] and body["digest"]["email"] == "asha@example.com"
    for section in ("matches", "deadlines", "age_headroom", "notices"):
        for item in body["digest"][section]:
            assert item["citations"] and all(c["url"] and c["page"] and c["quote"] for c in item["citations"])

    ravi = await login("ravi@example.com")
    assert (await api.get("/digest/latest", headers=ravi)).status_code == 404
    assert (await api.get("/digest/latest")).status_code == 401
