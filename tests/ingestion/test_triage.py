"""
Backlog triage over tests/fixtures/notices_snapshot.json: 282 real recruitment notice links discovered on
official sites (captured 2026-10-01). "Now" is pinned to the capture time, so every count below is stable.

The only stand-in is the HTTP layer of the draft test: every document fetch answers 404 (no LLM runs, no
document is stored), which shows which notice the draft queue picks next without touching the network.
"""
import json
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from govcareeros.backend.models import GovernmentSource, NoticeTriage, RecruitmentNotice, TriageDecision
from govcareeros.backend.services import run_log
from govcareeros.backend.services.extraction_drafts import draft_pending_notices
from govcareeros.backend.services.source_discovery import url_hash
from govcareeros.backend.services.triage import (
    NOT_REQUIRED, QUEUED, STALE, NoticeFacts, decide, notice_date, triage_notices,
)
from govcareeros.backend.sources.generic_notices import PageFetcher
from govcareeros.backend.sources.registry import SOURCES
from govcareeros.scripts import daily_job, triage_notices as triage_script

SNAPSHOT = json.loads((Path(__file__).parents[1] / "fixtures" / "notices_snapshot.json").read_text(encoding="utf-8"))
NOTICES = SNAPSHOT["notices"]
NOW = datetime.fromisoformat(SNAPSHOT["meta"]["captured_at"])
BY_URL = {}
for _n in NOTICES:
    BY_URL.setdefault(_n["url"], _n)
PARSERS = {s.name: s.parser for s in SOURCES} | {"UPSC": "upsc_advertisement"}

def real(url_tail: str) -> dict:
    """The snapshot notice whose URL ends with url_tail (so every example below is a real captured link)."""
    matches = [n for n in NOTICES if n["url"].endswith(url_tail)]
    assert len({n["url"] for n in matches}) == 1, url_tail
    return matches[0]

async def load_snapshot(session) -> None:
    sources = {}
    for n in NOTICES:
        if n["source"] not in sources:
            sources[n["source"]] = GovernmentSource(
                name=n["source"], organization=n["source"], category=n["source_category"],
                official_domain=n["official_domain"], recruitment_url=f"https://{n['official_domain']}/",
                source_type="DOCUMENT", adapter_name=PARSERS.get(n["source"]))
            session.add(sources[n["source"]])
    await session.flush()
    for n in NOTICES:
        seen = datetime.fromisoformat(n["first_seen_at"])
        session.add(RecruitmentNotice(
            source_id=sources[n["source"]].id, url=n["url"], url_hash=url_hash(n["url"]), title=n["title"],
            document_type=n["document_type"], notice_kind=n["notice_kind"], first_seen_at=seen, last_seen_at=seen,
            review_status=n["review_status"]))
    await session.commit()

# ------------------------------------------------------------------ stated dates on real titles / links

@pytest.mark.parametrize("url_tail, expected, rule, text", [
    # file names
    ("SSB_vacancy_29092026.PDF", date(2026, 9, 29), "url_filename_date", "29092026"),
    ("CircularVacancy_03082026.pdf", date(2026, 8, 3), "url_filename_date", "03082026"),
    ("advtVRDE31082026En.pdf", date(2026, 8, 31), "url_filename_date", "31082026"),
    ("Advertisement_Associate_Legal_27_08_2026.pdf", date(2026, 8, 27), "url_filename_date", "27_08_2026"),
    ("NCI-25-8-26.pdf", date(2026, 8, 25), "url_filename_date", "25-8-26"),
    ("NoticeCancellation-06-Posts-Advt-13-25-Engl-200826.pdf", date(2026, 8, 20), "url_filename_date", "200826"),
    ("27-09-2024na-vacancy.pdf", date(2024, 9, 27), "url_filename_date", "27-09-2024"),
    # the title's two dates (a correction window) are ambiguous; the file name states the notice date
    ("Notice_12-05-2026-994.pdf", date(2026, 5, 12), "url_filename_date", "12-05-2026"),
    ("vacancy-circular-dated-29.12.2025_c.pdf", date(2025, 12, 29), "url_dated", "dated-29.12.2025"),
    # titles
    ("NESACRecruitment24.html", date(2026, 9, 30), "title_dated", "dated 30/09/2026"),
    ("HSFCRecruitment2026_1.html", date(2026, 8, 10), "title_dated", "Dated.10.08.2026"),
    ("URSCRecruitment12.html", date(2026, 7, 29), "title_dated", "Dated:29.07.2026"),
    ("SDSCRecruitment25.html", date(2026, 6, 23), "title_dated", "dated June 23, 2026"),
    ("ICRB_Recruitment9.html", date(2025, 5, 27), "title_dated", "dated 27.05.2025"),
    ("Annexure-I_updated_25.09.2026.pdf", date(2026, 9, 25), "title_as_on", "as on 25.09.2026"),
    # CRPF: the title's release date wins over the file's own number
    ("09012026 compassionate-604.pdf", date(2026, 1, 8), "title_released_on", "released on 08/01/2026"),
    ("https://steel.gov.in/node/4989", date(2026, 7, 4), "title_date", "4.7.26"),
])
def test_stated_date_from_real_notices(url_tail, expected, rule, text):
    n = real(url_tail)
    found = notice_date(n["title"], n["url"])
    assert (found.date, found.rule, found.text, found.precision) == (expected, rule, text, "day")

def test_year_month_folder_has_month_precision_and_counts_from_month_end():
    n = real("/vacancy/2026-09/Vacancy.pdf")
    found = notice_date(n["title"], n["url"])
    assert (found.date, found.rule, found.precision, found.latest()) == (date(2026, 9, 1), "url_year_month", "month", date(2026, 9, 30))

@pytest.mark.parametrize("url_tail", [
    "vacancy/Image3230.pdf",  # "VIEW" link to a scanned image: nothing stated
    "Recuitment Notice 110042026-635.pdf",  # a 9-digit CRPF serial is not a date
    "Notice_for_Recruitment_of_Specialist_Gr_II_Sr_Jr_Scale_in_ESIC_Medical_Institutes_for_PUNJAB_Region_1790320192.pdf",  # upload timestamp
    "55c6c141-5288-11f0-8331-0a1dcac2b80f.pdf",
    "RECT 34022026-716.pdf",  # "34" is no day; the title has the release date instead (checked below)
])
def test_no_date_is_guessed(url_tail):
    n = real(url_tail)
    found = notice_date("", n["url"])
    assert found is None
    if url_tail.startswith("RECT"):
        assert notice_date(n["title"], n["url"]).rule == "title_released_on"
    elif not url_tail.startswith("Notice_for"):
        assert notice_date(n["title"], n["url"]) is None

def test_two_different_dates_in_a_title_are_not_guessed_between():
    n = real("Notice_12-05-2026-994.pdf")
    assert "21/05/2026 to 23/05/2026" in n["title"] and notice_date(n["title"], "") is None

def test_date_rules_across_the_snapshot_are_stable():
    rules = {}
    for n in NOTICES:
        found = notice_date(n["title"], n["url"])
        rules[found.rule if found else None] = rules.get(found.rule if found else None, 0) + 1
    assert rules == {None: 176, "title_dated": 32, "url_filename_date": 28, "url_year_month": 27,
                     "title_released_on": 13, "title_date": 3, "url_dated": 2, "title_as_on": 1}

# ------------------------------------------------------------------ decisions and priority

def facts(n) -> NoticeFacts:
    return NoticeFacts(n["title"], n["url"], n["notice_kind"], n["document_type"], datetime.fromisoformat(n["first_seen_at"]),
                       n["source_category"], bool(PARSERS.get(n["source"])))

def test_decisions_and_priority_terms_on_real_notices():
    fresh = decide(facts(real("SSB_vacancy_29092026.PDF")), NOW)
    assert (fresh.triage_status, fresh.rule, fresh.priority) == (QUEUED, "fresh_stated_date", 103)
    assert fresh.terms == {"kind VACANCY": 40, "recency": 30, "document PDF": 15, "source MINISTRY": 8, "no deterministic parser": 10}
    assert "2 days old, within the 120-day window" in fresh.reason and "= 103" in fresh.reason

    old = decide(facts(real("ICRB_Recruitment9.html")), NOW)  # Advt dated 27.05.2025
    assert (old.triage_status, old.rule) == (STALE, "stale_stated_date") and "492 days old" in old.reason
    amended = decide(facts(real("Recuitment Notice 112042026-256.pdf")), NOW)  # "Amendment dated 17/04/2026"
    assert (amended.triage_status, amended.terms["recency"]) == (STALE, 0)
    # The window is a parameter: a year keeps the April 2026 amendment in the queue
    assert decide(facts(real("Recuitment Notice 112042026-256.pdf")), NOW, stale_days=365).triage_status == QUEUED

    undated = decide(facts(real("vacancy/Image3230.pdf")), NOW)
    assert (undated.triage_status, undated.rule, undated.terms["recency (date not stated)"]) == (QUEUED, "fresh_first_seen", 10)
    result = decide(facts(real("RECT 34022026-716.pdf")), NOW)
    assert (result.triage_status, result.rule) == (NOT_REQUIRED, "not_a_vacancy")
    # A parser source (UPSC) earns no drafting points; an aggregator link fewer than the ministry's own
    upsc = decide(facts(real("AssttPublProseGNCTD-Notice-Engl-170926Rev.pdf")), NOW)
    assert upsc.terms["parser source"] == 0 and upsc.terms["kind CORRIGENDUM"] == 25
    assert decide(facts(real("advtLRDE07092026.pdf")), NOW).terms["source OFFICIAL_AGGREGATOR"] == 2

def test_counts_over_the_snapshot_are_stable_and_non_vacancies_are_never_queued():
    decisions = [(n, decide(facts(n), NOW)) for n in NOTICES]
    count = lambda key: {k: sum(1 for x in decisions if key(x) == k) for k in {key(x) for x in decisions}}
    assert count(lambda x: x[1].triage_status) == {QUEUED: 132, NOT_REQUIRED: 121, STALE: 29}
    assert count(lambda x: x[1].rule) == {"not_a_vacancy": 121, "fresh_first_seen": 83, "fresh_stated_date": 49, "stale_stated_date": 29}
    assert all((d.triage_status == NOT_REQUIRED) == (n["notice_kind"] in ("RESULT", "RULES", "OTHER")) for n, d in decisions)

# ------------------------------------------------------------------ database runs

async def _table_counts(session):
    return [(await session.execute(select(func.count()).select_from(t))).scalar_one() for t in (NoticeTriage, TriageDecision)]

async def _notice_state(session):
    rows = (await session.execute(select(RecruitmentNotice).order_by(RecruitmentNotice.url_hash))).scalars().all()
    return [(r.id, r.title, r.url, r.notice_kind, r.review_status, r.first_seen_at) for r in rows]

@pytest.mark.asyncio
async def test_dry_run_writes_nothing_and_apply_records_every_decision(db_session):
    await load_snapshot(db_session)
    before = await _notice_state(db_session)

    run = await triage_notices(db_session, now=NOW)
    assert run.counts() == {QUEUED: 132, NOT_REQUIRED: 121, STALE: 29} and run.changed() == 282
    await db_session.commit()
    assert await _table_counts(db_session) == [0, 0]

    # The script's default is the same dry run
    lines = []
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False, class_=AsyncSession)
    assert await triage_script.main([], session_factory=factory, now=NOW, out=lines.append) == 0
    assert lines[0].startswith("DRY RUN") and lines[1] == "decisions: NOT_REQUIRED 121, QUEUED 132, STALE 29"
    assert len([l for l in lines if l.startswith("  ")]) == 20
    assert "SSB_vacancy_29092026.PDF" not in "\n".join(lines)  # already REJECTED by a reviewer: not awaiting review
    assert await _table_counts(db_session) == [0, 0]

    assert await triage_script.main(["--apply"], session_factory=factory, now=NOW, out=lines.append) == 0
    db_session.expire_all()
    assert await _table_counts(db_session) == [282, 282]
    stale = (await db_session.execute(select(NoticeTriage, RecruitmentNotice).join(RecruitmentNotice)
                                      .where(RecruitmentNotice.url.endswith("ICRB_Recruitment9.html")))).one()
    assert (stale[0].triage_status, stale[0].rule, stale[0].notice_date, stale[0].notice_date_rule, stale[0].notice_date_text) == \
        (STALE, "stale_stated_date", date(2025, 5, 27), "title_dated", "dated 27.05.2025")

    # Re-running changes nothing; a wider window re-queues only the notices it affects, and logs just those
    assert (await triage_notices(db_session, now=NOW, apply=True)).changed() == 0
    wider = await triage_notices(db_session, now=NOW, stale_days=365, apply=True)
    await db_session.commit()
    assert wider.counts()[STALE] < 29
    assert await _table_counts(db_session) == [282, 282 + wider.changed()]
    # Notices are never deleted or edited by triage
    assert await _notice_state(db_session) == before

def _not_found(seen):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(404)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)

@pytest.mark.asyncio
async def test_drafts_follow_priority_and_skip_stale_and_not_required(db_session):
    await load_snapshot(db_session)
    await triage_notices(db_session, now=NOW, apply=True)
    await db_session.commit()
    rows = (await db_session.execute(
        select(RecruitmentNotice, NoticeTriage, GovernmentSource).join(NoticeTriage).join(GovernmentSource))).all()
    eligible = [(t.priority, n.first_seen_at, n.url_hash, n.id) for n, t, s in rows
                if n.notice_kind == "VACANCY" and n.document_type == "PDF" and n.review_status == "MANUAL_REVIEW"
                and s.adapter_name is None and t.triage_status == QUEUED]
    expected = [i for *_, i in sorted(eligible, key=lambda e: (-e[0], -e[1].timestamp(), e[2]))]
    assert len(expected) == 63

    # One notice per call: the queue always takes the highest-priority notice left
    order, seen = [], []
    async with PageFetcher(allow_browser=False, client=_not_found(seen)) as fetcher:
        while True:
            before = {n.id: n.review_status for n in (await db_session.execute(select(RecruitmentNotice))).scalars()}
            assert await draft_pending_notices(db_session, fetcher, provider=None, limit=1) == []
            touched = [n.id for n in (await db_session.execute(select(RecruitmentNotice))).scalars() if before[n.id] != n.review_status]
            if not touched:
                break
            order += touched
    assert order == expected
    assert not any(u.endswith("SSB_vacancy_29092026.PDF") for u in seen)  # REJECTED by a reviewer: stays out
    settled = {n.id: n for n in (await db_session.execute(select(RecruitmentNotice))).scalars()}
    assert {settled[i].review_status for i in order} <= {"FETCH_FAILED_404", "URL_REFUSED", "ROBOTS_DISALLOWED"}
    # Nothing STALE / NOT_REQUIRED (and so no RESULT / RULES / OTHER notice) was ever fetched
    triage = {t.notice_id: t.triage_status for t in (await db_session.execute(select(NoticeTriage))).scalars()}
    assert all(triage[i] == QUEUED and settled[i].notice_kind == "VACANCY" for i in order)
    assert not any(BY_URL.get(u, {}).get("notice_kind") in ("RESULT", "RULES", "OTHER") for u in seen)
    stale_pdf = real("Recuitment Notice 112042026-256.pdf")["url"]
    assert settled[next(i for i, n in settled.items() if n.url == stale_pdf)].review_status == "MANUAL_REVIEW"

@pytest.mark.asyncio
async def test_daily_job_runs_triage_before_drafts(db_session):
    await load_snapshot(db_session)
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False, class_=AsyncSession)
    assert daily_job.STEPS.index("triage") == daily_job.STEPS.index("drafts") - 1
    assert await daily_job.main(["--only", "triage"], session_factory=factory, now=NOW) == 0
    steps = {s["name"]: s for s in run_log.last_run()["steps"]}
    assert steps["triage"]["status"] == "ok"
    assert steps["triage"]["counts"]["by_status"] == {QUEUED: 132, NOT_REQUIRED: 121, STALE: 29}
    assert steps["triage"]["counts"]["applied"] is True
    db_session.expire_all()
    assert await _table_counts(db_session) == [282, 282]

# Regressions found on the live sweep of 1 Oct 2026 (strings copied verbatim from the official listings)

@pytest.mark.parametrize("title,url", [
    ("23D8C27D35294E888CE27F769E170330.pdf", "https://rpsc.rajasthan.gov.in/Static/RecruitmentAdvertisements/23D8C27D35294E888CE27F769E170330.pdf"),
    ("8FAB8DD230894E36B8418E2BFAD98E42.pdf", "https://rpsc.rajasthan.gov.in/Static/RecruitmentAdvertisements/8FAB8DD230894E36B8418E2BFAD98E42.pdf"),
])
def test_opaque_file_ids_are_not_read_as_dates(title, url):
    # The digit runs in RPSC's hex ids used to come out as 2030/2094 "dates"
    assert notice_date(title, url) is None

def test_real_dates_still_read_from_file_names():
    assert notice_date("ATTACHMENT", "https://gpsc.gujarat.gov.in/Documents/AdvertismentDocument/IN-CD-290926.pdf").date.isoformat() == "2026-09-29"

@pytest.mark.parametrize("title,url", [
    ("Announcement dated 30.09.2026 regarding schedule of Interview for the post of ADA", "https://hpsc.gov.in/Portals/0/Ann_Int_ADA_30_09_2026.pdf"),
    ("Announcement dated 28.09.2026 regarding Scheme and Pattern of Examination and Syllabus for SO", "https://hpsc.gov.in/Portals/0/Ann_Sch_Pattern_Syllabus_SO_28_09_2026.pdf"),
    ("Announcement dated 25.09.2026 regarding Schedule of Physical Measurement Test for DSP", "https://hpsc.gov.in/Portals/0/Ann_Sch_PMT_DSP_25.09.2026.pdf"),
])
def test_exam_process_announcements_are_not_vacancies(title, url):
    from govcareeros.backend.sources.notice_classifier import classify_notice
    assert classify_notice(title, url) == "EXAM_SCHEDULE"

@pytest.mark.asyncio
async def test_reclassify_brings_stored_notices_up_to_the_current_classifier(db_session):
    await load_snapshot(db_session)
    hpsc = GovernmentSource(name="HPSC", organization="Haryana Public Service Commission", category="STATE_PSC",
                            official_domain="hpsc.gov.in", recruitment_url="https://hpsc.gov.in/", source_type="DOCUMENT")
    db_session.add(hpsc)
    await db_session.flush()
    # Stored as VACANCY by the classifier before EXAM_SCHEDULE existed (real HPSC listing entry, 1 Oct 2026)
    url = "https://hpsc.gov.in/Portals/0/Ann_Sch_PMT_DSP_25.09.2026.pdf"
    db_session.add(RecruitmentNotice(
        source_id=hpsc.id, url=url, url_hash=url_hash(url), document_type="PDF", notice_kind="VACANCY",
        title="Announcement dated 25.09.2026 regarding Schedule of Physical Measurement Test for DSP",
        first_seen_at=NOW, last_seen_at=NOW, review_status="MANUAL_REVIEW"))
    await db_session.commit()

    factory = async_sessionmaker(db_session.bind, expire_on_commit=False, class_=AsyncSession)
    lines = []
    assert await triage_script.main(["--reclassify"], session_factory=factory, now=NOW, out=lines.append) == 0
    assert "VACANCY->EXAM_SCHEDULE" in lines[0] and lines[1].startswith("DRY RUN")
    db_session.expire_all()
    kind = select(RecruitmentNotice.notice_kind).where(RecruitmentNotice.url == url)
    assert (await db_session.execute(kind)).scalar_one() == "VACANCY"  # dry run wrote nothing

    assert await triage_script.main(["--reclassify", "--apply"], session_factory=factory, now=NOW, out=lines.append) == 0
    db_session.expire_all()
    assert (await db_session.execute(kind)).scalar_one() == "EXAM_SCHEDULE"
    status = select(NoticeTriage.triage_status).join(RecruitmentNotice).where(RecruitmentNotice.url == url)
    assert (await db_session.execute(status)).scalar_one() == NOT_REQUIRED
    # Idempotent: a second pass finds nothing to change
    from govcareeros.backend.services.source_discovery import reclassify_notices
    assert await reclassify_notices(db_session) == {}
