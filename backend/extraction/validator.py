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
MIN_TEXT_WORDS = 5    # text fields are stored verbatim, so a fragment like "the" or "may relax" is not evidence

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

def _words_pattern(tokens: List[str]) -> "re.Pattern":
    # Whole words only, separated by at least one non-word character ("10 0" must not match "100")
    return re.compile(r"(?<![a-z0-9])" + r"[^a-z0-9]+".join(re.escape(t) for t in tokens) + r"(?![a-z0-9])")

def _exact_at(q: str, text: str) -> int:
    """Offset of q in text where it does not start or end inside a word, or -1."""
    i = text.find(q)
    while i >= 0:
        before, after = text[i - 1:i], text[i + len(q):i + len(q) + 1]
        if not (q[0].isalnum() and before.isalnum()) and not (q[-1].isalnum() and after.isalnum()):
            return i
        i = text.find(q, i + 1)
    return -1

def find_quote(quote: str, pages: List[PageText], hint_page: Optional[int]) -> Optional[Tuple[int, str, str]]:
    """(page, matched document text, match kind) or None. The hinted page is tried first."""
    q = _norm(quote)
    if len(q) < 3:
        return None
    ordered = sorted(pages, key=lambda p: p.page_number != hint_page)
    for p in ordered:
        flat = re.sub(r"\s+", " ", p.text).strip()
        i = _exact_at(q, _norm(flat))
        if i >= 0:
            # _norm only swaps single characters and lowercases, so offsets line up with `flat`
            return p.page_number, flat[i:i + len(q)], "EXACT"
    q_tokens = _tokens(quote)
    if not q_tokens:
        return None
    # Same words in the same order, differing only in punctuation/spacing ("D-Wing,Room" vs "D-Wing, Room")
    if len(q_tokens) >= 3:
        pattern = _words_pattern(q_tokens)
        for p in ordered:
            flat = re.sub(r"\s+", " ", p.text).strip()
            m = pattern.search(_norm(flat))
            if m:
                return p.page_number, flat[m.start():m.end()], "EXACT_WORDS"
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
            # Return the page's own text for the matched window, not the normalised tokens
            flat = re.sub(r"\s+", " ", p.text).strip()
            m = _words_pattern(best_at[1]).search(_norm(flat))
            doc_text = flat[m.start():m.end()] if m else " ".join(best_at[1])
            return p.page_number, doc_text, f"OCR_FUZZY({best:.2f})"
    return None

_DATE_PATTERNS = [
    (re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b"), "dmy"),
    (re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec)[.,'’`]*\s+(\d{4})\b", re.I), "d_month_y"),  # "03 Sept’ 2025"
]
_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_TIME_RE = re.compile(r"\b(\d{1,2})(?:[:.](\d{2}))?\s*(A\.?M\.?|P\.?M\.?|hrs|hours)\b|\b(\d{4})\s*hrs\b", re.I)

def _date_spans(text: str) -> List[Tuple[date, int, int]]:
    found = []
    for rx, kind in _DATE_PATTERNS:
        for m in rx.finditer(text):
            try:
                if kind == "dmy":
                    found.append((date(int(m.group(3)), int(m.group(2)), int(m.group(1))), m.start(), m.end()))
                else:
                    found.append((date(int(m.group(3)), _MONTHS[m.group(2)[:3].lower()], int(m.group(1))), m.start(), m.end()))
            except ValueError:
                continue
    return found

def dates_in(text: str) -> List[date]:
    return [d for d, _, _ in _date_spans(text)]

_SENTENCE_END = re.compile(r"[.;]\s+(?=[A-Z(])")

def _clause_around(text: str, start: int, end: int) -> str:
    """The sentence holding text[start:end], cut at any other date: a time there belongs to this date."""
    spans = _date_spans(text)
    lo = max([e for _, _, e in spans if e <= start] + [m.end() for m in _SENTENCE_END.finditer(text[:start])] + [0])
    hi = min([s for _, s, _ in spans if s >= end] + [end + m.start() for m in _SENTENCE_END.finditer(text[end:])] + [len(text)])
    return text[lo:hi]

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
    # Up to four words right before each "vacanc"/"post" keyword; per keyword, so "Four vacancies for the post"
    # is not swallowed as one match ending at "post"
    for k in re.finditer(r"(?:\(\d+\)\s*)?(?:vacanc|post)", quote, re.I):
        m = re.search(r"\b((?:[A-Za-z]+[\s-]+){1,4})$", quote[:k.start()])
        if not m:
            continue
        words = m.group(1).replace("-", " ").split()
        for i in range(len(words)):
            n = words_to_number(" ".join(words[i:]))
            if n:
                nums.append(n)
                break
    return nums

_CAT = r"(?:UR|GEN(?:ERAL)?|OBC|SC|ST|EWS)"
# "relaxable upto 10 years for PwBD", "05 years for SC/ST" in a relaxation clause: amounts, not limits
_RELAXATION_RE = re.compile(r"relax|concession", re.I)
# "should not be below 21 years": a minimum, never an upper limit
_AGE_MIN_RE = re.compile(r"(?:not\s+(?:be\s+)?(?:below|less\s+than)|minimum\s+age(?:[\s-]+limit)?)\D{0,40}?(\d{2})\)?\s*years", re.I)
# "40 years for UR/EWS", "43 years for OBC"
_AGE_CAT_RE = re.compile(r"(\d{2})\s*years?\s*(?:for|in\s+case\s+of)\s*(" + _CAT + r"(?:\s*/\s*" + _CAT + r")*)\b", re.I)
_AGE_GENERAL_RE = re.compile(
    r"(?:not\s+(?:be\s+)?(?:exceed(?:ing)?|more\s+than|above)|upper\s+age[\s-]+limit|maximum\s+age(?:[\s-]+limit)?|below|upto|up\s+to)"
    # up to 80 non-digit characters, e.g. "... for appointment on deputation shall be" or "fifty-two ("
    r"\D{0,80}?(\d{2})\)?\s*years", re.I)
# "55 years or less than 55 years", "56 years and below"
_AGE_OR_LESS_RE = re.compile(r"(\d{2})\s*years?\s*(?:of\s+age\s+)?(?:or\s+(?:less|below)|and\s+below)", re.I)
# "18 - 25 years", "18 to 25 years", "Between 18 and 25 years"
_AGE_RANGE_RE = re.compile(r"\b(?:between\s+)?(\d{2})\s*(?:-|to|and)\s*(\d{2})\s*years", re.I)

def _age_limits(quote: str) -> List[Dict[str, Any]]:
    per_cat = [{"category": "UR" if c.upper().startswith("GEN") else c.upper(), "max_age": int(a)}
               for a, cats in _AGE_CAT_RE.findall(quote) for c in re.split(r"\s*/\s*", cats)]
    if per_cat:
        return per_cat
    m = _AGE_RANGE_RE.search(quote)
    if m and int(m.group(1)) < int(m.group(2)):
        return [{"category": "ALL", "min_age": int(m.group(1)), "max_age": int(m.group(2))}]
    low = _AGE_MIN_RE.search(quote)
    rest = quote[:low.start()] + " " + quote[low.end():] if low else quote
    m = _AGE_GENERAL_RE.search(rest) or _AGE_OR_LESS_RE.search(rest)
    if not m:
        return []
    return [{"category": "ALL", **({"min_age": int(low.group(1))} if low else {}), "max_age": int(m.group(1))}]

def age_limits_in(quote: str) -> List[Dict[str, Any]]:
    """Upper age limits stated in the quote; category-specific when the quote names categories.
    A relaxation clause or an implausible limit (under 18) yields nothing."""
    if _RELAXATION_RE.search(quote):
        return []
    limits = _age_limits(quote)
    return [] if any(l["max_age"] < 18 or l.get("min_age", 0) >= l["max_age"] for l in limits) else limits

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
        tokens = _tokens(str(value or ""))
        base = re.sub(r"\s+", " ", doc_text).strip()
        m = _words_pattern(tokens).search(_norm(base)) if tokens else None
        if sum(len(t) for t in tokens if t.isalpha()) < 3:
            v.reason = "post name is too short to identify a post"
        elif m:
            # Store the document's own words (also the merge key), not the LLM's spelling
            v.accepted, v.value, v.reason = True, base[m.start():m.end()], "post name is whole words of the quote" + page_note
        else:
            v.reason = "post name is not whole words of its quote"
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
        # A quote can hold several dates (opening, closing, interview); the LLM's value picks one of them
        spans = _date_spans(doc_text)
        wanted = set(dates_in(str(value or "")))
        hits = [(d, s, e) for d, s, e in spans if d in wanted]
        if not spans:
            v.reason = "no date in the quote"
        elif len(wanted) != 1 or not hits:
            v.reason = f"{value!r} is not one of the dates in the quote"
        else:
            d, s, e = hits[0]
            hm = time_in(_clause_around(doc_text, s, e))
            v.accepted, v.value = True, datetime(d.year, d.month, d.day, *(hm or (23, 59)), tzinfo=IST)
            v.reason = ("date parsed from the quote" + ("" if hm else "; time not stated, 23:59 IST assumed") + page_note)
    elif name == "age_limit":
        limits = age_limits_in(doc_text)
        # An OCR near match can hide a changed number ("62" quoted, "67" printed): the LLM's ages must be the document's
        stated = {int(n) for n in re.findall(r"\b\d{2}\b", str(value or ""))}
        if limits and not stated <= {a for l in limits for a in (l.get("min_age"), l["max_age"])}:
            v.reason = f"{value!r} disagrees with the ages in the quote"
        elif limits:
            v.accepted, v.value, v.reason = True, limits, "age limit parsed from the quote" + page_note
        elif _RELAXATION_RE.search(doc_text):
            v.reason = "quote is about age relaxation, not the age limit"
        else:
            v.reason = "no upper age limit recognisable in the quote"
    elif len(_tokens(doc_text)) < MIN_TEXT_WORDS:
        v.reason = f"quote has fewer than {MIN_TEXT_WORDS} words"
    else:
        v.accepted, v.value, v.reason = True, doc_text, "quote found; stored verbatim" + page_note
    return v

def verify_vacancy(raw: Dict[str, Any], pages: List[PageText]) -> VacancyVerdict:
    return VacancyVerdict({name: verify_field(name, raw.get(name), pages) for name in ALL_FIELDS})
