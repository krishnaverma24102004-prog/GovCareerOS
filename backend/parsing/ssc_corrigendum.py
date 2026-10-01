"""
Deterministic reader for SSC corrigenda, addenda and important notices that change an examination.

Only sentences and tables of these real shapes are turned into changes (fixtures in tests/fixtures/ssc):
  * Re-opened application window (CGL 2026, 23.06.2026): "re-open the window for submission of online
    applications ... from 23.06.2026 (23:00 hours) to 25.06.2026 (23:00 hours)", "The last date for making the
    online fee payment will be 26.06.2026 (23:00 hours)", "'Window for Application Form Correction' will now be
    opened from 01.07.2026 to 03.07.2026 (23:00 hours)".
  * Schedule notices: a table Name of Examination | Tier/Mode | Schedule of Examination (CGL 2026, 12.09.2026).
  * "... Essential Qualification for the post of SA in IMD at Para 2(13) of the Notice ... shall be read as:"
    followed by the replacement table row (JE 2026 addendum, 10.09.2026), including its continuation on the next page.
  * "... included 04 additional posts ... The details of these Posts are annexed": a post table with a header
    (Phase-XIV/2026/Selection Posts addendum, 20.04.2026).
Anything else in the notice is left alone; a notice from which nothing is read says so in `warnings`.
Which stored exam a notice concerns is decided by services/exam_corrigenda.py from the notice's own words.
"""
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, List, Optional

from govcareeros.backend.documents.pdf_text import PageText
from govcareeros.backend.parsing.ssc_exam_notice import _Doc
from govcareeros.backend.parsing.ssc_tables import (
    TablePost, assign_keys, cell_text, logical_tables, norm, parse_date, posts_from_table, read_tables,
)
from govcareeros.backend.parsing.upsc_advertisement import IST, Provenance

PARSER_VERSION = "ssc_corrigendum/1"

_D = r"(\d{2}[.-]\d{2}[.-]\d{4})"
_T = r"\s*\(\s*(\d{2}):(\d{2})\s*hours?\s*\)"

def _dt(d: str, hh: str = "00", mm: str = "00") -> datetime:
    x = parse_date(d)
    return datetime(x.year, x.month, x.day, int(hh), int(mm), tzinfo=IST)

@dataclass
class StatedChange:
    field: str  # application_end, fee_payment_end, correction_start, correction_end, reopened_window, closing_date, exam_schedule
    value: Any
    provenance: Provenance

@dataclass
class StatedPostRow:
    kind: str  # READ_AS: replaces the row of an existing post; ADDED: a post the notice adds
    cells: List[str]  # the row as printed
    reads: List[str]  # what the sentence says is restated: EDUCATION, ...
    provenance: Provenance
    post: Optional[TablePost] = None  # set when the notice's table has its own header

@dataclass
class ParsedCorrigendum:
    parser_version: str
    text: str  # whole text, for matching the notice to a stored exam by its file number or full name
    file_numbers: List[str]
    notice_date: Optional[date]
    changes: List[StatedChange]
    post_rows: List[StatedPostRow]
    warnings: List[str] = field(default_factory=list)

    @property
    def change_types(self) -> List[str]:
        kinds = []
        fields = {c.field for c in self.changes}
        if "reopened_window" in fields:
            kinds.append("REOPENED_WINDOW")
        if fields & {"application_end", "fee_payment_end", "correction_start", "correction_end"}:
            kinds.append("DATE_CHANGE")
        if "exam_schedule" in fields:
            kinds.append("SCHEDULE")
        if any(r.kind == "READ_AS" for r in self.post_rows):
            kinds.append("POST_CHANGE")
        if any(r.kind == "ADDED" for r in self.post_rows):
            kinds.append("POSTS_ADDED")
        return kinds

_FILE_NO = re.compile(r"F\.\s*No\.?\s*([A-Z][A-Z0-9/.\-]+?)(?=[.:]?\s)")
_DATED = re.compile(r"\bDated\s*:\s*" + _D)
_SIGNED = re.compile(r"Under\s+Secretary[^\n]*\n(?:\s*\n)*\s*(?:Dated\s*:?\s*)?" + _D, re.I)
_REOPEN = re.compile(r"re-?open\s+the\s+window\s+for\s+submission\s+of\s+online\s+applications?.{0,200}?from\s+" + _D + _T
                     + r"\s+to\s+" + _D + _T, re.I | re.S)
_FEE = re.compile(r"last\s+date\s+for\s+making\s+the\s+online\s+fee\s+payment\s+will\s+be\s+" + _D + _T, re.I | re.S)
_CORRECTION = re.compile(r"Window\s+for\s+(?:online\s+)?Application\s+Form\s+Correction.{0,30}?will\s+now\s+be\s+opened\s+from\s+"
                         + _D + r"\s+to\s+" + _D + _T, re.I | re.S)
_NEW_CLOSING = re.compile(r"new\s+closing\s+date\s+i\.?\s*e\.?,?\s*" + _D, re.I)
_READ_AS = re.compile(r"shall\s+be\s+read\s+as\s*:", re.I)
_PARA = re.compile(r"(?:^|\n)\s*\d+\.\s")
_EQ = re.compile(r"Essential\s+(?:Educational\s+)?Qualifications?", re.I)
_ADDED = re.compile(r"(?:included|added)\s+(\d+)\s+additional\s+posts?", re.I)

def parse_corrigendum(pdf_bytes: bytes, pages: List[PageText]) -> ParsedCorrigendum:
    doc = _Doc(pages)
    text = doc.text
    warnings: List[str] = []
    changes: List[StatedChange] = []

    def cite(m, section="Notice") -> Provenance:
        return doc.prov(m.start(), m.end(), section, 800)

    m = _REOPEN.search(text)
    if m:
        start, end = _dt(m.group(1), m.group(2), m.group(3)), _dt(m.group(4), m.group(5), m.group(6))
        changes.append(StatedChange("reopened_window", {"from": start.isoformat(), "to": end.isoformat()}, cite(m)))
        changes.append(StatedChange("application_end", end, cite(m)))
    m = _NEW_CLOSING.search(text)
    if m:
        changes.append(StatedChange("closing_date", parse_date(m.group(1)).isoformat(), cite(m)))
    m = _FEE.search(text)
    if m:
        changes.append(StatedChange("fee_payment_end", _dt(m.group(1), m.group(2), m.group(3)), cite(m)))
    m = _CORRECTION.search(text)
    if m:
        changes.append(StatedChange("correction_start", _dt(m.group(1)), cite(m)))
        changes.append(StatedChange("correction_end", _dt(m.group(2), m.group(3), m.group(4)), cite(m)))

    tables = logical_tables(read_tables(pdf_bytes))
    for t in tables:
        if t.kind != "SCHEDULE":
            continue
        c = t.cols
        for row in t.rows:
            exam, schedule = row.cells[c["exam_name"]], row.cells[c["schedule"]]
            stage = row.cells[c["tier"]] if "tier" in c else ""
            if exam and schedule:
                changes.append(StatedChange("exam_schedule", {"exam_name": exam, "stage": stage, "schedule": schedule},
                                            Provenance(row.page, "Schedule of Examination", " | ".join(x for x in row.cells if x))))

    post_rows: List[StatedPostRow] = []
    read_as = _READ_AS.search(text)
    sentence, reads = "", ["ROW"]
    if read_as:
        window = text[max(0, read_as.start() - 600): read_as.end()]
        para = list(_PARA.finditer(window))
        sentence = re.sub(r"\s+", " ", window[para[-1].end() if para else 0:]).strip()
        reads = ["EDUCATION"] if _EQ.search(sentence) else ["ROW"]
    added = _ADDED.search(text)
    for t in tables:
        if t.kind == "POSTS":
            posts = posts_from_table(t, "Details of the Posts", warnings)
            assign_keys(posts)
            kind = "ADDED" if added else "READ_AS" if read_as else None
            if kind is None:
                warnings.append(f"p.{t.first_page}: post table without a sentence saying what it changes; not applied")
                continue
            for p in posts:
                post_rows.append(StatedPostRow(kind, [x for x in p.provenance.excerpt.split(" | ")], ["ROW"], p.provenance, p))
            if added and int(added.group(1)) != len(posts):
                warnings.append(f"notice says {added.group(1)} additional posts, the table has {len(posts)}")
        elif t.kind is None and read_as and t.first_page >= doc.page_at(read_as.end()):
            for row in t.rows:
                cells = list(row.cells)
                if sum(1 for x in cells if x) >= 3:
                    post_rows.append(StatedPostRow("READ_AS", cells, reads, Provenance(
                        row.page, f"Read as (p.{doc.page_at(read_as.start())}: {sentence[-300:]})", " | ".join(x for x in cells if x)[:1500])))
    if read_as and not any(r.kind == "READ_AS" for r in post_rows):
        warnings.append("a 'shall be read as' sentence without a readable replacement row")

    file_numbers = [cell_text(f).rstrip(".") for f in _FILE_NO.findall(text)]
    dated = _SIGNED.search(text) or _DATED.search(text)  # the signature date; "Notice dated ..." is the old notice
    if not changes and not post_rows:
        warnings.append("no change this parser knows was found in the notice")
    return ParsedCorrigendum(PARSER_VERSION, text, file_numbers, parse_date(dated.group(1)) if dated else None, changes, post_rows, warnings)

def names_exam(parsed: ParsedCorrigendum, exam_name: str, external_ref: Optional[str]) -> bool:
    """The notice concerns the exam when it quotes the exam's file number or its full name (year included)."""
    flat = re.sub(r"\s+", "", parsed.text)
    if external_ref and external_ref.replace(" ", "") in flat:
        return True
    return bool(exam_name) and f" {norm(exam_name)} " in f" {norm(parsed.text)} "
