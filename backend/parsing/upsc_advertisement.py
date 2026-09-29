"""
Deterministic parser for UPSC "Recruitment by Selection" advertisements
(e.g. https://www.upsc.gov.in/sites/default/files/AdvtNo-12-2026-Engl-250926_0.pdf).

Every extracted value carries provenance: the 1-based PDF page where it starts,
the advertisement section it came from, and the exact source excerpt.
No value is inferred when the text does not state it; missing values stay None.
"""
import bisect
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from govcareeros.backend.documents.pdf_text import PageText

PARSER_VERSION = "upsc_advertisement/2"
IST = timezone(timedelta(hours=5, minutes=30))

_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90,
}

def words_to_number(text: str) -> Optional[int]:
    """'Sixty' -> 60, 'One hundred forty' -> 140, 'Twenty-five' -> 25, '12' -> 12; None if not a number."""
    text = text.strip().lower()
    if text.isdigit():
        return int(text)
    total, current = 0, 0
    tokens = [t for t in re.split(r"[\s-]+", text) if t and t != "and"]
    if not tokens:
        return None
    for tok in tokens:
        if tok in _UNITS:
            current += _UNITS[tok]
        elif tok == "hundred":
            current = max(current, 1) * 100
        elif tok == "thousand":
            total += max(current, 1) * 1000
            current = 0
        else:
            return None
    return total + current

# Section headings that appear inside every vacancy block
SECTION_HEADINGS = [
    "RESERVATION POSITION", "PAY SCALE", "AGE", "ESSENTIAL QUALIFICATIONS",
    "DESIRABLE QUALIFICATIONS", "DUTIES", "OTHER DETAILS", "PROBATION",
    "HEADQUARTERS", "ANY OTHER CONDITIONS",
]
_SECTION_RE = re.compile(r"^\s*(" + "|".join(re.escape(h) for h in SECTION_HEADINGS) + r")\s*:", re.M)
_VACANCY_START_RE = re.compile(r"\(Vacancy No\.\s*(\d+)\)")
_HEADER_RE = re.compile(
    r"\(Vacancy No\.\s*(?P<ref>\d+)\)\s*(?P<count>\d+|[A-Za-z][A-Za-z\s-]*?)\s+vacanc(?:y|ies)\s+for\s+the\s+post\s+of\s+"
    r"(?P<post>.+?)\s+in\s+(?P<org>.+?)\.?\s*$",
    re.S | re.I,
)
_ADVT_NO_RE = re.compile(r"ADVERTISEMENT\s+NO\.\s*(\d+\s*/\s*\d{4})", re.I)
_ONLINE_MODE_RE = re.compile(r"INVITES\s+ONLINE\s+RECRUITMENT\s+APPLICATIONS", re.I)
_OPENING_RE = re.compile(r"TO\s+THE\s+ABOVE\s+POSTS\s+FROM\s+(\d{2}-\d{2}-\d{4})", re.I)
_CLOSING_RE = re.compile(
    r"CLOSING\s+DATE\s+FOR\s+SUBMISSION\s+OF\s+ONLINE\s+RECRUITMENT\s+APPLICATION\s+"
    r"THROUGH\s+WEBSITE\s+IS\s+(\d{4})\s*HRS\s+ON\s+(\d{2}-\d{2}-\d{4})",
    re.I,
)
_CRUCIAL_DATE_RE = re.compile(
    r"DATE\s+FOR\s+DETERMINING\s+THE\s+ELIGIBILITY\s+OF\s+ALL\s+CANDIDATES\s+IN\s+EVERY\s+"
    r"RESPECT\s+SHALL\s+BE\s+THE\s+PRESCRIBED\s+CLOSING\s+DATE",
    re.I,
)
_APPLICATION_URL_RE = re.compile(r"https://upsconline\.nic\.in", re.I)
_GENERAL_INSTRUCTIONS_RE = re.compile(r"\(IMPORTANT\)")
_AGE_LIMIT_RE = re.compile(r"(\d{2})\s+years\s+for\s+([A-Za-z/]+)")
_AGE_RELAXATION_RE = re.compile(r"relaxable\s+up\s*to\s+(\d{1,2})\s+years\s+for\s+([A-Za-z]+)", re.I)
_RESERVATION_RE = re.compile(r"\b(UR|EWS|OBC|SC|ST|PwBD)-(\d+)")
_PAGE_NUMBER_HEADER_RE = re.compile(r"\A\s*\d{1,3}\s*\n")

@dataclass
class Provenance:
    page: int
    section: str
    excerpt: str

@dataclass
class ParsedField:
    value: Any
    provenance: Provenance

@dataclass
class ParsedRequirement:
    req_type: str
    value: Any
    is_mandatory: bool
    provenance: Provenance

@dataclass
class ParsedVacancy:
    external_ref: str
    fields: Dict[str, ParsedField] = field(default_factory=dict)
    requirements: List[ParsedRequirement] = field(default_factory=list)

    def value(self, name: str) -> Any:
        f = self.fields.get(name)
        return f.value if f else None

@dataclass
class ParsedAdvertisement:
    parser_version: str
    fields: Dict[str, ParsedField]
    vacancies: List[ParsedVacancy]
    warnings: List[str]

def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()

class _PagedText:
    """Concatenated page text with offset -> page lookup."""
    def __init__(self, pages: List[PageText]):
        self.starts: List[int] = []
        self.numbers: List[int] = []
        parts = []
        offset = 0
        for p in pages:
            # Every page opens with its printed page number; drop it so it doesn't leak into field values
            page_text = _PAGE_NUMBER_HEADER_RE.sub("", p.text, count=1)
            self.starts.append(offset)
            self.numbers.append(p.page_number)
            parts.append(page_text)
            offset += len(page_text) + 1
        self.text = "\n".join(parts)

    def page_at(self, offset: int) -> int:
        return self.numbers[bisect.bisect_right(self.starts, offset) - 1]

    def prov(self, start: int, end: int, section: str, max_len: int = 400) -> Provenance:
        return Provenance(page=self.page_at(start), section=section, excerpt=_clean(self.text[start:end])[:max_len])

def _parse_date(ddmmyyyy: str, hhmm: str = "0000") -> datetime:
    return datetime.strptime(f"{ddmmyyyy} {hhmm}", "%d-%m-%Y %H%M").replace(tzinfo=IST)

def _split_org(org: str) -> Tuple[Optional[str], str]:
    """'Legislative Department, Ministry Of Law and Justice' -> (ministry, department)."""
    m = re.search(r"(Ministry\s+Of\s+.+)$", org, re.I)
    if not m:
        return None, org
    department = org[: m.start()].rstrip(" ,")
    return m.group(1).strip(), department or m.group(1).strip()

def parse_advertisement(pages: List[PageText]) -> ParsedAdvertisement:
    doc = _PagedText(pages)
    text = doc.text
    warnings: List[str] = []
    ad_fields: Dict[str, ParsedField] = {}

    m = _ADVT_NO_RE.search(text)
    if m:
        ad_fields["advertisement_number"] = ParsedField(re.sub(r"\s+", "", m.group(1)), doc.prov(m.start(), m.end(), "Header"))
    else:
        warnings.append("advertisement number not found")

    m = _ONLINE_MODE_RE.search(text)
    if m:
        ad_fields["application_mode"] = ParsedField("ONLINE", doc.prov(m.start(), m.end(), "Header"))
    else:
        warnings.append("application mode not stated")

    m = _OPENING_RE.search(text)
    if m:
        ad_fields["application_start"] = ParsedField(_parse_date(m.group(1)), doc.prov(m.start(), m.end(), "Important Dates"))
    else:
        warnings.append("application opening date not found")

    m = _CLOSING_RE.search(text)
    if m:
        ad_fields["application_deadline"] = ParsedField(_parse_date(m.group(2), m.group(1)), doc.prov(m.start(), m.end(), "Important Dates"))
    else:
        warnings.append("closing date not found")

    m = _CRUCIAL_DATE_RE.search(text)
    if m and "application_deadline" in ad_fields:
        ad_fields["eligibility_crucial_date"] = ParsedField(
            ad_fields["application_deadline"].value, doc.prov(m.start(), m.end(), "Important Dates")
        )
    else:
        warnings.append("eligibility crucial date not stated; not inferred")

    m = _APPLICATION_URL_RE.search(text)
    if m:
        ad_fields["application_url"] = ParsedField(m.group(0), doc.prov(max(0, m.start() - 80), m.end(), "Header"))

    starts = [m.start() for m in _VACANCY_START_RE.finditer(text)]
    if not starts:
        warnings.append("no '(Vacancy No. ...)' blocks found")
    gi = _GENERAL_INSTRUCTIONS_RE.search(text, starts[-1]) if starts else None
    block_end_limit = gi.start() if gi else len(text)
    vacancies: List[ParsedVacancy] = []

    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else block_end_limit
        # Drop the "N." item number that precedes the next vacancy
        block = re.sub(r"\n\s*\d+\.\s*$", "", text[start:end])
        vacancies.append(_parse_vacancy(doc, start, block, warnings))

    return ParsedAdvertisement(PARSER_VERSION, ad_fields, vacancies, warnings)

def _parse_vacancy(doc: _PagedText, block_start: int, block: str, warnings: List[str]) -> ParsedVacancy:
    sections = list(_SECTION_RE.finditer(block))
    header_end = sections[0].start() if sections else len(block)
    header = block[:header_end]

    ref = _VACANCY_START_RE.search(block).group(1)
    vac = ParsedVacancy(external_ref=ref)
    hp = doc.prov(block_start, block_start + header_end, "Vacancy Details")

    hm = _HEADER_RE.search(_clean(header))
    if not hm:
        warnings.append(f"vacancy {ref}: header not parsed")
    else:
        count_word = hm.group("count")
        count = words_to_number(count_word)
        if count is None:
            warnings.append(f"vacancy {ref}: vacancy count '{count_word}' not understood")
        vac.fields["post_name"] = ParsedField(_clean(hm.group("post")), hp)
        vac.fields["vacancy_count"] = ParsedField(count, hp)
        ministry, department = _split_org(_clean(hm.group("org")))
        vac.fields["department"] = ParsedField(department, hp)
        if ministry:
            vac.fields["ministry"] = ParsedField(ministry, hp)

    seen = set()
    for j, s in enumerate(sections):
        name = s.group(1)
        body_end = sections[j + 1].start() if j + 1 < len(sections) else len(block)
        body = block[s.end():body_end]
        prov = doc.prov(block_start + s.start(), block_start + body_end, name.title())
        if name in seen:
            warnings.append(f"vacancy {ref}: repeated section {name} ignored")
            continue
        seen.add(name)
        _apply_section(vac, name, body, prov)

    # Cross-check: category-wise reservation (excluding the PwBD overlay) must add up to the stated count
    split = [r.value["vacancies"] for r in vac.requirements if r.req_type == "RESERVATION" and r.value["category"] != "PwBD"]
    if split and vac.value("vacancy_count") is not None and sum(split) != vac.value("vacancy_count"):
        warnings.append(f"vacancy {ref}: reservation split {sum(split)} != vacancy count {vac.value('vacancy_count')}")

    for required in ("post_name", "vacancy_count"):
        if vac.value(required) is None:
            warnings.append(f"vacancy {ref}: {required} missing")
    return vac

def _apply_section(vac: ParsedVacancy, name: str, body: str, prov: Provenance) -> None:
    clean = _clean(body)
    if name == "RESERVATION POSITION":
        for cat, n in _RESERVATION_RE.findall(clean.split("Category-wise")[0]):
            vac.requirements.append(ParsedRequirement("RESERVATION", {"category": cat, "vacancies": int(n)}, False, prov))
    elif name == "PAY SCALE":
        vac.fields["pay_scale"] = ParsedField(clean, prov)
    elif name == "AGE":
        relaxations = _AGE_RELAXATION_RE.findall(clean)
        without_relax = _AGE_RELAXATION_RE.sub("", clean)
        for years, cats in _AGE_LIMIT_RE.findall(without_relax):
            for cat in cats.split("/"):
                vac.requirements.append(ParsedRequirement("AGE_MAX", {"category": cat, "max_age": int(years)}, True, prov))
        for years, cat in relaxations:
            vac.requirements.append(ParsedRequirement("AGE_RELAXATION", {"category": cat, "years": int(years)}, False, prov))
    elif name == "ESSENTIAL QUALIFICATIONS":
        edu, exp = _split_education_experience(clean)
        vac.requirements.append(ParsedRequirement("EDUCATION", edu, True, prov))
        if exp:
            vac.requirements.append(ParsedRequirement("EXPERIENCE", exp, True, prov))
    elif name == "DESIRABLE QUALIFICATIONS":
        vac.requirements.append(ParsedRequirement("DESIRABLE", clean, False, prov))
    elif name == "HEADQUARTERS":
        vac.fields["headquarters"] = ParsedField(clean, prov)
    elif name == "OTHER DETAILS":
        vac.fields["service_classification"] = ParsedField(clean, prov)

def _split_education_experience(clean: str) -> Tuple[str, Optional[str]]:
    # "(A) EDUCATIONAL ... (B) EXPERIENCE: ..." — text kept verbatim, only split
    m = re.search(r"\(B\)\s*EXPERIENCE\s*:?", clean)
    edu = clean[: m.start()] if m else clean
    exp = clean[m.end():] if m else None
    edu = re.sub(r"^\(A\)\s*EDUCATIONAL\s*:?\s*|^EDUCATIONAL\s*:\s*", "", edu).strip()
    if exp:
        exp = re.split(r"\bNOTE\s*\d*\s*:", exp)[0].strip()
    edu = re.split(r"\bNOTE\s*\d*\s*:", edu)[0].strip()
    return edu, exp
