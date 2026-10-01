"""
SSC corrigendum / addendum / important notice PDF -> RawDocument -> parse -> ExamVersion -> exam values recomputed.

Same pattern as vacancy notices (services/notice_ingestion.py + document_ingestion.apply_official_notices):
  * The notice is matched to a stored exam only by its own words: the exam's file number or full name.
  * Each (exam, notice document) gives one ExamVersion whose `details` keep what the notice states, the page
    and the quoted words, and the value it replaced.
  * apply_exam_versions recomputes the exam from the notice's own values (ExamFieldProvenance) plus every
    version in notice-date order, so neither re-parsing the notice nor the order corrigenda arrive in decides the
    result. Post rows "read as" anew supersede the post's requirement rows (never edited, never deleted); posts
    an addendum adds become ExamPost rows citing the addendum.
Nothing is applied that the notice does not state; a stated change the code cannot apply is kept in `details`
under "not_applied" and reported.
"""
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.documents.pdf_text import extract_pages
from govcareeros.backend.models.artifact import RawDocument
from govcareeros.backend.models.exam import Exam, ExamFieldProvenance, ExamPost, ExamRequirement
from govcareeros.backend.models.exam_version import ExamVersion
from govcareeros.backend.models.notice import RecruitmentNotice
from govcareeros.backend.models.source import GovernmentSource
from govcareeros.backend.parsing.ssc_corrigendum import ParsedCorrigendum, StatedPostRow, names_exam, parse_corrigendum
from govcareeros.backend.parsing.ssc_tables import (
    Relaxations, TablePost, norm, parse_age, post_age_requirements,
)
from govcareeros.backend.parsing.upsc_advertisement import IST, Provenance
from govcareeros.backend.services.document_ingestion import _as_text, store_raw_document

DATE_FIELDS = ("application_end", "fee_payment_end", "correction_start", "correction_end")
_OLDEST = datetime.min.replace(tzinfo=timezone.utc)

@dataclass
class CorrigendumApplication:
    url: str
    raw_document_id: Any
    notice_date: Optional[str]
    change_types: List[str]
    applied_exam_ids: List[Any] = field(default_factory=list)
    already_applied_exam_ids: List[Any] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

# ---------- what the notice states for one exam ----------

def _stage_token(stage: str) -> str:
    m = re.match(r"\s*((?:Tier|Paper)\s*-?\s*[IVX]+)\b", stage or "", re.I)
    return norm(m.group(1)) if m else norm(stage)

def _row_matches(post: ExamPost, cells: List[str]) -> bool:
    keys = {norm(c) for c in cells if c}
    return norm(post.post_name) in keys and (not post.department or norm(post.department) in keys)

def _post_change(post: ExamPost, row: StatedPostRow, warnings: List[str]) -> Tuple[List[dict], List[dict]]:
    """(changes, not_applied) for an existing post whose row the notice restates."""
    changes, not_applied = [], []
    cells = [c for c in row.cells if c]
    if row.post is not None:
        education, age_text = row.post.education_text, row.post.age_text
    else:
        named = {norm(post.post_name), norm(post.department)}
        ages = [c for c in cells if parse_age(c)[1] is not None and len(c) < 40]
        rest = [c for c in cells if norm(c) not in named and c not in ages and not re.fullmatch(r"\d+\.?", c)]
        education = max(rest, key=len) if rest and "EDUCATION" in row.reads else None
        age_text = ages[0] if ages else None
    if education and education != post.education_text:
        changes.append({"post_key": post.post_key, "post_name": post.post_name, "department": post.department, "req_type": "EDUCATION",
                        "value": education, "old": post.education_text, "page": row.provenance.page,
                        "section": row.provenance.section[:255], "quote": education})
    if age_text:
        lo, hi = parse_age(age_text)
        if hi != post.age_max or (lo is not None and lo != post.age_min):
            not_applied.append({"post_key": post.post_key, "what": "age limit", "stated": age_text, "page": row.provenance.page})
            warnings.append(f"{post.post_name}: restated age limit {age_text!r} differs from {post.age_min}-{post.age_max}; not applied")
    return changes, not_applied

def _added_post(p: TablePost) -> dict:
    return {"post_key": p.post_key, "post_name": p.post_name, "post_code": p.post_code, "department": p.department,
            "classification": p.classification, "pay_level": p.pay_level, "pay_scale": p.pay_scale, "age_text": p.age_text,
            "age_min": p.age_min, "age_max": p.age_max, "education_text": p.education_text, "vacancy_count": p.vacancy_count,
            "page": p.provenance.page, "section": p.provenance.section, "quote": p.provenance.excerpt, "stated": p.extra}

def stated_for_exam(exam: Exam, posts: List[ExamPost], parsed: ParsedCorrigendum, warnings: List[str]) -> Dict[str, Any]:
    details: Dict[str, Any] = {"changes": {}}
    for c in parsed.changes:
        quote = {"page": c.provenance.page, "quote": c.provenance.excerpt}
        if c.field in DATE_FIELDS:
            old = getattr(exam, c.field)
            details["changes"][c.field] = {"old": old.astimezone(IST).isoformat() if old else None,
                                           "new": c.value.astimezone(IST).isoformat(), **quote}
        elif c.field in ("reopened_window", "closing_date"):
            details[c.field] = {"value": c.value, **quote}
        elif c.field == "exam_schedule" and norm(c.value["exam_name"]) == norm(exam.name):
            details.setdefault("schedule", []).append({"stage": c.value["stage"], "schedule": c.value["schedule"], **quote})
    post_changes, added, not_applied = [], [], []
    for row in parsed.post_rows:
        hits = [p for p in posts if (row.post is not None and p.post_key == row.post.post_key) or _row_matches(p, row.cells)]
        if len(hits) == 1:
            ch, na = _post_change(hits[0], row, warnings)
            post_changes += ch
            not_applied += na
        elif not hits and row.kind == "ADDED" and row.post is not None:
            added.append(_added_post(row.post))
        else:
            what = "matches no stored post" if not hits else f"matches {len(hits)} posts"
            not_applied.append({"row": row.provenance.excerpt[:300], "page": row.provenance.page, "reason": what})
            warnings.append(f"p.{row.provenance.page}: restated row {what}; not applied")
    if post_changes:
        details["post_changes"] = post_changes
    if added:
        details["posts_added"] = added
    if not_applied:
        details["not_applied"] = not_applied
    if not details["changes"]:
        del details["changes"]
    return details

def _change_log(details: Dict[str, Any]) -> str:
    lines = [f"{f}: {c['old']} -> {c['new']} (p.{c['page']})" for f, c in details.get("changes", {}).items()]
    lines += [f"schedule {s['stage']}: {s['schedule']} (p.{s['page']})" for s in details.get("schedule", [])]
    lines += [f"{p['post_name']} ({p['department']}): {p['req_type']} read as stated on p.{p['page']}" for p in details.get("post_changes", [])]
    lines += [f"post added: {p['post_name']} ({p['department']}) p.{p['page']}" for p in details.get("posts_added", [])]
    return "\n".join(lines)

# ---------- recompute ----------

async def _relaxations(session: AsyncSession, exam: Exam) -> Optional[Relaxations]:
    rows = (await session.execute(select(ExamRequirement).where(
        ExamRequirement.exam_id == exam.id, ExamRequirement.post_id.is_(None), ExamRequirement.superseded_at.is_(None),
        ExamRequirement.req_type == "AGE_RELAXATION"))).scalars().all()
    years, cited = {}, {}
    for r in rows:
        v = json.loads(r.value)
        if v.get("category") in ("SC", "ST", "OBC"):
            years[v["category"]] = v["years"]
            cited[v["category"]] = Provenance(r.source_page, r.source_section or "", r.source_excerpt or "")
    return Relaxations(years, cited, []) if years else None

async def _ensure_post_requirement(session: AsyncSession, exam: Exam, post: ExamPost, req_type: str, values: List[Tuple[Any, Provenance]],
                                   raw_document_id) -> None:
    """Active rows of this type for the post become exactly `values` from this document; otherwise superseded and re-added."""
    active = (await session.execute(select(ExamRequirement).where(
        ExamRequirement.post_id == post.id, ExamRequirement.req_type == req_type, ExamRequirement.superseded_at.is_(None)))).scalars().all()
    want = sorted(_as_text(v) for v, _ in values)
    if sorted(r.value for r in active) == want and all(r.raw_document_id == raw_document_id for r in active):
        return
    now = datetime.now(timezone.utc)
    for r in active:
        r.superseded_at = now
    for value, prov in values:
        session.add(ExamRequirement(exam_id=exam.id, post_id=post.id, req_type=req_type, value=_as_text(value), is_mandatory=True,
                                    raw_document_id=raw_document_id, source_page=prov.page, source_section=prov.section[:255],
                                    source_excerpt=prov.excerpt))

async def apply_exam_versions(session: AsyncSession, exam: Exam) -> None:
    rows = (await session.execute(
        select(ExamVersion, RawDocument.retrieved_at).outerjoin(RawDocument, RawDocument.id == ExamVersion.raw_document_id)
        .where(ExamVersion.exam_id == exam.id))).all()
    if not rows:
        return
    base = {p.field_name: p.value for p in (await session.execute(select(ExamFieldProvenance).where(
        ExamFieldProvenance.exam_id == exam.id, ExamFieldProvenance.raw_document_id == exam.raw_document_id))).scalars()}
    values = {f: datetime.fromisoformat(base[f]) if base.get(f) else getattr(exam, f) for f in DATE_FIELDS}
    stages = json.loads(base["exam_dates"]) if base.get("exam_dates") else (json.loads(exam.exam_dates) if exam.exam_dates else [])
    post_changes: Dict[Tuple[str, str], Tuple[ExamVersion, dict]] = {}
    added: Dict[str, Tuple[ExamVersion, dict]] = {}
    for version, _ in sorted(rows, key=lambda r: (r[0].notice_date or _OLDEST, r[1] or _OLDEST)):
        d = json.loads(version.details or "{}")
        for f, c in d.get("changes", {}).items():
            values[f] = datetime.fromisoformat(c["new"])
        for s in d.get("schedule", []):
            hit = next((x for x in stages if _stage_token(x["stage"]) == _stage_token(s["stage"])), None)
            entry = {"stage": hit["stage"] if hit else s["stage"], "schedule": s["schedule"], "notice_page": s["page"],
                     "notice_date": version.notice_date.astimezone(IST).date().isoformat() if version.notice_date else None}
            stages = [entry if x is hit else x for x in stages] if hit else stages + [entry]
        for pc in d.get("post_changes", []):
            post_changes[(pc["post_key"], pc["req_type"])] = (version, pc)
        for pa in d.get("posts_added", []):
            added[pa["post_key"]] = (version, pa)
    for f in DATE_FIELDS:
        setattr(exam, f, values[f])
    exam.exam_dates = json.dumps(stages, ensure_ascii=False) if stages else None

    posts = {p.post_key: p for p in (await session.execute(select(ExamPost).where(ExamPost.exam_id == exam.id))).scalars()}
    relax = await _relaxations(session, exam)
    for key, (version, pa) in added.items():
        post = posts.get(key)
        if post is None:
            post = ExamPost(id=uuid.uuid4(), exam_id=exam.id, post_key=key, post_name=pa["post_name"], post_code=pa["post_code"],
                            department=pa["department"], classification=pa["classification"], pay_level=pa["pay_level"],
                            pay_scale=pa["pay_scale"], age_min=pa["age_min"], age_max=pa["age_max"], education_text=pa["education_text"],
                            vacancy_count=pa["vacancy_count"], raw_document_id=version.raw_document_id, source_page=pa["page"],
                            source_excerpt=pa["quote"])
            session.add(post)
            await session.flush()
            posts[key] = post
        if post.raw_document_id != version.raw_document_id:
            continue  # the post was in the notice itself; the notice's own rows stand
        prov = Provenance(pa["page"], pa["section"], pa["quote"])
        if pa["age_max"] is not None and relax is not None:
            tp = TablePost(key, pa["post_name"], None, pa["post_code"], pa["department"], pa["classification"], pa["pay_level"],
                           pa["pay_scale"], pa["age_text"], pa["age_min"], pa["age_max"], pa["education_text"], pa["vacancy_count"], prov)
            ages = post_age_requirements(tp, relax, [], None, [])
            await _ensure_post_requirement(session, exam, post, "AGE_MAX", [(r.value, r.provenance) for r in ages], version.raw_document_id)
        if pa["education_text"]:
            await _ensure_post_requirement(session, exam, post, "EDUCATION", [(pa["education_text"], prov)], version.raw_document_id)
    for (key, req_type), (version, pc) in post_changes.items():
        post = posts.get(key)
        if post is None:
            continue
        await _ensure_post_requirement(session, exam, post, req_type,
                                       [(pc["value"], Provenance(pc["page"], pc["section"], pc["quote"]))], version.raw_document_id)
        if req_type == "EDUCATION":
            post.education_text = pc["value"]
    await session.flush()
    await refresh_education_summary(session, exam)

async def refresh_education_summary(session: AsyncSession, exam: Exam) -> None:
    """Exam.education_text when the notice states qualifications post-wise: the distinct post paragraphs, in post order.
    An exam with exam-level paragraphs keeps the text the ingestion wrote from them."""
    rows = (await session.execute(select(ExamRequirement, ExamPost.source_page, ExamPost.post_key)
                                  .join(ExamPost, ExamPost.id == ExamRequirement.post_id, isouter=True).where(
        ExamRequirement.exam_id == exam.id, ExamRequirement.req_type == "EDUCATION", ExamRequirement.superseded_at.is_(None)))).all()
    if not rows or any(r.post_id is None for r, _, _ in rows):
        return
    ordered = sorted(rows, key=lambda x: (x[1] or 0, x[0].source_page or 0, x[2] or ""))
    exam.education_text = "\n".join(dict.fromkeys(r.value for r, _, _ in ordered))
    await session.flush()

# ---------- entry points ----------

async def apply_corrigendum(session: AsyncSession, source: GovernmentSource, raw: RawDocument, parsed: ParsedCorrigendum,
                            notice: Optional[RecruitmentNotice] = None, title: Optional[str] = None) -> CorrigendumApplication:
    result = CorrigendumApplication(raw.url, raw.id, parsed.notice_date.isoformat() if parsed.notice_date else None,
                                    parsed.change_types, warnings=list(parsed.warnings))
    exams = (await session.execute(select(Exam).where(Exam.source_id == source.id))).scalars().all()
    matched = [e for e in exams if names_exam(parsed, e.name, e.external_ref)]
    if not matched:
        result.warnings.append("the notice names no stored exam (by file number or full name)")
    notice_dt = datetime.combine(parsed.notice_date, time(0), tzinfo=IST) if parsed.notice_date else None
    for exam in matched:
        if (await session.execute(select(ExamVersion.id).where(ExamVersion.exam_id == exam.id,
                                                               ExamVersion.raw_document_id == raw.id))).scalar_one_or_none():
            result.already_applied_exam_ids.append(exam.id)
            continue
        posts = list((await session.execute(select(ExamPost).where(ExamPost.exam_id == exam.id))).scalars())
        details = stated_for_exam(exam, posts, parsed, result.warnings)
        if not any(k in details for k in ("changes", "schedule", "post_changes", "posts_added")):
            result.warnings.append(f"{exam.name}: named in the notice, but nothing the parser reads applies to it")
            continue
        details.update(title=title, url=raw.url, parser_version=parsed.parser_version)
        session.add(ExamVersion(exam_id=exam.id, raw_document_id=raw.id, notice_id=notice.id if notice else None,
                                version_hash=raw.content_hash, change_type=",".join(parsed.change_types)[:200], notice_date=notice_dt,
                                parser_version=parsed.parser_version, change_log=_change_log(details),
                                details=json.dumps(details, ensure_ascii=False, default=str)))
        await session.flush()
        await apply_exam_versions(session, exam)
        result.applied_exam_ids.append(exam.id)
    if notice is not None:
        notice.raw_document_id = raw.id
        notice.review_status = "PARSED" if result.applied_exam_ids or result.already_applied_exam_ids else "NO_MATCHING_EXAM"
    await session.flush()
    return result

async def ingest_corrigendum_pdf(session: AsyncSession, source: GovernmentSource, url: str, body: bytes,
                                 notice: Optional[RecruitmentNotice] = None, title: Optional[str] = None) -> CorrigendumApplication:
    raw, _ = await store_raw_document(session, source, url, body, 200, "application/pdf")
    parsed = parse_corrigendum(body, extract_pages(body))
    raw.parser_version = parsed.parser_version
    return await apply_corrigendum(session, source, raw, parsed, notice, title or (notice.title if notice else None))
