"""
Fixed rules that read what one official sentence asks of an applicant: certificate conditions, photograph/
signature upload specifications, fee amounts and exemption groups, and which document a list item names.

Shared by the deterministic notice parsers (parsing/ssc_application.py, parsing/upsc_application.py) and the
LLM-draft validator (extraction/validator.py), so a requirement means the same thing whichever path read it.
Every rule reads only the text it is given; anything the text does not state stays None.
"""
import re
from dataclasses import asdict
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from govcareeros.backend.eligibility.certificate_types import CertificateKind, CertificateRequirement

# applies_when values; the checklist (services/application_prep.py) personalises on these
ALWAYS, IF_CLAIMED = "ALWAYS", "IF_CLAIMED"
SC_ST_OBC, OBC, PWBD = "CATEGORY_SC_ST_OBC", "CATEGORY_OBC", "PWBD"
AGE_RELAXATION, GOVT_SERVICE, NAME_CHANGE = "AGE_RELAXATION", "GOVT_SERVICE", "NAME_CHANGE"
SC, ST, EWS, RESERVED = "CATEGORY_SC", "CATEGORY_ST", "CATEGORY_EWS", "CATEGORY_RESERVED"
ESM, CENTRAL_GOVT_EMPLOYEE = "ESM", "CENTRAL_GOVT_EMPLOYEE"

CERTIFICATE_APPLIES_WHEN = {
    CertificateKind.OBC_NCL: OBC, CertificateKind.EWS: EWS, CertificateKind.SC: SC, CertificateKind.ST: ST,
    CertificateKind.PWBD: PWBD, CertificateKind.ESM: ESM, CertificateKind.DOMICILE: IF_CLAIMED,
}

# A sentence about a certificate kind names it in one of these ways
KIND_WORDS = {
    CertificateKind.OBC_NCL: re.compile(r"creamy\s+layer|\bNCL\b", re.I),
    CertificateKind.EWS: re.compile(r"\bEWS\b|Economically\s+Weaker|Income\s*&\s*Asset", re.I),
    CertificateKind.SC: re.compile(r"\bSC\b|(?i:Scheduled\s+Castes?)"),
    CertificateKind.ST: re.compile(r"\bST\b|(?i:Scheduled\s+Tribes?)"),
    CertificateKind.PWBD: re.compile(r"PwBD|Disabilit", re.I),
    CertificateKind.ESM: re.compile(r"Ex-?\s*Servicem[ae]n|\bESM\b", re.I),
    CertificateKind.DOMICILE: re.compile(r"domicile|permanent\s+resident", re.I),
}

_DMY = r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})"

def _dmy(m: "re.Match", at: int = 1) -> Optional[date]:
    try:
        return date(int(m.group(at + 2)), int(m.group(at + 1)), int(m.group(at)))
    except ValueError:
        return None

# ---------- financial years ----------

FY_RE = re.compile(r"(20\d{2})\s*-\s*(20\d{2}|\d{2})\b")
_FY = FY_RE.pattern
# "on the basis of Income for the Financial Year 2025-2026", "based on the income for Financial Year (FY) 2024-2025"
_INCOME_FY_RE = re.compile(r"(?:basis\s+of|based\s+on)\s+(?:the\s+)?income\s+for\s+(?:the\s+)?Financial\s+Year\s*(?:\(FY\)\s*)?" + _FY, re.I)
_ANOTHER_FY_RE = re.compile(r"\s*(?:,|and)\s*20\d{2}\s*-")

def financial_year(start: str, end: str) -> Optional[str]:
    """('2025', '2026') or ('2025', '26') -> '2025-26'; None unless the years are consecutive."""
    nxt = int(end) if len(end) == 4 else int(start[:2] + end)
    return f"{start}-{str(nxt)[2:]}" if nxt == int(start) + 1 else None

def income_financial_year(text: str) -> Optional[str]:
    """The one financial year whose income a certificate is based on; None when the text lists several
    ("2024-25, 2023-24 and 2022-23" for OBC non-creamy layer is three years, not one)."""
    m = _INCOME_FY_RE.search(text)
    if not m or _ANOTHER_FY_RE.match(text, m.end()):
        return None
    return financial_year(m.group(1), m.group(2))

# ---------- certificate conditions ----------

_ISSUED_AFTER_RE = re.compile(r"issued\s+on\s*(?:/|or)\s*after\s+" + _DMY, re.I)
_ISSUED_BEFORE_RE = re.compile(r"(?:not\s+later\s+than|issued\s+on\s+or\s+before)\s+" + _DMY, re.I)
_NOT_LATER_THAN_CLOSING_RE = re.compile(r"not\s+later\s+than\s+the\s+closing\s+date", re.I)
# "on the crucial date i.e. the closing date", "Crucial date for claim of ... will be the closing date",
# "the prescribed closing date ... is to be treated as crucial date", "in possession of ... by the closing date"
_CRUCIAL_IS_CLOSING_RE = re.compile(
    r"crucial\s+date\s+i\.?\s*e\.?,?\s+the\s+closing\s+date|crucial\s+date\s+for\s+(?:the\s+)?claim\s+of.{0,200}?will\s+be\s+the\s+closing\s+date"
    r"|closing\s+date.{0,120}?(?:is\s+to\s+be\s+)?treated\s+as\s+(?:the\s+)?crucial\s+date|possession\s+of.{0,200}?by\s+the\s+closing\s+date", re.I | re.S)
_CRUCIAL_DATE_RE = re.compile(r"crucial\s+date\s+(?:is|will\s+be|shall\s+be|fixed\s+as)\s+" + _DMY, re.I)
_FORMAT_ANNEXURE_RE = re.compile(r"(?:format|proforma)\s*\(\s*(Annexure\s*-?\s*[IVXL]+[A-Z]?)\s*\)", re.I)
_FORMAT_LIST_RE = re.compile(r"as\s+per\s+(Annexure-.{3,120}?)\s*,?\s+whichever\s+is\s+applicable", re.I | re.S)
_FORMAT_PRESCRIBED_RE = re.compile(r"prescribed\s+(format|proforma)", re.I)

def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()

def format_note(text: str) -> Optional[str]:
    m = _FORMAT_LIST_RE.search(text)
    if m:
        return re.sub(r"Annexure-\s+", "Annexure-", _clean(m.group(1))) + " whichever is applicable"
    m = _FORMAT_ANNEXURE_RE.search(text)
    if m:
        return re.sub(r"Annexure\s*-?\s*", "Annexure-", m.group(1))
    m = _FORMAT_PRESCRIBED_RE.search(text)
    return f"prescribed {m.group(1).lower()}" if m else None

def certificate_fields(kind: CertificateKind, text: str, closing: Optional[date]) -> Dict[str, Any]:
    """What `text` states about a certificate of `kind`. "The closing date" resolves to `closing` (the notice's
    own closing date, read separately) and stays None when that is not known."""
    fields: Dict[str, Any] = {"crucial_date": None, "issued_on_or_after": None, "issued_on_or_before": None,
                              "financial_year": None, "format_note": format_note(text)}
    m = _CRUCIAL_DATE_RE.search(text)
    if m:
        fields["crucial_date"] = _dmy(m)
    elif _CRUCIAL_IS_CLOSING_RE.search(text):
        fields["crucial_date"] = closing
    m = _ISSUED_AFTER_RE.search(text)
    if m:
        fields["issued_on_or_after"] = _dmy(m)
    m = _ISSUED_BEFORE_RE.search(text)
    if m:
        fields["issued_on_or_before"] = _dmy(m)
    elif _NOT_LATER_THAN_CLOSING_RE.search(text):
        fields["issued_on_or_before"] = closing
    if kind == CertificateKind.EWS:
        fields["financial_year"] = income_financial_year(text)
    return fields

def merge_fields(first: Dict[str, Any], *more: Dict[str, Any]) -> Dict[str, Any]:
    """The first clause's values win; later clauses only fill what it leaves unstated."""
    out = dict(first)
    for other in more:
        for k, v in other.items():
            if out.get(k) is None:
                out[k] = v
    return out

def certificate_from_clauses(kind: CertificateKind, clauses: List[Tuple[int, str]], closing: Optional[date]
                             ) -> Tuple[CertificateRequirement, List[Tuple[int, str]]]:
    """Requirement read from (page, quote) clauses, main clause first; returns it with the clauses it used
    (the main one, and each later one that stated something the earlier ones did not)."""
    fields, used = None, []
    for page, quote in clauses:
        f = certificate_fields(kind, quote, closing)
        merged = f if fields is None else merge_fields(fields, f)
        if fields is None or merged != fields:
            fields = merged
            used.append((page, quote))
    req = CertificateRequirement(kind, **fields, source_page=used[0][0], source_quote=" | ".join(q for _, q in used))
    return req, used

def certificate_value(req: CertificateRequirement, clauses: List[Tuple[int, str]]) -> Dict[str, Any]:
    """CertificateRequirement as stored JSON (ApplicationRequirement.value for req_type CERTIFICATE), plus each
    clause it was read from with its own page: the main clause first, a clause giving e.g. the crucial date after."""
    value = {k: (v.isoformat() if isinstance(v, date) else v) for k, v in asdict(req).items()}
    value["kind"] = req.kind.value
    value["clauses"] = [{"page": p, "quote": q} for p, q in clauses]
    return value

def certificate_from_value(value: Dict[str, Any]) -> CertificateRequirement:
    d = lambda k: date.fromisoformat(value[k]) if value.get(k) else None
    return CertificateRequirement(
        kind=CertificateKind(value["kind"]), crucial_date=d("crucial_date"), issued_on_or_after=d("issued_on_or_after"),
        issued_on_or_before=d("issued_on_or_before"), financial_year=value.get("financial_year"),
        format_note=value.get("format_note"), source_page=value.get("source_page"), source_quote=value.get("source_quote"))

# ---------- photograph / signature ----------

_SUBJECT_RE = {"PHOTOGRAPH": re.compile(r"\bphoto(?:graph)?s?\b", re.I), "SIGNATURE": re.compile(r"\bsignatures?\b", re.I)}
_FORMATS_RE = re.compile(r"(?<![A-Za-z])\.?(JPE?G|PNG|PDF|GIF)\b", re.I)
_KB_RANGE_RE = re.compile(r"(\d+)\s*(?:to|-)\s*(\d+)\s*KB\b", re.I)
_KB_MAX_MIN_RE = re.compile(r"not\s+exceed\s+(\d+)\s*KB\s+and\s+(?:must|should)\s+not\s+be\s+less\s+than\s+(\d+)\s*KB", re.I)
_KB_MAX_RE = re.compile(r"(?:less\s+than|not\s+(?:exceed|more\s+than)|maximum(?:\s+of)?|up\s*to)\s+(\d+)\s*KB\b", re.I)
_CM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*cm\s*\(\s*width\s*\)\s*x\s*(\d+(?:\.\d+)?)\s*cm\s*\(\s*height\s*\)", re.I)
_PX_RE = re.compile(r"(\d{2,4})\s*(?:x|×|\*)\s*(\d{2,4})\s*pixels?", re.I)
_BACKGROUND_RE = re.compile(r"\b(plain|white|light(?:[\s-]coloured)?)\s+background", re.I)
_DATE_ON_PHOTO_RE = re.compile(
    r"date\s+of\s+(?:taking|capture|capturing)\s+(?:of\s+)?(?:the\s+)?photo|photo(?:graph)?\s+(?:should|must)\s+(?:also\s+)?"
    r"(?:bear|have|contain|show|display)\s+(?:the\s+)?(?:name\s+(?:of\s+the\s+candidate\s+)?and\s+)?(?:the\s+)?date", re.I)
_LIVE_RE = re.compile(r"capture\s+(?:a|the|their)\s+(?:live\s+|real-time\s+)?photograph|live\s+photograph", re.I)

def _windows(text: str, subject: str) -> List[str]:
    """Stretches of text from each mention of the subject to the next mention of the other subject."""
    other = _SUBJECT_RE["SIGNATURE" if subject == "PHOTOGRAPH" else "PHOTOGRAPH"]
    out = []
    for m in _SUBJECT_RE[subject].finditer(text):
        nxt = other.search(text, m.end())
        out.append(text[m.start(): nxt.start() if nxt else len(text)])
    return out

def upload_spec(text: str, subject: str) -> Dict[str, Any]:
    """Format, file size, dimensions, background and date-on-photo the text states for a PHOTOGRAPH or SIGNATURE."""
    spec: Dict[str, Any] = {"formats": None, "min_kb": None, "max_kb": None, "width_cm": None, "height_cm": None,
                            "width_px": None, "height_px": None, "background": None, "date_on_photo": None, "live_capture": None}
    windows = _windows(text, subject)
    for w in windows:
        if spec["max_kb"] is None:
            m = _KB_MAX_MIN_RE.search(w)
            if m:
                spec["max_kb"], spec["min_kb"] = int(m.group(1)), int(m.group(2))
            elif _KB_RANGE_RE.search(w):
                m = _KB_RANGE_RE.search(w)
                spec["min_kb"], spec["max_kb"] = int(m.group(1)), int(m.group(2))
            elif _KB_MAX_RE.search(w):
                spec["max_kb"] = int(_KB_MAX_RE.search(w).group(1))
        if spec["width_cm"] is None and _CM_RE.search(w):
            m = _CM_RE.search(w)
            spec["width_cm"], spec["height_cm"] = float(m.group(1)), float(m.group(2))
        if spec["width_px"] is None and _PX_RE.search(w):
            m = _PX_RE.search(w)
            spec["width_px"], spec["height_px"] = int(m.group(1)), int(m.group(2))
        if spec["formats"] is None and _FORMATS_RE.search(w):
            spec["formats"] = list(dict.fromkeys(f.upper() for f in _FORMATS_RE.findall(w)))
    if windows and spec["formats"] is None and _FORMATS_RE.search(text):
        # "photograph and signature duly scanned in the .jpg format": one format stated for both
        spec["formats"] = list(dict.fromkeys(f.upper() for f in _FORMATS_RE.findall(text)))
    if subject == "PHOTOGRAPH" and windows:
        m = _BACKGROUND_RE.search(text)
        spec["background"] = m.group(1).lower() if m else None
        spec["date_on_photo"] = True if _DATE_ON_PHOTO_RE.search(text) else None
        spec["live_capture"] = True if _LIVE_RE.search(text) else None
    return spec

def spec_is_empty(spec: Dict[str, Any]) -> bool:
    return all(v is None for v in spec.values())

# ---------- fee ----------

_RUPEES_RE = re.compile(r"(?:Rs\.?|₹|INR)\s*([\d,]+)", re.I)
EXEMPTION_GROUPS = [
    ("FEMALE", re.compile(r"(?i:\bWomen\b|\bWoman\b|\bFemale\b)")),
    ("SC", re.compile(r"\bSC\b|(?i:Scheduled\s+Castes?)")),
    ("ST", re.compile(r"\bST\b|(?i:Scheduled\s+Tribes?)")),
    ("PWBD", re.compile(r"PwBD|(?i:Benchmark\s+Disabilit)")),
    ("ESM", re.compile(r"(?i:Ex-?\s*Servicem[ae]n)|\bESM\b")),
]
CATEGORY_WORDS = {
    "UR": re.compile(r"\bUR\b|(?i:Unreserved|General)"), "OBC": re.compile(r"\bOBC\b"), "EWS": re.compile(r"\bEWS\b"),
    **{code: rx for code, rx in EXEMPTION_GROUPS},
}

PAYMENT_MODES = [
    ("VISA", r"\bVisa\b"), ("MASTERCARD", r"\bMaster"), ("MAESTRO", r"\bMaestro\b"), ("RUPAY", r"\bRupay\b"), ("CREDIT_CARD", r"Credit"),
    ("DEBIT_CARD", r"Debit"), ("UPI", r"\bUPI\b"), ("INTERNET_BANKING", r"internet\s+banking|Net\s+Banking"),
]

def payment_modes(text: str) -> List[str]:
    return [code for code, rx in PAYMENT_MODES if re.search(rx, text, re.I)]

def rupee_amounts(text: str) -> List[int]:
    return [int(a.replace(",", "")) for a in _RUPEES_RE.findall(text) if a.replace(",", "")]

def exemption_groups(text: str) -> List[str]:
    return sorted(code for code, rx in EXEMPTION_GROUPS if rx.search(text))

# A fee clause ends at a sentence stop (not the one in "Rs."), a semicolon or ", "; a clause that exempts or excepts
# anyone ("Candidates (Except Female/SC/ST/...) are required to pay a fee of Rs. 25/-") charges no category it names
_FEE_CLAUSE_SPLIT_RE = re.compile(r"(?<![Rr][Ss])\.\s+|;|,\s+|\n")
_FEE_EXCLUDES_RE = re.compile(r"exempt|no\s+fee|not\s+required\s+to\s+pay|\bexcept\b|\bnil\b", re.I)

def fee_ties(text: str) -> Dict[str, List[int]]:
    """Category -> the rupee amounts the text charges it, within one clause. The clause's own order sets the
    direction: categories first ("UR/OBC: Rs. 100/- and SC/ST: Rs. 50/-") tie each to the first amount after it; an
    amount first ("Rs. 100/- for UR/OBC and Rs. 50/- for SC/ST") ties each to the last amount before it. A category
    with no amount on that side is tied to none; naming a category and an amount somewhere in the same quote is not enough."""
    ties: Dict[str, List[int]] = {}
    for clause in _FEE_CLAUSE_SPLIT_RE.split(text):
        if _FEE_EXCLUDES_RE.search(clause):
            continue
        amounts = [(m.start(), int(m.group(1).replace(",", ""))) for m in _RUPEES_RE.finditer(clause) if m.group(1).replace(",", "")]
        named = [(m, code) for code, rx in CATEGORY_WORDS.items() for m in rx.finditer(clause)]
        if not amounts or not named:
            continue
        amount_first = amounts[0][0] < min(m.start() for m, _ in named)
        for m, code in named:
            side = [a for pos, a in amounts if pos < m.start()][-1:] if amount_first else [a for pos, a in amounts if pos >= m.end()][:1]
            for amount in side:
                if amount not in ties.setdefault(code, []):
                    ties[code].append(amount)
    return ties

# ---------- document list items ----------

# (code, applies_when, keyword regex): first match wins, checked against the item's own text
DOCUMENT_KINDS = [
    ("NAME_CHANGE_DOCUMENTS", NAME_CHANGE, re.compile(r"change\s+(?:in|of)\s+name", re.I)),
    ("ESM_DOCUMENTS", ESM, re.compile(r"Ex-?\s*Servicemen\s*\(ESM\)|Serving\s+Defence\s+Personnel|Discharge\s+Certificate", re.I)),
    ("CENTRAL_GOVT_EMPLOYEE_CERTIFICATE", CENTRAL_GOVT_EMPLOYEE, re.compile(r"Central\s+Government\s+Civilian\s+Employees", re.I)),
    # CAPF 2026: "Certificate as per Annexure-IX by the Departmental candidates of Delhi Police", "Driving License for Motor Cycle and Car ..."
    ("DEPARTMENTAL_CANDIDATE_CERTIFICATE", IF_CLAIMED, re.compile(r"Departmental\s+candidates", re.I)),
    ("DRIVING_LICENCE", IF_CLAIMED, re.compile(r"Driving\s+Licen[cs]e", re.I)),
    ("NO_OBJECTION_CERTIFICATE", GOVT_SERVICE, re.compile(r"No\s+Objection\s+Certificate", re.I)),
    ("DISABILITY_CERTIFICATE", PWBD, re.compile(r"Disabilit", re.I)),
    ("CASTE_CERTIFICATE", RESERVED, re.compile(r"Caste|Category\s+Certificate", re.I)),
    ("AGE_RELAXATION_CERTIFICATE", AGE_RELAXATION, re.compile(r"age[\s-]+relaxation", re.I)),
    ("EQUIVALENCE_ORDER", IF_CLAIMED, re.compile(r"equivalent\s+Educational\s+Qualification", re.I)),
    ("EXPERIENCE_CERTIFICATE", IF_CLAIMED, re.compile(r"Experience\s+Certificate", re.I)),
    ("MATRICULATION_CERTIFICATE", ALWAYS, re.compile(r"Matriculation|Secondary\s+Certificate", re.I)),
    ("EDUCATION_CERTIFICATE", ALWAYS, re.compile(r"Educational\s+Qualification|Degree|Diploma", re.I)),
    ("BIO_DATA", ALWAYS, re.compile(r"bio-?\s*data", re.I)),
    ("VIGILANCE_CLEARANCE", ALWAYS, re.compile(r"Vigilance\s+Clearance", re.I)),
    ("ACR_APAR", ALWAYS, re.compile(r"\bA(?:CR|PAR)s?\b")),
]
DOCUMENT_NOUN_RE = re.compile(r"certificate|cop(?:y|ies)|proof|bio-?\s*data|document|affidavit|report|order|mark\s*sheet|degree|diploma"
                              r"|undertaking|declaration|\bNOC\b|\bA(?:CR|PAR)s?\b|deed|gazette", re.I)

def classify_document(text: str) -> Optional[Tuple[str, str]]:
    return next(((code, when) for code, when, rx in DOCUMENT_KINDS if rx.search(text)), None)
