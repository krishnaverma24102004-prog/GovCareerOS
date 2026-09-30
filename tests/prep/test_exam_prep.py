"""
Exam preparation on real UPSC documents captured from upsc.gov.in on 30-09-2026 (tests/fixtures/upsc/prep/SOURCES.txt):
the Previous Question Papers listing, the IES/ISS 2026 examination page, its examination notice (syllabus on
pages 18-25) and the IES/ISS 2026 General Studies question paper. Served through an httpx MockTransport.
"""
import re
from datetime import date
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from govcareeros.backend.documents.pdf_text import extract_pages
from govcareeros.backend.models import QuestionPaper, RawDocument, Syllabus
from govcareeros.backend.parsing.upsc_syllabus import parse_syllabus
from govcareeros.backend.services import exam_prep, study_plan
from govcareeros.backend.services.document_ingestion import get_or_create_source
from govcareeros.backend.sources import upsc, upsc_pyq

PREP = Path(__file__).parents[1] / "fixtures" / "upsc" / "prep"
EXAM_PAGE = "https://www.upsc.gov.in/examinations/Indian%20Economic%20Service%20-%20Indian%20Statistical%20Service%20Examination%2C%202026"
NOTICE_URL = "https://www.upsc.gov.in/sites/default/files/Exam_Notification_IES_ISS_Eng_11022026.pdf"
GS_URL = "https://www.upsc.gov.in/sites/default/files/QP-IES-ISS-26-220626-GENERAL-STUDIES.pdf"
NOTICE = PREP / "Exam_Notification_IES_ISS_Eng_11022026.pdf"

def _portal(status_override=None):
    files = {
        upsc_pyq.LISTING_URL: ("text/html", PREP / "previous-question-papers.html"),
        EXAM_PAGE: ("text/html", PREP / "ies-iss-2026-exam-page.html"),
        NOTICE_URL: ("application/pdf", NOTICE),
        GS_URL: ("application/pdf", PREP / "QP-IES-ISS-26-220626-GENERAL-STUDIES.pdf"),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if status_override:
            return httpx.Response(status_override, text="Access Denied")
        hit = files.get(str(request.url))
        if hit is None:
            return httpx.Response(404)
        return httpx.Response(200, content=hit[1].read_bytes(), headers={"content-type": hit[0]})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))

def _squash(text: str) -> str:
    """Whitespace-free text without the running "Page N of M" header."""
    return re.sub(r"Page\d+of\d+", "", re.sub(r"\s+", "", text))

def test_syllabus_parsed_from_notice_with_page_provenance():
    pages = extract_pages(NOTICE.read_bytes(), ocr=False)
    parsed = parse_syllabus(pages)
    assert parsed.warnings == []
    assert [p.name for p in parsed.papers] == [
        "General English", "General Studies", "General Economics-I", "General Economics-II", "General Economics-III",
        "Indian Economics", "Statistics-I", "Statistics-II", "Statistics-III", "Statistics-IV"]
    papers = {p.name: p for p in parsed.papers}
    assert papers["Indian Economics"].note == "For IES only"
    assert papers["Statistics-I"].note == "OBJECTIVE TYPE; For ISS only"
    assert [len(papers[n].topics) for n in ("General Economics-I", "Indian Economics", "Statistics-IV")] == [7, 11, 7]

    ge1 = papers["General Economics-I"].topics
    assert (ge1[0].title, ge1[0].part, ge1[0].page) == ("Theory of Consumer’s Demand", "Part A", 18)
    assert "Slutsky theorem and derivation of demand curve" in [s.text for s in ge1[0].subtopics]
    assert "Price, income and substitution effects" in [s.text for s in ge1[0].subtopics]
    # Numbering restarts at 1. under PART B
    assert (ge1[5].title, ge1[5].part) == ("Mathematical Methods in Economics", "Part B: Quantitative Methods in Economics")
    last = papers["Statistics-IV"].topics[-1]
    assert (last.title, last.page) == ("Computing with C and R", 25)
    # A paper without numbered topics is one topic of its sentences
    gs = papers["General Studies"].topics
    assert len(gs) == 1 and gs[0].subtopics[1].text.startswith("The paper will also include questions on Indian Polity")

    # Every subtopic is the notice's own wording, on the page it is cited to (or running over to the next)
    text = {p.page_number: _squash(p.text) for p in pages}
    for paper in parsed.papers:
        for topic in paper.topics:
            for sub in topic.subtopics:
                assert _squash(sub.text) in text[sub.page] + text.get(sub.page + 1, ""), (paper.name, sub)

def test_question_paper_listing_and_exam_page():
    links = upsc_pyq.parse_listing((PREP / "previous-question-papers.html").read_text(encoding="utf-8"))
    assert len(links) == 603 and all(l.url.endswith(".pdf") for l in links)  # MP3 dictation papers are left out
    ies26 = [l for l in links if l.exam_code == "IES_ISS" and l.year == 2026]
    assert [l.paper_name for l in ies26][:4] == ["General Studies", "General English", "Indian Economics", "General Economics Paper - I"]
    assert ies26[0].url == GS_URL and ies26[0].size_label == "1.27 MB"
    csm = next(l for l in links if l.exam_name == "Civil Services (Main) Examination, 2026")
    assert (csm.exam_code, csm.stage, csm.category, csm.paper_name) == ("CSE", "MAIN", "General", "General Studies Paper - I")
    assert {l.year for l in links} == {2023, 2024, 2025, 2026}

    page = upsc_pyq.parse_exam_page((PREP / "ies-iss-2026-exam-page.html").read_text(encoding="utf-8"), EXAM_PAGE)
    assert page.exam_code == "IES_ISS"
    assert page.commencement_date == date(2026, 6, 19)
    assert page.notice_urls == [NOTICE_URL]

def test_study_plan_is_proportional_and_ends_with_revision():
    parsed = parse_syllabus(extract_pages(NOTICE.read_bytes(), ocr=False))
    topics = [study_plan.TopicInput(p.name, t.title, len(t.subtopics), t.page)
              for p in parsed.papers if p.note and "IES" in p.note for t in p.topics]
    papers = [study_plan.PaperInput("gs-2026", 2026, "General Studies"),
              study_plan.PaperInput("ge1-2025", 2025, "General Economics Paper - I")]
    plan = study_plan.build_plan(date(2026, 10, 1), date(2027, 6, 18), 20, topics, papers)

    assert (plan.total_weeks, plan.revision_weeks) == (37, 6)  # ceil(15% of 37)
    topic_items = [i for i in plan.items if i.kind == "TOPIC"]
    assert max(i.week for i in topic_items) == 31 and [i.week for i in topic_items] == sorted(i.week for i in topic_items)
    for week in range(1, 32):
        assert sum(i.hours for i in topic_items if i.week == week) == pytest.approx(20, abs=0.05)
    # Hours per subtopic are the same for every topic
    total_size = sum(t.size for t in topics)
    for t in topics:
        hours = sum(i.hours for i in topic_items if (i.section, i.topic) == (t.section, t.topic))
        assert hours == pytest.approx(31 * 20 * t.size / total_size, abs=0.05)
    revision = [i for i in plan.items if i.week > 31]
    assert {i.kind for i in revision} == {"REVISION", "PYQ"}
    pyq = [i for i in revision if i.kind == "PYQ"]
    assert [(i.week, i.question_paper_id, i.topic) for i in pyq] == [
        (32, "gs-2026", "Solve General Studies (2026)"), (33, "ge1-2025", "Solve General Economics Paper - I (2025)")]
    assert pyq[1].section == "General Economics-I"
    assert {i.section for i in revision if i.kind == "REVISION"} == {t.section for t in topics}

    with pytest.raises(study_plan.PlanError):
        study_plan.build_plan(date(2027, 6, 5), date(2027, 6, 18), 20, topics)

@pytest.mark.asyncio
async def test_prep_sync_stores_papers_syllabus_and_downloads(db_session):
    async with _portal() as client:
        run = await exam_prep.run_prep_sync(db_session, client, exam_pages=[EXAM_PAGE])
        assert run.source_health == "HEALTHY", run.errors
        assert (run.papers_listed, run.papers_created) == (603, 603)
        assert run.syllabi[0].entries > 500 and len(run.syllabi[0].papers) == 10

        source = await get_or_create_source(db_session, upsc.UPSC_SOURCE)
        paper = (await db_session.execute(select(QuestionPaper).where(QuestionPaper.official_url == GS_URL))).scalar_one()
        await exam_prep.download_papers(db_session, client, source, [paper], run)
    await db_session.commit()
    assert run.papers_downloaded == 1
    raw = await db_session.get(RawDocument, paper.raw_document_id)
    assert (raw.url, raw.discovery_method) == (GS_URL, "QUESTION_PAPER")
    assert Path(raw.storage_path).read_bytes().startswith(b"%PDF")

    notice_raw, rows = await exam_prep.latest_syllabus(db_session, "IES_ISS")
    assert (notice_raw.url, notice_raw.discovery_method) == (NOTICE_URL, "EXAM_NOTICE")
    assert rows[0].section == "General English" and rows[-1].topic == "Computing with C and R"

    # Same bytes again: rows replaced, not duplicated; listing re-run adds no papers
    before = len(rows)
    async with _portal() as client:
        again = await exam_prep.run_prep_sync(db_session, client, exam_pages=[EXAM_PAGE])
    assert again.papers_created == 0 and not again.syllabi[0].raw_document_created
    assert len((await db_session.execute(select(Syllabus))).scalars().all()) == before

@pytest.mark.asyncio
async def test_prep_sync_reports_access_restricted(db_session):
    async with _portal(status_override=403) as client:
        run = await exam_prep.run_prep_sync(db_session, client, exam_pages=[EXAM_PAGE])
    assert run.source_health == "ACCESS_RESTRICTED"
    assert (await db_session.execute(select(QuestionPaper))).first() is None

@pytest.mark.asyncio
async def test_prep_api_plans_are_user_scoped(db_session, api, login):
    source = await get_or_create_source(db_session, upsc.UPSC_SOURCE)
    await exam_prep.ingest_syllabus_pdf(db_session, source, NOTICE_URL, NOTICE.read_bytes(), "IES_ISS")
    await exam_prep.record_papers(db_session, upsc_pyq.parse_listing(
        (PREP / "previous-question-papers.html").read_text(encoding="utf-8")))
    async with _portal() as client:
        paper = (await db_session.execute(select(QuestionPaper).where(QuestionPaper.official_url == GS_URL))).scalar_one()
        await exam_prep.download_papers(db_session, client, source, [paper], exam_prep.PrepSyncResult("UNKNOWN"))
    await db_session.commit()

    alice, bob = await login("alice@example.com"), await login("bob@example.com")
    assert (await api.get("/prep/exams/IES_ISS/syllabus")).status_code == 401

    syl = (await api.get("/prep/exams/ies_iss/syllabus", headers=alice)).json()
    assert syl["notice_url"] == NOTICE_URL and len(syl["sections"]) == 10
    assert syl["sections"][2]["topics"][0]["subtopics"][0] == {"text": "Cardinal utility Analysis: Marginal utility and demand", "page": 18}
    papers = (await api.get("/prep/exams/IES_ISS/papers?downloaded_only=true", headers=alice)).json()
    assert [p["official_url"] for p in papers] == [GS_URL]
    assert len((await api.get("/prep/exams/IES_ISS/papers?year=2025", headers=alice)).json()) == 10
    assert (await api.get("/prep/exams/NOPE/syllabus", headers=alice)).status_code == 404

    body = {"exam_code": "IES_ISS", "exam_date": "2027-06-18", "weekly_hours": 15, "start_date": "2026-10-01",
            "sections": ["General Studies", "General Economics-I"]}
    r = await api.post("/prep/plans", json=body, headers=alice)
    assert r.status_code == 201, r.text
    plan = r.json()
    assert (plan["total_weeks"], plan["revision_weeks"]) == (37, 6)
    assert {i["section"] for i in plan["items"] if i["kind"] == "TOPIC"} == {"General Studies", "General Economics-I"}
    assert [i["question_paper_id"] for i in plan["items"] if i["kind"] == "PYQ"] == [str(paper.id)]

    item = plan["items"][0]
    url = f"/prep/plans/{plan['id']}/items/{item['id']}"
    done = (await api.patch(url, json={"status": "DONE"}, headers=alice)).json()
    assert done["status"] == "DONE" and done["completed_at"] is not None
    assert (await api.patch(url, json={"status": "FINISHED"}, headers=alice)).status_code == 422
    # Someone else's plan does not exist for bob
    assert (await api.get(f"/prep/plans/{plan['id']}", headers=bob)).status_code == 404
    assert (await api.patch(url, json={"status": "SKIPPED"}, headers=bob)).status_code == 404
    assert (await api.get("/prep/plans", headers=bob)).json() == []
    assert (await api.get(f"/prep/plans/{plan['id']}", headers=alice)).json()["items"][0]["status"] == "DONE"

    assert (await api.post("/prep/plans", json={**body, "sections": ["Botany"]}, headers=alice)).status_code == 422
    assert (await api.post("/prep/plans", json={**body, "exam_date": "2026-10-10"}, headers=alice)).status_code == 422
