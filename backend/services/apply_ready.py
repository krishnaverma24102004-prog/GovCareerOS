"""
Apply-ready page for one vacancy or exam: everything the official notice asks of an applicant, in eight sections
(dates, eligibility, certificates, fee, uploads, photo_id, documents, portal), each item with the page and quote it
was read from. Public; with a profile version of the caller, items also carry the candidate's own status.

Built only from stored rows:
  - eligibility: active VacancyRequirement / ExamRequirement rows (rows sharing one quote are one item);
  - everything else: the notice's ApplicationRequirement rows (application_prep), plus the record's current closing
    date when a later official notice changed it (change_log);
  - warnings: what the deterministic parser could not find (stored at ingestion), and what is missing here.

Personal statuses come from the existing deterministic code, never an LLM:
  - eligibility: the hard checks of eligibility/engine (vacancy) or exam_engine (exam; post-wise notices judge each
    post's own rows, and exam-level rows as for the best post), computed in memory and not stored.
    MET -> OK, NOT_MET -> FAILS, CONDITIONAL / NEEDS_HUMAN_REVIEW -> CANNOT_DECIDE, UNKNOWN -> UNKNOWN.
  - certificates: eligibility/certificate_check against the certificates the profile version records, its status as it
    gives it (for a notice that states no validity date it applies the closing date under a cited rule); NOT_APPLICABLE
    gives no status. A notice row that states no validity date says so in its detail, public or not.
  - documents and fee: application_prep's checklist. REQUIRED -> NEEDS_DOCUMENT, IF_APPLICABLE -> CANNOT_DECIDE,
    UNKNOWN -> UNKNOWN, NOT_REQUIRED -> no status; fee EXEMPT / PAY -> OK (the detail says which), UNKNOWN -> UNKNOWN.
  - dates, uploads, photo_id and portal apply to everyone: no status.

Page status: CLOSED when an official notice cancelled it; NOT_YET_OPEN before the application window opens (the record's
application_start, else the notice's stated opening date); OPEN until the closing date, CLOSED after it; UNKNOWN when
no closing date is known.

Labels are Hinglish; quotes stay in the notice's own English.
"""
import html
import json
import uuid
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.eligibility import certificate_check, certificates, engine, exam_engine, rules
from govcareeros.backend.models.application import ApplicationRequirement
from govcareeros.backend.models.artifact import RawDocument
from govcareeros.backend.models.exam import Exam, ExamPost, ExamRequirement
from govcareeros.backend.models.user import PersonalInfo
from govcareeros.backend.models.vacancy import Vacancy, VacancyRequirement
from govcareeros.backend.parsing.upsc_advertisement import IST
from govcareeros.backend.schemas.attribution import DISCLAIMER
from govcareeros.backend.services import application_prep, attribution, change_log
from govcareeros.backend.services.application_prep import PrepInputError

OK, FAILS, NEEDS_DOCUMENT, CANNOT_DECIDE, UNKNOWN = "OK", "FAILS", "NEEDS_DOCUMENT", "CANNOT_DECIDE", "UNKNOWN"
# Worst first: an item judged on several rows (or outcomes) shows the worst
_SEVERITY = (FAILS, NEEDS_DOCUMENT, CANNOT_DECIDE, UNKNOWN, OK)

SECTIONS = (
    ("dates", "Zaroori tareekhein"),
    ("eligibility", "Yogyata (eligibility)"),
    ("certificates", "Certificates (praman patra)"),
    ("fee", "Application fee"),
    ("uploads", "Photo / signature upload ke niyam"),
    ("photo_id", "Photo ID"),
    ("documents", "Documents jo taiyaar rakhne hain"),
    ("portal", "Apply kahan karein"),
)

_HARD = {"MET": OK, "NOT_MET": FAILS, "CONDITIONAL": CANNOT_DECIDE, "NEEDS_HUMAN_REVIEW": CANNOT_DECIDE, "UNKNOWN": UNKNOWN}
_CERT = {certificates.OK: OK, certificates.FAILS: FAILS, certificates.NEEDS_DOCUMENT: NEEDS_DOCUMENT,
         certificates.CANNOT_DECIDE: CANNOT_DECIDE}
_CHECKLIST = {application_prep.REQUIRED: NEEDS_DOCUMENT, application_prep.IF_APPLICABLE: CANNOT_DECIDE, application_prep.UNKNOWN: UNKNOWN}

_KEY_DATES = {
    "APPLICATION_START": "Online aavedan shuru", "APPLICATION_END": "Aavedan ki antim tithi",
    "FEE_PAYMENT_END": "Fee bharne ki antim tithi", "CORRECTION_START": "Correction window khulegi",
    "CORRECTION_END": "Correction window band", "AGE_CRUCIAL_DATE": "Aayu is tithi par gini jayegi (crucial date)",
    "EDUCATION_CUTOFF_DATE": "Shaikshik yogyata is tithi tak honi chahiye",
}
_ELIGIBILITY = {
    "CITIZENSHIP": "Nagrikta (citizenship)", "AGE_MAX": "Aayu seema (age limit)", "AGE_RELAXATION": "Aayu mein chhoot (age relaxation)",
    "AGE_RELAXATION_RULE": "Aayu chhoot ka niyam", "EDUCATION": "Shaikshik yogyata (education)", "EXPERIENCE": "Anubhav (experience)",
    "GOVT_SERVICE": "Sarkari seva (government service)", "PWBD_SUITABILITY": "PwBD ke liye upyukt (suitability)",
    "DESIRABLE": "Vanchhniya yogyata (desirable)",
}
# Not about who may apply: duties of the post, vacancy counts per category; certificates have their own section
_NOT_ELIGIBILITY = {"DUTIES", "RESERVATION", "CERTIFICATE"}
_CERTIFICATES = {
    "OBC_NCL": "OBC (Non-Creamy Layer) certificate", "EWS": "EWS Income & Asset certificate", "SC": "SC certificate",
    "ST": "ST certificate", "PWBD": "Divyangta (PwBD) certificate", "ESM": "Ex-Serviceman certificate", "DOMICILE": "Domicile certificate",
}
_APPLIES = {
    "CATEGORY_OBC": "OBC ummeedwar", "CATEGORY_EWS": "EWS ummeedwar", "CATEGORY_SC": "SC ummeedwar", "CATEGORY_ST": "ST ummeedwar",
    "CATEGORY_SC_ST_OBC": "SC/ST/OBC ummeedwar", "CATEGORY_RESERVED": "aarakshit varg ke ummeedwar", "PWBD": "PwBD ummeedwar",
    "ESM": "Ex-Servicemen", "AGE_RELAXATION": "aayu chhoot maangne wale", "GOVT_SERVICE": "sarkari seva mein karyarat",
    "CENTRAL_GOVT_EMPLOYEE": "Kendriya sarkari karmachari", "NAME_CHANGE": "jinka naam badla hai",
    "IF_CLAIMED": "jo daava karein", "ALWAYS": "sabhi ummeedwar",
}
_DOCUMENTS = {
    "DATE_OF_BIRTH_PROOF": "Janm tithi ka praman", "MATRICULATION_CERTIFICATE": "10th / Matriculation certificate",
    "EDUCATION_CERTIFICATE": "Shaikshik yogyata ka certificate", "EQUIVALENCE_ORDER": "Samkaksh (equivalent) qualification ka order",
    "EXPERIENCE_CERTIFICATE": "Anubhav (experience) certificate", "CASTE_CERTIFICATE": "Jaati / category certificate",
    "OBC_NON_CREAMY_LAYER_DECLARATION": "OBC non-creamy layer declaration", "DISABILITY_CERTIFICATE": "Divyangta (PwBD) certificate",
    "ESM_DOCUMENTS": "Ex-Serviceman documents", "AGE_RELAXATION_CERTIFICATE": "Aayu chhoot ka certificate",
    "CENTRAL_GOVT_EMPLOYEE_CERTIFICATE": "Kendriya sarkari karmachari certificate",
    "NO_OBJECTION_CERTIFICATE": "No Objection Certificate (NOC)", "GOVT_SERVICE_DECLARATION": "Sarkari seva ki declaration",
    "NAME_CHANGE_DOCUMENTS": "Naam badalne ke documents", "OTHER_CLAIM_CERTIFICATES": "Anya daavon ke certificates",
    "OTHER_CLAIMS": "Anya daavon ke praman",
}
_UPLOADS = {"PHOTOGRAPH": "Photograph", "SIGNATURE": "Signature", "NOT_ACCEPTED": "Ye documents upload na karein"}
_FEE_RULES = {"NO_REFUND": "Fee wapas nahi hogi", "NO_FEE_REJECTED": "Bina fee ke aavedan reject honge",
              "PAY_BY_CLOSING_DATE": "Fee closing date tak bharni hai"}
_EXEMPT = {"FEMALE": "Mahilayein", "SC": "SC", "ST": "ST", "PWBD": "PwBD", "ESM": "Ex-Servicemen"}

# ---------- small helpers ----------

def _humanize(code: str) -> str:
    return code.replace("_", " ").capitalize()

def _ist(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    dt = dt.astimezone(IST) if dt.tzinfo else dt
    return dt.strftime("%d %b %Y") if (dt.hour, dt.minute) == (0, 0) else dt.strftime("%d %b %Y, %H:%M IST")

def _day(value: Optional[str]) -> Optional[str]:
    return date.fromisoformat(value).strftime("%d %b %Y") if value else None

def _citation(page: Optional[int], quote: Optional[str], url: Optional[str]) -> Optional[Dict[str, Any]]:
    if page is None and not quote:
        return None
    return {"page": page, "quote": quote, "document_url": url}

def _item(label: str, detail: Optional[str], citation: Optional[Dict[str, Any]], status: Optional[str] = None) -> Dict[str, Any]:
    return {"label": label, "detail": detail, "status": status, "citation": citation}

def _worst(statuses: Iterable[Optional[str]]) -> Optional[str]:
    present = set(s for s in statuses if s)
    return next((s for s in _SEVERITY if s in present), None)

def _join(*parts: Optional[str]) -> Optional[str]:
    text = "; ".join(p for p in parts if p)
    return text or None

def _value(r) -> Any:
    try:
        return json.loads(r.value)
    except (json.JSONDecodeError, TypeError):
        return r.value

async def _urls(session: AsyncSession, ids: Iterable[Optional[uuid.UUID]]) -> Dict[uuid.UUID, Optional[str]]:
    ids = {i for i in ids if i}
    return dict((await session.execute(select(RawDocument.id, RawDocument.url).where(RawDocument.id.in_(ids)))).all()) if ids else {}

def _status(start: Optional[datetime], deadline: Optional[datetime], cancelled: bool, now: datetime) -> str:
    if cancelled:
        return "CLOSED"
    if start is not None and now < start:
        return "NOT_YET_OPEN"
    if deadline is None:
        return "UNKNOWN"
    return "OPEN" if now <= deadline else "CLOSED"

# ---------- eligibility ----------

def _eligibility_detail(req_type: str, v: Any) -> Optional[str]:
    if isinstance(v, str):
        v = " ".join(v.split())
        return v if len(v) <= 400 else v[:399].rstrip() + "…"
    if not isinstance(v, dict):
        return None
    cats = f" ({', '.join(v['categories'])})" if v.get("categories") else ""
    if req_type == "AGE_MAX" and v.get("max_age") is not None:
        span = f"{v['min_age']}-{v['max_age']} saal" if v.get("min_age") is not None else f"adhiktam {v['max_age']} saal"
        return f"{v.get('category') or 'Sabhi'}: {span}"
    if req_type == "AGE_RELAXATION" and v.get("years") is not None:
        return f"{v.get('category') or 'Sabhi'}: +{v['years']} saal"
    if req_type == "AGE_RELAXATION_RULE" and v.get("basis"):
        how = f"+{v['years']} saal" if v.get("years") is not None else (f"{v['max_age']} saal tak" if v.get("max_age") else None)
        return f"{_humanize(v['basis'])}{cats}" + (f": {how}" if how else "")
    if req_type == "CITIZENSHIP" and v.get("accepted"):
        return "Manya: " + ", ".join(_humanize(a) for a in v["accepted"])
    if req_type == "PWBD_SUITABILITY":
        fit = [k for k, s in v.items() if isinstance(s, dict) and s.get("suitable")]
        return ("Upyukt divyangta shreniyan: " + ", ".join(fit)) if fit else None
    return None

# The row types each hard check judges. An outcome naming no row (AGE UNKNOWN: date of birth, category or crucial date
# missing) is about all of them, so every row of those types it was judged on carries it
_CHECK_TYPES = {"AGE": ("AGE_MAX",), "EDUCATION": ("EDUCATION",), "EXPERIENCE": ("EXPERIENCE",), "CITIZENSHIP": ("CITIZENSHIP",),
                "GOVT_SERVICE": ("GOVT_SERVICE",)}

def _outcome_statuses(outcomes: Sequence[rules.RuleOutcome], reqs: Sequence[rules.Requirement], mapping: Dict[str, str]) -> Dict[Any, str]:
    """Requirement row id -> the worst status of the outcomes about it; `reqs` are the rows the outcomes were judged on."""
    out: Dict[Any, List[str]] = {}
    for o in outcomes:
        if o.outcome not in mapping:
            continue
        ids = [o.requirement_id] if o.requirement_id is not None else             [r.id for r in reqs if r.req_type in _CHECK_TYPES.get(o.check, ())]
        for i in ids:
            out.setdefault(i, []).append(mapping[o.outcome])
    return {k: _worst(v) for k, v in out.items()}

def _eligibility_items(rows: Sequence, statuses: Dict[Any, str], urls: Dict[uuid.UUID, Optional[str]], default_url: Optional[str],
                       posts: Dict[uuid.UUID, ExamPost], personal: bool) -> List[Dict[str, Any]]:
    """One item per (post, requirement type, quote): e.g. the five category rows of one age-limit sentence are one item."""
    order = list(_ELIGIBILITY)
    post_order = {pid: i for i, pid in enumerate(sorted(posts, key=lambda pid: (posts[pid].source_page or 0, posts[pid].post_key)))}
    groups: Dict[Tuple, List] = {}
    for r in rows:
        if r.req_type in _NOT_ELIGIBILITY:
            continue
        post_id = getattr(r, "post_id", None)
        groups.setdefault((post_id, r.req_type, r.source_excerpt or str(r.id)), []).append(r)

    def sort_key(key):
        post_id, req_type, _ = key
        return (-1 if post_id is None else post_order.get(post_id, 10 ** 6),
                order.index(req_type) if req_type in order else len(order), groups[key][0].source_page or 0)

    items = []
    for key in sorted(groups, key=sort_key):
        post_id, req_type, _ = key
        group = groups[key]
        label = _ELIGIBILITY.get(req_type, _humanize(req_type))
        post = posts.get(post_id) if post_id else None
        if post is not None:
            label += f" — {post.post_name}" + (f" ({post.department})" if post.department else "")
        details = []
        for r in group:
            d = _eligibility_detail(req_type, _value(r))
            if d and d not in details:
                details.append(d)
        first = group[0]
        url = urls.get(first.raw_document_id) if first.raw_document_id else default_url
        status = _worst(statuses.get(r.id) for r in group) if personal else None
        items.append(_item(label, _join(*details), _citation(first.source_page, first.source_excerpt, url or default_url), status))
    return items

# ---------- notice requirement sections ----------

def _no_validity(value: Dict[str, Any]) -> bool:
    return not any(value.get(k) for k in ("crucial_date", "issued_on_or_after", "issued_on_or_before", "financial_year"))

def _clauses_quote(r: ApplicationRequirement) -> Optional[str]:
    """A certificate row's quote, clause by clause: a clause on another page than the first is marked with its own
    page (" | p.10: ..."), as post-wise rows mark theirs. Rows without stored clauses keep their excerpt."""
    v = _value(r)
    clauses = v.get("clauses") if isinstance(v, dict) else None
    if not clauses:
        return r.source_excerpt
    first = clauses[0]["page"]
    return " | ".join([clauses[0]["quote"]] + [(f"p.{c['page']}: " if c["page"] != first else "") + c["quote"] for c in clauses[1:]])

def _certificate_items(rows: Sequence[ApplicationRequirement], cite, outcomes: Optional[Dict[Any, rules.RuleOutcome]]) -> List[Dict[str, Any]]:
    order = list(_CERTIFICATES)
    items = []
    for r in sorted((r for r in rows if r.req_type == "CERTIFICATE"), key=lambda r: (order.index(r.code) if r.code in order else len(order), r.code)):
        v = _value(r) if isinstance(_value(r), dict) else {}
        detail = _join(
            f"Kis par lagu: {_APPLIES.get(r.applies_when, _humanize(r.applies_when))}",
            f"Crucial date: {_day(v.get('crucial_date'))}" if v.get("crucial_date") else None,
            f"Is tithi ko ya baad jaari: {_day(v.get('issued_on_or_after'))}" if v.get("issued_on_or_after") else None,
            f"Is tithi tak jaari: {_day(v.get('issued_on_or_before'))}" if v.get("issued_on_or_before") else None,
            f"Financial year (aay): {v['financial_year']}" if v.get("financial_year") else None,
            f"Format: {v['format_note']}" if v.get("format_note") else None,
            # A fact about the notice, not a verdict: certificate_check decides what a silent notice means (closing date)
            "Validity: notice mein koi tithi nahi di gayi" if _no_validity(v) else None,
        )
        status = None
        if outcomes is not None:
            # certificate_check's own status, unchanged (it already applies the cited rules for a notice silent on dates)
            o = outcomes.get(r.id)
            status = _CERT.get(o.outcome) if o else None
            why = None if o is None else ("aap par lagu nahi: " if o.outcome == certificates.NOT_APPLICABLE else "") + o.rule
            detail = _join(detail, f"Aapke liye: {why}" if why else None)
        citation = cite(r)
        if citation is not None:
            citation["quote"] = _clauses_quote(r)
        items.append(_item(_CERTIFICATES.get(r.code, _humanize(r.code)), detail, citation, status))
    return items

def _fee_items(rows: Sequence[ApplicationRequirement], cite, fee: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    applies = {(c["page"], c["excerpt"]) for c in (fee or {}).get("citations", [])}
    if fee is None:
        status, why = None, None
    elif fee["status"] == "EXEMPT":
        status, why = OK, f"aapko fee nahi deni ({fee['reason']})"
    elif fee["status"] == "PAY":
        amount = f" ₹{fee['amount']}" if fee.get("amount") is not None else ""
        status, why = OK, f"aapko fee{amount} deni hai ({fee['reason']})"
    else:
        status, why = UNKNOWN, fee["reason"]

    def personal(r, detail):
        if fee is None or (r.source_page, r.source_excerpt) not in applies:
            return detail, None  # not the fee row (or exemption clause) application_prep decided on for this profile
        return _join(detail, f"Aapke liye: {why}"), status

    rank = {"FEE": 0, "FEE_EXEMPTION": 1, "PAYMENT_MODE": 2, "FEE_RULE": 3}
    items = []
    for r in sorted((r for r in rows if r.req_type in rank), key=lambda r: rank[r.req_type]):
        v = _value(r)
        if r.req_type == "FEE":
            label = "Application fee" + (f" ({v['category']})" if v.get("category") else "")
            amount = f"₹{v['amount']}" if v.get("currency") == "INR" else f"{v.get('amount')} {v.get('currency') or ''}".strip()
            detail, st = personal(r, amount)
        elif r.req_type == "FEE_EXEMPTION":
            label = "Fee maafi (exemption)"
            detail, st = personal(r, "Fee nahi lagegi: " + ", ".join(_EXEMPT.get(g, g) for g in v.get("exempt", [])))
        elif r.req_type == "PAYMENT_MODE":
            label, detail, st = "Bhugtan ka tareeka (payment mode)", ", ".join(_humanize(m) for m in v.get("modes", [])) or None, None
        else:
            label, detail, st = _FEE_RULES.get(r.code, _humanize(r.code)), None, None
        items.append(_item(label, detail, cite(r), st))
    return items

def _upload_detail(code: str, v: Any) -> Optional[str]:
    if not isinstance(v, dict):
        return None
    if code == "NOT_ACCEPTED" or v.get("documents"):
        return ", ".join(v.get("documents") or []) or None
    if v.get("text") and len(v) == 1:
        return " ".join(v["text"].split())
    size = (f"{v['min_kb']}-{v['max_kb']} KB" if v.get("min_kb") is not None and v.get("max_kb") is not None
            else f"adhiktam {v['max_kb']} KB" if v.get("max_kb") is not None else None)
    return _join(
        f"Format: {'/'.join(v['formats'])}" if v.get("formats") else None, f"Size: {size}" if size else None,
        f"Naap: {v['width_cm']} cm x {v['height_cm']} cm" if v.get("width_cm") and v.get("height_cm") else None,
        f"Pixels: {v['width_px']} x {v['height_px']}" if v.get("width_px") and v.get("height_px") else None,
        f"Background: {v['background']}" if v.get("background") else None,
        "Photo par tareekh honi chahiye" if v.get("date_on_photo") else None,
        "Photo application ke dauraan live capture hogi" if v.get("live_capture") else None,
    )

def _sections(rows: Sequence[ApplicationRequirement], cite, checklist: Optional[Dict[str, Any]],
              cert_outcomes: Optional[Dict[Any, rules.RuleOutcome]], deadline_item: Optional[Dict[str, Any]],
              eligibility: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {k: [] for k, _ in SECTIONS}
    out["eligibility"] = eligibility

    dates = sorted((r for r in rows if r.req_type == "KEY_DATE"), key=lambda r: _value(r)["at"])
    for r in dates:
        if r.code == "APPLICATION_END" and deadline_item is not None:
            out["dates"].append(deadline_item)
            deadline_item = None
            continue
        out["dates"].append(_item(_KEY_DATES.get(r.code, _humanize(r.code)), _ist(_value(r)["at"]), cite(r)))
    if deadline_item is not None:
        out["dates"].append(deadline_item)

    out["certificates"] = _certificate_items(rows, cite, cert_outcomes)
    out["fee"] = _fee_items(rows, cite, checklist["fee"] if checklist else None)

    upload_docs = [r for r in rows if r.req_type == "DOCUMENT" and r.code in ("PHOTOGRAPH", "SIGNATURE") and "item" not in (_value(r) or {})]
    for r in [r for r in rows if r.req_type == "UPLOAD_RULE"] + upload_docs:
        out["uploads"].append(_item(_UPLOADS.get(r.code, _humanize(r.code)), _upload_detail(r.code, _value(r)), cite(r)))

    for r in (r for r in rows if r.req_type == "PHOTO_ID"):
        v = _value(r)
        out["photo_id"].append(_item("Photo ID card (inmein se ek)", _join(
            ", ".join(v.get("accepted") or []) or None, "Aadhaar use karne ki salah di gayi hai" if v.get("aadhaar_advised") else None), cite(r)))

    personal = {d["code"]: d for d in checklist["documents"]} if checklist else {}
    upload_ids = {r.id for r in upload_docs}
    docs = [r for r in rows if r.req_type == "DOCUMENT" and r.id not in upload_ids]
    docs.sort(key=application_prep.document_order(docs))
    for r in docs:
        v = _value(r)
        label = _DOCUMENTS.get(r.code) or (f"Document ({v['item']})" if v.get("item") else _humanize(r.code))
        detail, status = (" ".join(v.get("text", "").split()) or None), None
        p = personal.get(r.code)
        if p is not None:
            status = _CHECKLIST.get(p["status"])
            why = p["reason"] if p["status"] != application_prep.NOT_REQUIRED else f"aapke liye zaroori nahi ({p['reason']})"
            detail = _join(detail, f"Aapke liye: {why}")
        out["documents"].append(_item(label, detail, cite(r), status))

    for r in (r for r in rows if r.req_type == "APPLICATION_PORTAL"):
        out["portal"].append(_item("Online aavedan ki website", _value(r).get("url"), cite(r)))
    return out

# ---------- assembly ----------

async def _common(session: AsyncSession, *, kind: str, record, title: str, organization: Optional[str], advt: Optional[str],
                  start: Optional[datetime], deadline: Optional[datetime], cancelled: bool, rows: List[ApplicationRequirement],
                  eligibility: List[Dict[str, Any]],
                  checklist: Optional[Dict[str, Any]], cert_outcomes, deadline_ev: Optional[change_log.Evidence],
                  attribution_out, now: Optional[datetime]) -> Dict[str, Any]:
    urls = await _urls(session, [r.raw_document_id for r in rows] + [record.raw_document_id])
    document_url = urls.get(record.raw_document_id) or record.notification_url

    def cite(r: ApplicationRequirement) -> Optional[Dict[str, Any]]:
        return _citation(r.source_page, r.source_excerpt, urls.get(r.raw_document_id))

    warnings: List[str] = []
    stated_end = next((_value(r)["at"] for r in rows if r.req_type == "KEY_DATE" and r.code == "APPLICATION_END"), None)
    deadline_item = None
    if deadline is not None and (stated_end is None or datetime.fromisoformat(stated_end) != deadline):
        # The record's closing date is not one the general instructions state: none stated (per-post dates, Advt 11/2026),
        # or a later official notice changed it. Shown with the document and words it stands on now (change_log)
        if deadline_ev is not None and (deadline_ev.page is not None or deadline_ev.quote):
            deadline_item = _item(_KEY_DATES["APPLICATION_END"], _ist(deadline.isoformat()),
                                  _citation(deadline_ev.page, deadline_ev.quote, deadline_ev.url))
        if stated_end is not None:
            warnings.append(f"the closing date on record ({_ist(deadline.isoformat())}) differs from the one in the notice's general "
                            f"instructions ({_ist(stated_end)}); the cited source of the closing date applies")
    doc_id = rows[0].raw_document_id if rows else record.raw_document_id
    warnings += await application_prep.parse_warnings(session, doc_id)
    if not rows:
        warnings.append(f"no application instructions extracted from this {kind}'s notice; read the official notification")
    elif not any(r.req_type == "APPLICATION_PORTAL" for r in rows):
        warnings.append("the application website was not extracted from the notice; see the official notification")
    if cancelled:
        warnings.append(f"this {kind} was cancelled by an official notice")

    if start is None:
        # The record's own opening date first (as parsed with the notice); else the one the general instructions state
        stated_start = next((_value(r)["at"] for r in rows if r.req_type == "KEY_DATE" and r.code == "APPLICATION_START"), None)
        start = datetime.fromisoformat(stated_start) if stated_start else None
        start = start if start is None or start.tzinfo else start.replace(tzinfo=IST)

    sections = _sections(rows, cite, checklist, cert_outcomes, deadline_item, eligibility)
    return {
        "kind": kind, "id": str(record.id), "title": title, "organization": organization, "advertisement_number": advt,
        "document_url": document_url, "status": _status(start, deadline, cancelled, now or datetime.now(timezone.utc)),
        "application_start": start.isoformat() if start else None, "deadline": deadline.isoformat() if deadline else None,
        "sections": [{"key": k, "title": t, "items": sections[k]} for k, t in SECTIONS],
        "warnings": warnings, "disclaimer": DISCLAIMER, "attribution": attribution_out,
    }

async def _certificate_outcomes(session: AsyncSession, snapshot: rules.ProfileSnapshot, rows: Sequence[ApplicationRequirement],
                                closing: Optional[datetime], now: Optional[datetime]) -> Dict[Any, rules.RuleOutcome]:
    reqs = [rules.Requirement(r.id, "CERTIFICATE", _value(r), r.source_page) for r in rows if r.req_type == "CERTIFICATE"]
    held = await certificate_check.load_held(session, snapshot.profile_version_id)
    outcomes = certificate_check.check_certificates(snapshot, reqs, held, closing.astimezone(IST).date() if closing else None,
                                                    (now or datetime.now(timezone.utc)).astimezone(IST).date())
    return {o.requirement_id: o for o in outcomes}

async def vacancy_page(session: AsyncSession, vacancy_id: uuid.UUID, user_id: Optional[uuid.UUID] = None,
                       profile_version_id: Optional[uuid.UUID] = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    vacancy = await session.get(Vacancy, vacancy_id)
    if vacancy is None:
        raise PrepInputError("vacancy not found")
    personal = user_id is not None and profile_version_id is not None
    # Ownership of the profile version is checked here (another user's version is "not found")
    checklist = await application_prep.build_checklist(session, user_id, vacancy.id, profile_version_id) if personal else None
    rows = await application_prep.requirements_for_vacancy(session, vacancy)
    req_rows = (await session.execute(select(VacancyRequirement).where(
        VacancyRequirement.vacancy_id == vacancy.id, VacancyRequirement.superseded_at.is_(None))
        .order_by(VacancyRequirement.source_page, VacancyRequirement.created_at))).scalars().all()
    statuses, cert_outcomes = {}, None
    if personal:
        snapshot = await engine.load_profile_snapshot(session, profile_version_id)
        reqs = await engine.load_requirements(session, vacancy.id)
        statuses = _outcome_statuses(engine.hard_outcomes(snapshot, reqs, vacancy), reqs, _HARD)
        cert_outcomes = await _certificate_outcomes(session, snapshot, rows, vacancy.application_deadline, now)
    urls = await _urls(session, [r.raw_document_id for r in req_rows] + [vacancy.raw_document_id])
    eligibility = _eligibility_items(req_rows, statuses, urls, urls.get(vacancy.raw_document_id) or vacancy.notification_url, {}, personal)
    _, deadline_ev = await change_log.vacancy_current_evidence(session, vacancy, "application_deadline")
    return await _common(
        session, kind="vacancy", record=vacancy, title=vacancy.post_name, organization=vacancy.organization,
        advt=vacancy.advertisement_number, start=vacancy.application_start, deadline=vacancy.application_deadline,
        cancelled=vacancy.status == "CANCELLED",
        rows=rows, eligibility=eligibility, checklist=checklist, cert_outcomes=cert_outcomes, deadline_ev=deadline_ev,
        attribution_out=(await attribution.for_vacancies(session, [vacancy]))[0], now=now)

async def exam_page(session: AsyncSession, exam_id: uuid.UUID, user_id: Optional[uuid.UUID] = None,
                    profile_version_id: Optional[uuid.UUID] = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    exam = await session.get(Exam, exam_id)
    if exam is None:
        raise PrepInputError("exam not found")
    personal = user_id is not None and profile_version_id is not None
    checklist = await application_prep.build_exam_checklist(session, user_id, exam.id, profile_version_id) if personal else None
    rows = await application_prep.requirements_for_exam(session, exam)
    req_rows = (await session.execute(select(ExamRequirement).where(
        ExamRequirement.exam_id == exam.id, ExamRequirement.superseded_at.is_(None))
        .order_by(ExamRequirement.source_page, ExamRequirement.created_at))).scalars().all()
    post_ids = {r.post_id for r in req_rows if r.post_id}
    posts = {p.id: p for p in (await session.execute(select(ExamPost).where(ExamPost.id.in_(post_ids)))).scalars()} if post_ids else {}
    statuses, cert_outcomes = {}, None
    if personal:
        snapshot = await engine.load_profile_snapshot(session, profile_version_id)
        gender = (await session.execute(select(PersonalInfo.gender).where(
            PersonalInfo.profile_version_id == profile_version_id))).scalar_one_or_none()
        judge = exam_engine.post_judge(session, exam, snapshot, gender)
        post_wise = await exam_engine.posts_with_own_rows(session, exam.id)
        # Exam-level rows as evaluate_exam judges them (best post for post-wise notices); each post's own rows by that post
        overall, judged = await exam_engine.best_post(post_wise, judge) if post_wise else await judge(None)
        statuses = _outcome_statuses(overall, judged, _HARD)
        if post_wise:
            post_rows = {r.id for r in req_rows if r.post_id is not None}
            statuses = {k: v for k, v in statuses.items() if k not in post_rows}
        for p in post_wise:
            own = {r.id for r in req_rows if r.post_id == p.id}
            outcomes, judged = await judge(p)
            statuses.update({k: v for k, v in _outcome_statuses(outcomes, judged, _HARD).items() if k in own})
        cert_outcomes = await _certificate_outcomes(session, snapshot, rows, exam.application_end, now)
    urls = await _urls(session, [r.raw_document_id for r in req_rows] + [exam.raw_document_id])
    eligibility = _eligibility_items(req_rows, statuses, urls, urls.get(exam.raw_document_id) or exam.notification_url, posts, personal)
    _, deadline_ev = await change_log.exam_current_evidence(session, exam, "application_end")
    return await _common(
        session, kind="exam", record=exam, title=exam.name, organization=exam.conducting_body, advt=exam.external_ref,
        start=exam.application_start, deadline=exam.application_end, cancelled=exam.status == "CANCELLED", rows=rows, eligibility=eligibility,
        checklist=checklist, cert_outcomes=cert_outcomes, deadline_ev=deadline_ev,
        attribution_out=(await attribution.for_exams(session, [exam]))[0], now=now)

# ---------- printable page ----------

_STATUS_LABELS = {OK: "Theek hai", FAILS: "Yogya nahi", NEEDS_DOCUMENT: "Document chahiye", CANNOT_DECIDE: "Tay nahi ho saka",
                  UNKNOWN: "Profile mein jaankari nahi"}
_PAGE_STATUS = {"OPEN": "Aavedan khula hai", "CLOSED": "Aavedan band ho chuka hai", "NOT_YET_OPEN": "Aavedan abhi shuru nahi hua",
                "UNKNOWN": "Aavedan ki sthiti pata nahi"}
_CSS = """
:root{--fg:#1d1d1f;--muted:#5f6368;--line:#d9d9de;--bg:#fff;--ok:#1e7a3a;--bad:#b3261e;--warn:#8a5a00;--info:#2a5db0}
*{box-sizing:border-box}body{margin:0;padding:24px 16px;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:860px;margin:0 auto}h1{font-size:1.5rem;margin:0 0 4px}h2{font-size:1.1rem;margin:28px 0 8px;padding-bottom:4px;border-bottom:2px solid var(--line)}
.meta{color:var(--muted);margin:0 0 12px}.pill{display:inline-block;padding:2px 10px;border-radius:999px;border:1px solid currentColor;font-size:.85rem;font-weight:600}
.OPEN{color:var(--ok)}.CLOSED{color:var(--bad)}.NOT_YET_OPEN{color:var(--warn)}.UNKNOWN{color:var(--muted)}
ul.items{list-style:none;margin:0;padding:0}li.item{padding:10px 0;border-bottom:1px solid var(--line);break-inside:avoid}
.label{font-weight:600}.detail{margin:2px 0}.s-OK{color:var(--ok)}.s-FAILS{color:var(--bad)}.s-NEEDS_DOCUMENT{color:var(--warn)}
.s-CANNOT_DECIDE{color:var(--warn)}.s-UNKNOWN{color:var(--muted)}blockquote{margin:6px 0 0;padding:4px 10px;border-left:3px solid var(--line);color:var(--muted);font-size:.9rem}
a{color:var(--info)}.warn{background:#fff7e0;border:1px solid #f0d58a;padding:8px 12px;border-radius:6px}.empty{color:var(--muted);font-style:italic}
footer{margin-top:32px;color:var(--muted);font-size:.85rem}
@media print{body{padding:0;font-size:12px}a{color:inherit}a[href]::after{content:" (" attr(href) ")";font-size:.8em;word-break:break-all}.noprint{display:none}}
"""

def _e(value: Any) -> str:
    return html.escape(str(value), quote=True)

def _link(url: Optional[str], page: Optional[int]) -> Optional[str]:
    if not url or not url.lower().startswith(("https://", "http://")):
        return None
    return f"{url}#page={page}" if page else url

def render_html(page: Dict[str, Any]) -> str:
    """The same data as the JSON, as one self-contained printable page (no scripts, no external files)."""
    deadline, opens = _ist(page["deadline"]), _ist(page.get("application_start"))
    head = [f"<h1>{_e(page['title'])}</h1>",
            "<p class=meta>" + " · ".join(_e(x) for x in (page.get("organization"), f"Advt / File: {page['advertisement_number']}"
                                                        if page.get("advertisement_number") else None) if x) + "</p>",
            f"<p><span class=\"pill {_e(page['status'])}\">{_e(_PAGE_STATUS[page['status']])}</span>"
            + (f" &nbsp;Aavedan shuru: <strong>{_e(opens)}</strong>" if opens and page["status"] == "NOT_YET_OPEN" else "")
            + (f" &nbsp;Antim tithi: <strong>{_e(deadline)}</strong>" if deadline else "") + "</p>"]
    doc = _link(page.get("document_url"), None)
    if doc:
        head.append(f"<p>Official notification: <a href=\"{_e(doc)}\">{_e(page['document_url'])}</a></p>")
    if page["warnings"]:
        head.append("<div class=warn><strong>Dhyan dein:</strong><ul>" + "".join(f"<li>{_e(w)}</li>" for w in page["warnings"]) + "</ul></div>")
    body = []
    for s in page["sections"]:
        body.append(f"<section><h2>{_e(s['title'])}</h2>")
        if not s["items"]:
            body.append("<p class=empty>Notice se is section ki jaankari nahi nikali ja saki; official notification dekhein.</p></section>")
            continue
        body.append("<ul class=items>")
        for it in s["items"]:
            parts = [f"<div class=label>{_e(it['label'])}"]
            if it.get("status"):
                parts.append(f" <span class=\"pill s-{_e(it['status'])}\">{_e(_STATUS_LABELS.get(it['status'], it['status']))}</span>")
            parts.append("</div>")
            if it.get("detail"):
                parts.append(f"<div class=detail>{_e(it['detail'])}</div>")
            c = it.get("citation")
            if c:
                if c.get("quote"):
                    parts.append(f"<blockquote lang=en>&ldquo;{_e(c['quote'])}&rdquo;</blockquote>")
                href = _link(c.get("document_url"), c.get("page"))
                where = f"Official document, page {c['page']}" if c.get("page") else "Official document"
                parts.append(f"<div class=meta><a href=\"{_e(href)}\">{_e(where)}</a></div>" if href else f"<div class=meta>{_e(where)}</div>")
            body.append(f"<li class=item>{''.join(parts)}</li>")
        body.append("</ul></section>")
    a = page.get("attribution")
    a = a.model_dump() if hasattr(a, "model_dump") else a
    source = ""
    if a:
        retrieved = a.get("document_retrieved_at")
        retrieved = retrieved.isoformat() if isinstance(retrieved, datetime) else retrieved
        source = (f"<p>Source: {_e(a['source_name'])} ({_e(a['organization'])})"
                  + (f", document retrieved {_e(_ist(retrieved))}" if retrieved else "") + "</p>")
    foot = (f"<footer>{source}<p>Official notification hi maanya hai. Har jaankari ko official notification se milaa lein.</p>"
            f"<p lang=en>{_e(page['disclaimer'])}</p></footer>")
    return ("<!doctype html><html lang=hi><head><meta charset=utf-8><meta name=viewport content=\"width=device-width,initial-scale=1\">"
            f"<title>{_e(page['title'])} — apply checklist</title><style>{_CSS}</style></head><body><main>"
            + "".join(head) + "".join(body) + foot + "</main></body></html>")
