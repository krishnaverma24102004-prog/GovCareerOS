"""
Deterministic parser for what an SSC examination notice asks of an applicant (the CGL, CHSL and JE 2026 notices
share the layout): certificates for category claims (OBC non-creamy layer, EWS, SC, ST, PwBD, ESM) with their
crucial date, income financial year and prescribed format; the live photograph and signature upload rules; the
fee, its exemptions, payment modes and refund rule; the documents to submit at Document Verification; key dates.

Rules:
  - Same item shape as parsing/upsc_application.py: every item keeps the 1-based page, section and exact excerpt.
  - Key dates are parsing/ssc_exam_notice.py's own parse of the front-page date table. "The closing date" in a
    certificate clause resolves to the parsed last date for receipt of online applications.
  - A certificate read from several clauses (e.g. the OBC creamy-layer clause, then the "prescribed format"
    clause) quotes each one it used, joined by " | "; the value's "clauses" keeps each with its own page.
    A clause that is not found leaves its fields None and adds a warning; nothing is guessed.
"""
import re
from typing import List, Tuple

from govcareeros.backend.documents.pdf_text import PageText
from govcareeros.backend.eligibility.certificate_types import CertificateKind
from govcareeros.backend.parsing import application_rules as ar
from govcareeros.backend.parsing.ssc_exam_notice import _clean, _Doc, parse_exam_notice
from govcareeros.backend.parsing.upsc_advertisement import Provenance
from govcareeros.backend.parsing.upsc_application import ApplicationItem, ParsedApplicationInfo

PARSER_VERSION = "ssc_application/1"

_KEY_DATES = [("application_start", "APPLICATION_START"), ("application_end", "APPLICATION_END"), ("fee_payment_end", "FEE_PAYMENT_END"),
              ("correction_start", "CORRECTION_START"), ("correction_end", "CORRECTION_END"), ("age_crucial_date", "AGE_CRUCIAL_DATE"),
              ("education_cutoff_date", "EDUCATION_CUTOFF_DATE")]

# "Crucial date for claim of SC/ST/OBC/PwBD/ESM status or any other benefit ... will be the closing date for receipt of online applications."
_CLAIM_CRUCIAL_RE = re.compile(r"(?:The\s+)?crucial\s+date\s+for\s+(?:the\s+)?claim\s+of\s+([A-Za-z/\s]+?)\s+status.{0,300}?"
                               r"will\s+be\s+the\s+closing\s+date[^.]*\.", re.I | re.S)
_CLAIM_KINDS = {"SC": CertificateKind.SC, "ST": CertificateKind.ST, "OBC": CertificateKind.OBC_NCL,
                "PWBD": CertificateKind.PWBD, "ESM": CertificateKind.ESM, "EWS": CertificateKind.EWS}
_PRESCRIBED_FORMAT_RE = re.compile(
    r"Candidates\s+who\s+wish\s+to\s+be\s+considered\s+against\s+reserved\s+vacancies\s+or\s+seek\s+age-?\s*relaxation\s+must\s+submit\s+"
    r"(?:the\s+)?requisite\s+certificates?\s+from\s+the\s+competent\s+authority,?\s+in\s+the\s+prescribed\s+format", re.I)
_OBC_RE = re.compile(r"A\s+person\s+seeking\s+appointment\s+on\s+the\s+basis\s+of\s+reservation\s+to\s+OBCs?\s+must\s+ensure\s+that\s+he\s+does\s+not\s+"
                     r"fall\s+in\s+creamy\s+layer\s+on\s+the\s+crucial\s+date.{0,600}?\(\s*Annexure\s*-?\s*[IVXL]+\s*\)\.?", re.I | re.S)
_EWS_RE = re.compile(r"(?:a\s+)?person,?\s+seeking\s+appointment\s+on\s+the\s+basis\s+of\s+reservation\s+to\s+EWS.{0,300}?Income\s*&\s*Asset\s+"
                     r"certificate.{0,200}?basis\s+of\s+income\s+for\s+the\s+financial\s+year\s+20\d{2}\s*-\s*\d{2,4}", re.I | re.S)
_EWS_FORMAT_RE = re.compile(r"applicants\s+seeking\s+reservation\s+of\s+EWS\s+category\s+shall\s+invariably\s+submit\s+the\s+requisite\s+"
                            r"certificate\s+as\s+per\s+Format\s*\(\s*Annexure\s*-?\s*[IVXL]+\s*\)", re.I)
_PWBD_RE = re.compile(r"PwBD\s+and\s+PwD\s+candidates\s+willing\s+to\s+avail.{0,200}?required\s+to\s+upload\s+Certificate\s+of\s+Disability\s+as\s+per\s+"
                      r"Annexure-.{0,120}?whichever\s+is\s+applicable,?\s+at\s+the\s+time\s+of\s+filling\s+(?:up\s+)?(?:the\s+)?online\s+application\s+form\.",
                      re.I | re.S)

_PHOTO_RE = re.compile(r"For\s+applying,\s+the\s+candidate\s+is\s+not\s+required\s+to\s+have\s+a\s+pre-existing\s+photograph.{0,800}?"
                       r"plain\s+background\.", re.I | re.S)
_SIGNATURE_RE = re.compile(r"Candidates\s+are\s+required\s+to\s+upload\s+the\s+scanned\s+signature\s+in\s+.{0,200}?\(\s*height\s*\)\.", re.I | re.S)

_FEE_RE = re.compile(r"Fee\s+payable\s*:\s*(?:Rs\.?|₹)\s*([\d,]+)\s*/-\s*\([^)]*\)\.?", re.I)
_FEE_EXEMPT_RE = re.compile(r"Women\s+candidates\s+and\s+candidates\s+belonging\s+to.{0,300}?exempted\s+from\s+payment\s+of\s+fee\.", re.I | re.S)
_PAYMENT_RE = re.compile(r"Fee\s+can\s+be\s+paid\s+(?:only\s+through\s+online\s+payment\s+modes,?\s+namely|online\s+through)\s+(.+?)\.(?=\s)", re.I | re.S)
_NO_REFUND_RE = re.compile(r"Fee\s+once\s+paid\s+shall\s+not\s+be\s+refunded[^.]*\.", re.I | re.S)

# "15.11 Candidates will have to submit copies of following documents:", "16.5 ... copies of various documents as given below"
_DV_HEAD_RE = re.compile(r"^[ \t]*(\d+\.\d+)\s+Candidates\s+will\s+have\s+to\s+submit\s+copies\s+of\s+(?:the\s+)?(?:following|various)\s+documents[^:]*:",
                         re.I | re.M)
# "(xi) It is reiterated that after scrutiny of the certificates ...": a note closing the list, not a document
_NOTE_ITEM_RE = re.compile(r"^It\s+is\s+(?:reiterated|clarified|informed)", re.I)
_ROMANS = ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "xi", "xii", "xiii", "xiv", "xv"]

def _quote(doc: _Doc, m: "re.Match") -> Tuple[int, str]:
    return doc.page_at(m.start()), _clean(m.group(0))

def _certificates(doc: _Doc, info: ParsedApplicationInfo, closing) -> None:
    text = doc.text
    found = {name: rx.search(text) for name, rx in (("crucial", _CLAIM_CRUCIAL_RE), ("format", _PRESCRIBED_FORMAT_RE), ("obc", _OBC_RE),
                                                     ("ews", _EWS_RE), ("ews_format", _EWS_FORMAT_RE), ("pwbd", _PWBD_RE))}
    for name, m in found.items():
        if m is None:
            info.warnings.append(f"certificate clause not found: {name}")
    q = {name: _quote(doc, m) for name, m in found.items() if m}
    crucial_kinds = []
    if found["crucial"]:
        crucial_kinds = [_CLAIM_KINDS[t] for t in re.split(r"\s*/\s*", _clean(found["crucial"].group(1)).upper()) if t in _CLAIM_KINDS]
    plan = {
        CertificateKind.OBC_NCL: ["obc", "crucial", "format"], CertificateKind.EWS: ["ews", "ews_format", "format"],
        CertificateKind.PWBD: ["pwbd", "crucial", "format"],
        CertificateKind.SC: ["crucial", "format"], CertificateKind.ST: ["crucial", "format"], CertificateKind.ESM: ["crucial", "format"],
    }
    for kind, names in plan.items():
        # The crucial-date clause counts for a kind only when it names that kind
        own = [n for n in names if n not in ("crucial", "format")]
        if not any(n in q for n in own) and kind not in crucial_kinds:
            continue
        clauses = [q[n] for n in names if n in q and (n != "crucial" or kind in crucial_kinds)]
        req, used = ar.certificate_from_clauses(kind, clauses, closing)
        info.items.append(ApplicationItem("CERTIFICATE", kind.value, ar.CERTIFICATE_APPLIES_WHEN[kind], ar.certificate_value(req, used),
                                          Provenance(req.source_page, "Certificates", req.source_quote)))

def _uploads(doc: _Doc, info: ParsedApplicationInfo) -> None:
    for code, rx in (("PHOTOGRAPH", _PHOTO_RE), ("SIGNATURE", _SIGNATURE_RE)):
        m = rx.search(doc.text)
        if not m:
            info.warnings.append(f"{code.lower()} upload rule not found")
            continue
        quote = _clean(m.group(0))
        info.items.append(ApplicationItem("UPLOAD_RULE", code, ar.ALWAYS, {**ar.upload_spec(quote, code), "text": quote},
                                          doc.prov(m.start(), m.end(), "How to apply: photograph and signature", 1500)))

def _fee(doc: _Doc, info: ParsedApplicationInfo) -> None:
    text, section = doc.text, "Application Fee"
    m = _FEE_RE.search(text)
    if not m:
        info.warnings.append("application fee not found")
        return
    info.items.append(ApplicationItem("FEE", "APPLICATION_FEE", ar.ALWAYS, {"amount": int(m.group(1).replace(",", "")), "currency": "INR"},
                                      doc.prov(m.start(), m.end(), section)))
    # The fee paragraphs follow each other; a later "Fee payable" (an annexed notice) is not this exam's
    window = text[m.end(): m.end() + 2500]
    ex = _FEE_EXEMPT_RE.search(window)
    if ex:
        quote = _clean(ex.group(0))
        info.items.append(ApplicationItem("FEE_EXEMPTION", "FEE_EXEMPT", ar.ALWAYS, {"exempt": ar.exemption_groups(quote), "text": quote},
                                          doc.prov(m.end() + ex.start(), m.end() + ex.end(), section)))
    else:
        info.warnings.append("fee exemption clause not found")
    pay = _PAYMENT_RE.search(window)
    if pay:
        info.items.append(ApplicationItem("PAYMENT_MODE", "ONLINE", ar.ALWAYS, {"modes": ar.payment_modes(pay.group(1)), "text": _clean(pay.group(1))},
                                          doc.prov(m.end() + pay.start(), m.end() + pay.end(), section)))
    refund = _NO_REFUND_RE.search(window)
    if refund:
        info.items.append(ApplicationItem("FEE_RULE", "NO_REFUND", ar.ALWAYS, {"text": _clean(refund.group(0))},
                                          doc.prov(m.end() + refund.start(), m.end() + refund.end(), section)))

def _documents(doc: _Doc, info: ParsedApplicationInfo) -> None:
    text = doc.text
    head = _DV_HEAD_RE.search(text)
    if not head:
        info.warnings.append("document verification list not found")
        return
    para = head.group(1)
    # The list ends at the next paragraph number that is not one of its own sub-items ("15.12", "16.6")
    end = re.compile(rf"^[ \t]*(?!{re.escape(para)}\.)\d+\.\d+(?:\s|$)", re.M).search(text, head.end())
    stop = end.start() if end else len(text)
    starts = [(n.group(1), n.start(), n.end()) for n in re.compile(rf"^[ \t]*{re.escape(para)}\.(\d+)(?!\.\d)", re.M).finditer(text, head.end(), stop)]
    if not starts:
        # (i), (ii), ... matched in sequence so an "(a)" sub-item is never a new item
        pos = head.end()
        for r in _ROMANS:
            n = re.compile(rf"^[ \t]*\({r}\)", re.M).search(text, pos, stop)
            if not n:
                break
            starts.append((r, n.start(), n.end()))
            pos = n.end()
    if not starts:
        info.warnings.append("document verification list has no items")
    seen = set()
    for i, (label, s, body_start) in enumerate(starts):
        e = starts[i + 1][1] if i + 1 < len(starts) else stop
        body = _clean(text[body_start:e])
        if _NOTE_ITEM_RE.match(body):
            continue
        code, when = ar.classify_document(body) or (f"DOCUMENT_{label.upper()}", ar.IF_CLAIMED)
        if code in seen:  # one row per code and document
            code = f"{code}_{label.upper()}"
        seen.add(code)
        info.items.append(ApplicationItem("DOCUMENT", code, when, {"item": label, "text": body},
                                          doc.prov(s, e, f"Document Verification: {para}.{label}" if label.isdigit() else
                                                   f"Document Verification: {para} ({label})", 1500)))

def parse_ssc_application_info(pages: List[PageText]) -> ParsedApplicationInfo:
    doc = _Doc(pages)
    info = ParsedApplicationInfo(PARSER_VERSION)
    exam = parse_exam_notice(pages)
    for name, code in _KEY_DATES:
        f = exam.fields.get(name)
        if f is not None:
            info.items.append(ApplicationItem("KEY_DATE", code, ar.ALWAYS, {"at": f.value.isoformat()}, f.provenance))
    end = exam.value("application_end")
    if end is None:
        info.warnings.append("closing date not found; certificate crucial dates stay unresolved")
    _certificates(doc, info, end.date() if end else None)
    _uploads(doc, info)
    _fee(doc, info)
    _documents(doc, info)
    return info
