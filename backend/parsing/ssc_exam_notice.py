"""
Deterministic parser for SSC examination notices ("Notice of Combined Higher Secondary (10+2) Level
Examination, 2026", https://ssc.gov.in/api/attachment/uploads/masterData/NoticeBoards/Notice_of_adv_chsle_2026.pdf).

SSC notices share one layout: a front-page date table, the F. No. line, then numbered paragraphs
(Pay Scale, Vacancies, Nationality/Citizenship, Age Limit + relaxation table, Essential Educational
Qualifications, Application Fee). Every value carries the page, section and exact excerpt it came from.
Nothing is inferred: a missing section stays missing and is reported in `warnings`.

Age limits per category are derived by fixed rules only: the stated upper limit plus the stated
category relaxation (e.g. 27 + 3 for OBC), and the excerpt cites both sentences. Relaxations that
are not "N years for category X" (ex-servicemen after deducting service, widows "up to 35 years",
Central Government employees "up to 40 years") are stored as AGE_RELAXATION_RULE rows with their own
basis names for the exam engine; they never reuse the UPSC rule names, whose meaning differs.
Post-wise age limits (e.g. JE: "up to 30 years" for some posts, "up to 32 years" for others) are
not mapped to posts here; the notice then gets no exam-level AGE_MAX and a warning.
"""
import bisect
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from govcareeros.backend.documents.pdf_text import PageText
from govcareeros.backend.parsing.upsc_advertisement import IST, ParsedField, ParsedRequirement, Provenance

PARSER_VERSION = "ssc_exam_notice/1"
CONDUCTING_BODY = "Staff Selection Commission"

# Conventional SSC short codes; a label for display and matching, not a fact taken from the notice
EXAM_CODES = [
    (r"Combined\s+Graduate\s+Level", "CGL"), (r"Combined\s+Higher\s+Secondary", "CHSL"),
    (r"Junior\s+Engineer", "JE"), (r"Multi[\s-]+Tasking", "MTS"), (r"Stenographer\s+Grade\s+.C.\s+and\s+.D.", "STENO"),
    (r"Sub-?Inspector\s+in\s+Delhi\s+Police", "CPO"), (r"Constable\s*\(GD\)", "GD"), (r"Selection\s+Posts?", "SELECTION_POST"),
    (r"Hindi\s+Translators?", "JHT"),
]

@dataclass
class ParsedExamPost:
    post_key: str
    post_name: str
    pay_level: Optional[str]
    pay_scale: Optional[str]
    provenance: Provenance

@dataclass
class ParsedExam:
    parser_version: str
    fields: Dict[str, ParsedField]
    posts: List[ParsedExamPost]
    requirements: List[ParsedRequirement]
    warnings: List[str] = field(default_factory=list)

    def value(self, name: str):
        f = self.fields.get(name)
        return f.value if f else None

def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()

class _Doc:
    """Concatenated page text (the "Page n of N" header dropped) with offset -> page lookup."""
    _HEADER = re.compile(r"\A\s*Page\s+\d+\s+of\s+\d+\s*\n")

    def __init__(self, pages: List[PageText]):
        self.starts, self.numbers, parts, offset = [], [], [], 0
        for p in pages:
            text = self._HEADER.sub("", p.text, count=1)
            self.starts.append(offset)
            self.numbers.append(p.page_number)
            parts.append(text)
            offset += len(text) + 1
        self.text = "\n".join(parts)

    def page_at(self, offset: int) -> int:
        return self.numbers[bisect.bisect_right(self.starts, offset) - 1]

    def prov(self, start: int, end: int, section: str, max_len: int = 600) -> Provenance:
        return Provenance(self.page_at(start), section, _clean(self.text[start:end])[:max_len])

_D = r"(\d{2}[.-]\d{2}[.-]\d{4})"
_TIME = r"\(\s*(\d{2}):(\d{2})\s*hours?\s*\)"

def _date(ddmmyyyy: str, hh: str = "00", mm: str = "00") -> datetime:
    d, m, y = re.split(r"[.-]", ddmmyyyy)
    return datetime(int(y), int(m), int(d), int(hh), int(mm), tzinfo=IST)

_NAME_RE = re.compile(r"^\s*(?:NOTICE|Notice)\s*\n\s*(?:\n\s*)*([^\n]*?Examination,?\s*(\d{4}))\s*$", re.M)
_FILE_NO_RE = re.compile(r"F\.\s*No\.\s*(\S+?)\s*:")
_WINDOW_RE = re.compile(r"Dates?\s+for\s+submission\s+of\s+online\s+applications?(?:\s+form)?\s+" + _D + r"\s+to\s+" + _D, re.I)
_LAST_RE = re.compile(r"Last\s+date\s+and\s+time\s+for\s+receipt\s+of\s+online\s+applications?(?:\s+form)?\s+" + _D + r"\s*" + _TIME, re.I)
_FEE_LAST_RE = re.compile(r"Last\s+date\s+and\s+time\s+for\s+making\s+online\s+fee\s+payment\s+" + _D + r"\s*" + _TIME, re.I)
_CORRECTION_RE = re.compile(r"Window\s+for\s+(?:online\s+)?Application\s+Form\s+Correction.{0,120}?" + _D + r"\s+to\s+" + _D + r"\s*" + _TIME, re.I | re.S)
_SCHEDULE_RE = re.compile(r"(?:Tentative\s+)?schedule\s+of\s+(.+?\))\s+(.+?)\s+(?=(?:Tentative\s+)?schedule\s+of|Toll)", re.I | re.S)
_TENTATIVE_RE = re.compile(r"approx\.?\s+(\d[\d,]*)\s+tentative\s+vacancies", re.I)
_PAY_SECTION_RE = re.compile(r"Pay\s+Scale\s*:(.*?)(?:Note\s*-?\s*I\b|\n\s*2\.\s*\n)", re.S | re.I)
_PAY_ITEM_RE = re.compile(r"\b1\.\d+\s+(?P<post>[^:]{3,200}?):\s*(?P<pay>Pay\s+Level.+?)\.(?=\s+1\.\d+\s|\s*$)", re.S)
_CITIZENSHIP_RE = re.compile(r"Nationality\s*/\s*Citizenship\s*:(.*?)Age\s+Limit", re.S | re.I)
_CITIZENSHIP_CLAUSES = [
    ("INDIAN_CITIZEN", r"a\s+citizen\s+of\s+India"), ("NEPAL_SUBJECT", r"a\s+subject\s+of\s+Nepal"),
    ("BHUTAN_SUBJECT", r"a\s+subject\s+of\s+Bhutan"),
    ("TIBETAN_REFUGEE_PRE_1962", r"Tibetan\s+refugee\s+who\s+came\s+over\s+to\s+India\s+before\s+(?:the\s+)?1st\s+January,?\s+1962"),
    ("PIO_MIGRANT", r"person\s+of\s+Indian\s+origin\s+who\s+has\s+migrated"),
]
_CRUCIAL_RE = re.compile(r"crucial\s+date\s+for\s+age\s+reckoning\s+is\s+fixed\s+as\s+" + _D, re.I)
_AGE_UNIFORM_RE = re.compile(r"Age\s+limit\s+for\s+the\s+posts?\s+is\s+(\d{2})\s*-\s*(\d{2})\s+years.*?eligible\s+to\s+apply\.", re.I | re.S)
_AGE_TIERS_RE = re.compile(r"age\s+limit\s+is\s+up\s*to\s+(\d{2})\s+years", re.I)
_RELAX_SECTION_RE = re.compile(r"Permissible\s+relaxations?\s+in\s+(?:the\s+)?upper\s+age\s+limit(.*?)(?:\n\s*\d+\.3\s*\n|Date\s+of\s+birth\s+filled)", re.S | re.I)
_ROW_CODE_RE = re.compile(r"^\s*(\d{2})\s*$", re.M)
_EDU_HEAD_RE = re.compile(r"Essential\s+Educational\s+Qualifications?\s*\(\s*As\s+on\s+" + _D + r"\s*\)\s*:", re.I)
_EDU_PARA_RE = re.compile(r"(?:^|\n)\s*(\d+\.\d+)\s*\n(.*?)(?=\n\s*\d+\.\d+\s*\n|\n\s*\d+\.\s*\n)", re.S)
_EDU_REQ_RE = re.compile(r"\b(?:12th|XII|10\+2|Matriculat|10th|degree|diploma|graduat)", re.I)
_FEE_RE = re.compile(r"Fee\s+payable\s*:\s*(?:Rs\.?|₹)\s*([\d,]+)\s*/-[^\n]*", re.I)
_FEE_EXEMPT_RE = re.compile(r"Women\s+candidates\s+and\s+candidates\s+belonging\s+to.*?exempted\s+from\s+payment\s+of\s+fee\.", re.I | re.S)

def exam_code_for(name: str) -> Optional[str]:
    return next((code for rx, code in EXAM_CODES if re.search(rx, name, re.I)), None)

def post_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()[:255]

def _relaxation_rows(body: str) -> List[Tuple[str, str, int]]:
    """(category text, relaxation text, offset in body) per numbered row of the relaxation table."""
    codes = list(_ROW_CODE_RE.finditer(body))
    rows = []
    for i, m in enumerate(codes):
        cell = body[m.end(): codes[i + 1].start() if i + 1 < len(codes) else len(body)]
        text = _clean(cell)
        # The last line(s) of a row are the relaxation: "5 years", "Up to 40 years of age", "03 years after deduction ..."
        # "not less than 3 years regular service" inside a category cell is a condition, not the relaxation
        split = re.search(r"Up\s+to\s+\d{2}\s+years.*$", text, re.I) or \
            next(reversed(list(re.finditer(r"\b\d{1,2}\s+years\b.*$", text, re.I))), None)
        if split:
            rows.append((text[:split.start()].strip(), text[split.start():].strip(), m.end()))
    return rows

_SC_ST = re.compile(r"\(?\s*SC\s*/\s*ST\s*\)?\.?$")

def _parse_relaxations(doc: _Doc, base: Dict[str, int], age_prov: Provenance, reqs: List[ParsedRequirement], warnings: List[str]) -> None:
    m = _RELAX_SECTION_RE.search(doc.text)
    if not m:
        warnings.append("age relaxation table not found")
        return
    years: Dict[str, int] = {}
    cited: Dict[str, str] = {}  # category -> the table row its relaxation came from
    pwbd: Dict[str, int] = {}
    pwbd_prov = None
    section = "Age Limit: relaxation table"
    for cat, relax, at in _relaxation_rows(m.group(1)):
        n = re.match(r"(\d{1,2})\s+years\b", relax, re.I)
        cap = re.match(r"Up\s+to\s+(\d{2})\s+years", relax, re.I)
        row = f"{cat} {relax}"  # the row's own words
        prov = Provenance(doc.page_at(m.start(1) + at), section, row)
        sc_st = bool(_SC_ST.search(cat))
        if re.fullmatch(r"SC\s*/\s*ST", cat) and n:
            years["SC"] = years["ST"] = int(n.group(1))
            cited["SC"] = cited["ST"] = row
        elif re.fullmatch(r"OBC", cat) and n:
            years["OBC"] = int(n.group(1))
            cited["OBC"] = row
        elif cat.startswith("PwBD") and n:
            key = "SC" if sc_st else "OBC" if "OBC" in cat else "UR"
            pwbd[key] = int(n.group(1))
            pwbd_prov = prov if key == "UR" else pwbd_prov
        elif re.match(r"Ex-?\s*Servicemen", cat, re.I) and n and re.search(r"after\s+deduction\s+of\s+the\s+military\s+service", relax, re.I):
            reqs.append(ParsedRequirement("AGE_RELAXATION_RULE", {"basis": "EX_SERVICEMEN_SERVICE_DEDUCTED", "years": int(n.group(1))},
                                          False, prov))
        elif re.match(r"Defence\s+Personnel\s+disabled", cat, re.I) and n:
            reqs.append(ParsedRequirement("AGE_RELAXATION_RULE", {"basis": "DISABLED_DEFENCE_PERSONNEL", "years": int(n.group(1)),
                                                                  "categories": ["SC", "ST"] if sc_st else ["UR", "EWS", "OBC"]}, False, prov))
        elif re.match(r"Central\s+Government\s+Civilian\s+Employees", cat, re.I) and cap:
            service = re.search(r"not\s+less\s+than\s+(\d+)\s+years", cat, re.I)
            reqs.append(ParsedRequirement("AGE_RELAXATION_RULE", {
                "basis": "CENTRAL_GOVT_CIVILIAN_EMPLOYEE", "max_age": int(cap.group(1)),
                "min_service_years": int(service.group(1)) if service else None,
                "categories": ["SC", "ST"] if sc_st else ["UR", "EWS", "OBC"]}, False, prov))
        elif re.match(r"Widows", cat, re.I) and cap:
            reqs.append(ParsedRequirement("AGE_RELAXATION_RULE", {"basis": "WIDOW_DIVORCED_SEPARATED", "max_age": int(cap.group(1)),
                                                                  "categories": ["SC", "ST"] if sc_st else ["UR", "EWS", "OBC"]}, False, prov))
        else:
            warnings.append(f"relaxation row not understood: {row[:120]}")

    if base:
        for category in ("UR", "EWS", "OBC", "SC", "ST"):
            extra = years.get(category, 0)
            value = {"category": category, "min_age": base["min_age"], "max_age": base["max_age"] + extra}
            if extra:
                value.update(base_max_age=base["max_age"], relaxation_years=extra)
            # Derived by addition from two stated facts; the excerpt quotes both (limit sentence | table row)
            reqs.append(ParsedRequirement("AGE_MAX", value, True, Provenance(
                age_prov.page, "Age Limit" + (" + relaxation table" if extra else ""),
                age_prov.excerpt + (f" | {cited[category]}" if extra else ""))))
    if "UR" in pwbd:
        reqs.append(ParsedRequirement("AGE_RELAXATION", {"category": "PwBD", "years": pwbd["UR"]}, False, pwbd_prov))
        # The PwBD rows for OBC and SC/ST are the category relaxation plus the PwBD one; anything else is not modelled
        for cat in ("OBC", "SC"):
            if cat in pwbd and pwbd[cat] != pwbd["UR"] + years.get(cat, 0):
                warnings.append(f"PwBD ({cat}) relaxation {pwbd[cat]} is not category + PwBD years; not modelled")

def parse_exam_notice(pages: List[PageText]) -> ParsedExam:
    doc = _Doc(pages)
    text = doc.text
    fields: Dict[str, ParsedField] = {}
    reqs: List[ParsedRequirement] = []
    posts: List[ParsedExamPost] = []
    warnings: List[str] = []

    def put(name, value, m, section, max_len=600):
        fields[name] = ParsedField(value, doc.prov(m.start(), m.end(), section, max_len))

    m = _NAME_RE.search(text)
    if m:
        name = _clean(m.group(1))
        put("name", name, m, "Title")
        fields["exam_year"] = ParsedField(int(m.group(2)), fields["name"].provenance)
    else:
        warnings.append("examination name not found")
    m = _FILE_NO_RE.search(text)
    if m:
        put("file_number", m.group(1).rstrip("."), m, "File number")
    m = _WINDOW_RE.search(text)
    if m:
        put("application_start", _date(m.group(1)), m, "Important dates")
    m = _LAST_RE.search(text)
    if m:
        put("application_end", _date(m.group(1), m.group(2), m.group(3)), m, "Important dates")
    else:
        warnings.append("last date for online applications not found")
    m = _FEE_LAST_RE.search(text)
    if m:
        put("fee_payment_end", _date(m.group(1), m.group(2), m.group(3)), m, "Important dates")
    m = _CORRECTION_RE.search(text)
    if m:
        put("correction_start", _date(m.group(1)), m, "Important dates")
        put("correction_end", _date(m.group(2), m.group(3), m.group(4)), m, "Important dates")
    # Exam schedule rows live in the front-page date table, before the F. No. paragraph
    file_no = _FILE_NO_RE.search(text)
    schedule = list(_SCHEDULE_RE.finditer(text[: file_no.start() if file_no else 4000]))
    if schedule:
        stages = [{"stage": _clean(s.group(1)), "schedule": _clean(s.group(2))} for s in schedule]
        fields["exam_dates"] = ParsedField(stages, doc.prov(schedule[0].start(), schedule[-1].end(), "Important dates"))
    m = _TENTATIVE_RE.search(text)
    if m:
        put("tentative_vacancies", int(m.group(1).replace(",", "")), m, "Vacancies")

    m = _PAY_SECTION_RE.search(text)
    if m:
        for item in _PAY_ITEM_RE.finditer(m.group(1)):
            name = _clean(item.group("post"))
            pay = _clean(item.group("pay"))
            start = m.start(1) + item.start()
            posts.append(ParsedExamPost(post_key(name), name, ", ".join(f"Level-{n}" for n in re.findall(r"Level-?\s*(\d+)", pay)),
                                        pay, doc.prov(start, m.start(1) + item.end(), "Pay Scale")))
    if not posts:
        warnings.append("no post-wise pay scale list found")

    m = _CITIZENSHIP_RE.search(text)
    if m:
        body = m.group(1)
        accepted = [name for name, rx in _CITIZENSHIP_CLAUSES if re.search(rx, body, re.I)]
        needs_cert = [a for a in accepted if a != "INDIAN_CITIZEN"] if re.search(r"certificate\s+of\s+eligibility", body, re.I) else []
        reqs.append(ParsedRequirement("CITIZENSHIP", {"accepted": accepted, "certificate_of_eligibility_required": needs_cert}, True,
                                      doc.prov(m.start(), m.start() + min(len(m.group(0)), 1200), "Nationality/ Citizenship", 1200)))
    else:
        warnings.append("citizenship section not found")

    m = _CRUCIAL_RE.search(text)
    if m:
        put("age_crucial_date", _date(m.group(1)), m, "Age Limit")
    else:
        warnings.append("crucial date for age not found")
    base: Dict[str, int] = {}
    m = _AGE_UNIFORM_RE.search(text)
    age_prov = None
    if m:
        base = {"min_age": int(m.group(1)), "max_age": int(m.group(2))}
        age_prov = doc.prov(m.start(), m.end(), "Age Limit")
        fields["age_limit"] = ParsedField(base, age_prov)
    else:
        tiers = sorted({int(t) for t in _AGE_TIERS_RE.findall(text)})
        warnings.append(f"post-wise age limits {tiers} are not mapped to posts; no exam-level age limit stored" if tiers
                        else "age limit not found")
    _parse_relaxations(doc, base, age_prov, reqs, warnings)

    m = _EDU_HEAD_RE.search(text)
    if m:
        put("education_cutoff_date", _date(m.group(1)), m, "Essential Educational Qualifications")
        section_end = re.search(r"\n\s*\d+\.\s*\n", text[m.end():])
        section = text[m.end(): m.end() + (section_end.start() if section_end else 6000)]
        for para in _EDU_PARA_RE.finditer(section + "\n9.\n"):
            body = _clean(para.group(2))
            # "The candidates who have appeared in their 12th ... can also apply" is a provisional-admission rule, not a requirement
            if not _EDU_REQ_RE.search(body) or re.search(r"can\s+also\s+apply|Open\s+and\s+Distance|Document\s+Verification|equivalent\s+educational\s+qualification,", body, re.I):
                continue
            start = m.end() + para.start(2)
            reqs.append(ParsedRequirement("EDUCATION", body, True, doc.prov(start, m.end() + para.end(2), f"Essential Educational Qualifications {para.group(1)}", 1200)))
        if not any(r.req_type == "EDUCATION" for r in reqs):
            warnings.append("no education requirement paragraph recognised")
    else:
        warnings.append("essential educational qualifications section not found")

    m = _FEE_RE.search(text)
    if m:
        put("fee_amount", int(m.group(1).replace(",", "")), m, "Application Fee")
        ex = _FEE_EXEMPT_RE.search(text, m.end())
        if ex and ex.start() - m.end() < 400:
            put("fee_exemption", _clean(ex.group(0)), ex, "Application Fee")
    else:
        warnings.append("application fee not found")

    return ParsedExam(PARSER_VERSION, fields, posts, reqs, warnings)
