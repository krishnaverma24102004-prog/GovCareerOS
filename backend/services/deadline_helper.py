"""
Closing dates stated relative to publication in Employment News / Rozgar Samachar, and an optional internal-forwarding
buffer for in-service applicants. Deterministic; nothing here is guessed.

A relative closing date is computed only when BOTH are stated in documents we hold:
  rule         "... (last|closing) date ... N days from the date of publication ... in (the) Employment News ..."
  publication  a single issue day: "published in (the) Employment News dated 04.07.2026" / "dated 4th July, 2026"
Otherwise the answer is UNKNOWN, with the words that were found and why they do not settle it:
  * an issue stated as a week ("published in employment news dated 25-31 March,2023") does not say which day;
  * "The tentative date for publishing the Advertisement in Employment News ... is 4.7.26" is a plan, not a publication;
  * a mention of Employment News without "N days from" (e.g. SSC's "before the date of publication of the
    Notice/Advertisement on the website of the Commission or Employments News") is not a closing-date rule.
closing date = publication day + N calendar days: "from" excludes the first day (General Clauses Act, 1897, s.9). No
holiday shift is applied; the rule's own sentence is returned beside the date so a reader can check it.

The forwarding buffer is NOT an official date. It is off unless configured (settings.IN_SERVICE_FORWARDING_BUFFER_DAYS
or an explicit argument), applies only to in-service applicants, and is returned as a separate, labelled estimate
next to the unchanged official date; nothing stores it as a deadline.
"""
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.config.settings import settings
from govcareeros.backend.documents.pdf_text import extract_pages
from govcareeros.backend.models.artifact import RawDocument
from govcareeros.backend.models.user import ProfileVersion
from govcareeros.backend.parsing.upsc_advertisement import IST
from govcareeros.backend.privacy import enforcement

_EN = r"(?:Employments?\s+News|Rozgaa?r\s*Samachar)"
_UNITS = {w: n for n, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
                                     "fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_NUMBER = r"(\d{1,3}|(?:(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)(?:[\s-]+(?:one|two|three|four|five|six|seven|eight|nine))?" \
          r"|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|one|two|three|four|five|six|seven|eight|nine))"
_RULE = re.compile(_NUMBER + r"\s*(?:\(\s*\w+\s*\)\s*)?days?\s+(?:from|after|of)\s+(?:the\s+)?date\s+of\s+(?:its\s+|the\s+)?publication"
                   r"(?:\s+of\s+(?:this|the)\s+(?:advertisement|notice|circular|notification))?\s+in\s+(?:the\s+)?" + _EN, re.I | re.S)
_CLOSING_WORDS = re.compile(r"last\s+date|closing\s+date|receipt\s+of\s+applications?|applications?\s+(?:should|must|shall)\s+reach", re.I)
_MONTHS = {m: i + 1 for i, m in enumerate(("january february march april may june july august september october november december").split())}
_DAY = r"(\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+,?\s*\d{4})"
_PUBLISHED = re.compile(r"(?:published|appeared|publication)\s+in\s+(?:the\s+)?" + _EN + r"[^.\]]{0,40}?dated\s+"
                        r"(\d{1,2}\s*(?:st|nd|rd|th)?\s*[-–]\s*\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+,?\s*\d{4}|" + _DAY[1:-1] + r")", re.I | re.S)
_WEEK = re.compile(r"\d{1,2}\s*(?:st|nd|rd|th)?\s*[-–]\s*\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+,?\s*\d{4}", re.I)  # "25-31 March,2023"
_TENTATIVE = re.compile(r"tentative\s+date\s+for\s+publishing[^.\]]{0,80}?" + _EN + r"(?:[^.\]]|\.(?=\d))*", re.I | re.S)

@dataclass
class HeldText:
    """Text of a document we hold: a page of a stored PDF, or a listing entry's own words (page None)."""
    document: str  # URL of the official document or page
    page: Optional[int]
    text: str

@dataclass
class Stated:
    value: object
    document: str
    page: Optional[int]
    quote: str

@dataclass
class RelativeDeadline:
    status: str  # KNOWN, UNKNOWN
    closing_date: Optional[date] = None
    rule: Optional[Stated] = None         # value: N (days)
    publication: Optional[Stated] = None  # value: the publication day
    reasons: List[str] = field(default_factory=list)  # why UNKNOWN, citing what was found

def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()

def _number(word: str) -> int:
    if word.isdigit():
        return int(word)
    parts = re.split(r"[\s-]+", word.lower())
    return _TENS.get(parts[0], _UNITS.get(parts[0], 0)) + (_UNITS[parts[1]] if len(parts) > 1 else 0)

def _parse_day(text: str) -> Optional[date]:
    try:
        return _day(text)
    except ValueError:  # not a calendar day ("31.02.2026"): not a stated publication day
        return None

def _day(text: str) -> Optional[date]:
    m = re.fullmatch(r"(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})", text.strip())
    if m:
        y = int(m.group(3))
        return date(y + 2000 if y < 100 else y, int(m.group(2)), int(m.group(1)))
    m = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+),?\s*(\d{4})", text.strip())
    if m and m.group(2).lower() in _MONTHS:
        return date(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1)))
    return None

def _sentence(text: str, start: int, end: int) -> str:
    """The sentence around [start, end): from the previous full stop to the next one."""
    a = max(text.rfind(". ", 0, start) + 2, 0) if text.rfind(". ", 0, start) >= 0 else 0
    b = text.find(". ", end)
    return text[a:b + 1 if b >= 0 else len(text)].strip()

def _once(reasons: List[str]) -> List[str]:
    return list(dict.fromkeys(reasons))  # a page repeating the same words (menus, mobile copies) says it once

def _where(t: HeldText) -> str:
    return f"{t.document}, p.{t.page}" if t.page else t.document

def find_rule(texts: Iterable[HeldText]) -> Tuple[Optional[Stated], List[str]]:
    reasons = []
    for t in texts:
        flat = _flat(t.text)
        for m in _RULE.finditer(flat):
            sentence = _sentence(flat, m.start(), m.end())
            if _CLOSING_WORDS.search(sentence):
                return Stated(_number(m.group(1)), t.document, t.page, sentence), _once(reasons)
            reasons.append(f"'{sentence}' ({_where(t)}) counts days from publication but is not about the closing date")
    return None, _once(reasons)

def find_publication(texts: Iterable[HeldText]) -> Tuple[Optional[Stated], List[str]]:
    reasons = []
    for t in texts:
        flat = _flat(t.text)
        for m in _PUBLISHED.finditer(flat):
            quote = flat[m.start():m.end()]
            if _WEEK.fullmatch(m.group(1)):
                reasons.append(f"'{quote}' ({_where(t)}) names an issue week, not the day of publication")
                continue
            day = _parse_day(m.group(1))
            if day:
                return Stated(day, t.document, t.page, quote), _once(reasons)
        for m in _TENTATIVE.finditer(flat):
            reasons.append(f"'{flat[m.start():m.end()].strip()}' ({_where(t)}) is a tentative date for publishing, not a publication")
    return None, _once(reasons)

def relative_deadline(texts: List[HeldText]) -> RelativeDeadline:
    """Closing date = Employment News publication day + N days, only when both are stated in `texts`."""
    rule, rule_reasons = find_rule(texts)
    publication, pub_reasons = find_publication(texts)
    result = RelativeDeadline("UNKNOWN", rule=rule, publication=publication, reasons=rule_reasons + pub_reasons)
    if rule is None:
        result.reasons.append("no document we hold states the closing date as N days from publication in Employment News")
    if publication is None:
        result.reasons.append("no document we hold states the day the advertisement was published in Employment News")
    if rule and publication:
        result.status, result.closing_date, result.reasons = "KNOWN", publication.value + timedelta(days=rule.value), []
    return result

# ---------- in-service forwarding buffer ----------

@dataclass
class ForwardingEstimate:
    estimate: datetime              # submit to your own office by about this time
    buffer_days: int
    official_deadline: datetime     # unchanged; the only official date
    label: str
    is_official: bool = False

def forwarding_estimate(official_deadline: Optional[datetime], in_service: bool,
                        buffer_days: Optional[int] = None) -> Optional[ForwardingEstimate]:
    """For in-service applicants whose application goes through their own office: the official date minus a
    configured buffer. None when not in service, when no official date is stored, or when no buffer is configured."""
    days = settings.IN_SERVICE_FORWARDING_BUFFER_DAYS if buffer_days is None else buffer_days
    if not in_service or official_deadline is None or not days or days <= 0:
        return None
    estimate = official_deadline - timedelta(days=days)
    official = official_deadline.astimezone(IST).strftime("%d %b %Y %H:%M IST")
    return ForwardingEstimate(estimate, days, official_deadline,
                              f"Estimate, not an official date: hand your application to your office for forwarding about "
                              f"{days} day(s) before the official last date ({official}), i.e. by "
                              f"{estimate.astimezone(IST).strftime('%d %b %Y')}. Your office's own instructions decide.")

# ---------- what the API returns ----------

async def held_texts(session: AsyncSession, raw_document_ids: Sequence[Optional[uuid.UUID]]) -> List[HeldText]:
    """Page texts of the stored documents (the advertisement and the notices recorded against it)."""
    out: List[HeldText] = []
    ids = [i for i in dict.fromkeys(raw_document_ids) if i]
    for raw in (await session.execute(select(RawDocument).where(RawDocument.id.in_(ids)))).scalars() if ids else []:
        path = Path(raw.storage_path or "")
        if raw.storage_path and path.is_file() and (raw.content_type or "").endswith("pdf"):
            out += [HeldText(raw.url, p.page_number, p.text) for p in extract_pages(path.read_bytes())]
    return out

async def user_in_service(session: AsyncSession, user_id: uuid.UUID) -> bool:
    """The latest profile version says the user is a serving Central or State Government employee."""
    from govcareeros.backend.eligibility import engine as eligibility_engine
    version = (await session.execute(select(ProfileVersion).where(ProfileVersion.user_id == user_id)
                                     .order_by(ProfileVersion.version_number.desc()).limit(1))).scalar_one_or_none()
    # Service details are eligibility data: without consent they are not read, and no estimate is offered
    if version is None or not await enforcement.allowed_for_version(session, user_id, version.id):
        return False
    snap = await eligibility_engine.load_profile_snapshot(session, version.id)
    return bool(snap.is_central_govt_employee or snap.is_state_govt_employee)

async def deadline_info(session: AsyncSession, user_id: uuid.UUID, official: Optional[datetime], evidence: Any,
                        raw_document_ids: Sequence[Optional[uuid.UUID]]) -> Dict[str, Any]:
    """The official last date with its evidence; when none is stored, the Employment-News-relative date (KNOWN only
    when rule and publication are both stated); and, apart from both, the caller's forwarding estimate if configured."""
    relative = None
    if official is None:
        relative = asdict(relative_deadline(await held_texts(session, raw_document_ids)))
    estimate = forwarding_estimate(official, await user_in_service(session, user_id))
    return {"official": {"date": official, "evidence": asdict(evidence) if evidence else None},
            "relative_to_employment_news": relative,
            "forwarding_estimate": asdict(estimate) if estimate else None}
