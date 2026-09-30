"""
Application preparation: stores what an advertisement asks of applicants (ApplicationRequirement rows,
from backend/parsing/upsc_application.py) and turns it into a checklist for one profile version.

Rules:
  - Every checklist item is an advertisement row with its page/section/excerpt; nothing is added that the
    advertisement does not state (e.g. no EWS certificate item unless the document lists one).
  - Personalisation only reads the profile's own fields: category, gender, PwBD, ex-serviceman, Central
    Government employee, experience and education-equivalence entries. A field the profile leaves empty gives
    UNKNOWN, never a guess.
  - Fee: EXEMPT when the profile matches a group in the advertisement's exemption clause, PAY when every field
    that clause depends on is known and none matches, otherwise UNKNOWN.
"""
import json
import uuid
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.documents.pdf_text import PageText
from govcareeros.backend.eligibility import rules
from govcareeros.backend.models.application import ApplicationRequirement
from govcareeros.backend.models.artifact import RawDocument
from govcareeros.backend.models.source import GovernmentSource
from govcareeros.backend.models.user import Education, Experience, PersonalInfo, ProfileVersion
from govcareeros.backend.models.vacancy import Vacancy, VacancyRequirement
from govcareeros.backend.parsing import upsc_application as ua
from govcareeros.backend.parsing.upsc_advertisement import IST

class PrepInputError(Exception):
    """Vacancy or profile version not found for this user (reported as 404)."""

REQUIRED, NOT_REQUIRED, IF_APPLICABLE, UNKNOWN = "REQUIRED", "NOT_REQUIRED", "IF_APPLICABLE", "UNKNOWN"

async def store_application_requirements(
    session: AsyncSession, source: GovernmentSource, raw: RawDocument, pages: List[PageText], advertisement_number: Optional[str],
) -> ua.ParsedApplicationInfo:
    """Idempotent per (document, parser version); a newer parser replaces the document's rows."""
    info = ua.parse_application_info(pages)
    current = (await session.execute(select(ApplicationRequirement.parser_version).where(
        ApplicationRequirement.raw_document_id == raw.id).limit(1))).scalar_one_or_none()
    if current == info.parser_version or not info.items:
        return info
    await session.execute(delete(ApplicationRequirement).where(ApplicationRequirement.raw_document_id == raw.id))
    for item in info.items:
        session.add(ApplicationRequirement(
            source_id=source.id, raw_document_id=raw.id, advertisement_number=advertisement_number,
            req_type=item.req_type, code=item.code, applies_when=item.applies_when,
            value=json.dumps(item.value, ensure_ascii=False), source_page=item.provenance.page,
            source_section=item.provenance.section, source_excerpt=item.provenance.excerpt, parser_version=info.parser_version,
        ))
    await session.flush()
    return info

async def requirements_for_vacancy(session: AsyncSession, vacancy: Vacancy) -> List[ApplicationRequirement]:
    """Rows of the document the vacancy was parsed from; else the latest parse of the same advertisement number."""
    rows = []
    if vacancy.raw_document_id:
        rows = (await session.execute(select(ApplicationRequirement).where(
            ApplicationRequirement.raw_document_id == vacancy.raw_document_id))).scalars().all()
    if not rows and vacancy.advertisement_number:
        latest = (await session.execute(select(ApplicationRequirement.raw_document_id).where(
            ApplicationRequirement.source_id == vacancy.source_id,
            ApplicationRequirement.advertisement_number == vacancy.advertisement_number,
        ).order_by(ApplicationRequirement.created_at.desc()).limit(1))).scalar_one_or_none()
        if latest:
            rows = (await session.execute(select(ApplicationRequirement).where(
                ApplicationRequirement.raw_document_id == latest))).scalars().all()
    return list(rows)

def _gender(value: Optional[str]) -> Optional[str]:
    v = (value or "").strip().lower()
    if v in ("female", "f", "woman", "women"):
        return "FEMALE"
    if v in ("male", "m", "man", "men"):
        return "MALE"
    return None  # not stated, or a value the exemption clause does not name

def _decide(when: str, code: str, info: Optional[PersonalInfo], has_experience: bool, has_equivalence: bool,
            post_needs_experience: bool, crucial: Optional[str]) -> Tuple[str, str]:
    category = rules.normalize_category(info.category) if info else None
    flag = lambda name: getattr(info, name) if info else None
    if when == ua.ALWAYS:
        return REQUIRED, "required of every applicant"
    if when == ua.SC_ST_OBC:
        if category is None:
            return UNKNOWN, "category not stated in profile"
        return (REQUIRED, f"you claim {category}") if category in ("SC", "ST", "OBC") else (NOT_REQUIRED, f"category {category}")
    if when == ua.OBC:
        if category is None:
            return UNKNOWN, "category not stated in profile"
        if category != "OBC":
            return NOT_REQUIRED, f"category {category}"
        return REQUIRED, "you claim OBC: non-creamy layer status must hold on the crucial date" + (f" ({crucial})" if crucial else "")
    if when == ua.PWBD:
        pwbd = flag("is_pwbd")
        if pwbd is None:
            return UNKNOWN, "PwBD status not stated in profile"
        return (REQUIRED, "you claim PwBD") if pwbd else (NOT_REQUIRED, "not claiming PwBD")
    if when == ua.AGE_RELAXATION:
        claims = [n for n, f in (("Ex-Serviceman", "is_ex_serviceman"), ("Central Government employee", "is_central_govt_employee")) if flag(f)]
        if claims:
            return REQUIRED, "if you claim age relaxation as " + " / ".join(claims)
        if flag("is_ex_serviceman") is None or flag("is_central_govt_employee") is None:
            return UNKNOWN, "ex-serviceman / Central Government service not stated in profile"
        return IF_APPLICABLE, "only for age relaxation under a special provision/order"
    if when == ua.GOVT_SERVICE:
        cgs = flag("is_central_govt_employee")
        if cgs:
            return REQUIRED, "you are in Central Government service"
        if cgs is None:
            return UNKNOWN, "government service not stated in profile"
        return IF_APPLICABLE, "applies to any regular Government service; your profile records only Central Government service"
    if when == ua.NAME_CHANGE:
        return IF_APPLICABLE, "only if your name changed after matriculation"
    if code == "EXPERIENCE_CERTIFICATE":
        if has_experience:
            return REQUIRED, "your profile lists experience"
        return (REQUIRED, "the post's essential qualifications include experience") if post_needs_experience else (IF_APPLICABLE, "only for experience you claim")
    if code == "EQUIVALENCE_ORDER":
        return (REQUIRED, "your profile claims an equivalent qualification") if has_equivalence else (IF_APPLICABLE, "only if you claim an equivalent qualification")
    return IF_APPLICABLE, "only for claims you make"

def _fee(rows: List[ApplicationRequirement], info: Optional[PersonalInfo], cite) -> Optional[Dict[str, Any]]:
    fee = next((r for r in rows if r.req_type == "FEE"), None)
    exemption = next((r for r in rows if r.req_type == "FEE_EXEMPTION"), None)
    if fee is None and exemption is None:
        return None
    amount = json.loads(fee.value) if fee else None
    groups = json.loads(exemption.value)["exempt"] if exemption else []
    category = rules.normalize_category(info.category) if info else None
    known = {"SC": category, "ST": category, "PWBD": info.is_pwbd if info else None, "FEMALE": _gender(info.gender) if info else None}
    matched = [g for g in groups if (g in ("SC", "ST") and category == g) or (g == "PWBD" and known["PWBD"]) or (g == "FEMALE" and known["FEMALE"] == "FEMALE")]
    missing = sorted({{"SC": "category", "ST": "category", "PWBD": "PwBD status", "FEMALE": "gender"}[g] for g in groups if known[g] is None})
    if matched:
        status, reason = "EXEMPT", "exempt as " + "/".join(matched)
    elif missing:
        status, reason = UNKNOWN, "profile does not state " + ", ".join(missing)
    else:
        status, reason = "PAY", "not in an exempted group"
    return {"status": status, "reason": reason, "amount": amount["amount"] if amount else None,
            "currency": amount["currency"] if amount else None, "exempt_groups": groups,
            "citations": [cite(r) for r in (fee, exemption) if r is not None]}

async def build_checklist(session: AsyncSession, user_id: uuid.UUID, vacancy_id: uuid.UUID, profile_version_id: uuid.UUID) -> Dict[str, Any]:
    version = (await session.execute(select(ProfileVersion).where(
        ProfileVersion.id == profile_version_id, ProfileVersion.user_id == user_id))).scalar_one_or_none()
    if version is None:
        raise PrepInputError("profile version not found")
    vacancy = await session.get(Vacancy, vacancy_id)
    if vacancy is None:
        raise PrepInputError("vacancy not found")

    info = (await session.execute(select(PersonalInfo).where(PersonalInfo.profile_version_id == version.id))).scalar_one_or_none()
    has_experience = bool((await session.execute(select(Experience.id).where(Experience.profile_version_id == version.id).limit(1))).first())
    has_equivalence = bool((await session.execute(select(Education.id).where(
        Education.profile_version_id == version.id, Education.equivalence.is_not(None), Education.equivalence != "").limit(1))).first())
    post_needs_experience = bool((await session.execute(select(VacancyRequirement.id).where(
        VacancyRequirement.vacancy_id == vacancy.id, VacancyRequirement.req_type == "EXPERIENCE",
        VacancyRequirement.superseded_at.is_(None)).limit(1))).first())
    crucial = vacancy.eligibility_crucial_date.astimezone(IST).strftime("%d %b %Y %H:%M IST") if vacancy.eligibility_crucial_date else None

    rows = await requirements_for_vacancy(session, vacancy)
    urls = {}
    for doc_id in {r.raw_document_id for r in rows}:
        urls[doc_id] = (await session.get(RawDocument, doc_id)).url

    def cite(r: ApplicationRequirement) -> Dict[str, Any]:
        return {"raw_document_id": str(r.raw_document_id), "document_url": urls.get(r.raw_document_id),
                "page": r.source_page, "section": r.source_section, "excerpt": r.source_excerpt}

    def single(req_type: str) -> Optional[Dict[str, Any]]:
        r = next((r for r in rows if r.req_type == req_type), None)
        return {**json.loads(r.value), "citation": cite(r)} if r else None

    items = []
    for r in sorted((r for r in rows if r.req_type == "DOCUMENT"), key=lambda r: (json.loads(r.value).get("item") or "~", r.code)):
        status, reason = _decide(r.applies_when, r.code, info, has_experience, has_equivalence, post_needs_experience, crucial)
        items.append({"code": r.code, "applies_when": r.applies_when, "status": status, "reason": reason,
                      "text": json.loads(r.value).get("text"), "citation": cite(r)})

    return {
        "vacancy_id": str(vacancy.id), "profile_version_id": str(version.id), "advertisement_number": vacancy.advertisement_number,
        "application_deadline": vacancy.application_deadline.isoformat() if vacancy.application_deadline else None,
        "portal": single("APPLICATION_PORTAL"), "photo_id": single("PHOTO_ID"), "payment": single("PAYMENT_MODE"),
        "fee": _fee(rows, info, cite),
        "fee_rules": [{"code": r.code, **json.loads(r.value), "citation": cite(r)} for r in rows if r.req_type == "FEE_RULE"],
        "upload_rules": [{"code": r.code, **json.loads(r.value), "citation": cite(r)} for r in rows if r.req_type == "UPLOAD_RULE"],
        "documents": items,
        "warnings": [] if rows else ["no application requirements extracted for this vacancy's advertisement"],
    }
