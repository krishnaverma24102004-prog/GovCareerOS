"""
Deterministic parser for the "how to apply" part of a UPSC recruitment advertisement's General Instructions:
the documents/certificates to upload, photograph and signature, the photo ID, the application fee with its
exemptions, payment modes and fee rules, and the online application (ORA) website.

Rules:
  - Only what the text states is extracted; each item keeps the 1-based page where it starts, its section and
    the exact excerpt. A clause that is not found produces a warning, never a guessed value.
  - The document list is the lettered list after "Candidates must upload the following documents/certificates".
    Letters are matched in sequence (a, b, c, ...) so roman sub-items such as "i) Ex-Servicemen" inside item
    (i) are not mistaken for list items.
  - Each document gets a code and an `applies_when` condition from keywords in its own text; the checklist
    personalises on that condition, never on anything the advertisement does not say.
"""
import re
from dataclasses import dataclass, field
from typing import Any, List, Optional

from govcareeros.backend.documents.pdf_text import PageText
from govcareeros.backend.parsing.upsc_advertisement import Provenance, _PagedText, _clean

PARSER_VERSION = "upsc_application/1"

# applies_when values
ALWAYS, IF_CLAIMED = "ALWAYS", "IF_CLAIMED"
SC_ST_OBC, OBC, PWBD = "CATEGORY_SC_ST_OBC", "CATEGORY_OBC", "PWBD"
AGE_RELAXATION, GOVT_SERVICE, NAME_CHANGE = "AGE_RELAXATION", "GOVT_SERVICE", "NAME_CHANGE"

@dataclass
class ApplicationItem:
    req_type: str  # DOCUMENT, UPLOAD_RULE, FEE, FEE_EXEMPTION, PAYMENT_MODE, FEE_RULE, PHOTO_ID, APPLICATION_PORTAL
    code: str
    applies_when: str
    value: Any
    provenance: Provenance

@dataclass
class ParsedApplicationInfo:
    parser_version: str
    items: List[ApplicationItem] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def get(self, req_type: str, code: Optional[str] = None) -> Optional[ApplicationItem]:
        return next((i for i in self.items if i.req_type == req_type and (code is None or i.code == code)), None)

_SECTION = "General Instructions"
_UPLOAD_START_RE = re.compile(r"Candidates\s+must\s+upload\s+the\s+following\s+documents\s*/\s*certificates", re.I)
# The list ends at the transcript note or the next roman paragraph of "HOW TO APPLY"
_UPLOAD_END_RE = re.compile(r"NOTE\s*:\s*If\s+any\s+document|^\s*v\)\s*$|^\s*v\)\s+IMPORTANT", re.I | re.M)
_NOT_ACCEPTED_RE = re.compile(r"Documents\s+like\s+(.+?)\s+must\s+not\s+be\s+uploaded", re.I | re.S)

# (code, applies_when, keyword regex) — first match wins, checked against the item's own text
_DOCUMENT_KINDS = [
    ("CASTE_CERTIFICATE", SC_ST_OBC, re.compile(r"^Caste\s+certificate", re.I)),
    ("OBC_NON_CREAMY_LAYER_DECLARATION", OBC, re.compile(r"creamy\s+layer", re.I)),
    ("DISABILITY_CERTIFICATE", PWBD, re.compile(r"Certificate\s+of\s+Disability", re.I)),
    ("AGE_RELAXATION_CERTIFICATE", AGE_RELAXATION, re.compile(r"Age\s+relaxation\s+for", re.I)),
    ("NAME_CHANGE_DOCUMENTS", NAME_CHANGE, re.compile(r"change\s+in\s+name", re.I)),
    ("GOVT_SERVICE_DECLARATION", GOVT_SERVICE, re.compile(r"Regular\s+Government\s+service", re.I)),
    ("EQUIVALENCE_ORDER", IF_CLAIMED, re.compile(r"equivalent\s+Educational\s+Qualification", re.I)),
    ("DATE_OF_BIRTH_PROOF", ALWAYS, re.compile(r"Date\s+of\s+Birth", re.I)),
    ("EDUCATION_CERTIFICATE", ALWAYS, re.compile(r"Degree\s*/\s*Diploma\s+certificate", re.I)),
    ("EXPERIENCE_CERTIFICATE", IF_CLAIMED, re.compile(r"experience\s+claimed", re.I)),
    ("OTHER_CLAIM_CERTIFICATES", IF_CLAIMED, re.compile(r"Professional\s+Registration|Publications|\bNET\b|\bGATE\b", re.I)),
    ("OTHER_CLAIMS", IF_CLAIMED, re.compile(r"any\s+other\s+claim", re.I)),
]
_PHOTO_RE = re.compile(r"upload\s+their\s+photograph\s+and\s+also\s+capture\s+their\s+live\s+photograph[^.]*\.", re.I | re.S)
_SIGNATURE_RE = re.compile(r"Applicants\s+are\s+required\s+to\s+sign\s+three\s+times.*?upload\s+the\s+same[^.]*\.", re.I | re.S)
_PORTAL_RE = re.compile(r"apply\s+online\s+by\s+using\s+the\s+website\s+(https?://[^\s,;)]+)", re.I)
_PHOTO_ID_RE = re.compile(
    r"details\s+of\s+one\s+Photo\s+ID\s+Card\s+viz\.?\s*(.+?)\s*/\s*(Any\s+other\s+Photo\s+ID\s+Card\s+issued\s+by\s+the\s+State\s*/\s*Central\s+Government)"
    r".*?while\s+filling\s+up\s+the\s+Universal\s+Registration\s*\(URN\)\s+profile", re.I | re.S)
_AADHAAR_ADVICE_RE = re.compile(r"strongly\s+advised\s+to\s+use\s+their\s+Aadhaar\s+Card\s+as\s+the\s+ID\s+document", re.I)

_FEE_SECTION_RE = re.compile(r"APPLICATION\s+FEE\s*:(.*?)CONCESSIONS\s*&\s*RELAXATIONS", re.I | re.S)
_FEE_AMOUNT_RE = re.compile(r"required\s+to\s+pay\s+a\s+fee\s+of\s+Rs\.?\s*(\d+)[^(]*(\([^)]*\))?\s+by\s+using\s+(.+?)\.\s", re.I | re.S)
_NO_FEE_RE = re.compile(r"No\s+fee\s+for\s+([A-Za-z/]+)\s+candidates[^.]*\.(\s*No\s+.fee\s+exemption.[^.]*\.)?", re.I | re.S)
_EXEMPT_TOKENS = {"SC": "SC", "ST": "ST", "PWBD": "PWBD", "PH": "PWBD", "WOMEN": "FEMALE", "WOMAN": "FEMALE", "FEMALE": "FEMALE"}
_PAYMENT_MODES = [
    ("VISA", r"\bVisa\b"), ("MASTERCARD", r"\bMaster"), ("RUPAY", r"\bRupay\b"), ("CREDIT_CARD", r"Credit"),
    ("DEBIT_CARD", r"Debit"), ("UPI", r"\bUPI\b"), ("INTERNET_BANKING", r"internet\s+banking"),
]
_FEE_RULES = [
    ("NO_FEE_REJECTED", re.compile(r"Applications\s+without\s+the\s+prescribed\s+fee\s+would\s+not\s+be\s+considered[^.]*\.", re.I | re.S)),
    ("NO_REFUND", re.compile(r"Fee\s+once\s+paid\s+shall\s+not\s+be\s+refunded[^.]*\.", re.I | re.S)),
    ("PAY_BY_CLOSING_DATE", re.compile(r"Applicants\s+must\s+opt\s+for\s+available\s+online.*?upto\s+the\s+closing\s+date\s*/\s*time\.", re.I | re.S)),
]

def _document_items(doc: _PagedText, info: ParsedApplicationInfo) -> None:
    text = doc.text
    start = _UPLOAD_START_RE.search(text)
    if not start:
        info.warnings.append("document upload list not found")
        return
    end = _UPLOAD_END_RE.search(text, start.end())
    stop = end.start() if end else len(text)

    m = _NOT_ACCEPTED_RE.search(text, start.end(), stop)
    if m:
        banned = [_clean(x).rstrip(". ") for x in re.split(r",", _clean(m.group(1)).replace(" etc", "")) if _clean(x)]
        info.items.append(ApplicationItem("UPLOAD_RULE", "NOT_ACCEPTED", ALWAYS, {"documents": banned},
                                          doc.prov(m.start(), m.end(), _SECTION)))

    # Sequential letters: the next item is the first "<letter>)" line-start after the previous one
    starts, pos = [], start.end()
    for letter in "abcdefghijklmnopqrstuvwxyz":
        m = re.compile(rf"^[ \t]*{letter}\)", re.M).search(text, pos, stop)
        if not m:
            break
        starts.append((letter, m.start(), m.end()))
        pos = m.end()
    if not starts:
        info.warnings.append("document upload list has no lettered items")
    for i, (letter, s, body_start) in enumerate(starts):
        e = starts[i + 1][1] if i + 1 < len(starts) else stop
        body = _clean(text[body_start:e])
        code, when = next(((c, w) for c, w, rx in _DOCUMENT_KINDS if rx.search(body)), (f"DOCUMENT_{letter.upper()}", IF_CLAIMED))
        prov = doc.prov(s, e, f"{_SECTION}: documents to upload ({letter})", max_len=900)
        info.items.append(ApplicationItem("DOCUMENT", code, when, {"item": letter, "text": body}, prov))

    for code, rx in (("PHOTOGRAPH", _PHOTO_RE), ("SIGNATURE", _SIGNATURE_RE)):
        m = rx.search(text)
        if m:
            info.items.append(ApplicationItem("DOCUMENT", code, ALWAYS, {"text": _clean(m.group(0))},
                                              doc.prov(m.start(), m.end(), f"{_SECTION}: how to apply")))
        else:
            info.warnings.append(f"{code.lower()} upload instruction not found")

def _fee_items(doc: _PagedText, info: ParsedApplicationInfo) -> None:
    sec = _FEE_SECTION_RE.search(doc.text)
    if not sec:
        info.warnings.append("application fee section not found")
        return
    base, body = sec.start(1), sec.group(1)
    section = f"{_SECTION}: application fee"

    m = _FEE_AMOUNT_RE.search(body)
    if m:
        modes = [code for code, rx in _PAYMENT_MODES if re.search(rx, m.group(3), re.I)]
        info.items.append(ApplicationItem("FEE", "APPLICATION_FEE", ALWAYS, {"amount": int(m.group(1)), "currency": "INR"},
                                          doc.prov(base + m.start(), base + m.end(), section)))
        info.items.append(ApplicationItem("PAYMENT_MODE", "ONLINE", ALWAYS, {"modes": modes, "text": _clean(m.group(3))},
                                          doc.prov(base + m.start(3), base + m.end(3), section)))
    else:
        info.warnings.append("application fee amount not found")

    m = _NO_FEE_RE.search(body)
    if m:
        groups = sorted({_EXEMPT_TOKENS[t.upper()] for t in m.group(1).split("/") if t.upper() in _EXEMPT_TOKENS})
        info.items.append(ApplicationItem("FEE_EXEMPTION", "FEE_EXEMPT", ALWAYS, {"exempt": groups, "text": _clean(m.group(0))},
                                          doc.prov(base + m.start(), base + m.end(), section)))
    else:
        info.warnings.append("fee exemption clause not found")

    for code, rx in _FEE_RULES:
        m = rx.search(body)
        if m:
            info.items.append(ApplicationItem("FEE_RULE", code, ALWAYS, {"text": _clean(m.group(0))},
                                              doc.prov(base + m.start(), base + m.end(), section)))

def parse_application_info(pages: List[PageText]) -> ParsedApplicationInfo:
    doc = _PagedText(pages)
    info = ParsedApplicationInfo(PARSER_VERSION)

    m = _PORTAL_RE.search(doc.text)
    if m:
        url = m.group(1).rstrip(".")
        info.items.append(ApplicationItem("APPLICATION_PORTAL", "ORA_WEBSITE", ALWAYS, {"url": url},
                                          doc.prov(m.start(), m.end(), f"{_SECTION}: how to apply")))
    else:
        info.warnings.append("online application website not found")

    _document_items(doc, info)

    m = _PHOTO_ID_RE.search(doc.text)
    if m:
        cards = [_clean(c) for c in m.group(1).split("/") if _clean(c)] + [_clean(m.group(2))]
        aadhaar = _AADHAAR_ADVICE_RE.search(doc.text, m.end())
        info.items.append(ApplicationItem("PHOTO_ID", "PHOTO_ID_CARD", ALWAYS,
                                          {"accepted": cards, "aadhaar_advised": bool(aadhaar)},
                                          doc.prov(m.start(), m.end(), f"{_SECTION}: how to apply", max_len=600)))
    else:
        info.warnings.append("photo ID requirement not found")

    _fee_items(doc, info)
    return info
