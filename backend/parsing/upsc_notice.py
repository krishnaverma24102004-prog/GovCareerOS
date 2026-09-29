"""
Deterministic parser for UPSC notices that modify earlier advertisements
(clarifications, corrigenda, addenda, date extensions, cancellations, selection-process changes).

Often these are scanned, so text may come from OCR; every finding keeps the page and the
sentence it came from. A notice only cancels a vacancy when a sentence says the
advertisement/vacancy/post itself is cancelled (not e.g. "scrutiny ... stands cancelled").
"""
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional

from govcareeros.backend.documents.pdf_text import PageText
from govcareeros.backend.parsing.upsc_advertisement import IST

PARSER_VERSION = "upsc_notice/1"

_ADVT_RE = re.compile(r"Advt\.?\s*No\.?\s*(\d{1,3})\s*[/\-–]\s*(\d{2,4})", re.I)
_VACANCY_RE = re.compile(r"\b(\d{11})\b")
_DATED_RE = re.compile(r"Dated\s*:?\s*(\d{1,2})[./-](\d{1,2})[./-](\d{4})", re.I)
_NEW_CLOSING_RE = re.compile(
    r"(?:closing|last)\s+date.{0,120}?(?:extended|revised|changed)\s+(?:up\s*to|till|to)\s+(?:(\d{4})\s*hrs?\.?\s+(?:on\s+)?)?(\d{1,2})[./-](\d{1,2})[./-](\d{4})",
    re.I | re.S)
_CATEGORY_RE = re.compile(r"\b(OBC|SC|ST|EWS|UR|PwBD|Ex-?Servicemen)\b", re.I)

_KIND_RULES = [
    ("DATE_EXTENSION", re.compile(r"(?:closing|last)\s+date.{0,80}?extended|extension\s+of\s+(?:the\s+)?(?:closing|last)\s+date", re.I | re.S)),
    ("SELECTION_PROCESS_CHANGE", re.compile(r"recruitment\s+test|mode\s+of\s+selection|scrutiny\s+followed\s+by\s+interview|change\s+in\s+recruitment\s+process", re.I)),
    ("CLARIFICATION", re.compile(r"clarification", re.I)),
    ("CORRIGENDUM", re.compile(r"corrigendum", re.I)),
    ("ADDENDUM", re.compile(r"addendum", re.I)),
]
_CANCEL_SUBJECT_OK = re.compile(r"advertisement|advt|vacanc|post|recruitment\s+case", re.I)
_CANCEL_SUBJECT_NOT = re.compile(r"scrutiny|shortlist|result|interview\s+schedule|admit|test\s+date", re.I)

@dataclass
class NoticeFinding:
    text: str
    page: int

@dataclass
class ParsedNotice:
    parser_version: str
    kinds: List[str]
    advertisement_refs: List[str]            # normalised "11/2026"
    vacancy_refs: List[str]                  # UPSC 11-digit vacancy numbers
    notice_date: Optional[date]
    new_closing: Optional[datetime]
    cancels_vacancies: bool
    cancellation: Optional[NoticeFinding]
    category_conditions: List[NoticeFinding] = field(default_factory=list)
    used_ocr: bool = False
    title: Optional[str] = None

def _norm_year(y: str) -> str:
    return y if len(y) == 4 else f"20{y}"

def _sentences(pages: List[PageText]):
    for p in pages:
        flat = re.sub(r"\s+", " ", p.text)
        for s in re.split(r"(?<=[.;])\s+(?=[A-Z0-9(])", flat):
            if s.strip():
                yield p.page_number, s.strip()

def parse_notice(pages: List[PageText], title: Optional[str] = None) -> ParsedNotice:
    body = "\n".join(p.text for p in pages)
    flat = re.sub(r"\s+", " ", body)
    haystack = f"{title or ''} {flat}"

    advts = []
    for n, y in _ADVT_RE.findall(haystack):
        ref = f"{int(n)}/{_norm_year(y)}"
        if ref not in advts:
            advts.append(ref)
    vacancies = list(dict.fromkeys(_VACANCY_RE.findall(flat)))

    kinds = [k for k, rx in _KIND_RULES if rx.search(haystack)]

    cancellation = None
    for page, sentence in _sentences(pages):
        if re.search(r"cancel", sentence, re.I):
            subject = re.split(r"\b(?:is|are|stands|has\s+been|have\s+been|shall\s+be|stand)\b", sentence, maxsplit=1, flags=re.I)[0]
            if _CANCEL_SUBJECT_OK.search(subject) and not _CANCEL_SUBJECT_NOT.search(subject):
                cancellation = NoticeFinding(sentence, page)
                break
    if cancellation:
        kinds.append("CANCELLATION")

    m = _DATED_RE.search(flat)
    notice_date = date(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None

    new_closing = None
    m = _NEW_CLOSING_RE.search(flat)
    if m:
        hhmm = m.group(1) or "2359"
        new_closing = datetime(int(m.group(5)), int(m.group(4)), int(m.group(3)), int(hhmm[:2]), int(hhmm[2:]), tzinfo=IST)

    conditions = [NoticeFinding(s, pg) for pg, s in _sentences(pages)
                  if _CATEGORY_RE.search(s) and re.search(r"reservation|certificate|considered\s+as|eligib|relax", s, re.I)]

    return ParsedNotice(PARSER_VERSION, kinds or ["NOTICE"], advts, vacancies, notice_date, new_closing,
                        cancellation is not None, cancellation, conditions, any(p.ocr for p in pages), title)
