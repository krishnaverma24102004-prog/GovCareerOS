"""
Official UPSC notices (scanned; read via OCR) captured from upsc.gov.in on 29-09-2026:
 - Clarification for Assistant Public Prosecutor, GNCTD (Advt 11/2026, Vacancy 26091106212)
 - Change of recruitment process for six posts of Advt 13/2025 (scrutiny cancelled, not the posts)
"""
import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from govcareeros.backend.documents.pdf_text import extract_pages, tessdata_dir
from govcareeros.backend.models import RecruitmentNotice, Vacancy, VacancyRequirement, VacancyVersion
from govcareeros.backend.parsing.upsc_notice import parse_notice
from govcareeros.backend.services.document_ingestion import run_upsc_pipeline
from govcareeros.backend.sources import upsc

UPSC = Path(__file__).parents[1] / "fixtures" / "upsc"
NOTICES = UPSC / "notices"
APP_NOTICE = "AssttPublProseGNCTD-Notice-Engl-170926Rev.pdf"
PROCESS_NOTICE = "NoticeCancellation-06-Posts-Advt-13-25-Engl-200826.pdf"

pytestmark = pytest.mark.skipif(tessdata_dir() is None, reason="Tesseract eng.traineddata not installed; these notices are scanned")

def test_notice_links_on_homepage():
    links = upsc.parse_notice_links((UPSC / "home.html").read_text(encoding="utf-8"), upsc.HOME_URL)
    assert [l.url.rsplit("/", 1)[-1] for l in links] == [PROCESS_NOTICE, APP_NOTICE]
    assert links[1].title.startswith("Clarification for the post of Assistant Public Prosecutor")

def test_app_clarification_is_parsed_from_ocr():
    pages = extract_pages((NOTICES / APP_NOTICE).read_bytes())
    assert pages[0].ocr is True
    n = parse_notice(pages, "Clarification for the post of Assistant Public Prosecutor in Delhi, vide Advt. No. 11 - 2026")
    assert n.kinds == ["CLARIFICATION"]
    assert n.advertisement_refs == ["11/2026"] and n.vacancy_refs == ["26091106212"]
    assert n.notice_date.isoformat() == "2026-09-15"
    assert not n.cancels_vacancies and n.new_closing is None
    assert any("certificates issued by GNCTD" in c.text for c in n.category_conditions)
    assert any("considered as UR candidates" in c.text for c in n.category_conditions)

def test_scrutiny_cancellation_does_not_cancel_the_posts():
    n = parse_notice(extract_pages((NOTICES / PROCESS_NOTICE).read_bytes()),
                     "Notice regarding change in recruitment process for six (06) posts advertised vide Advt. No.13/25")
    assert n.kinds == ["SELECTION_PROCESS_CHANGE"]
    assert n.cancels_vacancies is False  # "Any scrutiny released ... stands cancelled"
    assert n.advertisement_refs == ["13/2025"]
    assert len(n.vacancy_refs) == 6 and "25091303213" in n.vacancy_refs

def _portal():
    pdfs = {
        f"https://www.upsc.gov.in/sites/default/files/{name}": path for name, path in [
            ("AdvtNo-12-2026-Engl-250926_0.pdf", UPSC / "AdvtNo-12-2026-Engl-250926_0.pdf"),
            ("AdvtNo-11-2026-Engl-100926.pdf", UPSC / "AdvtNo-11-2026-Engl-100926.pdf"),
            (APP_NOTICE, NOTICES / APP_NOTICE), (PROCESS_NOTICE, NOTICES / PROCESS_NOTICE)]
    }

    def handler(request):
        url = str(request.url)
        if url == upsc.LISTING_URL:
            return httpx.Response(200, text=(UPSC / "recruitment-advertisement.html").read_text(encoding="utf-8"))
        if url == upsc.HOME_URL:
            return httpx.Response(200, text=(UPSC / "home.html").read_text(encoding="utf-8"))
        if url in pdfs:
            return httpx.Response(200, content=pdfs[url].read_bytes(), headers={"content-type": "application/pdf"})
        return httpx.Response(404)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))

@pytest.mark.asyncio
async def test_notices_apply_to_stored_vacancies_idempotently(db_session):
    async with _portal() as client:
        run = await run_upsc_pipeline(db_session, client)
        await db_session.commit()
        again = await run_upsc_pipeline(db_session, client)
        await db_session.commit()

    assert run.errors == [] and run.source_health == "HEALTHY"
    by_title = {n.title[:13]: n for n in run.notices}
    app = by_title["Clarification"]
    assert len(app.applied_vacancy_ids) == 1 and app.used_ocr
    process = by_title["Notice regard"]
    assert process.applied_vacancy_ids == [] and len(process.unmatched_vacancy_refs) == 6
    assert all(n.applied_vacancy_ids == [] for n in again.notices)

    vacancy = (await db_session.execute(select(Vacancy).where(Vacancy.external_ref == "26091106212"))).scalar_one()
    assert vacancy.status == "ACTIVE"
    versions = (await db_session.execute(select(VacancyVersion).where(VacancyVersion.vacancy_id == vacancy.id))).scalars().all()
    assert len(versions) == 1 and versions[0].change_type == "CLARIFICATION"
    details = json.loads(versions[0].details)
    assert details["ocr"] is True and any("GNCTD" in c["text"] for c in details["conditions"])
    conds = (await db_session.execute(select(VacancyRequirement).where(
        VacancyRequirement.vacancy_id == vacancy.id, VacancyRequirement.req_type == "NOTICE_CONDITION"))).scalars().all()
    assert len(conds) == 2 and all(c.raw_document_id == versions[0].raw_document_id for c in conds)
    parsed = (await db_session.execute(select(RecruitmentNotice).where(RecruitmentNotice.review_status == "PARSED"))).scalars().all()
    assert len(parsed) == 2

    # Re-parsing the advertisement must not drop conditions that came from the later notice
    vacancy.parser_version = "upsc_advertisement/0"
    await db_session.commit()
    async with _portal() as client:
        await run_upsc_pipeline(db_session, client)
        await db_session.commit()
    active = (await db_session.execute(select(VacancyRequirement).where(
        VacancyRequirement.vacancy_id == vacancy.id, VacancyRequirement.req_type == "NOTICE_CONDITION",
        VacancyRequirement.superseded_at.is_(None)))).scalars().all()
    assert len(active) == 2

@pytest.mark.asyncio
async def test_notice_condition_reaches_eligibility_for_obc_only(api, login, db_session):
    async with _portal() as client:
        await run_upsc_pipeline(db_session, client)
        await db_session.commit()
    app_id = str((await db_session.execute(select(Vacancy).where(Vacancy.external_ref == "26091106212"))).scalar_one().id)
    profile = {
        "personal_info": {"date_of_birth": "1995-03-10", "category": "OBC", "citizenship_status": "INDIAN_CITIZEN",
                          "is_pwbd": False, "is_ex_serviceman": False, "is_central_govt_employee": False},
        "education": [{"degree": "LL.B.", "institution": "University of Delhi", "end_date": "2021-06-30"}],
    }
    h = await login("asha@example.com")
    obc = (await api.put("/profile", json=profile, headers=h)).json()["version"]["id"]
    res = (await api.post("/eligibility", json={"profile_version_id": obc, "vacancy_id": app_id}, headers=h)).json()
    notice = [e for e in res["evidence"] if e["check_name"] == "NOTICE"]
    assert notice and all(e["outcome"] == "CONDITIONAL" for e in notice)
    assert any("GNCTD" in e["rule"] for e in notice)

    profile["personal_info"]["category"] = "SC"
    sc = (await api.put("/profile", json=profile, headers=h)).json()["version"]["id"]
    res = (await api.post("/eligibility", json={"profile_version_id": sc, "vacancy_id": app_id}, headers=h)).json()
    assert [e["outcome"] for e in res["evidence"] if e["check_name"] == "NOTICE"] == ["NOT_EVALUATED"]
