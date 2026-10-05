"""
Deterministic certificate-validity rules: does a certificate the candidate holds satisfy what one notice
states about that certificate kind? Pure functions over certificate_types; no database, no LLM.

Statuses:
  OK              every condition the notice states is satisfied by the recorded certificate
  FAILS           a stated condition is definitely not satisfied and the crucial date has passed
                  (in the decided cases this costs the category benefit: the candidate is treated as General)
  NEEDS_DOCUMENT  no (usable) certificate yet, but one can still be obtained or produced; the reason says which
  CANNOT_DECIDE   the notice is silent, a recorded field is missing, or the courts are split on the point
  NOT_APPLICABLE  the candidate does not claim the benefit this certificate supports

The notice decides. Where it is silent, only these general rules are applied, each cited from
tests/fixtures/certificate_cases.json "rules" (the test suite checks the citations match that file word for word):
  * crucial date = closing date (EWS: DoPT FAQ 21; other kinds: Sakshi Arha para 27)
  * EWS income year = financial year before the year of application (DoPT FAQ 15, worked example FAQ 23)
  * EWS certificate issued after the crucial date for a later year = major discrepancy (DoPT FAQ 21)
  * SC/ST/OBC candidates without the certificate may be appointed provisionally (DoPT O.M. 29.03.2023)
Everything else returns CANNOT_DECIDE. In particular (README "Known conflicts"): OBC-NCL / SC / ST certificates
issued late or outside a notice's window were accepted by some courts and refused by others, and PwBD
temporary certificates are contested, so those never FAIL here.

`as_of` is the day the check is made. None means the crucial date has passed (how courts look at a case);
with a date before the crucial date, a defect a fresh certificate can still cure is NEEDS_DOCUMENT, not FAILS.
A rule that holds is reported OK even for a certificate the candidate only recorded: `verified` says whether
a reviewer has checked it against the document (profile Fact VERIFIED), and the caller shows that.
"""
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Optional, Sequence, Tuple

from govcareeros.backend.eligibility.certificate_types import CertificateKind, CertificateRequirement, HeldCertificate

OK, FAILS, NEEDS_DOCUMENT, CANNOT_DECIDE, NOT_APPLICABLE = "OK", "FAILS", "NEEDS_DOCUMENT", "CANNOT_DECIDE", "NOT_APPLICABLE"
# Worst first; a summary over several certificates takes the first one present
SEVERITY = (FAILS, NEEDS_DOCUMENT, CANNOT_DECIDE, OK, NOT_APPLICABLE)

@dataclass(frozen=True)
class CitedRule:
    id: str
    reference: str
    quote: str

# Copied from tests/fixtures/certificate_cases.json "rules" (only entries with uncertain = false)
EWS_FY_PRIOR = CitedRule(
    "ews-income-fy-prior-to-application",
    "O.M. No. 43011/11/2022-Estt.(Res-II) dated 19.09.2022 (FAQ 15), restating O.M. No. 36039/1/2019-Estt(Res) dated 31.01.2019 para 4.1",
    "Income for the purpose shall include income from all sources i.e. salary, agriculture, business, profession, etc. for the financial year prior to the year")
EWS_FY_EXAMPLE = CitedRule(
    "ews-validity-year-example", "O.M. No. 43011/11/2022-Estt.(Res-II) dated 19.09.2022 (FAQ 23)",
    "the financial year for computing gross annual income of the family shall be 2021-2022 and the certificate shall be valid for 2022-2023.")
EWS_CRUCIAL_DATE = CitedRule(
    "ews-crucial-date-closing-date",
    "O.M. No. 43011/11/2022-Estt.(Res-II) dated 19.09.2022 (FAQ 21), restating O.M. dated 31.01.2019 para 5.3",
    "may be treated as the closing date for receipt of application for the post, except in cases where crucial date is fixed otherwise.")
EWS_MAJOR_DISCREPANCY = CitedRule(
    "ews-later-certificate-major-discrepancy", "O.M. No. 43011/11/2022-Estt.(Res-II) dated 19.09.2022 (FAQ 21)",
    "Income & Asset Certificates issued subsequent to the crucial date and for the later Financial Year shall be treated as major discrepancies.")
DEFAULT_CUT_OFF = CitedRule(
    "default-cut-off-last-date-of-application",
    "Sakshi Arha v. Rajasthan High Court, 2025 INSC 463, para 27 (following Bhupinderpal Singh (2000) 5 SCC 262)",
    "in case of absence of specifications in both context, the eligibility is to be adjudged in lieu of the last date of submission of applications")
PROVISIONAL_APPOINTMENT = CitedRule(
    "sc-st-obc-provisional-appointment", "O.M. No. 41034/1(ii)/2022-Estt.(Res-I) dated 29.03.2023 (restating O.M. 08.10.2015)",
    "he/she may be appointed provisionally on the basis of whatever prima-facie proof he/she is able to produce in support of his/her claim")
CITED_RULES = (EWS_FY_PRIOR, EWS_FY_EXAMPLE, EWS_CRUCIAL_DATE, EWS_MAJOR_DISCREPANCY, DEFAULT_CUT_OFF, PROVISIONAL_APPOINTMENT)

# Decided cases the courts split on (tests/fixtures/certificate_cases.json ids)
_LATE_CASTE_CERTIFICATE_SPLIT = ("courts are split on certificates issued late or outside the notice's window: refused in "
                                 "sc-2025-sakshi-arha, accepted in dhc-2025-raghvendra-singh / dhc-2026-bharath-singarappa / "
                                 "dhc-2025-amjad-khan where the status already existed")
_PWBD_TEMPORARY_SPLIT = ("a temporary disability certificate is contested: DoPT O.M. 19.05.2023 (as reproduced) refuses it for "
                         "permanent jobs, the CAT in cat-2026-samit-kumar held a valid time-bound certificate cannot be ignored")

_LABEL = {
    CertificateKind.OBC_NCL: "OBC (non-creamy layer) certificate", CertificateKind.EWS: "EWS Income & Asset Certificate",
    CertificateKind.SC: "SC certificate", CertificateKind.ST: "ST certificate", CertificateKind.PWBD: "disability certificate",
    CertificateKind.ESM: "ex-serviceman certificate", CertificateKind.DOMICILE: "domicile certificate",
}
# Claim (profile category or benefit) -> the certificate it rests on
_CLAIM_KIND = {
    "EWS": CertificateKind.EWS, "OBC": CertificateKind.OBC_NCL, "OBC-NCL": CertificateKind.OBC_NCL, "OBC_NCL": CertificateKind.OBC_NCL,
    "SC": CertificateKind.SC, "ST": CertificateKind.ST, "PWBD": CertificateKind.PWBD, "ESM": CertificateKind.ESM,
    "DOMICILE": CertificateKind.DOMICILE,
}
_CASTE_KINDS = (CertificateKind.OBC_NCL, CertificateKind.SC, CertificateKind.ST)

@dataclass(frozen=True)
class CertificateResult:
    kind: CertificateKind
    status: str
    reason: str
    # The notice's own words for this certificate (the evidence the decision rests on)
    source_page: Optional[int]
    source_quote: Optional[str]
    rules: Tuple[str, ...] = ()  # CitedRule ids applied because the notice was silent
    # The certificate judged was checked by a reviewer against the document (not just the candidate's word)
    verified: bool = False

# ---------- financial years ----------

_FY = re.compile(r"^\s*(\d{4})\s*-\s*(\d{2}|\d{4})\s*$")

def fy_start(fy: Optional[str]) -> Optional[int]:
    """'2020-21' / '2020-2021' -> 2020; None for anything else (a mismatched pair like 2020-23 too)."""
    m = _FY.match(fy or "")
    if not m:
        return None
    start, end = int(m.group(1)), m.group(2)
    return start if int(end) % 100 == (start + 1) % 100 else None

def fy_label(start: int) -> str:
    return f"{start}-{(start + 1) % 100:02d}"

def fy_containing(d: date) -> int:
    return d.year if d.month >= 4 else d.year - 1

# ---------- evaluation ----------

@dataclass
class _Findings:
    defects: list = field(default_factory=list)       # a stated condition is not met
    documents: list = field(default_factory=list)     # needs a different document (format)
    unclear: list = field(default_factory=list)       # cannot be decided
    ok: list = field(default_factory=list)
    rules: list = field(default_factory=list)

    def cite(self, rule: CitedRule) -> str:
        if rule.id not in self.rules:
            self.rules.append(rule.id)
        return f"[{rule.id}: {rule.reference}]"

def _d(v: Optional[date]) -> str:
    return v.isoformat() if v else "not stated"

def evaluate(requirement: CertificateRequirement, held: Optional[HeldCertificate], claimed: Optional[str], *,
             closing_date: Optional[date] = None, as_of: Optional[date] = None, verified: bool = False) -> CertificateResult:
    """
    requirement: what the notice states about one certificate kind.
    held: the candidate's certificate of that kind (None = none recorded, or a reviewer rejected it).
    claimed: the category/benefit the candidate claims for this kind (EWS, OBC, SC, ST, PWBD, ESM, DOMICILE, or
             UR/NONE for no claim); None = the profile does not say.
    closing_date: the notice's closing date, used as crucial date only when the requirement states none.
    """
    kind, label = requirement.kind, _LABEL[requirement.kind]

    def result(status: str, reason: str, f: Optional[_Findings] = None) -> CertificateResult:
        return CertificateResult(kind, status, reason, requirement.source_page, requirement.source_quote,
                                 tuple(f.rules) if f else (), verified and held is not None)

    if held is not None and held.kind != kind:
        raise ValueError(f"held {held.kind.value} certificate evaluated against a {kind.value} requirement")
    if claimed is None:
        return result(CANNOT_DECIDE, f"the profile does not say whether you claim the benefit that needs the {label}")
    if _CLAIM_KIND.get(re.sub(r"\s+", "", claimed).upper()) != kind:
        return result(NOT_APPLICABLE, f"no claim that needs the {label} (claimed: {claimed})")

    f = _Findings()
    crucial = requirement.crucial_date
    if crucial is None and closing_date is not None:
        crucial = closing_date
        rule = EWS_CRUCIAL_DATE if kind == CertificateKind.EWS else DEFAULT_CUT_OFF
        f.ok.append(f"the notice states no crucial date for the {label}; the closing date {crucial.isoformat()} is used {f.cite(rule)}")
    passed = as_of is None or (crucial is not None and as_of > crucial)
    caste = kind in _CASTE_KINDS

    if held is None:
        if kind == CertificateKind.EWS and passed:
            return result(FAILS, f"no {label} recorded; it had to be held on the crucial date {_d(crucial)}", f)
        why = f"you claim this benefit but no {label} is recorded (or a reviewer rejected it); obtain one"
        why += f" valid on {crucial.isoformat()}" if crucial else ""
        if caste:
            why += f"; without it you may at most be appointed provisionally {f.cite(PROVISIONAL_APPOINTMENT)}"
        return result(NEEDS_DOCUMENT, why, f)

    _check_dates(requirement, held, crucial, kind, label, caste, f)
    if kind == CertificateKind.EWS:
        _check_ews_year(requirement, held, crucial, f)
    elif kind == CertificateKind.OBC_NCL and not f.defects and not f.unclear:
        # Non-creamy-layer status is judged afresh each year; no cited general rule says how recent the certificate
        # must be, so only a window the notice states or a validity the certificate itself states settles it
        if requirement.issued_on_or_after is None and held.valid_until is None:
            f.unclear.append("the notice states no issue window for the OBC-NCL certificate and the certificate states no validity; "
                             "whether it shows non-creamy-layer status on the crucial date must be checked by a reviewer")
    _check_format(requirement, held, f)

    if f.defects:
        reason = "; ".join(f.defects)
        if passed:
            return result(FAILS, reason, f)
        return result(NEEDS_DOCUMENT, f"{reason}; obtain a certificate that meets the notice before {_d(crucial)}", f)
    if f.documents:
        return result(NEEDS_DOCUMENT, "; ".join(f.documents), f)
    if f.unclear:
        return result(CANNOT_DECIDE, "; ".join(f.unclear), f)
    reason = "; ".join(f.ok) or f"the {label} meets every condition the notice states"
    if not verified:
        reason += " (as you recorded it; not yet verified by a reviewer against the certificate)"
    return result(OK, reason, f)

def _check_dates(req: CertificateRequirement, held: HeldCertificate, crucial: Optional[date], kind: CertificateKind,
                 label: str, caste: bool, f: _Findings) -> None:
    issued = held.issue_date
    window = req.issued_on_or_after is not None or req.issued_on_or_before is not None
    if window:
        stated = f"issued between {_d(req.issued_on_or_after)} and {_d(req.issued_on_or_before)}"
        if issued is None:
            f.unclear.append(f"the notice wants the certificate {stated}; its issue date is not recorded")
        elif (req.issued_on_or_after and issued < req.issued_on_or_after) or (req.issued_on_or_before and issued > req.issued_on_or_before):
            if caste:
                f.unclear.append(f"issued {issued.isoformat()}, outside the notice's window ({stated}); {_LATE_CASTE_CERTIFICATE_SPLIT}")
            else:
                f.defects.append(f"issued {issued.isoformat()}; the notice wants it {stated}")
        else:
            f.ok.append(f"issued {issued.isoformat()}, within the notice's window ({stated})")
    if crucial is None:
        if req.issued_on_or_before is None:
            f.unclear.append(f"the notice states no crucial date or closing date for the {label}")
    elif issued is None:
        if not window:
            f.unclear.append(f"the certificate's issue date is not recorded, so possession on the crucial date {crucial.isoformat()} cannot be checked")
    elif issued > crucial and not (window and req.issued_on_or_before and req.issued_on_or_before >= issued):
        if caste:
            f.unclear.append(f"issued {issued.isoformat()}, after the crucial date {crucial.isoformat()}; {_LATE_CASTE_CERTIFICATE_SPLIT}; "
                             f"{f.cite(PROVISIONAL_APPOINTMENT)}")
        elif kind == CertificateKind.EWS:
            f.defects.append(f"issued {issued.isoformat()}, after the crucial date {crucial.isoformat()} on which it had to be held "
                             f"{f.cite(EWS_CRUCIAL_DATE)}")
        else:
            f.unclear.append(f"issued {issued.isoformat()}, after the crucial date {crucial.isoformat()}; the notice does not say "
                             f"whether a later certificate is accepted")
    elif not window:
        f.ok.append(f"issued {issued.isoformat()}, on or before the crucial date {crucial.isoformat()}")
    if held.valid_until is not None and crucial is not None:
        if held.valid_until < crucial:
            msg = f"the certificate is valid only until {held.valid_until.isoformat()}, before the crucial date {crucial.isoformat()}"
            (f.unclear if caste else f.defects).append(msg + (f"; {_LATE_CASTE_CERTIFICATE_SPLIT}" if caste else ""))
        elif kind == CertificateKind.PWBD:
            f.unclear.append(f"temporary certificate valid until {held.valid_until.isoformat()}; {_PWBD_TEMPORARY_SPLIT}")
        else:
            f.ok.append(f"valid until {held.valid_until.isoformat()}, on or after the crucial date")

def _check_ews_year(req: CertificateRequirement, held: HeldCertificate, crucial: Optional[date], f: _Findings) -> None:
    required = fy_start(req.financial_year)
    if req.financial_year and required is None:
        f.unclear.append(f"the notice's financial year '{req.financial_year}' could not be read")
        return
    if required is None and crucial is not None:
        required = fy_containing(crucial) - 1
        f.ok.append(f"the notice states no income year; income of FY {fy_label(required)}, the year before the year of application "
                    f"(closing date {crucial.isoformat()}), is required {f.cite(EWS_FY_PRIOR)} {f.cite(EWS_FY_EXAMPLE)}")
    if required is None:
        f.unclear.append("the notice states neither the income financial year nor a date to work it out from")
        return
    have = fy_start(held.financial_year)
    if have is None:
        f.unclear.append(f"the certificate's income financial year is not recorded (FY {fy_label(required)} is required)")
        return
    if have != required:
        msg = f"the certificate states income of FY {fy_label(have)}; FY {fy_label(required)} is required"
        if have > required and held.issue_date and crucial and held.issue_date > crucial:
            msg += f"; a later-year certificate issued after the crucial date is a major discrepancy {f.cite(EWS_MAJOR_DISCREPANCY)}"
        f.defects.append(msg)
        return
    fy_end = date(required + 1, 3, 31)
    if held.issue_date is not None and held.issue_date <= fy_end:
        # A certificate of a year's income cannot be issued before that year is over (sc-2026-poonam-dwivedi)
        f.defects.append(f"issued {held.issue_date.isoformat()}, before FY {fy_label(required)} ended on {fy_end.isoformat()}, so it "
                         f"cannot certify that year's income {f.cite(EWS_FY_PRIOR)}")
        return
    f.ok.append(f"income of FY {fy_label(required)} as required")

def _check_format(req: CertificateRequirement, held: HeldCertificate, f: _Findings) -> None:
    note = req.format_note
    if not note:
        return
    central, state = bool(re.search(r"\bcentral\b", note, re.I)), bool(re.search(r"\bstate\b", note, re.I))
    if held.central_format is None:
        f.unclear.append(f"the notice prescribes a format ({note}); the certificate's format is not recorded")
    elif central and not state:
        if held.central_format:
            f.ok.append(f"in the prescribed format ({note})")
        else:
            f.documents.append(f"the notice prescribes {note}; the certificate is not in the Central Government format, obtain one in that format")
    elif state and not central:
        if held.central_format:
            f.documents.append(f"the notice prescribes {note}; the certificate is in the Central Government format, obtain one in that format")
        else:
            f.unclear.append(f"the notice prescribes {note}; whether the certificate is in that State's format must be checked by a reviewer")
    else:
        f.unclear.append(f"the notice prescribes a format ({note}); whether the certificate follows it must be checked by a reviewer")

# Most favourable first: with several certificates of one kind, the best one counts
_PREFERENCE = (OK, CANNOT_DECIDE, NEEDS_DOCUMENT, FAILS, NOT_APPLICABLE)

def best(results: Sequence[CertificateResult]) -> CertificateResult:
    return min(results, key=lambda r: (_PREFERENCE.index(r.status), not r.verified))

def summary(statuses: Iterable[str]) -> Optional[str]:
    """Worst status over a section; None when there is nothing to report."""
    present = set(statuses)
    return next((s for s in SEVERITY if s in present), None)
