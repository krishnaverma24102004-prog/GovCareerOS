"""
Real SSC corrigenda/addenda applied to real exams (captured 2026-10-01, tests/fixtures/ssc/sources.json):

  CGLE_Reopen_23062026.pdf      CGL 2026 window re-opened: closing 25.06.2026 (23:00), fee 26.06.2026 (23:00),
                                correction window 01.07.2026 to 03.07.2026 (23:00 hours)
  Important_Notice_2026_cgle_2026_12092026.pdf   CGL 2026 Tier-I (CBE): "30th September, 2026 to 30th October, 2026"
  Addendum_JE_10092026.pdf      JE 2026: the Essential Qualification of Scientific Assistant (IMD) "shall be read as" ...
  English_JE_17092026.pdf       JE 2026: reminder that no extension will be granted (changes nothing)
  Addendum_to_Notice_of_Phase-XIV_2026-20.04.2026-updated.pdf   04 additional DRDO posts for Selection Posts Phase-XIV
"""
import json
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from govcareeros.backend.documents.pdf_text import extract_pages
from govcareeros.backend.models import Exam, ExamPost, ExamRequirement, ExamVersion, RawDocument
from govcareeros.backend.parsing.ssc_corrigendum import parse_corrigendum
from govcareeros.backend.parsing.upsc_advertisement import IST
from govcareeros.backend.services.document_ingestion import get_or_create_source
from govcareeros.backend.services.exam_corrigenda import ingest_corrigendum_pdf
from govcareeros.backend.services.exam_ingestion import ingest_exam_pdf
from govcareeros.backend.sources import ssc

SSC = Path(__file__).parents[1] / "fixtures" / "ssc"
SOURCES = json.loads((SSC / "sources.json").read_text(encoding="utf-8"))
REOPEN, SCHEDULE = "CGLE_Reopen_23062026.pdf", "Important_Notice_2026_cgle_2026_12092026.pdf"
JE_ADDENDUM, JE_REMINDER = "Addendum_JE_10092026.pdf", "English_JE_17092026.pdf"
PHASE_XIV = "Addendum_to_Notice_of_Phase-XIV_2026-20.04.2026-updated.pdf"

def _parse(name):
    body = (SSC / name).read_bytes()
    return parse_corrigendum(body, extract_pages(body, ocr=False))

def test_reopened_window_dates_are_read_with_their_sentences():
    p = _parse(REOPEN)
    assert p.file_numbers == ["HQ-C11018/1/2026-C-1"] and p.notice_date.isoformat() == "2026-06-23"
    assert p.change_types == ["REOPENED_WINDOW", "DATE_CHANGE"] and p.warnings == []
    c = {x.field: x for x in p.changes}
    assert c["application_end"].value == datetime(2026, 6, 25, 23, 0, tzinfo=IST)
    assert c["application_end"].provenance.excerpt.endswith("i.e. from 23.06.2026 (23:00 hours) to 25.06.2026 (23:00 hours)")
    assert c["fee_payment_end"].value == datetime(2026, 6, 26, 23, 0, tzinfo=IST)
    assert (c["correction_start"].value, c["correction_end"].value) == (datetime(2026, 7, 1, tzinfo=IST), datetime(2026, 7, 3, 23, 0, tzinfo=IST))
    assert c["closing_date"].value == "2026-06-25" and c["closing_date"].provenance.page == 1

def test_schedule_post_rows_and_additions_are_read_from_tables():
    s = _parse(SCHEDULE)
    assert [(c.field, c.value, c.provenance.page) for c in s.changes] == [("exam_schedule", {
        "exam_name": "Combined Graduate Level Examination, 2026", "stage": "Tier-I (CBE)",
        "schedule": "30th September, 2026 to 30th October, 2026"}, 1)]
    je = _parse(JE_ADDENDUM)
    assert je.change_types == ["POST_CHANGE"] and je.notice_date.isoformat() == "2026-09-10"
    (row,) = je.post_rows
    assert row.reads == ["EDUCATION"] and row.cells[:3] == ["13", "India Meteorological Department", "Scientific Assistant"]
    # The replacement cell continues on page 2 with Note1
    assert row.cells[3].endswith("Note1: Candidates possessing the qualification mentioned in (2) above must also have passed Class XII "
                                 "examination from a recognised Board with Physics and Mathematics as one of the subjects.")
    xiv = _parse(PHASE_XIV)
    assert xiv.change_types == ["POSTS_ADDED"] and xiv.warnings == []
    assert [(r.post.post_code, r.post.post_name, r.post.age_min, r.post.age_max, r.post.pay_level, r.post.vacancy_count, r.provenance.page)
            for r in xiv.post_rows] == [("NR21226", "Fire Engine Driver ‘A’", 18, 27, "Level-2", 4, 2), ("NR21326", "Firefighter", 18, 27, "Level-2", 8, 2),
                                        ("NR21426", "Security Assistant ‘A’", 18, 27, "Level-2", 17, 2),
                                        ("NR21526", "Vehicle Operator ‘A’", 18, 27, "Level-2", 75, 2)]
    assert xiv.post_rows[2].post.extra == {"region": "NR", "exam_level": "Higher Secondary (10+2)"}
    nothing = _parse(JE_REMINDER)
    assert nothing.changes == [] and nothing.post_rows == [] and nothing.warnings == ["no change this parser knows was found in the notice"]

async def _source(db_session):
    return await get_or_create_source(db_session, ssc.SSC_SOURCE)

async def _exam(db_session, name):
    source = await _source(db_session)
    result = await ingest_exam_pdf(db_session, source, SOURCES[name]["url"], (SSC / name).read_bytes())
    await db_session.commit()
    return await db_session.get(Exam, result.exam_id)

async def _corrigendum(db_session, name):
    source = await _source(db_session)
    result = await ingest_corrigendum_pdf(db_session, source, SOURCES[name]["url"], (SSC / name).read_bytes(), title=SOURCES[name]["title"])
    await db_session.commit()
    return result

@pytest.mark.asyncio
@pytest.mark.parametrize("order", [(REOPEN, SCHEDULE), (SCHEDULE, REOPEN)])
async def test_cgl_corrigenda_change_dates_and_schedule_in_any_order(db_session, order):
    exam = await _exam(db_session, "Notice_of_adv_cgl_2026.pdf")
    assert exam.application_end == datetime(2026, 6, 22, 23, 0, tzinfo=IST)
    results = {name: await _corrigendum(db_session, name) for name in order}
    assert all(r.applied_exam_ids == [exam.id] for r in results.values())
    await db_session.refresh(exam)
    assert exam.application_end == datetime(2026, 6, 25, 23, 0, tzinfo=IST)
    assert exam.fee_payment_end == datetime(2026, 6, 26, 23, 0, tzinfo=IST)
    assert (exam.correction_start, exam.correction_end) == (datetime(2026, 7, 1, tzinfo=IST), datetime(2026, 7, 3, 23, 0, tzinfo=IST))
    assert exam.age_crucial_date == datetime(2026, 8, 1, tzinfo=IST)  # fixed in the notice, not the closing date
    assert json.loads(exam.exam_dates) == [
        {"stage": "Tier-I (Computer Based Examination)", "schedule": "30th September, 2026 to 30th October, 2026",
         "notice_page": 1, "notice_date": "2026-09-12"},
        {"stage": "Tier-II (Computer Based Examination)", "schedule": "December, 2026"}]

    versions = {v.change_type: v for v in (await db_session.execute(select(ExamVersion))).scalars()}
    reopen = json.loads(versions["REOPENED_WINDOW,DATE_CHANGE"].details)
    assert reopen["changes"]["application_end"] == {
        "old": "2026-06-22T23:00:00+05:30",
        "new": "2026-06-25T23:00:00+05:30", "page": 1,
        "quote": "re-open the window for submission of online applications for the Combined Graduate Level Examination, 2026 for "
                 "two days i.e. from 23.06.2026 (23:00 hours) to 25.06.2026 (23:00 hours)"}
    assert reopen["url"] == SOURCES[REOPEN]["url"] and reopen["closing_date"]["value"] == "2026-06-25"
    raw = await db_session.get(RawDocument, versions["SCHEDULE"].raw_document_id)
    assert raw.url == SOURCES[SCHEDULE]["url"]
    assert versions["SCHEDULE"].notice_date == datetime(2026, 9, 12, tzinfo=IST)

    # Same documents again: nothing new; a re-parse of the notice keeps what the corrigenda state
    again = await _corrigendum(db_session, REOPEN)
    assert again.already_applied_exam_ids == [exam.id] and again.applied_exam_ids == []
    exam.parser_version = "ssc_exam_notice/0"
    await db_session.commit()
    exam = await _exam(db_session, "Notice_of_adv_cgl_2026.pdf")
    assert exam.application_end == datetime(2026, 6, 25, 23, 0, tzinfo=IST)
    assert json.loads(exam.exam_dates)[0]["schedule"] == "30th September, 2026 to 30th October, 2026"

@pytest.mark.asyncio
async def test_je_addendum_supersedes_the_post_qualification_only(db_session):
    exam = await _exam(db_session, "Notice_of_adv_je_2026.pdf")
    sa = (await db_session.execute(select(ExamPost).where(ExamPost.post_name == "Scientific Assistant"))).scalar_one()
    old = (await db_session.execute(select(ExamRequirement).where(ExamRequirement.post_id == sa.id,
                                                                  ExamRequirement.req_type == "EDUCATION"))).scalar_one()
    untouched = (await db_session.execute(select(ExamRequirement.id).where(
        ExamRequirement.post_id != sa.id, ExamRequirement.superseded_at.is_(None)))).scalars().all()

    result = await _corrigendum(db_session, JE_ADDENDUM)
    assert result.applied_exam_ids == [exam.id] and result.warnings == []
    await db_session.refresh(old)
    await db_session.refresh(sa)
    assert old.superseded_at is not None
    new = (await db_session.execute(select(ExamRequirement).where(
        ExamRequirement.post_id == sa.id, ExamRequirement.req_type == "EDUCATION", ExamRequirement.superseded_at.is_(None)))).scalar_one()
    raw = await db_session.get(RawDocument, new.raw_document_id)
    assert raw.url == SOURCES[JE_ADDENDUM]["url"] and new.source_page == 1
    assert new.value.startswith("(1) Bachelor’s Degree in Science with Physics") and new.value.endswith("Physics and Mathematics as one of the subjects.")
    assert new.source_section.startswith("Read as (p.1: In this connection, it is hereby informed that the Essential Qualification for "
                                         "the post of SA in IMD at Para 2(13) of the Notice dated 02.09.2026")
    assert sa.education_text == new.value
    # The age limit is restated unchanged ("Up to 30 years"); the other posts keep their rows
    assert sorted(untouched) == sorted((await db_session.execute(select(ExamRequirement.id).where(
        ExamRequirement.post_id != sa.id, ExamRequirement.superseded_at.is_(None)))).scalars().all())
    (version,) = (await db_session.execute(select(ExamVersion))).scalars().all()
    assert version.change_type == "POST_CHANGE" and "Scientific Assistant (India Meteorological Department): EDUCATION" in version.change_log
    assert json.loads(version.details)["post_changes"][0]["old"].startswith("Bachelor’s Degree in Science with Physics")

    # A reminder that names the exam but changes nothing creates no version
    reminder = await _corrigendum(db_session, JE_REMINDER)
    assert reminder.applied_exam_ids == [] and len((await db_session.execute(select(ExamVersion))).scalars().all()) == 1

@pytest.mark.asyncio
async def test_addendum_for_an_exam_not_stored_is_reported_not_guessed(db_session):
    await _exam(db_session, "Notice_of_adv_cgl_2026.pdf")
    result = await _corrigendum(db_session, PHASE_XIV)
    assert result.applied_exam_ids == [] and result.change_types == ["POSTS_ADDED"]
    assert "the notice names no stored exam (by file number or full name)" in result.warnings
    assert (await db_session.execute(select(ExamVersion))).scalars().all() == []

@pytest.mark.asyncio
async def test_versions_endpoint(api, login, db_session):
    exam = await _exam(db_session, "Notice_of_adv_cgl_2026.pdf")
    await _corrigendum(db_session, REOPEN)
    h = await login("reader@example.com")
    r = await api.get(f"/exams/{exam.id}/versions", headers=h)
    assert r.status_code == 200
    (v,) = r.json()
    assert v["change_type"] == "REOPENED_WINDOW,DATE_CHANGE" and v["details"]["changes"]["fee_payment_end"]["new"] == "2026-06-26T23:00:00+05:30"
    assert v["change_log"].startswith("application_end: 2026-06-22T23:00:00+05:30 -> 2026-06-25T23:00:00+05:30 (p.1)")
    detail = (await api.get(f"/exams/{exam.id}", headers=h)).json()
    assert datetime.fromisoformat(detail["application_end"]) == datetime(2026, 6, 25, 23, 0, tzinfo=IST)
    assert {p["classification"] for p in detail["posts"]} >= {"Group “C”"}
    assert (await api.get(f"/exams/{exam.id}/versions")).status_code == 401

def _replay_fetcher():
    """Serves only the captured PDFs; robots.txt answers 404 as it did at capture time."""
    import httpx
    from govcareeros.backend.sources.generic_notices import PageFetcher

    by_url = {SOURCES[n]["url"]: n for n in SOURCES if not n.startswith("_")}
    seen = []

    def handler(request):
        url = str(request.url)
        seen.append(url)
        if url in by_url:
            return httpx.Response(200, headers={"content-type": "application/pdf"}, content=(SSC / by_url[url]).read_bytes())
        return httpx.Response(404, headers={"content-type": "text/html"}, text="<html>404 Not Found</html>")

    fetcher = PageFetcher(allow_browser=False, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    fetcher.seen = seen
    return fetcher

@pytest.mark.asyncio
async def test_pipeline_step_applies_corrigenda_that_name_a_stored_exam(db_session):
    from datetime import timezone

    from govcareeros.backend.models import RecruitmentNotice
    from govcareeros.backend.services.exam_ingestion import _apply_pending_corrigenda
    from govcareeros.backend.services.source_discovery import record_probe
    from govcareeros.backend.sources.generic_notices import NoticeLink, ProbeResult

    cgl = await _exam(db_session, "Notice_of_adv_cgl_2026.pdf")
    await _exam(db_session, "Notice_of_adv_je_2026.pdf")
    source = await _source(db_session)
    names = [REOPEN, SCHEDULE, JE_ADDENDUM, PHASE_XIV]
    links = [NoticeLink(SOURCES[n]["title"], SOURCES[n]["url"], "PDF") for n in names]
    await record_probe(db_session, source, ProbeResult("OK", 200, ssc.notice_board_url(1), notices=links), datetime.now(timezone.utc))
    kinds = {n.url: n.notice_kind for n in (await db_session.execute(select(RecruitmentNotice))).scalars()}
    assert [kinds[SOURCES[n]["url"]] for n in names] == ["CORRIGENDUM", "SCHEDULE", "CORRIGENDUM", "CORRIGENDUM"]

    errors = []
    async with _replay_fetcher() as fetcher:
        applied = await _apply_pending_corrigenda(db_session, source, fetcher, 10, errors)
    await db_session.commit()
    assert errors == [] and len(applied) == 3
    # The Phase-XIV addendum names no stored exam in its title: not downloaded, left for later
    assert SOURCES[PHASE_XIV]["url"] not in fetcher.seen and all(u.startswith("https://ssc.gov.in/") for u in fetcher.seen)
    status = {n.url: n.review_status for n in (await db_session.execute(select(RecruitmentNotice))).scalars()}
    assert [status[SOURCES[n]["url"]] for n in names] == ["PARSED", "PARSED", "PARSED", "NEW"]
    await db_session.refresh(cgl)
    assert cgl.application_end == datetime(2026, 6, 25, 23, 0, tzinfo=IST)
    assert len((await db_session.execute(select(ExamVersion))).scalars().all()) == 3
