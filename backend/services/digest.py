"""
Weekly cited digest for a user's LATEST profile version.

Sections, all built from stored deterministic data:
  matches      ELIGIBLE / CONDITIONAL / MANUAL_REVIEW verdicts made in the period, on open ACTIVE vacancies.
               NEW, or UPDATED when an earlier verdict for the same vacancy exists (new profile version,
               official notice, reparse). Every UNKNOWN / CONDITIONAL check is listed as an open check with
               the requirement it concerns, e.g. "check experience relevance — p.8".
  deadlines    those vacancies closing within the next 14 days, whenever the verdict was made
  age_headroom within AGE_HEADROOM_YEARS of the vacancy's own stated age limit for the candidate's category
  notices      official notices recorded in the period for vacancies this profile version was assessed
               for (and not found INELIGIBLE)
  changes      the radar's alerts for official changes to exams/vacancies the user applied for or is eligible
               for, field by field, citing both the earlier value and the new one (services/change_log.py)

Every item carries citations: document URL + page + exact quote, taken from VacancyFieldProvenance,
VacancyRequirement (via EligibilityEvidence) or the sentences recorded with a notice. A fact that cannot
be cited is left out and listed in `omitted`; the digest never states anything it cannot cite.

Files go to <storage>/digests/<user_id>/<IST date>.html (+ .txt, .json). Nothing is emailed from here
unless send_run() is called explicitly, and only to a user with an active ALERTS consent (services/consent.py):
a user who never gave it, or withdrew it, still gets the digest on the dashboard but no email.
"""
import asyncio
import html
import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.config.settings import settings
from govcareeros.backend.eligibility import engine as eligibility_engine
from govcareeros.backend.eligibility import rules
from govcareeros.backend.models.alert import UserAlert
from govcareeros.backend.models.artifact import RawDocument
from govcareeros.backend.models.digest import DigestRun
from govcareeros.backend.models.eligibility import EligibilityEvidence, EligibilityResult
from govcareeros.backend.models.user import PersonalInfo, ProfileVersion, User
from govcareeros.backend.models.vacancy import Vacancy, VacancyFieldProvenance, VacancyRequirement, VacancyVersion
from govcareeros.backend.parsing.upsc_advertisement import IST
from govcareeros.backend.services import change_log, consent, mailer
from govcareeros.backend.services.radar import AGE_HEADROOM_YEARS, _current_result, age_out_date

DEFAULT_PERIOD = timedelta(days=7)
DEADLINE_WINDOW = timedelta(days=14)
_SHOWN = {"ELIGIBLE", "CONDITIONAL", "MANUAL_REVIEW"}
_OPEN = {"UNKNOWN", "CONDITIONAL"}
_CHECK_REQ = {"AGE": "AGE_MAX", "EDUCATION": "EDUCATION", "EXPERIENCE": "EXPERIENCE", "CITIZENSHIP": "CITIZENSHIP",
              "NOTICE": "NOTICE_CONDITION"}
_CHECK_LABEL = {"AGE": "age limit / relaxation", "EDUCATION": "qualification match", "EXPERIENCE": "experience relevance",
                "CITIZENSHIP": "citizenship", "NOTICE": "notice condition"}
SECTIONS = [("matches", "New and updated matches"), ("deadlines", "Closing in the next 14 days"),
            ("age_headroom", "Age limit close"), ("notices", "Official notices on your vacancies"),
            ("changes", "Official changes to what you applied for or are eligible for")]

@dataclass
class Citation:
    url: str
    page: int
    quote: str
    document: str

@dataclass
class OpenCheck:
    label: str      # "check experience relevance — p.8"
    reason: str     # the engine's rule text for this check
    citation: Citation

@dataclass
class DigestItem:
    kind: str       # MATCH, DEADLINE, AGE_HEADROOM, NOTICE, CHANGE
    vacancy_id: Optional[str]
    post: str
    organization: str
    advertisement: Optional[str]
    headline: str
    details: List[str]
    citations: List[Citation]
    status: Optional[str] = None
    change: Optional[str] = None
    due_at: Optional[datetime] = None
    open_checks: List[OpenCheck] = field(default_factory=list)
    exam_id: Optional[str] = None

@dataclass
class Digest:
    user_id: str
    email: str
    name: Optional[str]
    profile_version_id: Optional[str]
    profile_version_number: Optional[int]
    period_start: datetime
    period_end: datetime
    matches: List[DigestItem] = field(default_factory=list)
    deadlines: List[DigestItem] = field(default_factory=list)
    age_headroom: List[DigestItem] = field(default_factory=list)
    notices: List[DigestItem] = field(default_factory=list)
    changes: List[DigestItem] = field(default_factory=list)
    omitted: List[str] = field(default_factory=list)
    reported_keys: List[str] = field(default_factory=list)  # results/notices considered new in this digest

    def items(self) -> List[DigestItem]:
        return self.matches + self.deadlines + self.age_headroom + self.notices + self.changes

    def counts(self) -> Dict[str, int]:
        return {key: len(getattr(self, key)) for key, _ in SECTIONS}

    def to_dict(self) -> dict:
        return json.loads(json.dumps(asdict(self), default=lambda v: v.isoformat() if isinstance(v, datetime) else str(v)))

def fmt_dt(dt: datetime) -> str:
    return dt.astimezone(IST).strftime("%d %b %Y %H:%M IST")

def _quote(text: Optional[str], limit: int = 400) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"

def _cite(url: Optional[str], page, quote: Optional[str], document: str) -> Optional[Citation]:
    quote = _quote(quote)
    try:
        page = int(page) if page is not None else None
    except (TypeError, ValueError):
        page = None
    return Citation(url, page, quote, document) if url and page and quote else None

class _Ctx:
    """Per-digest caches so each document URL and vacancy's requirement rows are read once."""
    def __init__(self, session: AsyncSession):
        self.session = session
        self.urls: Dict[uuid.UUID, Optional[str]] = {}

    async def url(self, raw_id: Optional[uuid.UUID]) -> Optional[str]:
        if raw_id is None:
            return None
        if raw_id not in self.urls:
            self.urls[raw_id] = (await self.session.execute(select(RawDocument.url).where(RawDocument.id == raw_id))).scalar_one_or_none()
        return self.urls[raw_id]

def _advt_label(v: Vacancy) -> str:
    return f"{v.organization}, Advt {v.advertisement_number}" if v.advertisement_number else v.organization

async def _field_citation(ctx: _Ctx, v: Vacancy, field_name: str) -> Tuple[Optional[VacancyFieldProvenance], Optional[Citation]]:
    prov = (await ctx.session.execute(select(VacancyFieldProvenance).where(
        VacancyFieldProvenance.vacancy_id == v.id, VacancyFieldProvenance.field_name == field_name))).scalar_one_or_none()
    if prov is None:
        return None, None
    return prov, _cite(await ctx.url(prov.raw_document_id) or v.notification_url, prov.page, prov.excerpt, _advt_label(v))

async def _deadline_citation(ctx: _Ctx, v: Vacancy) -> Optional[Citation]:
    """Cites the document that set the vacancy's CURRENT closing date: the latest notice that moved it, else the advertisement."""
    if v.application_deadline is None:
        return None
    versions = (await ctx.session.execute(select(VacancyVersion).where(VacancyVersion.vacancy_id == v.id))).scalars().all()
    oldest = datetime.min.replace(tzinfo=timezone.utc)
    for vv in sorted(versions, key=lambda x: x.notice_date or oldest, reverse=True):
        details = json.loads(vv.details or "{}")
        change = details.get("deadline_change") or {}
        if change.get("new") and datetime.fromisoformat(change["new"]) == v.application_deadline:
            # The notice is the authority for this date; the advertisement's quote would be out of date
            return _cite(details.get("url") or await ctx.url(vv.raw_document_id), change.get("page"), change.get("sentence"),
                         details.get("title") or vv.change_log or "Official notice")
    prov, cit = await _field_citation(ctx, v, "application_deadline")
    if prov and prov.value and datetime.fromisoformat(prov.value) == v.application_deadline:
        return cit
    return None

async def _requirement_citation(ctx: _Ctx, v: Vacancy, row: VacancyRequirement) -> Optional[Citation]:
    from_notice = row.raw_document_id is not None and row.raw_document_id != v.raw_document_id
    document = f"Official notice ({row.source_section})" if from_notice and row.source_section else _advt_label(v)
    url = await ctx.url(row.raw_document_id) if row.raw_document_id else v.notification_url
    return _cite(url, row.source_page, row.source_excerpt, document)

async def _open_checks(ctx: _Ctx, v: Vacancy, result: EligibilityResult, rows: Dict[uuid.UUID, VacancyRequirement],
                       omitted: List[str]) -> List[OpenCheck]:
    evidence = (await ctx.session.execute(select(EligibilityEvidence).where(
        EligibilityEvidence.result_id == result.id, EligibilityEvidence.outcome.in_(_OPEN)))).scalars().all()
    out = []
    for ev in evidence:
        row = rows.get(ev.requirement_id) if ev.requirement_id else None
        if row is None and ev.check_name in _CHECK_REQ:
            # Some outcomes (e.g. missing date of birth) name only the page of the requirement they concern
            row = next((r for r in rows.values() if r.superseded_at is None and r.req_type == _CHECK_REQ[ev.check_name]
                        and (ev.source_page is None or str(r.source_page) == ev.source_page)), None)
        cit = await _requirement_citation(ctx, v, row) if row else None
        if cit is None:
            omitted.append(f"{v.post_name}: open {ev.check_name} check not shown (no citable requirement)")
            continue
        label = f"check {_CHECK_LABEL.get(ev.check_name, ev.check_name.lower())} — p.{cit.page}"
        note = re.search(r"\s*\[note: (.*)\]$", ev.rule, re.S)
        # The engine appends notice text as a note; the citation already quotes it
        reason = ev.rule[:note.start()] if note and _quote(note.group(1))[:80] in cit.quote else ev.rule
        out.append(OpenCheck(label, reason, cit))
    return out

async def latest_profile_version(session: AsyncSession, user_id: uuid.UUID) -> Optional[ProfileVersion]:
    return (await session.execute(select(ProfileVersion).where(ProfileVersion.user_id == user_id)
                                  .order_by(ProfileVersion.version_number.desc()).limit(1))).scalar_one_or_none()

async def previous_run(session: AsyncSession, user_id: uuid.UUID) -> Optional[DigestRun]:
    return (await session.execute(select(DigestRun).where(DigestRun.user_id == user_id)
                                  .order_by(DigestRun.period_end.desc(), DigestRun.created_at.desc()).limit(1))).scalar_one_or_none()

async def build_digest(session: AsyncSession, user_id: uuid.UUID, now: Optional[datetime] = None,
                       since: Optional[datetime] = None, evaluate_missing: bool = True) -> Digest:
    """Structured digest for [since, now]. since defaults to the end of the user's previous digest, else now - 7 days.
    evaluate_missing runs the deterministic engine where the vacancy has no verdict on its current inputs.

    "New" verdicts and notices are those recorded after the previous digest was generated (both are server
    timestamps, so a pinned `now` cannot re-report them); without a previous digest, those recorded since `since`."""
    now = now or datetime.now(timezone.utc)
    user = await session.get(User, user_id)
    if user is None:
        raise ValueError("user not found")
    reported_until = None
    if since is None:
        prev = await previous_run(session, user_id)
        since = prev.period_end if prev and prev.period_end < now else now - DEFAULT_PERIOD
        reported_until = prev.created_at if prev else None
        already = set(json.loads(prev.reported_keys or "[]")) if prev else set()
    else:
        already = set()

    def is_new(key: str, recorded: datetime) -> bool:
        # Timestamps alone can't decide: SQLite stores whole seconds and PostgreSQL gives one now() per transaction.
        # Include the boundary second and skip anything the previous digest already reported by key.
        new = key not in already and (recorded >= reported_until if reported_until else recorded >= since)
        if new:
            digest.reported_keys.append(key)
        return new

    version = await latest_profile_version(session, user_id)
    name = (await session.execute(select(PersonalInfo.full_name).where(PersonalInfo.profile_version_id == version.id))).scalar_one_or_none() \
        if version else None
    digest = Digest(str(user.id), user.email, name or user.full_name, str(version.id) if version else None,
                    version.version_number if version else None, since, now)
    if version is None:
        return digest

    ctx = _Ctx(session)
    snapshot = await eligibility_engine.load_profile_snapshot(session, version.id)
    category = rules.normalize_category(snapshot.category)
    vacancies = (await session.execute(select(Vacancy).where(
        Vacancy.status == "ACTIVE", (Vacancy.application_deadline.is_(None)) | (Vacancy.application_deadline > now),
    ).order_by(Vacancy.application_deadline, Vacancy.post_name))).scalars().all()

    for v in vacancies:
        reqs = await eligibility_engine.load_requirements(session, v.id)
        result = await _current_result(session, version.id, v.id, eligibility_engine.input_fingerprint(v, reqs))
        if result is None:
            if not evaluate_missing:
                continue
            result = await eligibility_engine.evaluate(session, user.id, version.id, v.id)
        if result.status not in _SHOWN:
            continue
        rows = {r.id: r for r in (await session.execute(select(VacancyRequirement).where(VacancyRequirement.vacancy_id == v.id))).scalars()}
        _, post_cit = await _field_citation(ctx, v, "post_name")
        deadline_cit = await _deadline_citation(ctx, v)
        base = dict(vacancy_id=str(v.id), post=v.post_name, organization=v.organization, advertisement=v.advertisement_number,
                    status=result.status, due_at=v.application_deadline)
        closes = f"Closes {fmt_dt(v.application_deadline)}" if v.application_deadline and deadline_cit else None
        if v.application_deadline and not deadline_cit:
            digest.omitted.append(f"{v.post_name}: closing date not shown (no citation)")

        if is_new(f"result:{result.id}", result.created_at):
            earlier = (await session.execute(select(func.count()).select_from(EligibilityResult).where(
                EligibilityResult.user_id == user.id, EligibilityResult.vacancy_id == v.id, EligibilityResult.id != result.id))).scalar_one()
            checks = await _open_checks(ctx, v, result, rows, digest.omitted)
            cits = [c for c in (post_cit, deadline_cit) if c]
            if cits:
                digest.matches.append(DigestItem(
                    kind="MATCH", headline=f"{result.status.replace('_', ' ').title()}: {v.post_name}",
                    details=[f"Profile v{version.version_number} is {result.status} for {v.post_name} ({_advt_label(v)})."]
                            + ([closes] if closes else []),
                    citations=cits, change="UPDATED" if earlier else "NEW", open_checks=checks, **base))
            else:
                digest.omitted.append(f"{v.post_name}: match not shown (no citation)")

        if v.application_deadline and deadline_cit and v.application_deadline - now <= DEADLINE_WINDOW:
            days = max(0, (v.application_deadline - now).days)
            digest.deadlines.append(DigestItem(
                kind="DEADLINE", headline=f"{days} day(s) left: {v.post_name}",
                details=[f"Applications for {v.post_name} ({_advt_label(v)}) close {fmt_dt(v.application_deadline)}."]
                        + (["Your eligibility needs a manual check first."] if result.status == "MANUAL_REVIEW" else []),
                citations=[deadline_cit], **base))

        limit = rules.age_limit_for(reqs, category) if snapshot.date_of_birth and category else None
        crucial_dt = v.eligibility_crucial_date or v.application_deadline
        if limit and crucial_dt:
            age = rules.age_on(snapshot.date_of_birth, crucial_dt.astimezone(IST).date())
            headroom = limit.value["max_age"] - age
            if 0 <= headroom <= AGE_HEADROOM_YEARS:
                age_cit = await _requirement_citation(ctx, v, rows[limit.id]) if limit.id in rows else None
                if age_cit is None:
                    digest.omitted.append(f"{v.post_name}: age headroom not shown (no citation for the age limit)")
                else:
                    out = age_out_date(snapshot.date_of_birth, limit.value["max_age"])
                    _, crucial_cit = await _field_citation(ctx, v, "eligibility_crucial_date") if v.eligibility_crucial_date else (None, deadline_cit)
                    digest.age_headroom.append(DigestItem(
                        kind="AGE_HEADROOM", headline=f"Age limit close: {v.post_name}",
                        details=[f"You are {age} on the crucial date {crucial_dt.astimezone(IST).strftime('%d %b %Y')}; "
                                 f"the {limit.value['category']} limit for this post is {limit.value['max_age']} (p.{age_cit.page}).",
                                 f"Posts with this limit stop accepting you from {out.strftime('%d %b %Y')} unless a relaxation applies."],
                        citations=[c for c in (age_cit, crucial_cit) if c], **{**base, "due_at": datetime(out.year, out.month, out.day, tzinfo=IST)}))

    assessed = select(EligibilityResult.vacancy_id).where(
        EligibilityResult.profile_version_id == version.id, EligibilityResult.status != "INELIGIBLE").distinct()
    rows = (await session.execute(select(VacancyVersion, Vacancy).join(Vacancy, Vacancy.id == VacancyVersion.vacancy_id)
                                  .where(VacancyVersion.vacancy_id.in_(assessed)).order_by(VacancyVersion.notice_date))).all()
    for vv, v in rows:
        if not is_new(f"notice:{vv.id}", vv.created_at):
            continue
        details = json.loads(vv.details or "{}")
        url = details.get("url") or await ctx.url(vv.raw_document_id)
        title = details.get("title") or vv.change_log or "Official notice"
        cits = [_cite(url, c.get("page"), c.get("text"), title) for c in details.get("conditions", [])]
        if details.get("cancellation"):
            cits.append(_cite(url, details["cancellation"].get("page"), details["cancellation"].get("sentence"), title))
        change = details.get("deadline_change") or {}
        moved = _cite(url, change.get("page"), change.get("sentence"), title) if change.get("new") else None
        cits = [c for c in cits + [moved] if c]
        if not cits:
            digest.omitted.append(f"{v.post_name}: {vv.change_type} notice not shown (no page/quote recorded)")
            continue
        cancelled = v.status == "CANCELLED" and "cancellation" in details
        dated = f" dated {vv.notice_date.astimezone(IST).strftime('%d %b %Y')}" if vv.notice_date else ""
        lines = [f"{vv.change_type} notice{dated} for {v.post_name} ({_advt_label(v)}): {title}"]
        if moved:
            lines.append(f"New closing date {fmt_dt(datetime.fromisoformat(change['new']))}.")
        digest.notices.append(DigestItem(
            kind="NOTICE", vacancy_id=str(v.id), post=v.post_name, organization=v.organization, advertisement=v.advertisement_number,
            headline=("Cancelled: " if cancelled else "Official notice: ") + v.post_name, details=lines, citations=cits,
            status="CANCELLED" if cancelled else None, due_at=v.application_deadline))

    await _changes(session, user.id, digest, is_new)

    digest.deadlines.sort(key=lambda i: i.due_at)
    digest.age_headroom.sort(key=lambda i: i.due_at)
    return digest

def _change_item(vc: change_log.VersionChanges, omitted: List[str], subject: str) -> Tuple[List[str], List[Citation]]:
    """A line per change and, for each, the citation of the earlier value and of the new one. A change whose new value
    cannot be cited is left out; an earlier value that cannot be cited is not stated."""
    lines, cits = [], []
    for c in vc.changes:
        new = _cite(c.new_evidence.url, c.new_evidence.page, c.new_evidence.quote, f"{c.new_evidence.document} (new value)")
        if new is None:
            omitted.append(f"{subject}: {c.label} change not shown (no citation)")
            continue
        old = _cite(c.old_evidence.url, c.old_evidence.page, c.old_evidence.quote, f"{c.old_evidence.document} (earlier value)") \
            if c.old_evidence else None
        what = f"{c.label} ({c.subject})" if c.subject else c.label
        now = change_log.fmt_value(c.field, c.new)
        if old:
            lines.append(f"{what}: {change_log.fmt_value(c.field, c.old)} (p.{old.page}) -> {now} (p.{new.page})")
            cits.append(old)
        elif c.old is None:
            lines.append(f"{what}: {now} (p.{new.page}), not stated before")
        else:
            omitted.append(f"{subject}: earlier {c.label.lower()} not shown (no citation)")
            lines.append(f"{what}: now {now} (p.{new.page})")
        if not c.applied:
            lines[-1] += "; stated by the notice, not applied (needs a manual check)"
        cits.append(new)
    return lines, cits

async def _changes(session: AsyncSession, user_id: uuid.UUID, digest: Digest, is_new) -> None:
    """The radar's change alerts recorded in the period, for targets the user still applied for or is eligible for.
    The keys are those of the notices section, so a vacancy notice is reported once."""
    alerts = (await session.execute(select(UserAlert).where(
        UserAlert.user_id == user_id, UserAlert.exam_version_id.is_not(None) | UserAlert.vacancy_version_id.is_not(None))
        .order_by(UserAlert.created_at))).scalars().all()
    if not alerts:
        return
    watched = {(kind, version.id): (target, vc, why)
               for kind, target, version, vc, why in await change_log.watched_changes(session, user_id)}
    for alert in alerts:
        kind, version_id = ("exam", alert.exam_version_id) if alert.exam_version_id else ("vacancy", alert.vacancy_version_id)
        hit = watched.get((kind, version_id))
        key = f"exam_notice:{version_id}" if kind == "exam" else f"notice:{version_id}"
        if hit is None or key in digest.reported_keys or not is_new(key, alert.created_at):
            continue
        target, vc, why = hit
        name = target.name if kind == "exam" else target.post_name
        lines, cits = _change_item(vc, digest.omitted, name)
        if not cits:  # each change was listed in `omitted`
            continue
        dated = f" dated {datetime.fromisoformat(vc.notice_date).strftime('%d %b %Y')}" if vc.notice_date else ""
        head = f"{vc.change_type} notice{dated} ({why}): {vc.document}"
        if kind == "exam":
            digest.changes.append(DigestItem(
                kind="CHANGE", vacancy_id=None, exam_id=str(target.id), post=target.name, organization=target.conducting_body,
                advertisement=target.external_ref, headline=f"Official change: {target.name}", details=[head] + lines,
                citations=cits, due_at=target.application_end))
        else:
            cancelled = any(c.field == "STATUS" and c.new == "CANCELLED" for c in vc.changes)
            digest.changes.append(DigestItem(
                kind="CHANGE", vacancy_id=str(target.id), post=target.post_name, organization=target.organization,
                advertisement=target.advertisement_number, headline=("Cancelled: " if cancelled else "Official change: ") + target.post_name,
                details=[head] + lines, citations=cits, status="CANCELLED" if cancelled else None, due_at=target.application_deadline))

# ---------- rendering ----------

_CSS = """
:root { color-scheme: light; }
body { font-family: "Segoe UI", "Noto Sans", "Noto Sans Devanagari", "Nirmala UI", Mangal, Arial, sans-serif;
       max-width: 46rem; margin: 0 auto; padding: 1.5rem 1rem; color: #1b1b1b; background: #fff; line-height: 1.5; }
h1 { font-size: 1.5rem; margin: 0 0 .25rem; } h2 { font-size: 1.15rem; margin: 2rem 0 .5rem; border-bottom: 2px solid #ddd; }
.meta { color: #555; font-size: .9rem; } .item { border: 1px solid #ddd; border-radius: 6px; padding: .75rem 1rem; margin: .75rem 0; }
.item h3 { font-size: 1rem; margin: 0 0 .35rem; } .tag { font-size: .75rem; padding: .05rem .4rem; border-radius: 4px; background: #eef; margin-left: .35rem; }
.checks { margin: .4rem 0; padding-left: 1.2rem; } .checks li { margin: .2rem 0; }
blockquote { margin: .4rem 0; padding: .3rem .7rem; border-left: 3px solid #999; background: #f7f7f7; font-size: .9rem; }
cite { display: block; font-style: normal; font-size: .8rem; color: #444; margin-top: .2rem; word-break: break-all; }
a { color: #0645ad; } .empty { color: #666; font-style: italic; } .omitted { font-size: .85rem; color: #555; }
@media print { body { max-width: none; padding: 0; } .item { break-inside: avoid; } a { color: #000; }
               a[href]::after { content: " (" attr(href) ")"; font-size: .75rem; } }
"""

def _e(text) -> str:
    return html.escape(str(text), quote=True)

def _cite_html(c: Citation) -> str:
    link = f'<a href="{_e(c.url)}">{_e(c.document)}</a>' if c.url.startswith(("https://", "http://")) else _e(c.document)
    return f"<blockquote>&ldquo;{_e(c.quote)}&rdquo;<cite>&mdash; {link}, p.{c.page}</cite></blockquote>"

def render_html(d: Digest) -> str:
    who = _e(d.name or d.email)
    version = f"profile v{d.profile_version_number}" if d.profile_version_number else "no profile yet"
    out = ["<!doctype html>", '<html lang="en"><head><meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width, initial-scale=1">',
           f"<title>Weekly digest {_e(d.period_end.astimezone(IST).strftime('%d %b %Y'))}</title><style>{_CSS}</style></head><body>",
           f"<h1>Weekly digest for {who}</h1>",
           f'<p class="meta">{_e(fmt_dt(d.period_start))} to {_e(fmt_dt(d.period_end))} &middot; {_e(version)}. '
           "Every statement below is quoted from an official document, with its page.</p>"]
    for key, heading in SECTIONS:
        items = getattr(d, key)
        out.append(f"<h2>{_e(heading)} ({len(items)})</h2>")
        if not items:
            out.append('<p class="empty">Nothing this week.</p>')
        for it in items:
            tags = "".join(f'<span class="tag">{_e(t)}</span>' for t in (it.change, it.status) if t)
            out.append(f'<div class="item"><h3>{_e(it.headline)}{tags}</h3>')
            out.extend(f"<p>{_e(line)}</p>" for line in it.details)
            if it.open_checks:
                out.append('<ul class="checks">')
                for chk in it.open_checks:
                    out.append(f"<li><strong>{_e(chk.label)}</strong>: {_e(chk.reason)}{_cite_html(chk.citation)}</li>")
                out.append("</ul>")
            out.extend(_cite_html(c) for c in it.citations)
            out.append("</div>")
    if d.omitted:
        out.append(f'<h2>Left out ({len(d.omitted)})</h2><p class="omitted">These could not be tied to a page and quote in an official document, so they are not stated here:</p><ul class="omitted">')
        out.extend(f"<li>{_e(o)}</li>" for o in d.omitted)
        out.append("</ul>")
    out.append("</body></html>")
    return "\n".join(out)

def _cite_text(c: Citation, indent: str) -> str:
    return f'{indent}"{c.quote}"\n{indent}  - {c.document}, p.{c.page}: {c.url}'

def render_text(d: Digest) -> str:
    version = f"profile v{d.profile_version_number}" if d.profile_version_number else "no profile yet"
    out = [f"Weekly digest for {d.name or d.email}", f"{fmt_dt(d.period_start)} to {fmt_dt(d.period_end)} ({version})",
           "Every statement is quoted from an official document, with its page.", ""]
    for key, heading in SECTIONS:
        items = getattr(d, key)
        out += [f"== {heading} ({len(items)}) =="] + (["  Nothing this week."] if not items else [])
        for it in items:
            tags = " ".join(f"[{t}]" for t in (it.change, it.status) if t)
            out.append(f"* {it.headline} {tags}".rstrip())
            out += [f"  {line}" for line in it.details]
            for chk in it.open_checks:
                out += [f"  - {chk.label}: {chk.reason}", _cite_text(chk.citation, "      ")]
            out += [_cite_text(c, "    ") for c in it.citations]
        out.append("")
    if d.omitted:
        out += [f"== Left out, no citation ({len(d.omitted)}) =="] + [f"  - {o}" for o in d.omitted]
    return "\n".join(out) + "\n"

# ---------- storage and delivery ----------

def run_file(run: DigestRun, suffix: str = ".html") -> Path:
    return (settings.storage_dir / run.file_path).with_suffix(suffix)

async def generate(session: AsyncSession, user: User, now: Optional[datetime] = None, since: Optional[datetime] = None) -> Tuple[Digest, DigestRun]:
    """Build the digest, write .html/.txt/.json and record a DigestRun. Does not send anything."""
    digest = await build_digest(session, user.id, now, since)
    rel = Path("digests") / str(user.id) / f"{digest.period_end.astimezone(IST).date().isoformat()}.html"
    path = settings.storage_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(digest), encoding="utf-8")
    path.with_suffix(".txt").write_text(render_text(digest), encoding="utf-8")
    path.with_suffix(".json").write_text(json.dumps(digest.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
    run = DigestRun(user_id=user.id, profile_version_id=uuid.UUID(digest.profile_version_id) if digest.profile_version_id else None,
                    period_start=digest.period_start, period_end=digest.period_end, file_path=rel.as_posix(),
                    item_count=len(digest.items()), item_counts=json.dumps(digest.counts()), omitted_count=len(digest.omitted),
                    reported_keys=json.dumps(digest.reported_keys))
    session.add(run)
    await session.flush()
    return digest, run

# The consent purpose that covers emailing a user their digest
EMAIL_CONSENT_PURPOSE = "ALERTS"

async def may_email(session: AsyncSession, user_id: uuid.UUID) -> bool:
    return await consent.active_consent(session, user_id, EMAIL_CONSENT_PURPOSE) is not None

async def send_run(session: AsyncSession, run: DigestRun, to: str) -> None:
    """Email one stored digest. Raises mailer.MailerNotConfigured when SMTP credentials are not set."""
    subject = f"Your weekly government jobs digest: {run.item_count} item(s), {run.period_end.astimezone(IST).strftime('%d %b %Y')}"
    await asyncio.to_thread(mailer.send, to, subject, run_file(run, ".txt").read_text(encoding="utf-8"),
                            run_file(run, ".html").read_text(encoding="utf-8"))
    run.sent_at = datetime.now(timezone.utc)
    run.sent_to = to
    await session.flush()

async def run_all(session: AsyncSession, now: Optional[datetime] = None, send: bool = False) -> List[DigestRun]:
    """Digest for every active user with a profile. Emails only when send=True, the mailer is configured and the user
    has an active ALERTS consent."""
    runs = []
    users = (await session.execute(select(User).where(User.is_active.is_(True)).order_by(User.email))).scalars().all()
    for user in users:
        if await latest_profile_version(session, user.id) is None:
            continue
        _, run = await generate(session, user, now)
        if send and await may_email(session, user.id):
            await send_run(session, run, user.email)
        runs.append(run)
    return runs
