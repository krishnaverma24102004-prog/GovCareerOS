"""
Backlog triage: which discovered notices need human/LLM attention, and in what order. Fixed rules only.

1. Stated date (notice_date): the publication date as the notice's own title or link states it, read by
   the rules below in this order; the first rule that yields exactly one date wins. Nothing is inferred:
   upload timestamps, serial numbers and bare years are ignored, and None means "not stated".
     title_dated        title says "dated 30/09/2026", "Dated:29.07.2026", "dated June 23, 2026"
     title_released_on  title says "(Vacancies released on 06/02/2026)"
     url_dated          file name says "...-dated-29.12.2025_c.pdf"
     title_as_on        title says "Updated Vacancies as on 25.09.2026"
     url_filename_date  one date in the file name: "SSB_vacancy_29092026.PDF", "Notice_12-05-2026-994.pdf",
                        "NCI-25-8-26.pdf", UPSC-style "...-Engl-200826.pdf" (DDMMYY)
     title_date         one date anywhere in the title ("... is 4.7.26]")
     url_year_month     the upload folder "/2026-09/Vacancy.pdf" (month precision)
2. Decision (rule):
     NOT_REQUIRED  not_a_vacancy         kind RESULT / RULES / OTHER (backend/sources/notice_classifier.py)
     STALE         stale_stated_date     VACANCY / CORRIGENDUM whose stated date is older than the window
                   stale_first_seen      ... no stated date and first seen longer ago than the window
     QUEUED        fresh_stated_date / fresh_first_seen
   The window defaults to 120 days. A month-precision date counts from the month's last day, so a notice
   is never called stale before it could be.
3. Priority (an integer, higher first; the reason column spells out every term):
     kind               VACANCY 40, CORRIGENDUM 25, others 0
     recency            stated date: 30 * (1 - age / window), clamped to 0..30; not stated: 10
     document type      PDF 15, DOC 10, HTML 5
     source category    commission 12, defence/CAPF 10, ministry 8, PSU bank/regulator 8,
                        autonomous body/PSU 6, state PSC 6, official aggregator 2, other 4
     no parser          +10 when the source has no deterministic parser (its notices need a draft or a
                        human; a parser source's notices are parsed automatically)

Triage writes notice_triage (current decision) and appends triage_decisions when a decision changes.
It never deletes or edits a RecruitmentNotice and never touches review_status.
"""
import calendar
import re
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.parse import unquote, urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.models.notice import RecruitmentNotice
from govcareeros.backend.models.source import GovernmentSource
from govcareeros.backend.models.triage import NoticeTriage, TriageDecision

RULES_VERSION = "triage-v1"
DEFAULT_STALE_DAYS = 120

QUEUED, STALE, NOT_REQUIRED = "QUEUED", "STALE", "NOT_REQUIRED"
SKIP_FOR_DRAFTS = (STALE, NOT_REQUIRED)
VACANCY_KINDS = ("VACANCY", "CORRIGENDUM")

KIND_POINTS = {"VACANCY": 40, "CORRIGENDUM": 25}
RECENCY_MAX = 30
RECENCY_UNKNOWN = 10
DOC_POINTS = {"PDF": 15, "DOC": 10, "HTML": 5}
CATEGORY_POINTS = {
    "RECRUITMENT_COMMISSION": 12, "CENTRAL_RECRUITMENT_COMMISSION": 12,
    "DEFENCE_CAPF": 10, "MINISTRY": 8, "PSU_BANK_REGULATOR": 8,
    "AUTONOMOUS_BODY_PSU": 6, "STATE_PSC": 6, "OFFICIAL_AGGREGATOR": 2,
}
CATEGORY_OTHER = 4
NO_PARSER_POINTS = 10

# ---------------------------------------------------------------- stated date

@dataclass(frozen=True)
class NoticeDate:
    date: date
    rule: str
    text: str  # the exact text the date was read from
    precision: str = "day"  # "day" or "month"

    def latest(self) -> date:
        """The latest day this date can mean (the month's last day for month precision)."""
        if self.precision == "month":
            return self.date.replace(day=calendar.monthrange(self.date.year, self.date.month)[1])
        return self.date

_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
_MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
_MONTHS["sept"] = 9
_MONTH_RE = r"(?:" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")"

# 30/09/2026, 30.09.2026, 30-09-2026, 30_09_2026, 4.7.26 (same separator twice)
_NUMERIC = r"(?<!\d)(?P<d>\d{1,2})(?P<sep>[./\-_])(?P<m>\d{1,2})(?P=sep)(?P<y>\d{4}|\d{2})(?!\d)"
# 19th October, 2026 / 14 Mar 26 / 14-mar-26
_DAY_MONTH = r"(?<!\d)(?P<d>\d{1,2})(?:st|nd|rd|th)?[\s\-_.,]*(?P<mon>" + _MONTH_RE + r")\b[\s\-_.,]*(?P<y>\d{4}|\d{2})(?!\d)"
# June 23, 2026
_MONTH_DAY = r"\b(?P<mon>" + _MONTH_RE + r")\.?[\s\-_]+(?P<d>\d{1,2})(?:st|nd|rd|th)?,?[\s\-_]+(?P<y>\d{4})(?!\d)"
_ANY_DATE = [re.compile(p, re.I) for p in (_NUMERIC, _DAY_MONTH, _MONTH_DAY)]
# DDMMYYYY / DDMMYY written without separators inside a file name (bounded by non-digits)
_COMPACT8 = re.compile(r"(?<!\d)(?P<d>\d{2})(?P<m>\d{2})(?P<y>\d{4})(?!\d)")
_COMPACT6 = re.compile(r"(?<!\d)(?P<d>\d{2})(?P<m>\d{2})(?P<y>\d{2})(?!\d)")
_YEAR_MONTH_FOLDER = re.compile(r"/(?P<y>20\d{2})-(?P<m>0[1-9]|1[0-2])/")

def _anchor(words: str) -> re.Pattern:
    """Anchor words a date follows, e.g. "dated" in "dated 30/09/2026" or "Dated:29.07.2026"."""
    # Letters-only boundaries: "_dated_" in a file name counts, "updated" and "Advt" do not
    return re.compile(rf"(?<![a-z])(?:{words})(?![a-z])[\s:.\-_]*", re.I)

_DATED = _anchor(r"dated|dt")
_RELEASED_ON = _anchor(r"released\s+on")
_AS_ON = _anchor(r"as\s+on")

def _year(y: str) -> int:
    return 2000 + int(y) if len(y) == 2 else int(y)

def _make(d: str, m: str, y: str) -> Optional[date]:
    try:
        month = int(m) if m.isdigit() else _MONTHS[m.lower().rstrip(".")]
        value = date(_year(y), month, int(d))
    except (ValueError, KeyError):
        return None
    return value if 2000 <= value.year <= 2099 else None

def _dates_in(text: str, compact: bool = False) -> List[Tuple[date, str]]:
    found: List[Tuple[date, str]] = []
    for rx in _ANY_DATE + ([_COMPACT8, _COMPACT6] if compact else []):
        for m in rx.finditer(text):
            value = _make(m.group("d"), m.group("mon") if "mon" in rx.groupindex else m.group("m"), m.group("y"))
            if value:
                found.append((value, m.group(0)))
    return found

def _after_anchor(text: str, anchor: re.Pattern) -> Optional[Tuple[date, str]]:
    """The first date written right after an anchor (the first anchor that has one)."""
    for a in anchor.finditer(text):
        for rx in _ANY_DATE:
            m = rx.match(text, a.end())
            if m:
                value = _make(m.group("d"), m.group("mon") if "mon" in rx.groupindex else m.group("m"), m.group("y"))
                if value:
                    return value, text[a.start():m.end()].strip()
    return None

def _single(found: Sequence[Tuple[date, str]]) -> Optional[Tuple[date, str]]:
    """Exactly one distinct date, else None: two different dates are ambiguous and nothing is guessed."""
    distinct = {d for d, _ in found}
    return found[0] if len(distinct) == 1 else None

_OPAQUE_ID = re.compile(r"[0-9a-fA-F]{16,}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")

def _plausible(d: date) -> bool:
    # A publication date can't be before 2000 or more than a year ahead; anything else is a misread number
    return 2000 <= d.year <= date.today().year + 1

def notice_date(title: str, url: str) -> Optional[NoticeDate]:
    found = _notice_date(title, url)
    return found if found and _plausible(found.date) else None

def _notice_date(title: str, url: str) -> Optional[NoticeDate]:
    """The publication date the notice's title or link states, with the rule that read it; None if not stated."""
    title = title or ""
    path = unquote(urlparse(url or "").path)
    filename = path.rsplit("/", 1)[-1]
    stem = re.sub(r"\.(pdf|docx?|html?|aspx|php|pptx?|xlsx?)$", "", filename, flags=re.I)
    # Opaque ids (e.g. RPSC "23D8C27D35294E888CE27F769E170330.pdf") contain digit runs that are not dates
    if _OPAQUE_ID.fullmatch(stem):
        stem = ""

    for rule, text, anchor in (("title_dated", title, _DATED), ("title_released_on", title, _RELEASED_ON),
                               ("url_dated", stem, _DATED), ("title_as_on", title, _AS_ON)):
        hit = _after_anchor(text, anchor)
        if hit:
            return NoticeDate(hit[0], rule, hit[1][:200])
    hit = _single(_dates_in(stem, compact=True))
    if hit:
        return NoticeDate(hit[0], "url_filename_date", hit[1])
    hit = _single(_dates_in(title))
    if hit:
        return NoticeDate(hit[0], "title_date", hit[1])
    m = _YEAR_MONTH_FOLDER.search(path)
    if m:
        return NoticeDate(date(int(m.group("y")), int(m.group("m")), 1), "url_year_month", m.group(0).strip("/"), "month")
    return None

# ---------------------------------------------------------------- decision + priority

@dataclass
class NoticeFacts:
    """What triage reads about a notice (a plain record, so it can run on fixtures as well as rows)."""
    title: str
    url: str
    notice_kind: str
    document_type: str
    first_seen_at: datetime
    source_category: str
    has_parser: bool

@dataclass
class Decision:
    triage_status: str
    rule: str
    priority: int
    reason: str
    notice_date: Optional[NoticeDate]
    terms: Dict[str, int] = field(default_factory=dict)

def _age_days(stated: Optional[NoticeDate], first_seen_at: datetime, today: date) -> Tuple[int, str]:
    if stated:
        return (today - stated.latest()).days, f"stated date {stated.date.isoformat()}" + \
            (f" (month {stated.date:%Y-%m})" if stated.precision == "month" else "") + f" [{stated.rule}: {stated.text!r}]"
    seen = first_seen_at.astimezone(timezone.utc).date()
    return (today - seen).days, f"no stated date; first seen {seen.isoformat()}"

def priority_terms(facts: NoticeFacts, stated: Optional[NoticeDate], today: date, stale_days: int) -> Dict[str, int]:
    if stated:
        age = (today - stated.latest()).days
        recency = max(0, min(RECENCY_MAX, round(RECENCY_MAX * (1 - max(age, 0) / stale_days))))
    else:
        recency = RECENCY_UNKNOWN
    return {
        f"kind {facts.notice_kind}": KIND_POINTS.get(facts.notice_kind, 0),
        "recency" if stated else "recency (date not stated)": recency,
        f"document {facts.document_type}": DOC_POINTS.get(facts.document_type, 0),
        f"source {facts.source_category}": CATEGORY_POINTS.get(facts.source_category, CATEGORY_OTHER),
        "no deterministic parser" if not facts.has_parser else "parser source": 0 if facts.has_parser else NO_PARSER_POINTS,
    }

def decide(facts: NoticeFacts, now: datetime, stale_days: int = DEFAULT_STALE_DAYS) -> Decision:
    today = now.astimezone(timezone.utc).date()
    stated = notice_date(facts.title, facts.url)
    terms = priority_terms(facts, stated, today, stale_days)
    priority = sum(terms.values())
    breakdown = "priority " + " + ".join(f"{k} {v}" for k, v in terms.items()) + f" = {priority}"
    if facts.notice_kind not in VACANCY_KINDS:
        status, rule = NOT_REQUIRED, "not_a_vacancy"
        why = f"kind {facts.notice_kind} is not a vacancy or corrigendum (notice_classifier)"
    else:
        age, basis = _age_days(stated, facts.first_seen_at, today)
        stale = age > stale_days
        status = STALE if stale else QUEUED
        rule = ("stale_" if stale else "fresh_") + ("stated_date" if stated else "first_seen")
        why = f"{basis}: {age} days old, {'over' if stale else 'within'} the {stale_days}-day window"
    return Decision(status, rule, priority, f"{status}: {why}; {breakdown}", stated, terms)

# ---------------------------------------------------------------- run over the database

@dataclass
class TriageRow:
    notice_id: uuid.UUID
    source: str
    title: str
    url: str
    notice_kind: str
    review_status: str
    decision: Decision
    previous_status: Optional[str]
    changed: bool

@dataclass
class TriageRun:
    rows: List[TriageRow]
    applied: bool
    stale_days: int
    now: datetime

    def counts(self) -> Dict[str, int]:
        return dict(Counter(r.decision.triage_status for r in self.rows))

    def rule_counts(self) -> Dict[str, int]:
        return dict(Counter(r.decision.rule for r in self.rows))

    def date_rule_counts(self) -> Dict[str, int]:
        return dict(Counter(r.decision.notice_date.rule if r.decision.notice_date else "not stated" for r in self.rows))

    def changed(self) -> int:
        return sum(r.changed for r in self.rows)

    def top(self, n: int = 20, open_only: bool = True) -> List[TriageRow]:
        """Highest-priority QUEUED notices (by default only those still awaiting review)."""
        rows = [r for r in self.rows if r.decision.triage_status == QUEUED
                and (not open_only or r.review_status in ("NEW", "MANUAL_REVIEW"))]
        return sorted(rows, key=lambda r: (-r.decision.priority, r.title, r.url))[:n]

    def summary(self) -> Dict[str, object]:
        return {"notices": len(self.rows), "applied": self.applied, "stale_after_days": self.stale_days,
                "by_status": self.counts(), "by_rule": self.rule_counts(), "changed": self.changed()}

def _same(current: NoticeTriage, d: Decision) -> bool:
    nd = d.notice_date
    return (current.triage_status, current.priority, current.rule, current.notice_date, current.notice_date_rule) == \
        (d.triage_status, d.priority, d.rule, nd.date if nd else None, nd.rule if nd else None)

async def triage_notices(session: AsyncSession, now: Optional[datetime] = None, stale_days: int = DEFAULT_STALE_DAYS,
                         apply: bool = False) -> TriageRun:
    """Decide every notice. Dry run (default) writes nothing; apply=True upserts notice_triage and logs changes."""
    if stale_days <= 0:
        raise ValueError("stale_days must be positive")
    now = now or datetime.now(timezone.utc)
    result = await session.execute(
        select(RecruitmentNotice, GovernmentSource, NoticeTriage)
        .join(GovernmentSource, GovernmentSource.id == RecruitmentNotice.source_id)
        .outerjoin(NoticeTriage, NoticeTriage.notice_id == RecruitmentNotice.id)
        .order_by(RecruitmentNotice.first_seen_at, RecruitmentNotice.url_hash))
    rows: List[TriageRow] = []
    for notice, source, current in result.all():
        facts = NoticeFacts(notice.title, notice.url, notice.notice_kind, notice.document_type, notice.first_seen_at,
                            source.category, bool(source.adapter_name))
        d = decide(facts, now, stale_days)
        changed = current is None or not _same(current, d)
        rows.append(TriageRow(notice.id, source.name, notice.title, notice.url, notice.notice_kind, notice.review_status,
                              d, current.triage_status if current else None, changed))
        if not apply:
            continue
        nd = d.notice_date
        values = dict(triage_status=d.triage_status, priority=d.priority, rule=d.rule, reason=d.reason,
                      notice_date=nd.date if nd else None, notice_date_rule=nd.rule if nd else None,
                      notice_date_text=nd.text if nd else None, stale_after_days=stale_days,
                      rules_version=RULES_VERSION, triaged_at=now)
        if current is None:
            session.add(NoticeTriage(id=uuid.uuid4(), notice_id=notice.id, **values))
        else:
            for k, v in values.items():
                setattr(current, k, v)
        if changed:
            session.add(TriageDecision(
                id=uuid.uuid4(), notice_id=notice.id, previous_status=rows[-1].previous_status,
                triage_status=d.triage_status, priority=d.priority, rule=d.rule, reason=d.reason,
                notice_date=nd.date if nd else None, notice_date_rule=nd.rule if nd else None,
                rules_version=RULES_VERSION, decided_at=now))
    if apply:
        await session.flush()
    return TriageRun(rows, apply, stale_days, now)
