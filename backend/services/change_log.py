"""
Field-level change log between consecutive official versions of an exam or a vacancy, and the alerts it drives.

Every change carries the evidence for the value it replaced AND for the new value: document, page, quoted words.
The log is derived, not stored. It is replayed from what is already kept, in the order the values themselves are
recomputed (apply_exam_versions / apply_official_notices: notice date, then retrieval), so it reads the same
whatever order the corrigenda arrived in:
  start   the notice's own values: ExamFieldProvenance / VacancyFieldProvenance, a post's requirement rows and row
  then    each ExamVersion / VacancyVersion `details` (what the corrigendum states, its page and sentence)
Fields: last date, fee date, correction window, exam schedule, a post's qualification text ("shall be read as"),
posts added with their vacancy counts, a restated age limit (reported; not applied, see exam_corrigenda), notice
conditions and cancellation. Fee amounts and age limits change here only once a parser reads such a change from a
notice; none of the captured corrigenda states one.

Alerts: a version of an exam or vacancy the user tracks an Application for (not withdrawn), or holds an ELIGIBLE /
CONDITIONAL verdict on for the latest profile version, becomes one alert per (user, version): "exam_notice:<id>",
or "notice:<id>" for vacancies, the key the radar's own notice alert uses, so re-runs and the two paths never
duplicate. The digest reports the same changes with both citations.
"""
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.models.application import Application
from govcareeros.backend.models.artifact import RawDocument
from govcareeros.backend.models.eligibility import EligibilityResult
from govcareeros.backend.models.exam import Exam, ExamEligibilityResult, ExamFieldProvenance, ExamPost, ExamRequirement
from govcareeros.backend.models.exam_version import ExamVersion
from govcareeros.backend.models.user import ProfileVersion
from govcareeros.backend.models.vacancy import Vacancy, VacancyFieldProvenance, VacancyVersion
from govcareeros.backend.parsing.upsc_advertisement import IST
from govcareeros.backend.services.exam_corrigenda import DATE_FIELDS, _OLDEST, _stage_token

# Exam date columns -> (change log field, label)
EXAM_DATE_FIELDS = {"application_end": ("LAST_DATE", "Last date for online applications"),
                    "fee_payment_end": ("FEE_DATE", "Last date for online fee payment"),
                    "correction_start": ("CORRECTION_START", "Correction window opens"),
                    "correction_end": ("CORRECTION_END", "Correction window closes")}
_DATE_LOG_FIELDS = {"LAST_DATE", "FEE_DATE", "CORRECTION_START", "CORRECTION_END"}
_ALERTABLE = ("ELIGIBLE", "CONDITIONAL")

@dataclass
class Evidence:
    document: str
    url: Optional[str]
    page: Optional[int]
    quote: Optional[str]
    notice_date: Optional[str] = None  # IST date of the document, when it states one

@dataclass
class FieldChange:
    field: str      # LAST_DATE, FEE_DATE, CORRECTION_START, CORRECTION_END, EXAM_DATE, ELIGIBILITY_TEXT, POST_ADDED, AGE_LIMIT, STATUS, CONDITION
    label: str
    subject: Optional[str]  # the post or exam stage the change is about; None for the whole exam/vacancy
    old: Optional[str]
    new: Optional[str]
    old_evidence: Optional[Evidence]  # None only when nothing stood before (a post or condition the notice adds)
    new_evidence: Evidence
    applied: bool = True  # False: stated by the notice but not applied (exam_corrigenda "not_applied")

@dataclass
class VersionChanges:
    version_id: str
    change_type: Optional[str]
    notice_date: Optional[str]
    document: str
    url: Optional[str]
    changes: List[FieldChange] = field(default_factory=list)

@dataclass
class _Replay:
    versions: List[VersionChanges]
    current: Dict[str, Tuple[Optional[str], Optional[Evidence]]]  # field -> value and the evidence it stands on now

def fmt_value(f: str, value: Optional[str]) -> str:
    if value is None:
        return "not stated"
    if f in _DATE_LOG_FIELDS:
        return datetime.fromisoformat(value).astimezone(IST).strftime("%d %b %Y %H:%M IST")
    value = re.sub(r"\s+", " ", value).strip()
    return value if len(value) <= 160 else value[:159].rstrip() + "…"

def _page(ev: Evidence) -> str:
    return f" (p.{ev.page})" if ev.page else ""  # a listing entry's own words have no page

def change_lines(vc: VersionChanges) -> List[str]:
    """One line per change for people: 'Last date ...: 22 Jun 2026 23:00 IST (p.1) -> 25 Jun 2026 23:00 IST (p.1)'."""
    out = []
    for c in vc.changes:
        what = f"{c.label} ({c.subject})" if c.subject else c.label
        was = fmt_value(c.field, c.old) + (_page(c.old_evidence) if c.old_evidence else "")
        line = f"{what}: {was} -> {fmt_value(c.field, c.new)}{_page(c.new_evidence)}"
        out.append(line + ("; stated, not applied" if not c.applied else ""))
    return out

def _narrow(excerpt: Optional[str], start: str, end: str) -> Optional[str]:
    """The part of a provenance excerpt from `start` through `end`, when both are in it (still verbatim)."""
    if not excerpt:
        return excerpt
    i = excerpt.find(start)
    j = excerpt.find(end, i + len(start)) if i >= 0 else -1
    return excerpt[i:j + len(end)] if i >= 0 and j >= 0 else excerpt

def _notice_day(dt: Optional[datetime]) -> Optional[str]:
    return dt.astimezone(IST).date().isoformat() if dt else None

# ---------- exams ----------

async def _replay_exam(session: AsyncSession, exam: Exam) -> _Replay:
    base_url = (await session.execute(select(RawDocument.url).where(RawDocument.id == exam.raw_document_id))).scalar_one_or_none() \
        if exam.raw_document_id else None
    base_doc = f"{exam.name} notice"
    prov = {p.field_name: p for p in (await session.execute(select(ExamFieldProvenance).where(
        ExamFieldProvenance.exam_id == exam.id, ExamFieldProvenance.raw_document_id == exam.raw_document_id))).scalars()}

    def base_ev(page, quote) -> Evidence:
        return Evidence(base_doc, base_url or exam.notification_url, page, quote)

    current: Dict[str, Tuple[Optional[str], Optional[Evidence]]] = {
        f: (prov[f].value, base_ev(prov[f].page, prov[f].excerpt)) if f in prov else (None, None) for f in DATE_FIELDS}
    stages: List[Tuple[str, Optional[str], Optional[Evidence]]] = []
    if "exam_dates" in prov and prov["exam_dates"].value:
        p = prov["exam_dates"]
        stages = [(s["stage"], s["schedule"], base_ev(p.page, _narrow(p.excerpt, s["stage"], s["schedule"])))
                  for s in json.loads(p.value)]

    posts = {p.post_key: p for p in (await session.execute(select(ExamPost).where(ExamPost.exam_id == exam.id))).scalars()}
    by_id = {p.id: p for p in posts.values()}
    # The notice's own requirement rows per post (superseded or not: they are what stood before any corrigendum)
    post_rows: Dict[Tuple[str, str], Tuple[Optional[str], Optional[Evidence]]] = {}
    rows = (await session.execute(select(ExamRequirement).where(
        ExamRequirement.exam_id == exam.id, ExamRequirement.post_id.is_not(None), ExamRequirement.req_type == "EDUCATION",
        ExamRequirement.raw_document_id == exam.raw_document_id).order_by(ExamRequirement.created_at))).scalars().all()
    for r in rows:  # a re-parse of the notice adds newer rows; the latest parse wins
        post = by_id.get(r.post_id)
        if post is not None:
            post_rows[(post.post_key, r.req_type)] = (r.value, base_ev(r.source_page, r.source_excerpt))

    versions = (await session.execute(
        select(ExamVersion, RawDocument.url, RawDocument.retrieved_at).outerjoin(RawDocument, RawDocument.id == ExamVersion.raw_document_id)
        .where(ExamVersion.exam_id == exam.id))).all()
    out: List[VersionChanges] = []
    for version, url, _ in sorted(versions, key=lambda r: (r[0].notice_date or _OLDEST, r[2] or _OLDEST)):
        d = json.loads(version.details or "{}")
        url = d.get("url") or url
        title = d.get("title") or f"{version.change_type or 'Official'} notice"
        day = _notice_day(version.notice_date)
        vc = VersionChanges(str(version.id), version.change_type, day, title, url)

        def ev(page, quote) -> Evidence:
            return Evidence(title, url, page, quote, day)

        for f, c in d.get("changes", {}).items():
            log_field, label = EXAM_DATE_FIELDS[f]
            old, old_ev = current.get(f, (None, None))
            new_ev = ev(c.get("page"), c.get("quote"))
            vc.changes.append(FieldChange(log_field, label, None, old if old_ev else c.get("old"), c["new"], old_ev, new_ev))
            current[f] = (c["new"], new_ev)
        for s in d.get("schedule", []):
            i = next((n for n, x in enumerate(stages) if _stage_token(x[0]) == _stage_token(s["stage"])), None)
            new_ev = ev(s.get("page"), s.get("quote"))
            stage, old, old_ev = stages[i] if i is not None else (s["stage"], None, None)
            vc.changes.append(FieldChange("EXAM_DATE", "Exam schedule", stage, old, s["schedule"], old_ev, new_ev))
            if i is None:
                stages.append((stage, s["schedule"], new_ev))
            else:
                stages[i] = (stage, s["schedule"], new_ev)
        for pc in d.get("post_changes", []):
            key = (pc["post_key"], pc["req_type"])
            old, old_ev = post_rows.get(key, (pc.get("old"), None))
            new_ev = ev(pc.get("page"), pc.get("quote"))
            subject = f"{pc['post_name']} ({pc['department']})" if pc.get("department") else pc["post_name"]
            label = "Essential qualification" if pc["req_type"] == "EDUCATION" else pc["req_type"].replace("_", " ").title()
            vc.changes.append(FieldChange("ELIGIBILITY_TEXT", label, subject, old, pc["value"], old_ev, new_ev))
            post_rows[key] = (pc["value"], new_ev)
        for pa in d.get("posts_added", []):
            post = posts.get(pa["post_key"])
            if post is not None and post.raw_document_id == exam.raw_document_id:
                continue  # the post was in the notice itself; nothing was added
            subject = f"{pa['post_name']} ({pa['department']})" if pa.get("department") else pa["post_name"]
            n = pa.get("vacancy_count")
            vc.changes.append(FieldChange("POST_ADDED", "Post added (vacancies)", subject, None,
                                          str(n) if n is not None else "number not stated", None, ev(pa.get("page"), pa.get("quote"))))
        for na in d.get("not_applied", []):
            post = posts.get(na.get("post_key"))
            if na.get("what") != "age limit" or post is None:
                continue
            was = f"{post.age_min}-{post.age_max} years" if post.age_min is not None else f"up to {post.age_max} years"
            vc.changes.append(FieldChange("AGE_LIMIT", "Age limit", post.post_name, was, na["stated"],
                                          base_ev(post.source_page, post.source_excerpt), ev(na.get("page"), na["stated"]), applied=False))
        out.append(vc)
    for stage, schedule, sev in stages:
        current[f"exam_dates:{stage}"] = (schedule, sev)
    return _Replay(out, current)

async def exam_change_log(session: AsyncSession, exam: Exam) -> List[VersionChanges]:
    """Every official version of the exam, oldest notice first, each with its field-level changes."""
    return (await _replay_exam(session, exam)).versions

async def exam_current_evidence(session: AsyncSession, exam: Exam, column: str) -> Tuple[Optional[str], Optional[Evidence]]:
    """The exam's current value of a date column and the document, page and words it stands on now."""
    return (await _replay_exam(session, exam)).current.get(column, (None, None))

# ---------- vacancies ----------

def _advt_label(v: Vacancy) -> str:
    return f"{v.organization}, Advt {v.advertisement_number}" if v.advertisement_number else v.organization

async def _replay_vacancy(session: AsyncSession, vacancy: Vacancy) -> _Replay:
    base_url = (await session.execute(select(RawDocument.url).where(RawDocument.id == vacancy.raw_document_id))).scalar_one_or_none() \
        if vacancy.raw_document_id else None
    prov = {p.field_name: p for p in (await session.execute(select(VacancyFieldProvenance).where(
        VacancyFieldProvenance.vacancy_id == vacancy.id, VacancyFieldProvenance.raw_document_id == vacancy.raw_document_id))).scalars()}

    def base_ev(name) -> Optional[Evidence]:
        p = prov.get(name)
        return Evidence(_advt_label(vacancy), base_url or vacancy.notification_url, p.page, p.excerpt) if p else None

    deadline = (prov["application_deadline"].value, base_ev("application_deadline")) if "application_deadline" in prov else (None, None)
    status: Tuple[Optional[str], Optional[Evidence]] = ("ACTIVE", base_ev("post_name"))  # advertised: the post as the advertisement states it
    rows = (await session.execute(
        select(VacancyVersion, RawDocument.url, RawDocument.retrieved_at).outerjoin(RawDocument, RawDocument.id == VacancyVersion.raw_document_id)
        .where(VacancyVersion.vacancy_id == vacancy.id))).all()
    out: List[VersionChanges] = []
    for vv, url, _ in sorted(rows, key=lambda r: (r[0].notice_date or _OLDEST, r[2] or _OLDEST)):
        d = json.loads(vv.details or "{}")
        url = d.get("url") or url
        title = d.get("title") or vv.change_log or "Official notice"
        day = _notice_day(vv.notice_date)
        vc = VersionChanges(str(vv.id), vv.change_type, day, title, url)
        change = d.get("deadline_change") or {}
        if change.get("new"):
            new_ev = Evidence(title, url, change.get("page"), change.get("sentence"), day)
            vc.changes.append(FieldChange("LAST_DATE", "Closing date", None, deadline[0] if deadline[1] else change.get("old"),
                                          change["new"], deadline[1], new_ev))
            deadline = (change["new"], new_ev)
        for c in d.get("conditions", []):
            vc.changes.append(FieldChange("CONDITION", "Condition stated by the notice", None, None, c["text"], None,
                                          Evidence(title, url, c.get("page"), c["text"], day)))
        if d.get("cancellation"):
            new_ev = Evidence(title, url, d["cancellation"].get("page"), d["cancellation"].get("sentence"), day)
            vc.changes.append(FieldChange("STATUS", "Status", None, d["cancellation"].get("previous_status") or status[0],
                                          "CANCELLED", status[1], new_ev))
            status = ("CANCELLED", new_ev)
        out.append(vc)
    return _Replay(out, {"application_deadline": deadline, "status": status})

async def vacancy_change_log(session: AsyncSession, vacancy: Vacancy) -> List[VersionChanges]:
    return (await _replay_vacancy(session, vacancy)).versions

async def vacancy_current_evidence(session: AsyncSession, vacancy: Vacancy, name: str) -> Tuple[Optional[str], Optional[Evidence]]:
    """"application_deadline" or "status" as it stands now, with the document, page and words it stands on."""
    return (await _replay_vacancy(session, vacancy)).current.get(name, (None, None))

# ---------- who is told ----------

async def watched_targets(session: AsyncSession, user_id: uuid.UUID) -> Tuple[Dict[uuid.UUID, str], Dict[uuid.UUID, str]]:
    """(exam_id -> reason, vacancy_id -> reason): tracked applications (not withdrawn), and ELIGIBLE / CONDITIONAL
    verdicts of the latest profile version (the latest verdict per vacancy, per exam post)."""
    exams: Dict[uuid.UUID, str] = {}
    vacancies: Dict[uuid.UUID, str] = {}
    for app in (await session.execute(select(Application).where(Application.user_id == user_id, Application.status != "WITHDRAWN"))).scalars():
        why = f"you track an application for it (status {app.status})"
        if app.exam_id:
            exams[app.exam_id] = why
        if app.vacancy_id:
            vacancies[app.vacancy_id] = why
    version = (await session.execute(select(ProfileVersion).where(ProfileVersion.user_id == user_id)
                                      .order_by(ProfileVersion.version_number.desc()).limit(1))).scalar_one_or_none()
    if version is None:
        return exams, vacancies
    latest: Dict[Any, Tuple[Any, str]] = {}
    for r in (await session.execute(select(ExamEligibilityResult).where(ExamEligibilityResult.profile_version_id == version.id)
                                    .order_by(ExamEligibilityResult.created_at))).scalars():
        latest[(r.exam_id, r.post_id)] = (r.exam_id, r.status)
    for exam_id, status in latest.values():
        if status in _ALERTABLE:
            exams.setdefault(exam_id, f"profile v{version.version_number} is {status} for it")
    latest = {}
    for r in (await session.execute(select(EligibilityResult).where(EligibilityResult.profile_version_id == version.id)
                                    .order_by(EligibilityResult.created_at))).scalars():
        latest[r.vacancy_id] = (r.vacancy_id, r.status)
    for vacancy_id, status in latest.values():
        if status in _ALERTABLE:
            vacancies.setdefault(vacancy_id, f"profile v{version.version_number} is {status} for it")
    return exams, vacancies

def _dated(day: Optional[str]) -> str:
    return f" dated {datetime.fromisoformat(day).strftime('%d %b %Y')}" if day else ""

def exam_alert(user_id: uuid.UUID, exam: Exam, version: ExamVersion, vc: VersionChanges, why: str) -> dict:
    return dict(kind="EXAM_UPDATED", dedupe_key=f"exam_notice:{version.id}", user_id=user_id, exam_id=exam.id, exam_version_id=version.id,
                title=f"Official change: {exam.name}",
                body="\n".join([f"{vc.change_type} notice{_dated(vc.notice_date)} for {exam.name} ({why}): {vc.document}"]
                               + change_lines(vc)),
                due_at=exam.application_end)

def vacancy_alert(user_id: uuid.UUID, vacancy: Vacancy, vv: VacancyVersion, vc: Optional[VersionChanges], why: Optional[str]) -> dict:
    """Same key and kind as the radar's notice alert (whichever path runs first writes it); the change lines follow."""
    cancelled = vacancy.status == "CANCELLED" and "cancellation" in json.loads(vv.details or "{}")
    head = f"{vv.change_type} notice" + (f" dated {vv.notice_date.astimezone(IST).strftime('%d %b %Y')}" if vv.notice_date else "") \
        + f" for {vacancy.post_name}: {vv.change_log or ''}".rstrip()
    lines = change_lines(vc) if vc else []
    return dict(kind="VACANCY_CANCELLED" if cancelled else "VACANCY_UPDATED", dedupe_key=f"notice:{vv.id}", user_id=user_id,
                vacancy_id=vacancy.id, vacancy_version_id=vv.id,
                title=("Cancelled: " if cancelled else "Official notice: ") + vacancy.post_name,
                body="\n".join([head + (f" ({why})" if why else "")] + lines), due_at=vacancy.application_deadline)

class LogCache:
    """Change logs read once per exam / vacancy within one radar or digest run."""
    def __init__(self, session: AsyncSession):
        self.session = session
        self.exams: Dict[uuid.UUID, Dict[str, VersionChanges]] = {}
        self.vacancies: Dict[uuid.UUID, Dict[str, VersionChanges]] = {}

    async def exam(self, exam: Exam, version_id) -> Optional[VersionChanges]:
        if exam.id not in self.exams:
            self.exams[exam.id] = {vc.version_id: vc for vc in await exam_change_log(self.session, exam)}
        return self.exams[exam.id].get(str(version_id))

    async def vacancy(self, vacancy: Vacancy, version_id) -> Optional[VersionChanges]:
        if vacancy.id not in self.vacancies:
            self.vacancies[vacancy.id] = {vc.version_id: vc for vc in await vacancy_change_log(self.session, vacancy)}
        return self.vacancies[vacancy.id].get(str(version_id))

async def watched_changes(session: AsyncSession, user_id: uuid.UUID, cache: Optional[LogCache] = None
                          ) -> List[Tuple[str, Any, Any, VersionChanges, str]]:
    """("exam" | "vacancy", exam/vacancy, version row, its changes, why the user is told) for every watched target."""
    cache = cache or LogCache(session)
    exams, vacancies = await watched_targets(session, user_id)
    out = []
    if exams:
        for version, exam in (await session.execute(select(ExamVersion, Exam).join(Exam, Exam.id == ExamVersion.exam_id)
                                                    .where(ExamVersion.exam_id.in_(list(exams))))).all():
            vc = await cache.exam(exam, version.id)
            if vc and vc.changes:
                out.append(("exam", exam, version, vc, exams[exam.id]))
    if vacancies:
        for vv, vacancy in (await session.execute(select(VacancyVersion, Vacancy).join(Vacancy, Vacancy.id == VacancyVersion.vacancy_id)
                                                  .where(VacancyVersion.vacancy_id.in_(list(vacancies))))).all():
            vc = await cache.vacancy(vacancy, vv.id)
            if vc and vc.changes:
                out.append(("vacancy", vacancy, vv, vc, vacancies[vacancy.id]))
    return out

async def change_alerts(session: AsyncSession, user_id: uuid.UUID, cache: Optional[LogCache] = None) -> List[dict]:
    """Alert values (for radar._add_alert) for official changes to what the user applied for or is eligible for."""
    return [exam_alert(user_id, target, version, vc, why) if kind == "exam" else vacancy_alert(user_id, target, version, vc, why)
            for kind, target, version, vc, why in await watched_changes(session, user_id, cache)]
