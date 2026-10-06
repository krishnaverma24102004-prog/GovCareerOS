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
Post-wise age limits given only in a table (e.g. JE: "up to 30 years" for some posts, "up to 32 years" for
others) are not mapped to posts here; the notice then gets no exam-level AGE_MAX and a warning. parsing/ssc_tables.py
reads the post-wise tables and adds per-post rows on top of this parse (parse_exam_pdf).

Sub-Inspector in Delhi Police and CAPFs 2026 states its posts in running text instead, and that is read here:
"1.1 <post>: The post carries pay scale of Level-6 (...) and is classified as Group 'B' ...", post-wise age
sentences ("Age limit for all posts except SI(FIRE) in CISF is 20-25 years; i.e. ... born not earlier than ...")
and post-wise qualification sentences ("Educational Qualification for Sub Inspector (Fire) is ..."). A sentence
names posts the short way ("SI(FIRE) in CISF"); it is tied to a post only when its words (SI = Sub-Inspector) occur
in that post's name in order and exactly one post fits, otherwise a warning. Relaxation rows stated for some posts
only ("For the post of SI in Delhi Police (Category Codes-12 and 13)") carry those posts' keys (post_keys).
"""
import bisect
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

from govcareeros.backend.documents.pdf_text import PageText
from govcareeros.backend.parsing.upsc_advertisement import IST, ParsedField, ParsedRequirement, Provenance

PARSER_VERSION = "ssc_exam_notice/2"
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
    # Set when the notice states them post-wise (parsing/ssc_tables.py)
    post_code: Optional[str] = None
    department: Optional[str] = None
    classification: Optional[str] = None
    age_min: Optional[int] = None
    age_max: Optional[int] = None
    education_text: Optional[str] = None
    vacancy_count: Optional[int] = None
    requirements: List[ParsedRequirement] = field(default_factory=list)  # rows that apply to this post only

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
_TIME = r"\(\s*(\d{2}):(\d{2})\s*(?:hours?|hrs\.?)\s*\)"

def _date(ddmmyyyy: str, hh: str = "00", mm: str = "00") -> datetime:
    d, m, y = re.split(r"[.-]", ddmmyyyy)
    return datetime(int(y), int(m), int(d), int(hh), int(mm), tzinfo=IST)

def years_before(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year - years)
    except ValueError:  # 29 February
        return d.replace(year=d.year - years, day=28)

def age_on(dob: date, on: date) -> int:
    return on.year - dob.year - ((on.month, on.day) < (dob.month, dob.day))

# The title may wrap onto a second line ("Sub-Inspector in Delhi Police and Central Armed Police\nForces Examination, 2026")
_NAME_RE = re.compile(r"^\s*(?:NOTICE|Notice)\s*\n\s*(?:\n\s*)*([^\n]*?(?:\n[^\n]*?)?Examination,?\s*(\d{4}))\s*$", re.M)
_FILE_NO_RE = re.compile(r"F\.\s*No\.\s*(\S+?)\s*:")
# CAPF 2026 opens its first paragraph with the section's file reference instead: "E/37/2026-C-2 SECTION: The Staff Selection ..."
_FILE_REF_RE = re.compile(r"^\s*([A-Z]{1,3}/\d+/\d{4}-C-\d+)\s+SECTION\s*:", re.M)
_WINDOW_RE = re.compile(r"Dates?\s+for\s+submission\s+of\s+online\s+applications?(?:\s+form)?\s+" + _D + r"\s+to\s+" + _D, re.I)
_LAST_RE = re.compile(r"Last\s+date\s+and\s+time\s+for\s+receipt\s+of\s+online\s+applications?(?:\s+form)?\s+" + _D + r"\s*" + _TIME, re.I)
_FEE_LAST_RE = re.compile(r"Last\s+date\s+and\s+time\s+for\s+(?:making\s+)?online\s+fee\s+payment\s+" + _D + r"\s*" + _TIME, re.I)
_CORRECTION_RE = re.compile(r"Window\s+for\s+(?:online\s+)?Application\s+Form\s+Correction.{0,120}?" + _D + r"\s+to\s+" + _D + r"\s*" + _TIME, re.I | re.S)
_SCHEDULE_RE = re.compile(r"(?:Tentative\s+)?schedule\s+of\s+(.+?\))\s+(.+?)\s+(?=(?:Tentative\s+)?schedule\s+of|Toll)", re.I | re.S)
# "Schedule of Computer Based Examination\nTo be notified later": a stage without the "(...)" the pattern above needs
_SCHEDULE_PLAIN_RE = re.compile(r"Schedule\s+of\s+([^\n()]+?Examination)\s*\n\s*([^\n]+?)\s*\n", re.I)
_TENTATIVE_RE = re.compile(r"approx\.?\s+(\d[\d,]*)\s+tentative\s+vacancies", re.I)
_PAY_SECTION_RE = re.compile(r"Pay\s+Scale\s*:(.*?)(?:Note\s*-?\s*I\b|\n\s*2\.\s*\n)", re.S | re.I)
_PAY_ITEM_RE = re.compile(r"\b1\.\d+\s+(?P<post>[^:]{3,200}?):\s*(?P<pay>Pay\s+Level.+?)\.(?=\s+1\.\d+\s|\s*$)", re.S)
# CAPF 2026: "1.1 Sub-Inspector (GD) in CAPFs: The post carries pay scale of Level-6 (Rs.35,400-\nRs.1,12,400/-) and is classified as Group 'B' ..."
_POST_CARRIES_RE = re.compile(r"(?m)^[ \t]*1\.\d+\.?\s+(?P<post>[^:\n]{3,200}?):\s*The\s+post\s+carries\s+pay\s+scale\s+of\s+"
                              r"(?P<pay>Level-?\s*(?P<level>\d+)\s*\([^)]*\))\s*and\s+is\s+classified\s+as\s+(?P<cls>[^.]+?)\.(?=[ \t]*\n)")
# "Age limit for all posts except SI(FIRE) in CISF is 20-25 years; i.e. Candidate must have been born not earlier than 02.08.2001 and
# not later than 01.08.2006"; the uniform "Age limit for the posts is ..." is _AGE_UNIFORM_RE's
_AGE_POST_RE = re.compile(r"Age\s+limit\s+for\s+(?!the\s+posts?\s)(?P<scope>[^;]{3,150}?)\s+is\s+(\d{2})\s*-\s*(\d{2})\s+years;?\s*i\.?\s*e\.?,?\s*"
                          r"Candidates?\s+must\s+have\s+been\s+born\s+not\s+earlier\s+than\s+" + _D + r"\s+and\s+not\s+later\s+than\s+" + _D, re.I)
_ALL_EXCEPT_RE = re.compile(r"all\s+(?:other\s+)?posts\s+except\s+(.+)", re.I | re.S)
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
# A heading row inside the table that limits the rows below it: "For the post of SI in Delhi Police (Category Codes-12 and 13)"
_RELAX_SCOPE_RE = re.compile(r"\bFor\s+(?P<scope>[^().]{5,200}?)\s*\(\s*Category\s+Codes?\s*-?\s*(?P<codes>\d{2}(?:\s*(?:,|and)\s*\d{2})*)\s*\)")
_EDU_HEAD_RE = re.compile(r"Essential\s+Educational\s+Qualifications?\s*\(\s*As\s+on\s+" + _D + r"\s*\)\s*:", re.I)
# CAPF 2026: "Essential Educational Qualification (as on closing date for receipt of online applications):"
_EDU_HEAD_CLOSING_RE = re.compile(r"Essential\s+Educational\s+Qualifications?\s*\(\s*as\s+on\s+(?:the\s+)?closing\s+date\s+for\s+receipt\s+of\s+"
                                  r"online\s+applications?\s*\)\s*:", re.I)
# "7.2. Educational Qualification for Sub Inspector (Fire) is Bachelor's degree ...": the requirement sentence(s), up to the
# provisional-admission sentence or the next paragraph number
_EDU_POST_RE = re.compile(r"Educational\s+Qualification\s+for\s+(?P<posts>[^\n]+?(?:\n[^\n]+?)?)\s+is\s+(?P<req>.+?)"
                          r"(?=\s+The\s+candidates\s+who\s+have\s+appeared|\n\s*\d+\.\d+\.?\s|\Z)", re.I | re.S)
_EDU_PARA_RE = re.compile(r"(?:^|\n)\s*(\d+\.\d+)\s*\n(.*?)(?=\n\s*\d+\.\d+\s*\n|\n\s*\d+\.\s*\n)", re.S)
_EDU_REQ_RE = re.compile(r"\b(?:12th|XII|10\+2|Matriculat|10th|degree|diploma|graduat)", re.I)
_FEE_RE = re.compile(r"Fee\s+payable\s*:\s*(?:Rs\.?|₹)\s*([\d,]+)\s*/-[^\n]*", re.I)
_FEE_EXEMPT_RE = re.compile(r"Women\s+candidates\s+and\s+candidates\s+belonging\s+to.*?exempted\s+from\s+payment\s+of\s+fee\.", re.I | re.S)

def exam_code_for(name: str) -> Optional[str]:
    return next((code for rx, code in EXAM_CODES if re.search(rx, name, re.I)), None)

def post_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()[:255]

def _ref_words(name: str) -> List[str]:
    # "SI(FIRE) in CISF" -> sub inspector fire in cisf; gender marks do not tell posts apart in a reference
    words = re.sub(r"[^a-z0-9]+", " ", re.sub(r"\bSI\b", "Sub Inspector", name).lower()).split()
    return [w for w in words if w not in ("male", "female")]

def posts_named(ref: str, posts: List["ParsedExamPost"]) -> List["ParsedExamPost"]:
    """Posts whose name contains the reference's words in order ("Sub Inspector (Fire)" -> "Sub-Inspector (FIRE)- (Male) in CISF")."""
    want = _ref_words(ref)

    def fits(p: "ParsedExamPost") -> bool:
        have = iter(_ref_words(p.post_name))
        return bool(want) and all(w in have for w in want)
    return [p for p in posts if fits(p)]

def _posts_in_scope(scope: str, posts: List["ParsedExamPost"]) -> List["ParsedExamPost"]:
    """Posts whose organisation (the words after the last " in " of the post name) the scope text names:
    "For the post of SI in Delhi Police" -> the Delhi Police post."""
    words = " ".join(_ref_words(scope))
    out = []
    for p in posts:
        org = re.split(r"\s+in\s+", p.post_name)[-1] if re.search(r"\s+in\s+", p.post_name) else ""
        if org and re.search(rf"\b{re.escape(' '.join(_ref_words(org)))}\b", words):
            out.append(p)
    return out

def _relaxation_rows(body: str) -> List[Tuple[str, str, int]]:
    """(category text, relaxation text, offset in body) per numbered row of the relaxation table."""
    codes = list(_ROW_CODE_RE.finditer(body))
    rows = []
    for i, m in enumerate(codes):
        cell = body[m.end(): codes[i + 1].start() if i + 1 < len(codes) else len(body)]
        # The last row runs into the next paragraph's number ("Up to 35 years of age 5.4.")
        text = re.sub(r"\s+\d{1,2}\.\d{1,2}\.?$", "", _clean(cell))
        # The last line(s) of a row are the relaxation: "5 years", "Up to 40 years of age", "03 years after deduction ..."
        # "not less than 3 years regular service" inside a category cell is a condition, not the relaxation
        split = re.search(r"Up\s+to\s+\d{2}\s+years.*$", text, re.I) or \
            next(reversed(list(re.finditer(r"\b\d{1,2}\s+years\b.*$", text, re.I))), None)
        if split:
            cat, relax = text[:split.start()].strip(), text[split.start():].strip()
            # A category cell cut by the page break ends after the relaxation cell: "... who are not re- | Up to 40 years
            # of age | married. (SC/ST)"; words without a number after "of age" belong to the category
            rest = re.match(r"(Up\s+to\s+\d{2}\s+years\s+of\s+age)\s+(\D+)$", relax, re.I)
            if rest:
                cat, relax = f"{cat} {rest.group(2)}", rest.group(1)
            rows.append((cat, relax, m.end()))
    return rows

_SC_ST = re.compile(r"\(?\s*SC\s*/\s*ST\s*\)?\.?$")

def _parse_relaxations(doc: _Doc, base: Dict[str, int], age_prov: Provenance, reqs: List[ParsedRequirement], warnings: List[str],
                       posts: Optional[List["ParsedExamPost"]] = None) -> Tuple[Dict[str, int], Dict[str, Provenance]]:
    """Category years (and the row each came from) for per-post limits; rule rows and exam-level AGE_MAX go to reqs."""
    m = _RELAX_SECTION_RE.search(doc.text)
    if not m:
        warnings.append("age relaxation table not found")
        return {}, {}
    years: Dict[str, int] = {}
    cited: Dict[str, str] = {}  # category -> the table row its relaxation came from
    cited_prov: Dict[str, Provenance] = {}
    pwbd: Dict[str, int] = {}
    pwbd_prov = None
    section = "Age Limit: relaxation table"
    body = m.group(1)
    # Scope headings are blanked (same length, so offsets keep their pages) and remembered per row code
    scopes: Dict[str, str] = {}
    for s in _RELAX_SCOPE_RE.finditer(body):
        for code in re.findall(r"\d{2}", s.group("codes")):
            scopes[code] = _clean(s.group(0))
    body = _RELAX_SCOPE_RE.sub(lambda s: " " * len(s.group(0)), body)
    codes = {r.end(): r.group(1) for r in _ROW_CODE_RE.finditer(body)}
    for cat, relax, at in _relaxation_rows(body):
        n = re.match(r"(\d{1,2})\s+years\b", relax, re.I)
        cap = re.match(r"Up\s+to\s+(\d{2})\s+years", relax, re.I)
        row = f"{cat} {relax}"  # the row's own words
        scope = scopes.get(codes.get(at, ""))
        scoped: Dict[str, object] = {}
        if scope:
            row = f"{row} [{scope}]"
            hits = _posts_in_scope(scope, posts or [])
            if not hits:
                warnings.append(f"relaxation row scope matches no post, row not mapped: {row[:160]}")
                continue
            scoped = {"post_keys": [p.post_key for p in hits], "posts_scope": scope}
        prov = Provenance(doc.page_at(m.start(1) + at), section, row)
        sc_st = bool(_SC_ST.search(cat))
        if scope and not (cap and re.match(r"Widows|Departmental\s+candidates", cat, re.I)):
            # Only the post-limited rows CAPF 2026 states are modelled with a scope
            warnings.append(f"relaxation row not understood: {row[:120]}")
        elif re.match(r"Departmental\s+candidates", cat, re.I) and cap:
            # "Departmental candidates (UR/ EWS) who have rendered not less than 3 years of regular and continuous service ... Up to 30 years of age"
            service = re.search(r"not\s+less\s+than\s+(\d+)\s+years", cat, re.I)
            named = re.match(r"Departmental\s+candidates\s*\(\s*([A-Z/\s]+?)\s*\)", cat, re.I)
            cats = [c for c in re.split(r"\s*/\s*", named.group(1).upper()) if c in ("UR", "EWS", "OBC", "SC", "ST")] if named else []
            if not cats:
                warnings.append(f"relaxation row not understood: {row[:120]}")
                continue
            reqs.append(ParsedRequirement("AGE_RELAXATION_RULE", {
                "basis": "DEPARTMENTAL_CANDIDATE", "max_age": int(cap.group(1)),
                "min_service_years": int(service.group(1)) if service else None, "categories": cats, **scoped}, False, prov))
        elif re.fullmatch(r"SC\s*/\s*ST", cat) and n:
            years["SC"] = years["ST"] = int(n.group(1))
            cited["SC"] = cited["ST"] = row
            cited_prov["SC"] = cited_prov["ST"] = prov
        elif re.fullmatch(r"OBC", cat) and n:
            years["OBC"] = int(n.group(1))
            cited["OBC"] = row
            cited_prov["OBC"] = prov
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
                                                                  "categories": ["SC", "ST"] if sc_st else ["UR", "EWS", "OBC"], **scoped},
                                          False, prov))
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
    return years, cited_prov

CATEGORIES = ("UR", "EWS", "OBC", "SC", "ST")

@dataclass
class _PostAge:
    min_age: int
    max_age: int
    born_not_earlier_than: Optional[date]
    born_not_later_than: Optional[date]
    provenance: Provenance

def _post_age_limits(doc: _Doc, stated: List["re.Match"], posts: List[ParsedExamPost], crucial: Optional[date],
                     warnings: List[str]) -> Dict[str, _PostAge]:
    """post_key -> the age limit sentence that names it ("Age limit for SI(FIRE) in CISF is 18-30 years; i.e. ...")."""
    out: Dict[str, _PostAge] = {}
    for s in stated:
        scope = _clean(s.group("scope"))
        excluded = _ALL_EXCEPT_RE.match(scope)
        if excluded:
            others = posts_named(excluded.group(1), posts)
            targets = [p for p in posts if p not in others] if others else []
        else:
            targets = posts_named(scope, posts)
            targets = targets if len(targets) == 1 else []
        if not targets:
            warnings.append(f"age limit sentence names no single post, not mapped: {scope[:100]}")
            continue
        lo, hi = int(s.group(2)), int(s.group(3))
        earliest, latest = _date(s.group(4)).date(), _date(s.group(5)).date()
        prov = doc.prov(s.start(), s.end(), "Age Limit")
        # The birth-date window is the notice's exact rule; kept only when it says the same as the stated years
        if crucial is None or age_on(earliest, crucial) != hi - 1 or age_on(latest, crucial) != lo:
            warnings.append(f"birth-date window does not match {lo}-{hi} years on the crucial date; window not used: {scope[:80]}")
            earliest = latest = None
        for p in targets:
            if p.post_key in out:
                warnings.append(f"two age limit sentences name {p.post_name}; the first is kept")
                continue
            out[p.post_key] = _PostAge(lo, hi, earliest, latest, prov)
    return out

def _post_age_rows(p: ParsedExamPost, lim: _PostAge, years: Dict[str, int], cited: Dict[str, Provenance]) -> List[ParsedRequirement]:
    """AGE_MAX per category for one post: its stated limit plus the category row of the relaxation table (both quoted).
    A relaxed category's earliest birth date moves back by the relaxation years."""
    p.age_min, p.age_max = lim.min_age, lim.max_age
    rows = []
    for category in CATEGORIES:
        extra = years.get(category, 0)
        value = {"category": category, "min_age": lim.min_age, "max_age": lim.max_age + extra}
        if extra:
            value.update(base_max_age=lim.max_age, relaxation_years=extra)
        if lim.born_not_earlier_than:
            value.update(born_not_earlier_than=years_before(lim.born_not_earlier_than, extra).isoformat(),
                         born_not_later_than=lim.born_not_later_than.isoformat())
        quotes = [lim.provenance.excerpt] + ([f"p.{cited[category].page}: {cited[category].excerpt}"] if extra else [])
        rows.append(ParsedRequirement("AGE_MAX", value, True, Provenance(
            lim.provenance.page, "Age Limit" + (" + relaxation table" if extra else ""), " | ".join(quotes))))
    return rows

def _post_education(doc: _Doc, offset: int, section: str, posts: List[ParsedExamPost], warnings: List[str]) -> bool:
    """Post-wise qualification sentences ("Educational Qualification for Sub Inspector in Delhi Police and Sub Inspector (GD)
    in CAPFs is Bachelor's degree ..."); True when every sentence found was tied to its posts."""
    found = list(_EDU_POST_RE.finditer(section))
    if not found:
        return False
    plan = []
    for e in found:
        refs = re.split(r"\s+and\s+", _clean(e.group("posts")))
        hits = [posts_named(r, posts) for r in refs]
        if any(len(h) != 1 for h in hits):
            warnings.append(f"education sentence names no single post, not mapped: {_clean(e.group('posts'))[:100]}")
            return False
        plan.append((e, [h[0] for h in hits]))
    for e, targets in plan:
        req = _clean(e.group("req"))
        prov = doc.prov(offset + e.start(), offset + e.end("req"), "Essential Educational Qualification", 1200)
        for p in targets:
            if p.education_text is not None:
                warnings.append(f"two education sentences name {p.post_name}; the first is kept")
                continue
            p.education_text = req
            p.requirements.append(ParsedRequirement("EDUCATION", req, True, prov))
    for p in posts:
        if p.education_text is None:
            warnings.append(f"no essential qualification found for {p.post_name}")
    return True

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
    file_no = _FILE_NO_RE.search(text) or _FILE_REF_RE.search(text)
    if file_no:
        put("file_number", file_no.group(1).rstrip("."), file_no, "File number")
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
    front = text[: file_no.start() if file_no else 4000]
    schedule = list(_SCHEDULE_RE.finditer(front)) or list(_SCHEDULE_PLAIN_RE.finditer(front))
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
        for item in _POST_CARRIES_RE.finditer(text):
            name = _clean(item.group("post"))
            pay = re.sub(r"-\s+", "-", _clean(item.group("pay")))  # "Rs.35,400-\nRs.1,12,400/-"
            posts.append(ParsedExamPost(post_key(name), name, f"Level-{item.group('level')}", pay,
                                        doc.prov(item.start(), item.end(), "Details of posts"), classification=_clean(item.group("cls"))))
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
    limits: Dict[str, _PostAge] = {}
    if not m:
        stated = list(_AGE_POST_RE.finditer(text))
        crucial = fields["age_crucial_date"].value.date() if "age_crucial_date" in fields else None
        if stated and posts:
            limits = _post_age_limits(doc, stated, posts, crucial, warnings)
        elif stated:
            warnings.append("post-wise age limits stated but no posts parsed; no age limit stored")
        else:
            tiers = sorted({int(t) for t in _AGE_TIERS_RE.findall(text)})
            warnings.append(f"post-wise age limits {tiers} are not mapped to posts; no exam-level age limit stored" if tiers
                            else "age limit not found")
    years, cited = _parse_relaxations(doc, base, age_prov, reqs, warnings, posts)
    for p in posts:
        if p.post_key in limits:
            p.requirements += _post_age_rows(p, limits[p.post_key], years, cited)
        elif limits:
            warnings.append(f"no age limit stated for post {p.post_name}")

    m = _EDU_HEAD_RE.search(text)
    closing_head = None if m else _EDU_HEAD_CLOSING_RE.search(text)
    if closing_head:
        m, end = closing_head, fields.get("application_end")
        if end:
            # "as on closing date for receipt of online applications": the cut-off is the parsed closing date; both are quoted
            d = end.value
            fields["education_cutoff_date"] = ParsedField(datetime(d.year, d.month, d.day, tzinfo=IST), Provenance(
                doc.page_at(m.start()), "Essential Educational Qualifications",
                f"{_clean(m.group(0))} | p.{end.provenance.page}: {end.provenance.excerpt}"))
        else:
            warnings.append("education cut-off is the closing date, which was not found")
    elif m:
        put("education_cutoff_date", _date(m.group(1)), m, "Essential Educational Qualifications")
    if m:
        section_end = re.search(r"\n\s*\d+\.\s*\n", text[m.end():])
        section = text[m.end(): m.end() + (section_end.start() if section_end else 6000)]
        post_wise = _post_education(doc, m.end(), section, posts, warnings) if posts else False
        for para in ([] if post_wise else _EDU_PARA_RE.finditer(section + "\n9.\n")):
            body = _clean(para.group(2))
            # "The candidates who have appeared in their 12th ... can also apply" is a provisional-admission rule, not a requirement
            if not _EDU_REQ_RE.search(body) or re.search(r"can\s+also\s+apply|Open\s+and\s+Distance|Document\s+Verification|equivalent\s+educational\s+qualification,", body, re.I):
                continue
            start = m.end() + para.start(2)
            reqs.append(ParsedRequirement("EDUCATION", body, True, doc.prov(start, m.end() + para.end(2), f"Essential Educational Qualifications {para.group(1)}", 1200)))
        if not post_wise and not any(r.req_type == "EDUCATION" for r in reqs):
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
