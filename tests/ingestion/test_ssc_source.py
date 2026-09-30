"""
SSC adapter on responses captured from ssc.gov.in's public JSON API on 2026-09-30 (tests/fixtures/ssc/sources.json)
and the real "Notice of Combined Higher Secondary (10+2) Level Examination, 2026" PDF.
The pipeline test replays exactly those captured bytes through PageFetcher, so url_guard and robots.txt handling run.
"""
import json
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from govcareeros.backend.models import Exam, ExamFieldProvenance, ExamPost, RecruitmentNotice
from govcareeros.backend.parsing.upsc_advertisement import IST
from govcareeros.backend.services.exam_ingestion import run_ssc_pipeline
from govcareeros.backend.sources import ssc
from govcareeros.backend.sources.generic_notices import PageFetcher

SSC = Path(__file__).parents[1] / "fixtures" / "ssc"
SOURCES = json.loads((SSC / "sources.json").read_text(encoding="utf-8"))
CHSL_URL = "https://ssc.gov.in/api/attachment/uploads/masterData/NoticeBoards/Notice_of_adv_chsle_2026.pdf"

def _board(page: int):
    return json.loads((SSC / f"notice_boards_page{page}.json").read_text(encoding="utf-8"))

def test_notice_board_json_yields_classified_notice_links():
    notices = {n.url: n for n in ssc.parse_notice_board(_board(3))}
    chsl = notices[CHSL_URL]
    assert chsl.title == "Notice of Combined Higher Secondary (10+2) Level Examination, 2026"
    assert (chsl.kind, chsl.document_type, chsl.exam_id) == ("EXAM_NOTICE", "PDF", "s40d16nackd16h0")
    assert chsl.published_at.isoformat() == "2026-09-07T15:59:02.300000+00:00"
    assert [n for n in notices.values() if n.kind == "EXAM_NOTICE"] == [chsl]
    assert all(url.startswith("https://ssc.gov.in/api/attachment/uploads/masterData/NoticeBoards/") for url in notices)

    board = ssc.parse_notice_board(_board(1))

    def kind(title_start):
        return next(n.kind for n in board if n.title.startswith(title_start))
    assert kind("Stenographer Grade C and D Examination 2026, Addendum") == "CORRIGENDUM"
    assert kind("Information regarding the city of examination and Admission Certificate") == "ADMIT_CARD"
    assert kind("Combined Hindi Translators Examination, 2026: Uploading of Tentative Answer Key") == "ANSWER_KEY"
    # A list of an exam's tentative vacancies is not a job notification
    assert kind("Tentative Vacancy of Combined Graduate Level Examination, 2026") == "OTHER"

def test_attachment_paths_are_turned_into_guarded_urls():
    assert ssc.attachment_url("uploads\\masterData\\NoticeBoards\\Important Notice 2026_cgle_2026_12092026.pdf") == \
        "https://ssc.gov.in/api/attachment/uploads/masterData/NoticeBoards/Important%20Notice%202026_cgle_2026_12092026.pdf"
    assert ssc.notice_board_url(3) == SOURCES["notice_boards_page3.json"]["url"]
    live = ssc.parse_live_exams(json.loads((SSC / "live_exams.json").read_text(encoding="utf-8")))
    assert {e.exam_code for e in live} == {"CAPF", "CHSL"}

def _replay_fetcher() -> PageFetcher:
    """Serves only the captured responses; robots.txt answered 404 (text/html) at capture time."""
    captured = {SOURCES[name]["url"]: name for name in SOURCES if not name.startswith("_")}
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        seen.append(url)
        if url == "https://ssc.gov.in/robots.txt":
            return httpx.Response(404, headers={"content-type": "text/html"}, text="<html>404 Not Found</html>")
        name = captured.get(url)
        if name is None:
            return httpx.Response(404, headers={"content-type": "text/html"})
        body = (SSC / name).read_bytes()
        return httpx.Response(200, headers={"content-type": "application/pdf" if name.endswith(".pdf") else "application/json; charset=utf-8"},
                              content=body)

    fetcher = PageFetcher(allow_browser=False, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    fetcher.seen = seen
    return fetcher

@pytest.mark.asyncio
async def test_pipeline_turns_the_exam_notice_into_an_exam(db_session):
    async with _replay_fetcher() as fetcher:
        run = await run_ssc_pipeline(db_session, fetcher, pages=1, first_page=3)
        # Re-running finds the same notices and stores nothing twice
        again = await run_ssc_pipeline(db_session, fetcher, pages=1, first_page=3)
    assert run["errors"] == [] and run["probe"].status == "OK" and run["probe"].new_notices == 10
    assert again["probe"].new_notices == 0 and again["exams"] == []
    assert all(u.startswith("https://ssc.gov.in/") for u in fetcher.seen)

    notice = (await db_session.execute(select(RecruitmentNotice).where(RecruitmentNotice.url == CHSL_URL))).scalar_one()
    assert (notice.notice_kind, notice.review_status) == ("EXAM_NOTICE", "PARSED")
    exam = (await db_session.execute(select(Exam))).scalar_one()
    assert exam.name == "Combined Higher Secondary (10+2) Level Examination, 2026" and exam.exam_code == "CHSL"
    assert exam.external_ref == "HQ-C1102/5/2026-C-1" and exam.notification_url == CHSL_URL and exam.notice_id == notice.id
    assert exam.application_start == datetime(2026, 9, 7, tzinfo=IST)
    assert exam.application_end == datetime(2026, 10, 7, 23, 0, tzinfo=IST)
    assert exam.fee_payment_end == datetime(2026, 10, 8, 23, 0, tzinfo=IST)
    assert (exam.correction_start, exam.correction_end) == (datetime(2026, 10, 14, tzinfo=IST), datetime(2026, 10, 16, 23, 0, tzinfo=IST))
    assert exam.age_crucial_date == datetime(2026, 8, 1, tzinfo=IST)
    assert exam.education_cutoff_date == datetime(2026, 10, 7, tzinfo=IST)
    assert (exam.fee_amount, exam.tentative_vacancies) == (100, 2536)
    assert json.loads(exam.exam_dates) == [{"stage": "Tier-I (Computer Based Examination)", "schedule": "To be notified later"},
                                           {"stage": "Tier-II (Computer Based Examination)", "schedule": "To be notified later"}]
    posts = {p.post_name: p for p in (await db_session.execute(select(ExamPost))).scalars()}
    assert posts["Lower Division Clerk (LDC)/ Junior Secretariat Assistant (JSA)"].pay_scale == "Pay Level-2 (Rs. 19,900- 63,200)"
    assert posts["Data Entry Operator (DEO)"].pay_level == "Level-4, Level-5"
    assert len(posts) == 3 and all(p.source_page == 1 for p in posts.values())
    prov = {p.field_name: p for p in (await db_session.execute(select(ExamFieldProvenance))).scalars()}
    assert prov["fee_amount"].page == 13 and prov["fee_amount"].excerpt == "Fee payable: Rs 100/- (Rs one hundred only)."
    assert prov["age_crucial_date"].excerpt == "crucial date for age reckoning is fixed as 01-08-2026"
