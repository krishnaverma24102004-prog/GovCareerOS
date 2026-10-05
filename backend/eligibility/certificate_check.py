"""
Certificate section of an eligibility result (vacancy or exam), kept apart from the hard verdict.

  * CERTIFICATE requirement rows (req_type "CERTIFICATE", value = CertificateRequirement fields as JSON, quote =
    the row's source_excerpt) are judged by certificates.evaluate against the certificates of the same immutable
    profile version. A certificate is "verified" only when its profile Fact is VERIFIED by a reviewer; a certificate
    whose Facts were all REJECTED counts as not held.
  * The hard verdict is untouched: rules.overall_status (status, confidence) is computed from the hard checks first,
    and certificate rows are stored after it as evidence rows with check_name CERTIFICATE and outcome
    OK / FAILS / NEEDS_DOCUMENT / CANNOT_DECIDE / NOT_APPLICABLE. The API shows them as `certificates` with their own
    `certificate_status` (worst row) beside `status`.
  * How the two combine: `status` says whether the candidate may apply at all; `certificate_status` says whether the
    category benefit the verdict assumed (reservation, category age relaxation, fee concession) will hold up.
    FAILS means that benefit is lost (in the decided cases the candidate was moved to General, not disqualified);
    if the hard verdict relied on a category relaxation, it must then be re-checked as UR. OK with
    `verified` false is the candidate's own word until a reviewer verifies the certificate.
  * No CERTIFICATE rows extracted for the notice -> no section (nothing is assumed about certificates).
"""
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Dict, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from govcareeros.backend.eligibility import certificates, rules
from govcareeros.backend.eligibility.certificate_types import CertificateKind, CertificateRequirement, HeldCertificate
from govcareeros.backend.models.fact import Fact
from govcareeros.backend.models.user import ReservationCertificate
from govcareeros.backend.services.profile_service import CERTIFICATE_PREDICATE, CERTIFICATE_SUBJECT, certificate_fact_value

CHECK = "CERTIFICATE"
REQ_TYPE = "CERTIFICATE"
_DATE_FIELDS = ("crucial_date", "issued_on_or_after", "issued_on_or_before")

@dataclass(frozen=True)
class HeldRecord:
    held: HeldCertificate
    verified: bool
    fact_value: str

def _day(v) -> Optional[date]:
    if isinstance(v, datetime):
        return v.date()
    return date.fromisoformat(v) if isinstance(v, str) else v

def parse_requirement(req: rules.Requirement) -> Optional[CertificateRequirement]:
    """A CERTIFICATE row's JSON value -> the shared contract; None when the row cannot be read (reported, never guessed)."""
    v = req.value if isinstance(req.value, dict) else None
    try:
        kind = CertificateKind(v["kind"]) if v else None
        if kind is None:
            return None
        return CertificateRequirement(
            kind=kind, financial_year=v.get("financial_year"), format_note=v.get("format_note"),
            source_page=v.get("source_page", req.source_page), source_quote=v.get("source_quote"),
            **{k: _day(v.get(k)) for k in _DATE_FIELDS})
    except (KeyError, ValueError, TypeError):
        return None

async def load_held(session: AsyncSession, profile_version_id: uuid.UUID) -> Dict[CertificateKind, List[HeldRecord]]:
    rows = (await session.execute(select(ReservationCertificate).where(
        ReservationCertificate.profile_version_id == profile_version_id).order_by(ReservationCertificate.created_at,
                                                                               ReservationCertificate.id))).scalars().all()
    facts = (await session.execute(select(Fact).where(
        Fact.profile_version_id == profile_version_id, Fact.subject == CERTIFICATE_SUBJECT,
        Fact.predicate == CERTIFICATE_PREDICATE))).scalars().all()
    statuses: Dict[str, set] = {}
    for f in facts:
        statuses.setdefault(f.value, set()).add(f.status)
    out: Dict[CertificateKind, List[HeldRecord]] = {}
    for row in rows:
        value = certificate_fact_value(row)
        seen = statuses.get(value, set())
        if seen and seen == {"REJECTED"}:
            continue  # a reviewer rejected this certificate claim: it does not count as held
        held = HeldCertificate(kind=CertificateKind(row.kind), issue_date=_day(row.issue_date), financial_year=row.financial_year,
                               valid_until=_day(row.valid_until), issuing_authority=row.issuing_authority,
                               central_format=row.central_format)
        out.setdefault(held.kind, []).append(HeldRecord(held, "VERIFIED" in seen, value))
    return out

def claim_for(kind: CertificateKind, profile: rules.ProfileSnapshot, held: Dict[CertificateKind, List[HeldRecord]]) -> Optional[str]:
    """What the profile claims that this certificate kind supports; None when the profile does not say."""
    if kind in (CertificateKind.EWS, CertificateKind.OBC_NCL, CertificateKind.SC, CertificateKind.ST):
        return rules.normalize_category(profile.category)
    flag = {CertificateKind.PWBD: profile.is_pwbd, CertificateKind.ESM: profile.is_ex_serviceman}.get(kind)
    if kind in (CertificateKind.PWBD, CertificateKind.ESM):
        return None if flag is None else (kind.value if flag else "NONE")
    # The profile records no domicile claim; holding a domicile certificate is the only sign of one
    return CertificateKind.DOMICILE.value if held.get(kind) else None

def check_certificates(profile: rules.ProfileSnapshot, reqs: Sequence[rules.Requirement],
                       held: Dict[CertificateKind, List[HeldRecord]], closing: Optional[date],
                       as_of: Optional[date]) -> List[rules.RuleOutcome]:
    out, seen = [], set()
    for r in reqs:
        if r.req_type != REQ_TYPE or r.id in seen:
            continue
        seen.add(r.id)
        req = parse_requirement(r)
        if req is None:
            out.append(rules.RuleOutcome(CHECK, certificates.CANNOT_DECIDE, "the certificate requirement could not be read",
                                         str(r.value)[:300], "-", r.id, r.source_page))
            continue
        claimed = claim_for(req.kind, profile, held)
        records = held.get(req.kind, [])
        judged = [(certificates.evaluate(req, h.held, claimed, closing_date=closing, as_of=as_of, verified=h.verified), h)
                  for h in records] or [(certificates.evaluate(req, None, claimed, closing_date=closing, as_of=as_of), None)]
        res = certificates.best([j[0] for j in judged])
        record = next(h for j, h in judged if j is res)
        rule = res.reason + (f" [rules: {', '.join(res.rules)}]" if res.rules else "")
        requirement = f"{req.kind.value}: " + (req.source_quote or _fields(req))
        out.append(rules.RuleOutcome(
            CHECK, res.status, rule, requirement, record.fact_value if record else "no certificate of this kind recorded",
            r.id, res.source_page, match_type=("REVIEWER_VERIFIED" if res.verified else "SELF_REPORTED") if record else None))
    return out

def _fields(req: CertificateRequirement) -> str:
    parts = [f"{k} {getattr(req, k).isoformat()}" for k in _DATE_FIELDS if getattr(req, k)]
    parts += [f"financial year {req.financial_year}"] if req.financial_year else []
    parts += [f"format {req.format_note}"] if req.format_note else []
    return "; ".join(parts) or "certificate required (no conditions extracted)"

def split(evidence: Sequence) -> tuple:
    """Stored evidence rows -> (hard-check rows, certificate rows)."""
    return [e for e in evidence if e.check_name != CHECK], [e for e in evidence if e.check_name == CHECK]
