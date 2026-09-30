"""
Deterministic verification of LLM-located fields against the document text.

A field is accepted only if its quote is found in the document (exactly after whitespace/case
normalisation, or — for OCR text — as a near match) and the value is derivable from the quote.
What gets stored is always the document's own words: the matched quote, and numbers/dates
parsed from that quote by fixed rules. The LLM's paraphrase is never stored.
"""
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

from govcareeros.backend.documents.pdf_text import PageText
from govcareeros.backend.parsing.upsc_advertisement import words_to_number

IST = timezone(timedelta(hours=5, minutes=30))
OCR_MIN_RATIO = 0.90  # near-match threshold for OCR pages only

TEXT_FIELDS = ("organization", "pay_level", "essential_qualification", "experience", "mode_of_selection", "how_to_apply")
ALL_FIELDS = ("post_name", "vacancy_count", "last_date", "age_limit") + TEXT_FIELDS

@dataclass
class FieldVerdict:
    name: str
    accepted: bool
    reason: str
    llm_value: Any = None
    quote: Optional[str] = None       # the document's text that was matched
    page: Optional[int] = None        # page where the quote was actually found
    value: Any = None                 # value derived from the quote by fixed rules
    match: Optional[str] = None       # EXACT or OCR_FUZZY(0.93)

@dataclass
class VacancyVerdict:
    fields: Dict[str, FieldVerdict] = field(default_factory=dict)

    @property
    def accepted(self) -> Dict[str, FieldVerdict]:
        return {k: v for k, v in self.fields.items() if v.accepted}

def _norm(text: str) -> str:
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"').replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", text).strip().lower()

def _tokens(text: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", _norm(text))

def find_quote(quote: str, pages: List[PageText], hint_page: Optional[int]) -> Optional[Tuple[int, str, str]]:
    """(page, matched document text, match kind) or None. The hinted page is tried first."""
    q = _norm(quote)
    if len(q) < 3:
        return None
    ordered = sorted(pages, key=lambda p: p.page_number != hint_page)
    for p in ordered:
        flat = re.sub(r"\s+", " ", p.text).strip()
        i = _norm(flat).find(q)
        if i >= 0:
            # _norm only swaps single characters and lowercases, so offsets line up with `flat`
            return p.page_number, flat[i:i + len(q)], "EXACT"
    q_tokens = _tokens(quote)
    if not q_tokens:
        return None
    for p in ordered:
        if not p.ocr:
            continue
        doc_tokens = _tokens(p.text)
        n = len(q_tokens)
        best, best_at = 0.0, None
        for start in range(0, max(1, len(doc_tokens) - n + 1)):
            window = doc_tokens[start:start + n]
            ratio = SequenceMatcher(None, " ".join(q_tokens), " ".join(window)).ratio()
            if ratio > best:
                best, best_at = ratio, (start, window)
        if best >= OCR_MIN_RATIO:
            return p.page_number, " ".join(best_at[1]), f"OCR_FUZZY({best:.2f})"
    return None

_DATE_PATTERNS = [
    (re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b"), "dmy"),
    (re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec)[.,'’`]*\s+(\d{4})\b", re.I), "d_month_y"),  # "03 Sept’ 2025"
]
_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_TIME_RE = re.compile(r"\b(\d{1,2})(?:[:.](\d{2}))?\s*(A\.?M\.?|P\.?M\.?|hrs|hours)\b|\b(\d{4})\s*hrs\b", re.I)

def dates_in(text: str) -> List[date]:
    found = []
    for rx, kind in _DATE_PATTERNS:
        for m in rx.finditer(text):
            try:
                if kind == "dmy":
                    found.append(date(int(m.group(3)), int(m.group(2)), int(m.group(1))))
                else:
                    found.append(date(int(m.group(3)), _MONTHS[m.group(2)[:3].lower()], int(m.group(1))))
            except ValueError:
                continue
    return found

def time_in(text: str) -> Optional[Tuple[int, int]]:
    m = _TIME_RE.search(text)
    if not m:
        return None
    if m.group(4):
        return int(m.group(4)[:2]), int(m.group(4)[2:])
    hour, minute, suffix = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower().replace(".", "")
    if suffix == "pm" and hour < 12:
        hour += 12
    if suffix == "am" and hour == 12:
        hour = 0
    return (hour, minute) if hour < 24 and minute < 60 else None

def _count_in(quote: str) -> List[int]:
    nums = [int(n) for n in re.findall(r"\b(\d{1,4})\b", quote)]
    for m in re.finditer(r"\b([A-Za-z]+(?:[\s-][A-Za-z]+){0,3})\s+(?:\(\d+\)\s*)?(?:vacanc|post)", quote, re.I):
        words = m.group(1).split()
        for i in range(len(words)):
            n = words_to_number(" ".join(words[i:]))
            if n:
                nums.append(n)
                break
    return nums

_AGE_CAT_RE = re.compile(r"(\d{2})\s*years?\s*(?:for|in\s+case\s+of)\s*(UR|GEN(?:ERAL)?|OBC|SC|ST|EWS)\b", re.I)
_AGE_GENERAL_RE = re.compile(r"(?:not\s+(?:exceed(?:ing)?|more\s+than|above)|upper\s+age\s+limit(?:\s+is)?|maximum\s+age(?:\s+limit)?(?:\s+is)?|below|upto|up\s+to)\D{0,20}?(\d{2})\s*years", re.I)
# "55 years or less than 55 years", "56 years and below"
_AGE_OR_LESS_RE = re.compile(r"(\d{2})\s*years?\s*(?:of\s+age\s+)?(?:or\s+(?:less|below)|and\s+below)", re.I)
_AGE_RANGE_RE = re.compile(r"\b(\d{2})\s*(?:-|to)\s*(\d{2})\s*years", re.I)

def age_limits_in(quote: str) -> List[Dict[str, Any]]:
    """Upper age limits stated in the quote; category-specific when the quote names categories."""
    per_cat = [{"category": "UR" if c.upper().startswith("GEN") else c.upper(), "max_age": int(a)} for a, c in _AGE_CAT_RE.findall(quote)]
    if per_cat:
        return per_cat
    m = _AGE_RANGE_RE.search(quote)
    if m and int(m.group(1)) < int(m.group(2)):
        return [{"category": "ALL", "min_age": int(m.group(1)), "max_age": int(m.group(2))}]
    m = _AGE_GENERAL_RE.search(quote) or _AGE_OR_LESS_RE.search(quote)
    return [{"category": "ALL", "max_age": int(m.group(1))}] if m else []

def verify_field(name: str, item: Any, pages: List[PageText]) -> FieldVerdict:
    if not isinstance(item, dict):
        return FieldVerdict(name, False, "missing")
    value, quote, page = item.get("value"), (item.get("quote") or "").strip(), item.get("page")
    if value in (None, "", []) and not quote:
        return FieldVerdict(name, False, "not stated", value)
    if not quote:
        return FieldVerdict(name, False, "no supporting quote", value)
    found = find_quote(quote, pages, page if isinstance(page, int) else None)
    if not found:
        return FieldVerdict(name, False, "quote not found in document", value, quote)
    at_page, doc_text, match = found
    v = FieldVerdict(name, False, "", value, doc_text, at_page, None, match)
    page_note = "" if at_page == page else f" (LLM said page {page})"

    if name == "post_name":
        if value and _norm(str(value)) in _norm(doc_text):
            v.accepted, v.value, v.reason = True, re.sub(r"\s+", " ", str(value)).strip(), "value is inside the quote" + page_note
        else:
            v.reason = "post name is not a substring of its quote"
    elif name == "vacancy_count":
        try:
            want = int(str(value).strip())
        except (TypeError, ValueError):
            v.reason = f"count {value!r} is not an integer"
            return v
        if want in _count_in(doc_text):
            v.accepted, v.value, v.reason = True, want, "number appears in the quote" + page_note
        else:
            v.reason = f"{want} does not appear in the quote"
    elif name == "last_date":
        ds = dates_in(doc_text)
        if not ds:
            v.reason = "no date in the quote"
        else:
            d = max(ds)  # a closing date quote may mention the opening date too; the closing one is the later
            hm = time_in(doc_text)
            dt = datetime(d.year, d.month, d.day, *(hm or (23, 59)), tzinfo=IST)
            v.accepted, v.value = True, dt
            v.reason = ("date parsed from the quote" + ("" if hm else "; time not stated, 23:59 IST assumed") + page_note)
    elif name == "age_limit":
        limits = age_limits_in(doc_text)
        if limits:
            v.accepted, v.value, v.reason = True, limits, "age limit parsed from the quote" + page_note
        else:
            v.reason = "no upper age limit recognisable in the quote"
    else:
        v.accepted, v.value, v.reason = True, doc_text, "quote found; stored verbatim" + page_note
    return v

def verify_vacancy(raw: Dict[str, Any], pages: List[PageText]) -> VacancyVerdict:
    return VacancyVerdict({name: verify_field(name, raw.get(name), pages) for name in ALL_FIELDS})
