"""
Deterministic parser for the "how to apply" part of a UPSC recruitment advertisement's General Instructions:
the documents/certificates to upload, photograph and signature, the photo ID, the application fee with its
exemptions, payment modes and fee rules, the online application (ORA) website, the closing date, and the
certificate conditions (parsing/application_rules.py) its document items state. parse_exam_application_info
reads the same kinds of facts from a UPSC examination notice (e.g. IES/ISS 2026).

Rules:
  - Only what the text states is extracted; each item keeps the 1-based page where it starts, its section and
    the exact excerpt. A clause that is not found produces a warning, never a guessed value.
  - The document list is the lettered list after "Candidates must upload the following documents/certificates".
    Letters are matched in sequence (a, b, c, ...) so roman sub-items such as "i) Ex-Servicemen" inside item
    (i) are not mistaken for list items.
  - Each document gets a code and an `applies_when` condition from keywords in its own text; the checklist
    personalises on that condition, never on anything the advertisement does not say.
  - CERTIFICATE rows hold eligibility/certificate_types.CertificateRequirement as JSON; "the closing date" in a
    certificate clause resolves to the closing date stated in the same document, and stays None when the
    document states several (Advt 11/2026: one for UT of Ladakh posts, another for the rest).
"""
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from govcareeros.backend.documents.pdf_text import PageText
from govcareeros.backend.eligibility.certificate_types import CertificateKind
from govcareeros.backend.parsing import application_rules as ar
from govcareeros.backend.parsing.upsc_advertisement import IST, Provenance, _PagedText, _clean

PARSER_VERSION = "upsc_application/2"
EXAM_PARSER_VERSION = "upsc_exam_application/1"

# applies_when values (shared with the other notice parsers)
ALWAYS, IF_CLAIMED = ar.ALWAYS, ar.IF_CLAIMED
SC_ST_OBC, OBC, PWBD = ar.SC_ST_OBC, ar.OBC, ar.PWBD
AGE_RELAXATION, GOVT_SERVICE, NAME_CHANGE = ar.AGE_RELAXATION, ar.GOVT_SERVICE, ar.NAME_CHANGE

@dataclass
class ApplicationItem:
    # DOCUMENT, UPLOAD_RULE, FEE, FEE_EXEMPTION, PAYMENT_MODE, FEE_RULE, PHOTO_ID, APPLICATION_PORTAL, CERTIFICATE, KEY_DATE
    req_type: str
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
_CLOSING_RE = re.compile(r"closing\s+date\s+for\s+submission\s+of\s+online\s+recruitment\s+application\s+through\s+website\s+"
                         r"is\s+(\d{2})(\d{2})\s*Hrs\s+on\s+(\d{2})-(\d{2})-(\d{4})", re.I)
_ESM_CERT_RE = re.compile(r"Ex-?\s*Servicemen\s+and\s+Commissioned\s+Officers.{0,80}?in\s+prescribed\s+proforma\s+from\s+competent\s+authority\.?",
                          re.I | re.S)
# Certificate kinds whose conditions an advertisement's document item states
_DOCUMENT_CERTIFICATES = {
    "CASTE_CERTIFICATE": (CertificateKind.SC, CertificateKind.ST), "OBC_NON_CREAMY_LAYER_DECLARATION": (CertificateKind.OBC_NCL,),
    "DISABILITY_CERTIFICATE": (CertificateKind.PWBD,),
}
_FEE_RULES = [
    ("NO_FEE_REJECTED", re.compile(r"Applications\s+without\s+the\s+prescribed\s+fee\s+would\s+not\s+be\s+considered[^.]*\.", re.I | re.S)),
    ("NO_REFUND", re.compile(r"Fee\s+once\s+paid\s+shall\s+not\s+be\s+refunded[^.]*\.", re.I | re.S)),
    ("PAY_BY_CLOSING_DATE", re.compile(r"Applicants\s+must\s+opt\s+for\s+available\s+online.*?upto\s+the\s+closing\s+date\s*/\s*time\.", re.I | re.S)),
]

def _document_items(doc: _PagedText, info: ParsedApplicationInfo) -> Dict[str, Tuple[int, int]]:
    """Adds the document items; returns the (start, end) offsets of each item's own text by code."""
    text = doc.text
    spans: Dict[str, Tuple[int, int]] = {}
    start = _UPLOAD_START_RE.search(text)
    if not start:
        info.warnings.append("document upload list not found")
        return spans
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
        spans[code] = (body_start, e)

    for code, rx in (("PHOTOGRAPH", _PHOTO_RE), ("SIGNATURE", _SIGNATURE_RE)):
        m = rx.search(text)
        if m:
            info.items.append(ApplicationItem("DOCUMENT", code, ALWAYS, {"text": _clean(m.group(0))},
                                              doc.prov(m.start(), m.end(), f"{_SECTION}: how to apply")))
        else:
            info.warnings.append(f"{code.lower()} upload instruction not found")
    return spans

def _closing_date(doc: _PagedText, info: ParsedApplicationInfo) -> Optional[date]:
    m = _CLOSING_RE.search(doc.text)
    if not m:
        info.warnings.append("closing date not found")
        return None
    if re.match(r"\s*\(", doc.text[m.end():]):
        # "02-10-2026 (for posts other than those under ... Ladakh) and 09-10-2026 (for post under ...)": the closing
        # date differs by post, so it is not a document fact; each vacancy keeps its own (the checklist reads it there)
        return None
    at = datetime(int(m.group(5)), int(m.group(4)), int(m.group(3)), int(m.group(1)), int(m.group(2)), tzinfo=IST)
    info.items.append(ApplicationItem("KEY_DATE", "APPLICATION_END", ALWAYS, {"at": at.isoformat()},
                                      doc.prov(m.start(), m.end(), f"{_SECTION}: last date")))
    return at.date()

def _certificate(info: ParsedApplicationInfo, kind: CertificateKind, clauses: List[Tuple[int, str]], closing: Optional[date],
                 section: str) -> None:
    req, used = ar.certificate_from_clauses(kind, clauses, closing)
    info.items.append(ApplicationItem("CERTIFICATE", kind.value, ar.CERTIFICATE_APPLIES_WHEN[kind], ar.certificate_value(req, used),
                                      Provenance(req.source_page, section, req.source_quote)))

def _certificate_items(doc: _PagedText, info: ParsedApplicationInfo, spans: Dict[str, Tuple[int, int]], closing: Optional[date]) -> None:
    section = f"{_SECTION}: documents to upload"
    for code, kinds in _DOCUMENT_CERTIFICATES.items():
        if code in spans:
            s, e = spans[code]
            for kind in kinds:
                _certificate(info, kind, [(doc.page_at(s), _clean(doc.text[s:e]))], closing, section)
    if "AGE_RELAXATION_CERTIFICATE" in spans:
        s, e = spans["AGE_RELAXATION_CERTIFICATE"]
        m = _ESM_CERT_RE.search(doc.text, s, e)
        if m:
            _certificate(info, CertificateKind.ESM, [(doc.page_at(m.start()), _clean(m.group(0)))], closing, section)

def _fee_items(doc: _PagedText, info: ParsedApplicationInfo) -> None:
    sec = _FEE_SECTION_RE.search(doc.text)
    if not sec:
        info.warnings.append("application fee section not found")
        return
    base, body = sec.start(1), sec.group(1)
    section = f"{_SECTION}: application fee"

    m = _FEE_AMOUNT_RE.search(body)
    if m:
        modes = ar.payment_modes(m.group(3))
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

    spans = _document_items(doc, info)
    _certificate_items(doc, info, spans, _closing_date(doc, info))

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

# ---------- examination notices (e.g. Indian Economic Service/Indian Statistical Service Examination, 2026) ----------

_EXAM_SECTION = "Examination notice"
_MONTH = r"(January|February|March|April|May|June|July|August|September|October|November|December)"
_EXAM_WINDOW_RE = re.compile(r"The\s+Online\s+Application\s+can\s+be\s+filled\s+from\s+(\d{1,2})(?:st|nd|rd|th)?\s+" + _MONTH + r",?\s+(\d{4})\s+to\s+"
                             r"(\d{1,2})(?:st|nd|rd|th)?\s+" + _MONTH + r",?\s+(\d{4})\s+till\s+(\d{1,2}):(\d{2})\s+hours", re.I)
# "Candidates seeking reservation/relaxation benefits available for SC/ST/OBC/EWS/PwBD/Ex-servicemen must ensure ...
#  in possession of all the requisite valid certificates in the prescribed format ... by the closing date of the application."
_EXAM_POSSESSION_RE = re.compile(r"Candidates\s+seeking\s+reservation\s*/\s*relaxation\s+benefits\s+available\s+for\s+([A-Za-z/\-\s]+?)\s+must\s+ensure"
                                 r".{0,400}?by\s+the\s+closing\s+date\s+of\s+the\s+application\.", re.I | re.S)
_EXAM_OBC_RE = re.compile(r"The\s+OBC\s+candidates\s+applying\s+for.{0,200}?must\s+produce\s+OBC\s*\(\s*Non-?\s*Creamy\s+Layer\s*\)\s+certificate"
                          r".{0,400}?closing\s+date\s+of\s+the\s+application[^.]*\.", re.I | re.S)
_EXAM_EWS_RE = re.compile(r"A\s+candidate\s+of\s+[^.]{0,200}?will\s+be\s+eligible\s+to\s+get\s+the\s+benefit\s+of\s+the\s+Economically\s+Weaker\s+Section\s+"
                          r"reservation\s+only\s+in\s+case\s+the\s+candidate\s+meets\s+the\s+criteria.{0,400}?closing\s+date\s+of\s+the\s+application[^.]*\.",
                          re.I | re.S)
_EXAM_KINDS = {"SC": CertificateKind.SC, "ST": CertificateKind.ST, "OBC": CertificateKind.OBC_NCL, "EWS": CertificateKind.EWS,
               "PWBD": CertificateKind.PWBD, "EXSERVICEMEN": CertificateKind.ESM, "ESM": CertificateKind.ESM}
_EXAM_FEE_RE = re.compile(r"Candidates\s*\(\s*except\s+(.+?)\s+Candidates\s+who\s+are\s+exempted\s+from\s+payment\s+of\s+fee\s*\)\s+are\s+required\s+"
                          r"to\s+pay\s+fee\s+of\s+Rs\.?\s*(\d+)\s*/-\s*\([^)]*\)\s+either\s+by\s+using\s+(.+?)\s+for\s+appearing", re.I | re.S)
_EXAM_UPLOAD_RE = re.compile(r"Before\s+filling\s+up\s+Online\s+Application\s+Form,\s+a\s+candidate\s+must\s+have\s+his\s+photograph\s+and\s+signature\s+"
                             r"duly\s+scanned.{0,400}?in\s+size\.", re.I | re.S)

def _month_date(d: str, month: str, y: str) -> date:
    return datetime.strptime(f"{d} {month} {y}", "%d %B %Y").date()

def parse_exam_application_info(pages: List[PageText]) -> ParsedApplicationInfo:
    """Fee, exemptions, photograph/signature files, application window and certificate conditions of a UPSC
    examination notice. Same item shapes and provenance as parse_application_info."""
    doc = _PagedText(pages)
    text = doc.text
    info = ParsedApplicationInfo(EXAM_PARSER_VERSION)

    m = _PORTAL_RE.search(text)
    if m:
        info.items.append(ApplicationItem("APPLICATION_PORTAL", "ORA_WEBSITE", ALWAYS, {"url": m.group(1).rstrip(".")},
                                          doc.prov(m.start(), m.end(), _EXAM_SECTION)))
    closing = None
    m = _EXAM_WINDOW_RE.search(text)
    if m:
        start = datetime.combine(_month_date(*m.group(1, 2, 3)), datetime.min.time(), IST)
        end = datetime.combine(_month_date(*m.group(4, 5, 6)), datetime.min.time(), IST).replace(hour=int(m.group(7)), minute=int(m.group(8)))
        prov = doc.prov(m.start(), m.end(), _EXAM_SECTION)
        info.items.append(ApplicationItem("KEY_DATE", "APPLICATION_START", ALWAYS, {"at": start.isoformat()}, prov))
        info.items.append(ApplicationItem("KEY_DATE", "APPLICATION_END", ALWAYS, {"at": end.isoformat()}, prov))
        closing = end.date()
    else:
        info.warnings.append("online application window not found")

    found = {name: rx.search(text) for name, rx in (("possession", _EXAM_POSSESSION_RE), ("obc", _EXAM_OBC_RE), ("ews", _EXAM_EWS_RE))}
    for name, f in found.items():
        if f is None:
            info.warnings.append(f"certificate clause not found: {name}")
    quote = {name: (doc.page_at(f.start()), _clean(f.group(0))) for name, f in found.items() if f}
    listed = []
    if found["possession"]:
        listed = [_EXAM_KINDS[k] for k in (re.sub(r"[\s-]", "", t).upper() for t in found["possession"].group(1).split("/")) if k in _EXAM_KINDS]
    section = f"{_EXAM_SECTION}: certificates"
    for kind in (CertificateKind.OBC_NCL, CertificateKind.EWS, CertificateKind.SC, CertificateKind.ST, CertificateKind.PWBD, CertificateKind.ESM):
        own = {CertificateKind.OBC_NCL: "obc", CertificateKind.EWS: "ews"}.get(kind)
        clauses = ([quote[own]] if own in quote else []) + ([quote["possession"]] if kind in listed else [])
        if clauses:
            _certificate(info, kind, clauses, closing, section)

    m = _EXAM_UPLOAD_RE.search(text)
    if m:
        q = _clean(m.group(0))
        for code in ("PHOTOGRAPH", "SIGNATURE"):
            info.items.append(ApplicationItem("UPLOAD_RULE", code, ALWAYS, {**ar.upload_spec(q, code), "text": q},
                                              doc.prov(m.start(), m.end(), _EXAM_SECTION, max_len=900)))
    else:
        info.warnings.append("photograph/signature file rule not found")

    m = _EXAM_FEE_RE.search(text)
    if m:
        section = f"{_EXAM_SECTION}: fee"
        info.items.append(ApplicationItem("FEE", "APPLICATION_FEE", ALWAYS, {"amount": int(m.group(2)), "currency": "INR"},
                                          doc.prov(m.start(), m.end(), section, max_len=900)))
        exempt_text = _clean(m.group(1))
        info.items.append(ApplicationItem("FEE_EXEMPTION", "FEE_EXEMPT", ALWAYS, {"exempt": ar.exemption_groups(exempt_text), "text": exempt_text},
                                          doc.prov(m.start(), m.end(2), section, max_len=900)))
        info.items.append(ApplicationItem("PAYMENT_MODE", "ONLINE", ALWAYS, {"modes": ar.payment_modes(m.group(3)), "text": _clean(m.group(3))},
                                          doc.prov(m.start(3), m.end(3), section)))
        for code, rx in _FEE_RULES:
            r = rx.search(text, m.end())
            if r and r.start() - m.end() < 1500:
                info.items.append(ApplicationItem("FEE_RULE", code, ALWAYS, {"text": _clean(r.group(0))}, doc.prov(r.start(), r.end(), section)))
    else:
        info.warnings.append("application fee not found")
    return info
