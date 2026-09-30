"""
Exam preparation data from official UPSC documents: syllabus (from the examination notice), previous year
question papers, and user study plans built from them.

- Syllabus: the notice PDF is stored as a RawDocument and its syllabus section parsed deterministically
  (backend/parsing/upsc_syllabus.py). Re-ingesting the same bytes replaces that document's rows, so a newer
  parser never leaves stale lines behind. The syllabus of an exam is the one from its most recently retrieved notice.
- Question papers: every PDF on the official listing is recorded (upsert by URL); downloading stores the PDF as a
  RawDocument and links it. Only downloaded papers are used for PYQ practice in study plans.
- Study plans: backend/services/study_plan.py over the syllabus rows; items keep the syllabus page / paper id.
"""
import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Iterable, List, Optional, Sequence, Tuple

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.documents.pdf_text import extract_pages
from govcareeros.backend.models.artifact import RawDocument
from govcareeros.backend.models.exam_prep import QuestionPaper, StudyPlan, StudyPlanItem, Syllabus
from govcareeros.backend.models.source import GovernmentSource
from govcareeros.backend.parsing.upsc_syllabus import parse_syllabus
from govcareeros.backend.services import study_plan
from govcareeros.backend.services.document_ingestion import get_or_create_source, store_raw_document
from govcareeros.backend.sources import upsc, upsc_pyq

@dataclass
class SyllabusIngestResult:
    raw_document_id: uuid.UUID
    raw_document_created: bool
    entries: int
    papers: List[str]
    warnings: List[str] = field(default_factory=list)

async def ingest_syllabus_pdf(
    session: AsyncSession, source: GovernmentSource, url: str, body: bytes, exam_code: str,
    status: int = 200, content_type: str = "application/pdf",
) -> SyllabusIngestResult:
    raw, created = await store_raw_document(session, source, url, body, status, content_type, discovery_method="EXAM_NOTICE")
    parsed = parse_syllabus(extract_pages(body))
    await session.execute(delete(Syllabus).where(Syllabus.raw_document_id == raw.id, Syllabus.exam_code == exam_code))
    count = 0
    for section_order, paper in enumerate(parsed.papers, 1):
        for topic in paper.topics:
            for i, sub in enumerate(topic.subtopics, 1):
                session.add(Syllabus(
                    exam_code=exam_code, section=paper.name, section_order=section_order, section_note=paper.note, part=topic.part,
                    topic=topic.title, topic_order=topic.order, subtopic=sub.text, subtopic_order=i,
                    raw_document_id=raw.id, page=sub.page, parser_version=parsed.parser_version,
                ))
                count += 1
    raw.parser_version = parsed.parser_version
    await session.flush()
    warnings = list(parsed.warnings) + ([] if count else ["no syllabus section found in the notice"])
    return SyllabusIngestResult(raw.id, created, count, [p.name for p in parsed.papers], warnings)

def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()

async def record_papers(session: AsyncSession, links: Iterable[upsc_pyq.PaperLink],
                        listing_url: str = upsc_pyq.LISTING_URL) -> Tuple[int, int]:
    """Upserts listed papers by URL. Returns (created, already known)."""
    now = datetime.now(timezone.utc)
    links = list(links)
    hashes = {_url_hash(l.url): l for l in links}
    existing = {p.url_hash: p for p in (await session.execute(
        select(QuestionPaper).where(QuestionPaper.url_hash.in_(list(hashes))))).scalars()} if hashes else {}
    created = 0
    for h, link in hashes.items():
        paper = existing.get(h)
        if paper is None:
            session.add(QuestionPaper(
                exam_code=link.exam_code, exam_name=link.exam_name, stage=link.stage, year=link.year,
                category=link.category, paper_name=link.paper_name, official_url=link.url, url_hash=h,
                size_label=link.size_label, listing_url=listing_url, first_seen_at=now, last_seen_at=now,
            ))
            created += 1
        else:
            paper.last_seen_at = now
    await session.flush()
    return created, len(hashes) - created

def size_mb(label: Optional[str]) -> Optional[float]:
    m = re.match(r"([\d.]+)\s*([KMG])B", label or "", re.I)
    if not m:
        return None
    return float(m.group(1)) * {"K": 1 / 1024, "M": 1, "G": 1024}[m.group(2).upper()]

async def attach_paper_pdf(session: AsyncSession, source: GovernmentSource, paper: QuestionPaper,
                           doc: upsc.DownloadedDocument) -> RawDocument:
    raw, _ = await store_raw_document(session, source, doc.url, doc.body, doc.status, doc.content_type,
                                      discovery_method="QUESTION_PAPER")
    paper.raw_document_id = raw.id
    await session.flush()
    return raw

@dataclass
class PrepSyncResult:
    source_health: str
    papers_listed: int = 0
    papers_created: int = 0
    papers_downloaded: int = 0
    syllabi: List[SyllabusIngestResult] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

async def download_papers(session: AsyncSession, client: httpx.AsyncClient, source: GovernmentSource,
                          papers: Sequence[QuestionPaper], run: "PrepSyncResult", limit: Optional[int] = None,
                          max_mb: Optional[float] = None) -> None:
    for paper in papers:
        if limit is not None and run.papers_downloaded >= limit:
            break
        mb = size_mb(paper.size_label)
        if max_mb is not None and mb is not None and mb > max_mb:
            run.skipped.append(f"{paper.official_url}: listed as {paper.size_label}, above {max_mb} MB")
            continue
        try:
            doc = await upsc_pyq.download_paper(client, paper.official_url)
            async with session.begin_nested():
                await attach_paper_pdf(session, source, paper, doc)
            run.papers_downloaded += 1
        except upsc.AccessRestricted:
            raise
        except Exception as e:  # one bad paper must not stop the rest
            run.errors.append(f"{paper.official_url}: {type(e).__name__}: {e}")

async def run_prep_sync(
    session: AsyncSession, client: Optional[httpx.AsyncClient] = None, download_exam_codes: Sequence[str] = (),
    download_limit: Optional[int] = None, max_mb: Optional[float] = None, exam_pages: Sequence[str] = (),
) -> PrepSyncResult:
    """Lists all papers; downloads missing papers of `download_exam_codes` (newest first, up to `download_limit`,
    skipping listed sizes above `max_mb`); ingests the syllabus notice linked from each examination page URL."""
    source = await get_or_create_source(session, upsc.UPSC_SOURCE)
    owns_client = client is None
    client = client or upsc.make_client()
    run = PrepSyncResult(source_health="UNKNOWN")
    try:
        links = await upsc_pyq.discover_papers(client)
        run.papers_listed = len(links)
        run.papers_created, _ = await record_papers(session, links)
        if download_exam_codes:
            pending = (await session.execute(
                select(QuestionPaper).where(QuestionPaper.exam_code.in_(list(download_exam_codes)),
                                            QuestionPaper.raw_document_id.is_(None))
                .order_by(QuestionPaper.year.desc(), QuestionPaper.paper_name))).scalars().all()
            await download_papers(session, client, source, pending, run, download_limit, max_mb)
        for page_url in exam_pages:
            try:
                page = await upsc_pyq.fetch_exam_page(client, page_url)
                if not page.notice_urls or not page.exam_code:
                    run.errors.append(f"{page_url}: no examination notice link found")
                    continue
                doc = await upsc.download_pdf(client, page.notice_urls[0])
                async with session.begin_nested():
                    run.syllabi.append(await ingest_syllabus_pdf(session, source, doc.url, doc.body, page.exam_code,
                                                                 doc.status, doc.content_type))
            except upsc.AccessRestricted:
                raise
            except Exception as e:
                run.errors.append(f"{page_url}: {type(e).__name__}: {e}")
        run.source_health = "HEALTHY" if not run.errors else "DEGRADED"
    except upsc.AccessRestricted as e:
        run.source_health = "ACCESS_RESTRICTED"
        run.errors.append(str(e))
    except upsc.RobotsDisallowed as e:
        run.source_health = "ROBOTS_DISALLOWED"
        run.errors.append(str(e))
    except httpx.HTTPError as e:
        run.source_health = "UNREACHABLE"
        run.errors.append(f"{type(e).__name__}: {e}")
    finally:
        if owns_client:
            await client.aclose()
    return run

async def latest_syllabus(session: AsyncSession, exam_code: str) -> Tuple[Optional[RawDocument], List[Syllabus]]:
    """Syllabus rows from the exam's most recently retrieved notice, in document order."""
    raw = (await session.execute(
        select(RawDocument).join(Syllabus, Syllabus.raw_document_id == RawDocument.id)
        .where(Syllabus.exam_code == exam_code)
        .order_by(RawDocument.retrieved_at.desc(), RawDocument.id).limit(1))).scalar_one_or_none()
    if raw is None:
        return None, []
    rows = (await session.execute(
        select(Syllabus).where(Syllabus.raw_document_id == raw.id, Syllabus.exam_code == exam_code)
        .order_by(Syllabus.section_order, Syllabus.topic_order, Syllabus.subtopic_order))).scalars().all()
    return raw, list(rows)

def topics_of(rows: Sequence[Syllabus]) -> List[study_plan.TopicInput]:
    topics: List[study_plan.TopicInput] = []
    index = {}
    for r in rows:
        key = (r.section, r.topic_order)
        if key not in index:
            index[key] = study_plan.TopicInput(section=r.section, topic=r.topic, size=0, page=r.page)
            topics.append(index[key])
        index[key].size += 1
        index[key].page = min(index[key].page, r.page)
    return topics

async def create_study_plan(
    session: AsyncSession, user_id: uuid.UUID, exam_code: str, exam_date: date, weekly_hours: float,
    sections: Optional[Sequence[str]] = None, start: Optional[date] = None,
) -> Tuple[StudyPlan, List[StudyPlanItem]]:
    start = start or date.today()
    raw, rows = await latest_syllabus(session, exam_code)
    if raw is None:
        raise study_plan.PlanError(f"no official syllabus stored for {exam_code}")
    if sections:
        known = {study_plan.section_key(r.section): r.section for r in rows}
        unknown = [s for s in sections if study_plan.section_key(s) not in known]
        if unknown:
            raise study_plan.PlanError(f"not a paper of {exam_code}: {', '.join(unknown)}")
        wanted = {study_plan.section_key(s) for s in sections}
        rows = [r for r in rows if study_plan.section_key(r.section) in wanted]
    papers = (await session.execute(select(QuestionPaper).where(
        QuestionPaper.exam_code == exam_code, QuestionPaper.raw_document_id.is_not(None)))).scalars().all()
    paper_inputs = study_plan.match_papers(
        [study_plan.PaperInput(p.id, p.year, p.paper_name) for p in papers], sections)
    plan = study_plan.build_plan(start, exam_date, weekly_hours, topics_of(rows), paper_inputs)

    record = StudyPlan(
        user_id=user_id, exam_code=exam_code, start_date=start, target_date=exam_date, weekly_hours=weekly_hours,
        total_weeks=plan.total_weeks, revision_weeks=plan.revision_weeks,
        sections=json.dumps(list(sections)) if sections else None, syllabus_raw_document_id=raw.id,
        generator_version=study_plan.GENERATOR_VERSION,
    )
    session.add(record)
    await session.flush()
    items = [StudyPlanItem(
        plan_id=record.id, position=i.position, planned_week=i.week, kind=i.kind, section=i.section, topic=i.topic,
        planned_hours=i.hours, syllabus_page=i.page, question_paper_id=i.question_paper_id, status="PENDING",
    ) for i in plan.items]
    session.add_all(items)
    await session.flush()
    return record, items
