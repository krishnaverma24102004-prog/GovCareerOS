"""
Post-wise tables of SSC examination notices, read deterministically with PyMuPDF's page.find_tables().

Real layouts this handles (fixtures in tests/fixtures/ssc):
  * CGL 2026, para 2 "Details of the Posts": S. No. | Name of Post | Ministry/Department/Office/Cadre |
    Classification of Post | Age Limit, grouped under section rows "2.1 Pay Level-8 (Rs 47600 to 151100):".
    The CBIC cell (department, group, age) of row 9 is merged over rows 10-11 and split by the page break.
  * JE 2026, para 2: S. No. | Organization | Post | Essential Educational Qualifications | Age limit, the header
    repeated on every page, qualification text split across pages, organisation/age cells merged over posts.
  * Age-class tables ("For the posts for which age limit is 18-27 years | born not earlier than ... and not later
    than ...") and the category-wise relaxation table (Code | Category | Age-relaxation permissible ...), whose
    rows may carry a scope ("Additional permissible relaxation in upper age limit for Group 'C' posts").
  * Corrigendum/addendum post tables (Phase-XIV Selection Posts addendum: Post Code, Age Limit, Pay Scale, Vacancies).

Assembly rules, all mechanical:
  * A table continues on the next page when it is the last table of its page, ends in the lower part of the
    page, and the next page's first table starts near the top with the same number of columns. A repeated
    header row is dropped.
  * Leading rows of a continuation whose key cells (S. No./Post, Code, first column) are empty carry the rest of
    the previous row's cells (text split by the page break) and are appended to it.
  * A cell PyMuPDF reports as None is covered by a merged cell: it takes the value of the row above. An empty
    string in the first row of a continuation is the lower half of a merged cell cut by the page break: same.
    Inherited values are named in the row's excerpt; an empty cell anywhere else stays empty.
  * Section rows (only the first cell filled, the others merged into it) set the pay level / scope of the rows below.
Nothing is inferred beyond that: a cell that cannot be read is reported in `warnings`.
"""
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from functools import lru_cache
from typing import Dict, List, Optional, Sequence, Tuple

import pymupdf

from govcareeros.backend.documents.pdf_text import PageText
from govcareeros.backend.parsing.ssc_exam_notice import ParsedExam, ParsedExamPost, _Doc, parse_exam_notice
from govcareeros.backend.parsing.upsc_advertisement import IST, ParsedField, ParsedRequirement, Provenance

TABLES_VERSION = "ssc_tables/1"
STRATEGY = "lines_strict"  # only ruled lines make cells; also finds the borderless-header tables of addenda
CATEGORIES = ("UR", "EWS", "OBC", "SC", "ST")

_D = r"(\d{2}[.-]\d{2}[.-]\d{4})"
# Pages worth running find_tables on (it costs ~0.2 s/page); continuation pages contain an age cell anyway
_SIGNAL = re.compile(r"\byears\b|Pay\s+(?:Level|Scale)|Name\s+of\s+(?:the\s+)?Post|relaxation|Schedule\s+of|shall\s+be\s+read", re.I)

def cell_text(c: Optional[str]) -> str:
    if not c:
        return ""
    c = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "-", c)  # "Non-\nMinisterial" -> "Non-Ministerial"
    return re.sub(r"\s+", " ", c).strip()

def norm(s: Optional[str]) -> str:
    """Comparison key: case, punctuation and spacing ignored."""
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower().replace("‘", "").replace("’", "")).strip()

def parse_date(s: str) -> date:
    d, m, y = re.split(r"[.-]", s)
    return date(int(y), int(m), int(d))

def years_before(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year - years)
    except ValueError:  # 29 February
        return d.replace(year=d.year - years, day=28)

def age_on(dob: date, on: date) -> int:
    return on.year - dob.year - ((on.month, on.day) < (dob.month, dob.day))

# ---------- raw tables ----------

@dataclass(frozen=True)
class RawTable:
    page: int  # 1-based
    index: int  # position on the page, top to bottom
    top: float  # bbox y0 / page height
    bottom: float  # bbox y1 / page height
    last_on_page: bool
    rows: Tuple[Tuple[Optional[str], ...], ...]

    @property
    def ncols(self) -> int:
        return len(self.rows[0]) if self.rows else 0

@lru_cache(maxsize=16)
def read_tables(pdf_bytes: bytes) -> Tuple[RawTable, ...]:
    out = []
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        wanted = {i for i, page in enumerate(doc) if _SIGNAL.search(page.get_text())}
        wanted |= {i + 1 for i in wanted if i + 1 < doc.page_count}
        for i in sorted(wanted):
            page = doc[i]
            tables = sorted(page.find_tables(strategy=STRATEGY).tables, key=lambda t: t.bbox[1])
            h = page.rect.height
            for k, t in enumerate(tables):
                rows = tuple(tuple(r) for r in t.extract())
                if rows:
                    out.append(RawTable(i + 1, k, t.bbox[1] / h, t.bbox[3] / h, k == len(tables) - 1, rows))
    return tuple(out)

# ---------- logical tables (page breaks undone) ----------

@dataclass
class Row:
    cells: List[str]
    page: int
    pages: List[int]
    raw: List[Optional[str]]
    inherited: List[int] = field(default_factory=list)  # column indexes taken from a merged cell above
    section: bool = False
    continuation_start: bool = False  # first row of a table piece that continues the previous page's table

@dataclass
class LogicalTable:
    kind: Optional[str]  # POSTS, RELAXATION, SCHEDULE or None
    cols: Dict[str, int]
    header: List[str]
    first_page: int
    rows: List[Row] = field(default_factory=list)
    ncols: int = 0
    last: Optional[RawTable] = None

def _header(row: Sequence[Optional[str]]) -> Optional[Tuple[str, Dict[str, int]]]:
    cells = [cell_text(c).lower() for c in row]
    cols: Dict[str, int] = {}
    for i, c in enumerate(cells):
        if not c:
            continue
        if re.fullmatch(r"(?:s|sr|sl)\.?\s*no\.?", c):
            cols.setdefault("serial", i)
        elif re.fullmatch(r"(?:name\s+of\s+(?:the\s+)?)?posts?", c):
            cols.setdefault("post", i)
        elif re.fullmatch(r"post\s*code|post\s+category(?:\s+no\.?)?", c):
            cols.setdefault("post_code", i)
        elif "qualification" in c:
            cols.setdefault("education", i)
        elif re.match(r"age[\s-]*(?:limit|relaxation)", c):
            cols.setdefault("relaxation" if "relaxation" in c else "age", i)
        elif "classification" in c:
            cols.setdefault("classification", i)
        elif re.search(r"\bpay\s*(?:level|scale)", c):
            cols.setdefault("pay", i)
        elif re.search(r"ministry|department|organi[sz]ation|cadre", c):
            cols.setdefault("department", i)
        elif re.fullmatch(r"code(?:\s+no\.?)?", c):
            cols.setdefault("code", i)
        elif c == "category":
            cols.setdefault("category", i)
        elif c == "region":
            cols.setdefault("region", i)
        elif "exam level" in c:
            cols.setdefault("exam_level", i)
        elif c == "vacancies":
            cols.setdefault("vacancies", i)
        elif "name of examination" in c:
            cols.setdefault("exam_name", i)
        elif "schedule" in c:
            cols.setdefault("schedule", i)
        elif re.fullmatch(r"tier(?:\s*/\s*mode)?", c):
            cols.setdefault("tier", i)
    if "post" in cols and "age" in cols:
        return "POSTS", cols
    if "category" in cols and "relaxation" in cols:
        return "RELAXATION", cols
    if "exam_name" in cols and "schedule" in cols:
        return "SCHEDULE", cols
    return None

def _key_cols(t: LogicalTable) -> List[int]:
    if t.kind == "POSTS":
        return [t.cols[k] for k in ("serial", "post") if k in t.cols]
    if t.kind == "RELAXATION":
        return [t.cols.get("code", 0)]
    return [0]

def _is_section(raw: Sequence[Optional[str]]) -> bool:
    return bool(cell_text(raw[0])) and len(raw) > 1 and all(c is None for c in raw[1:])

def _continues(prev: Optional[RawTable], t: RawTable) -> bool:
    return (prev is not None and t.index == 0 and t.page == prev.page + 1 and prev.last_on_page
            and prev.bottom > 0.7 and t.top < 0.3 and t.ncols == prev.ncols)

def logical_tables(raw_tables: Sequence[RawTable]) -> List[LogicalTable]:
    out: List[LogicalTable] = []
    current: Optional[LogicalTable] = None
    for t in raw_tables:
        rows = list(t.rows)
        head = _header(rows[0])
        if current is not None and _continues(current.last, t) and (head is None or [cell_text(c) for c in rows[0]] == current.header):
            if head is not None:
                rows = rows[1:]  # header repeated on the new page
            piece_start = True
        else:
            caption = None
            if head is None and len(rows) > 1 and _header(rows[1]):
                # A row above the header: "2.1 Pay Level-8 (Rs 47600 to 151100):" opens the first section
                caption, rows = rows[0], rows[1:]
                head = _header(rows[0])
            kind, cols = head if head else (None, {})
            current = LogicalTable(kind, cols, [cell_text(c) for c in rows[0]] if head else [], t.page, ncols=t.ncols)
            out.append(current)
            if head:
                rows = rows[1:]
            if caption is not None and _is_section(caption):
                current.rows.append(Row([cell_text(c) for c in caption], t.page, [t.page], list(caption), section=True))
            if head:
                # A second header row naming sub-columns (Vacancies -> SC ST OBC UR EWS Total ...)
                if rows and rows[0][0] is None and any(cell_text(c).lower() == "total" for c in rows[0]):
                    total = next(i for i, c in enumerate(rows[0]) if cell_text(c).lower() == "total")
                    current.cols["vacancy_total"] = total
                    rows = rows[1:]
            piece_start = False
        current.last = t
        keys = _key_cols(current)
        leading = piece_start
        for raw in rows:
            cells = [cell_text(c) for c in raw]
            if leading and current.rows and all(not cells[k] for k in keys if k < len(cells)) and any(cells):
                # Text of the previous row cut by the page break
                prev = current.rows[-1]
                for i, c in enumerate(cells):
                    if c:
                        prev.cells[i] = f"{prev.cells[i]} {c}".strip()
                if t.page not in prev.pages:
                    prev.pages.append(t.page)
                continue
            row = Row(cells, t.page, [t.page], list(raw), section=_is_section(raw), continuation_start=leading)
            leading = False
            current.rows.append(row)
    return out

# ---------- posts ----------

@dataclass
class TablePost:
    post_key: str
    post_name: str
    serial: Optional[str]
    post_code: Optional[str]
    department: Optional[str]
    classification: Optional[str]
    pay_level: Optional[str]
    pay_scale: Optional[str]
    age_text: Optional[str]
    age_min: Optional[int]
    age_max: Optional[int]
    education_text: Optional[str]
    vacancy_count: Optional[int]
    provenance: Provenance
    inherited: List[str] = field(default_factory=list)
    education_provenance: Optional[Provenance] = None
    extra: Dict[str, str] = field(default_factory=dict)  # stated cells with no column of their own (region, exam level)

_PAY_SECTION = re.compile(r"Pay\s+Level\s*-?\s*(\d+)\s*(\([^)]*\))?", re.I)
_AGE_RANGE = re.compile(r"(\d{2})\s*-\s*(\d{2})(?:\s*years)?", re.I)
_AGE_UPTO = re.compile(r"up\s*to\s+(\d{2})\s*years", re.I)

def parse_age(text: str) -> Tuple[Optional[int], Optional[int]]:
    m = _AGE_RANGE.search(text or "")
    if m:
        return int(m.group(1)), int(m.group(2))
    m = _AGE_UPTO.search(text or "")
    return (None, int(m.group(1))) if m else (None, None)

def post_key(name: str, department: Optional[str], pay_level: Optional[str], code: Optional[str] = None) -> str:
    """The same post name recurs under different departments and pay levels (CGL: ASO in five ministries)."""
    parts = [code] if code else [name, department or "", pay_level or ""]
    return " | ".join(norm(p) for p in parts)[:255]

def _pay_from_cell(text: str) -> Tuple[Optional[str], Optional[str]]:
    m = re.search(r"Level[\s-]*(\d+)", text or "", re.I)
    return (f"Level-{m.group(1)}", text) if m else (None, text or None)

def posts_from_table(t: LogicalTable, section_title: str, warnings: List[str]) -> List[TablePost]:
    c = t.cols
    posts: List[TablePost] = []
    pay_level = pay_scale = None
    section = section_title
    prev: Optional[Row] = None
    names = {k: t.header[i] for k, i in c.items() if i < len(t.header)}
    for row in t.rows:
        if row.section:
            m = _PAY_SECTION.search(row.cells[0])
            if m:
                pay_level, pay_scale = f"Level-{m.group(1)}", row.cells[0].rstrip(":").strip()
                pay_scale = re.sub(r"^\d+(?:\.\d+)*\s*", "", pay_scale)
            section = f"{section_title}: {row.cells[0].rstrip(':').strip()}"
            prev = None
            continue
        if [x.lower() for x in row.cells] == [x.lower() for x in t.header]:
            continue
        # Merged cells: None = covered by the cell above; "" at the top of a continuation = merged cell cut by the page break
        for i, raw in enumerate(row.raw):
            if prev is not None and not row.cells[i] and (raw is None or row.continuation_start):
                row.cells[i] = prev.cells[i]
                row.inherited.append(i)
        name = row.cells[c["post"]]
        if not name:
            warnings.append(f"p.{row.page}: table row without a post name skipped: {' | '.join(x for x in row.cells if x)[:120]}")
            continue
        get = lambda k: row.cells[c[k]] or None if k in c else None  # noqa: E731
        age_text = get("age")
        age_min, age_max = parse_age(age_text or "")
        if age_text and age_max is None:
            warnings.append(f"p.{row.page}: age limit not understood for {name}: {age_text!r}")
        level, scale = (_pay_from_cell(get("pay")) if "pay" in c else (pay_level, pay_scale))
        vacancies = None
        if "vacancy_total" in c and re.fullmatch(r"\d+", row.cells[c["vacancy_total"]] or ""):
            vacancies = int(row.cells[c["vacancy_total"]])
        code = get("post_code")
        inherited = [names.get(k, k) for k, i in c.items() if i in row.inherited]
        excerpt = " | ".join(x for x in row.cells if x)
        if inherited:
            excerpt += f" [merged cell(s) continued from the row above: {', '.join(inherited)}]"
        posts.append(TablePost(
            "", name, get("serial"), code, get("department"), get("classification"),
            level, scale, age_text, age_min, age_max, get("education"), vacancies,
            Provenance(row.page, section, excerpt[:1200]), inherited,
            Provenance(row.page, f"{section_title}: Essential Educational Qualifications", get("education")[:1200])
            if get("education") else None,
            {k: get(k) for k in ("region", "exam_level") if get(k)},
        ))
        prev = row
    return posts

def assign_keys(posts: List[TablePost]) -> None:
    seen: Dict[str, int] = {}
    for p in posts:  # identical rows (same name, department and level) stay distinct by their serial number
        p.post_key = post_key(p.post_name, p.department, p.pay_level, p.post_code)
        seen[p.post_key] = seen.get(p.post_key, 0) + 1
        if seen[p.post_key] > 1:
            p.post_key = f"{p.post_key} | {norm(p.serial) or seen[p.post_key]}"[:255]

# ---------- age classes and relaxations ----------

@dataclass
class AgeClass:
    label: str  # normalised "18 27" / "up to 30"
    earliest: date  # born not earlier than
    latest: date  # born not later than
    provenance: Provenance

_AGE_CLASS = re.compile(r"age\s+limit\s+is\s+((?:up\s*to\s+)?\d{2}(?:\s*-\s*\d{2})?)\s+years.*?born\s+not\s+(?:earlier\s+than|before)\s+"
                        + _D + r"\s+and\s+not\s+later\s+than\s+" + _D, re.I | re.S)

def age_classes(tables: Sequence[LogicalTable]) -> List[AgeClass]:
    out = []
    for t in tables:
        for row in t.rows:
            text = " ".join(x for x in row.cells if x)
            m = _AGE_CLASS.search(text)
            if m:
                out.append(AgeClass(norm(m.group(1)), parse_date(m.group(2)), parse_date(m.group(3)),
                                    Provenance(row.page, "Age Limit", text)))
    return out

@dataclass
class RelaxationRow:
    code: str
    category: str
    relaxation: str
    scope: Optional[str]
    provenance: Provenance

def relaxation_rows(tables: Sequence[LogicalTable]) -> List[RelaxationRow]:
    rows = []
    for t in tables:
        if t.kind != "RELAXATION":
            continue
        scope = None
        for row in t.rows:
            if row.section:
                scope = row.cells[0]
                continue
            code, cat, relax = (row.cells[t.cols[k]] if k in t.cols else "" for k in ("code", "category", "relaxation"))
            if cat and relax:
                excerpt = f"{code} {cat} {relax}".strip() + (f" [{scope}]" if scope else "")
                rows.append(RelaxationRow(code, cat, relax, scope, Provenance(row.page, "Age Limit: relaxation table", excerpt)))
    return rows

_SC_ST = re.compile(r"\(\s*SC\s*/\s*ST\s*\)\.?$")

@dataclass
class Relaxations:
    years: Dict[str, int]  # UR/EWS/OBC/SC/ST -> years added to the upper age limit
    cited: Dict[str, Provenance]
    requirements: List[ParsedRequirement]  # exam-level PwBD / rule rows

def map_relaxations(rows: Sequence[RelaxationRow], warnings: List[str]) -> Relaxations:
    """Each table row exactly as stated. Same basis names as parsing/ssc_exam_notice.py so exam_engine judges them."""
    years: Dict[str, int] = {}
    cited: Dict[str, Provenance] = {}
    pwbd: Dict[str, Tuple[int, Provenance]] = {}
    reqs: List[ParsedRequirement] = []
    for r in rows:
        cat = re.sub(r"\s+", " ", r.category).strip()
        n = re.match(r"(\d{1,2})\s+years\b", r.relaxation, re.I)
        cap = re.match(r"Up\s+to\s+(\d{2})\s+years", r.relaxation, re.I)
        sc_st = bool(_SC_ST.search(cat))
        others = ["UR", "EWS", "OBC"]
        group_c = bool(r.scope and re.search(r"Group\s*.?\s*C\s*.?\s*posts", r.scope, re.I))
        scoped = {"post_group": "C"} if group_c else {}
        if r.scope and not group_c:
            warnings.append(f"relaxation scope not understood, rows under it not mapped: {r.scope[:100]}")
            continue
        if re.fullmatch(r"SC\s*/\s*ST", cat) and n:
            for k in ("SC", "ST"):
                years[k], cited[k] = int(n.group(1)), r.provenance
        elif re.fullmatch(r"OBC", cat) and n:
            years["OBC"], cited["OBC"] = int(n.group(1)), r.provenance
        elif cat.startswith("PwBD") and n:
            key = "SC/ST" if sc_st else "OBC" if "OBC" in cat else "UR/EWS"
            pwbd[key] = (int(n.group(1)), r.provenance)
        elif re.match(r"Ex-?\s*Servicemen", cat, re.I) and n and re.search(r"after\s+deduction\s+of\s+the\s+military\s+service", r.relaxation, re.I):
            reqs.append(ParsedRequirement("AGE_RELAXATION_RULE", {"basis": "EX_SERVICEMEN_SERVICE_DEDUCTED", "years": int(n.group(1)), **scoped},
                                          False, r.provenance))
        elif re.match(r"Defence\s+Personnel\s+disabled", cat, re.I) and n:
            reqs.append(ParsedRequirement("AGE_RELAXATION_RULE", {"basis": "DISABLED_DEFENCE_PERSONNEL", "years": int(n.group(1)),
                                                                  "categories": ["SC", "ST"] if sc_st else others, **scoped}, False, r.provenance))
        elif re.match(r"Central\s+Govt\.?\s+Civilian\s+Employees|Central\s+Government\s+Civilian\s+Employees", cat, re.I) and cap:
            service = re.search(r"not\s+less\s+than\s+(\d+)\s+years", cat, re.I)
            reqs.append(ParsedRequirement("AGE_RELAXATION_RULE", {
                "basis": "CENTRAL_GOVT_CIVILIAN_EMPLOYEE", "max_age": int(cap.group(1)),
                "min_service_years": int(service.group(1)) if service else None,
                "categories": ["SC", "ST"] if sc_st else others, **scoped}, False, r.provenance))
        elif re.match(r"Widows", cat, re.I) and cap:
            reqs.append(ParsedRequirement("AGE_RELAXATION_RULE", {"basis": "WIDOW_DIVORCED_SEPARATED", "max_age": int(cap.group(1)),
                                                                  "categories": ["SC", "ST"] if sc_st else others, **scoped}, False, r.provenance))
        else:
            warnings.append(f"relaxation row not understood: {r.provenance.excerpt[:120]}")
    if "UR/EWS" in pwbd:
        base, prov = pwbd["UR/EWS"]
        stated = {k: v for k, (v, _) in pwbd.items()}
        excerpt = " | ".join(p.excerpt for _, p in pwbd.values())
        # exam_engine adds the PwBD years on top of the category's own limit, so the OBC/SC rows must be that sum
        for key, cat in (("OBC", "OBC"), ("SC/ST", "SC")):
            if key in pwbd and pwbd[key][0] != base + years.get(cat, 0):
                warnings.append(f"PwBD ({key}) relaxation {pwbd[key][0]} is not category + PwBD years; not modelled")
        reqs.append(ParsedRequirement("AGE_RELAXATION", {"category": "PwBD", "years": base, "stated": stated}, False,
                                      Provenance(prov.page, prov.section, excerpt)))
    # The category rows themselves, as stated (the per-post AGE_MAX rows already include them; posts added later by
    # an addendum are given their limits from these rows)
    for category in ("SC", "ST", "OBC"):
        if category in years:
            reqs.append(ParsedRequirement("AGE_RELAXATION", {"category": category, "years": years[category]}, False, cited[category]))
    return Relaxations(years, cited, reqs)

def post_age_requirements(post: TablePost, relax: Relaxations, classes: Sequence[AgeClass], crucial: Optional[date],
                          warnings: List[str]) -> List[ParsedRequirement]:
    """AGE_MAX per category for one post: the post's own limit plus the category row of the relaxation table.

    When the notice's age-class table states the birth-date window for this limit ("born not earlier than
    02-08-1996 and not later than 01-08-2008"), the window is stored too: it is the notice's exact rule
    (a candidate must not have reached the upper limit on the crucial date). For a relaxed category the
    earliest date moves back by the relaxation years.
    """
    if post.age_max is None:
        return []
    label = norm(f"{post.age_min}-{post.age_max}") if post.age_min is not None else norm(f"up to {post.age_max}")
    cls = next((c for c in classes if c.label == label), None)
    min_age = post.age_min
    if cls and crucial:
        if age_on(cls.earliest, crucial) != post.age_max - 1 or (post.age_min is not None and age_on(cls.latest, crucial) != post.age_min):
            warnings.append(f"age class {cls.provenance.excerpt[:80]!r} does not match {post.age_text!r} on {crucial}; window not used")
            cls = None
        elif min_age is None:
            min_age = age_on(cls.latest, crucial)  # "Up to 30 years" + "not later than 01.08.2008" on 01.08.2026
    out = []
    for category in CATEGORIES:
        extra = relax.years.get(category, 0)
        value = {"category": category, "min_age": min_age, "max_age": post.age_max + extra}
        if extra:
            value.update(base_max_age=post.age_max, relaxation_years=extra)
        quotes = [post.provenance.excerpt]
        if cls:
            value.update(born_not_earlier_than=years_before(cls.earliest, extra).isoformat(), born_not_later_than=cls.latest.isoformat())
            quotes.append(f"p.{cls.provenance.page}: {cls.provenance.excerpt}")
        if extra:
            quotes.append(f"p.{relax.cited[category].page}: {relax.cited[category].excerpt}")
        out.append(ParsedRequirement("AGE_MAX", value, True, Provenance(
            post.provenance.page, post.provenance.section + (" + age classes" if cls else "") + (" + relaxation table" if extra else ""),
            " | ".join(quotes))))
    return out

# ---------- post-wise education paragraphs (CGL para 8) ----------

_EQ_HEAD = re.compile(r"(?m)^\s*(\d+)\.\s+Essential\s+Educational\s+Qualifications?\s*\(\s*As\s+on\s+" + _D + r"\s*\)\s*:")

def education_paragraphs(pages: Sequence[PageText]) -> List[Tuple[str, str, Provenance]]:
    """(post heading, essential qualification text, provenance) per post-wise paragraph:
    "8.1.1 Assistant Audit Officer (Central Cadre): 8.1.1.1 Essential Qualifications: Bachelor's Degree ..."."""
    doc = _Doc(list(pages))
    text = doc.text
    m = _EQ_HEAD.search(text)
    if not m:
        return []
    n = m.group(1)
    end = re.compile(r"(?m)^\s*" + str(int(n) + 1) + r"\.\s+[A-Z]").search(text, m.end())
    body_end = end.start() if end else min(len(text), m.end() + 20000)
    heads = list(re.finditer(r"(?m)^\s*(" + n + r"\.\d+(?:\.\d+)?)\s*\n?\s*([A-Z][^\n:]{2,150}?)\s*:\s*$", text[m.end():body_end]))
    out = []
    for i, h in enumerate(heads):
        start = m.end() + h.end()
        stop = m.end() + heads[i + 1].start() if i + 1 < len(heads) else body_end
        second = h.group(1).split(".")[1]
        other = re.compile(r"(?m)^\s*" + n + r"\.(?!" + second + r"\b)\d+\s").search(text, start, stop)
        stop = other.start() if other else stop
        body = text[start:stop]
        eq = re.search(r"Essential\s+Qualifications?\s*:\s*(.*?)(?=\n\s*" + re.escape(h.group(1)) + r"\.\d+\s*\n|Desirable\s+Qualifications?|$)", body, re.S | re.I)
        if eq:
            quote, at = eq.group(1), start + eq.start(1)
        else:
            sub = re.match(r"\s*" + re.escape(h.group(1)) + r"\.\d+\s*\n?", body)
            quote, at = body[sub.end():] if sub else body, start + (sub.end() if sub else 0)
        quote = re.sub(r"\s+", " ", quote).strip()
        if quote:
            out.append((re.sub(r"\s+", " ", h.group(2)).strip(), quote,
                        Provenance(doc.page_at(at), f"Essential Educational Qualifications {h.group(1)}", quote[:1200])))
    return out

def assign_education(posts: List[TablePost], paragraphs, warnings: List[str]) -> None:
    """Paragraph headings name posts exactly; "All other Posts" covers the rest."""
    rest = None
    for heading, text, prov in paragraphs:
        if re.fullmatch(r"all\s+other\s+posts?", heading.strip(), re.I):
            rest = (text, prov)
            continue
        hits = [p for p in posts if norm(p.post_name) == norm(heading)]
        if not hits:
            warnings.append(f"education paragraph for {heading!r} matches no post in the table")
        for p in hits:
            p.education_text, p.education_provenance = text, prov
    if rest:
        for p in posts:
            if p.education_text is None:
                p.education_text, p.education_provenance = rest

# ---------- the whole notice ----------

_SCOPE_PAY = re.compile(r"posts\s+are\s+of\s+(Group\s+\W?[ABC]\W?)\s+in\s+(Level-?\s*(\d+)\s*\([^)]*\))", re.I)
_AGE_AS_ON = re.compile(r"Age\s+limit\s*\(\s*As\s+on\s+" + _D + r"\s*\)", re.I)

@dataclass
class PostTables:
    posts: List[TablePost]
    relaxation_rows: List[RelaxationRow]
    relaxations: Optional[Relaxations]
    age_classes: List[AgeClass]
    fields: Dict[str, ParsedField]
    warnings: List[str]

def parse_post_tables(pdf_bytes: bytes, pages: Sequence[PageText]) -> PostTables:
    warnings: List[str] = []
    tables = logical_tables(read_tables(pdf_bytes))
    posts: List[TablePost] = []
    for t in tables:
        if t.kind == "POSTS":
            posts += posts_from_table(t, "Details of the Posts", warnings)
    fields: Dict[str, ParsedField] = {}
    joined = "\n".join(p.text for p in pages)
    if posts:
        m = _AGE_AS_ON.search(joined)
        if m:
            page = next(p.page_number for p in pages if _AGE_AS_ON.search(p.text))
            fields["age_crucial_date"] = ParsedField(parse_date(m.group(1)), Provenance(page, "Age Limit", re.sub(r"\s+", " ", m.group(0))))
        if all(p.pay_level is None for p in posts):
            # JE: "The posts are of Group 'B' in Level-6 (Rs. 35400-112400/-) of the Pay Matrix" covers every post
            for p in pages:
                m = _SCOPE_PAY.search(re.sub(r"\s+", " ", p.text))
                if m:
                    fields["pay_level_all_posts"] = ParsedField(f"Level-{m.group(3)}",
                                                                Provenance(p.page_number, "Pay Level", m.group(0)))
                    for post in posts:
                        post.pay_level, post.pay_scale = f"Level-{m.group(3)}", m.group(2)
                        post.classification = post.classification or m.group(1)
                    break
        if any(p.education_text is None for p in posts):
            assign_education(posts, education_paragraphs(pages), warnings)
        for p in posts:
            if p.education_text is None:
                warnings.append(f"no essential qualification found for {p.post_name} ({p.department})")
    assign_keys(posts)
    rows = relaxation_rows(tables)
    relax = map_relaxations(rows, warnings) if rows else None
    return PostTables(posts, rows, relax, age_classes(tables), fields, warnings)

# ---------- combined with the text parser ----------

def with_post_tables(parsed: ParsedExam, pdf_bytes: bytes, pages: Sequence[PageText]) -> ParsedExam:
    """The text parse of parsing/ssc_exam_notice.py, with what the notice's post-wise tables state.

    Notices without a post table (CHSL 2026: one age limit for all posts) come back unchanged. Otherwise posts
    come from the table, each with its own AGE_MAX rows (per category) and EDUCATION row; the exam-level
    relaxation rows come from the relaxation table; exam-level EDUCATION paragraphs give way to the post-wise ones.
    """
    tables = parse_post_tables(pdf_bytes, pages)
    if not tables.posts:
        return parsed
    fields = dict(parsed.fields)
    for name, f in tables.fields.items():
        if name == "age_crucial_date":
            if name not in fields:
                d = f.value
                fields[name] = ParsedField(datetime(d.year, d.month, d.day, tzinfo=IST), f.provenance)
        else:
            fields.setdefault(name, f)
    crucial = fields["age_crucial_date"].value.astimezone(IST).date() if "age_crucial_date" in fields else None
    relax = tables.relaxations
    all_edu = all(p.education_text for p in tables.posts)
    resolved = ["no post-wise pay scale list found"]
    if any(p.age_max is not None for p in tables.posts):
        resolved += ["age limit not found", "post-wise age limits"]
    if relax:
        resolved += ["relaxation row not understood", "PwBD (", "age relaxation table not found"]
    if crucial:
        resolved.append("crucial date for age not found")
    if all_edu:
        resolved += ["essential educational qualifications section not found", "no education requirement paragraph recognised"]
    warnings = [w for w in parsed.warnings if not w.startswith(tuple(resolved))] + tables.warnings
    reqs = list(parsed.requirements)
    if relax:
        reqs = [r for r in reqs if r.req_type not in ("AGE_MAX", "AGE_RELAXATION", "AGE_RELAXATION_RULE")] + relax.requirements
    else:
        warnings.append("relaxation table not found: post-wise age limits stored on the posts, no per-category limits")
    if all_edu:
        reqs = [r for r in reqs if r.req_type != "EDUCATION"]
    posts = []
    for tp in tables.posts:
        preqs = post_age_requirements(tp, relax, tables.age_classes, crucial, warnings) if relax else []
        if tp.education_text:
            preqs.append(ParsedRequirement("EDUCATION", tp.education_text, True, tp.education_provenance))
        posts.append(ParsedExamPost(tp.post_key, tp.post_name, tp.pay_level, tp.pay_scale, tp.provenance, tp.post_code,
                                    tp.department, tp.classification, tp.age_min, tp.age_max, tp.education_text,
                                    tp.vacancy_count, preqs))
    return ParsedExam(f"{parsed.parser_version}+{TABLES_VERSION}", fields, posts, reqs, list(dict.fromkeys(warnings)))

def parse_exam_pdf(pdf_bytes: bytes, pages: Sequence[PageText]) -> ParsedExam:
    return with_post_tables(parse_exam_notice(list(pages)), pdf_bytes, pages)
