"""
Ex-servicemen (ESM) clauses of a recruitment/examination notice, read deterministically into ESM_RULE rows
(value = JSON dict with a "kind"; provenance = page + the clause's own words). eligibility/esm.py applies them.

Kinds, each emitted only when the notice states it:
  RESERVATION_REUSE_BAR  ESM who already secured a civil post lose the ESM reservation for later posts.
                         SSC (CGL/CHSLE/JE 2026, para 5.4 / 7.4): only when that post was a Group 'C'/'D' post secured
                         "after availing of the benefits of reservation"; the bar lifts if the candidate gave the
                         employer the self-declaration of earlier applications (DoP&T OM 36034/1/2014-Estt (Res),
                         14.08.2014); fee concession is lost too.
                         UPSC (Advt 12/2026, p.22 NOTE): any regular Central Govt civil post; age relaxation stays.
                         "government": CENTRAL where the clause says "under the Central Government/Govt." (JE 2026,
                         UPSC), ANY where it says only "under Government" (CGL, CHSLE 2026).
  RELEASE_WITHIN_ONE_YEAR  serving personnel may apply when they will complete the term of engagement within one year
                         from the closing date (SSC para 5.6 / 7.6); JE 2026 also excludes status acquired "by way of
                         dismissal or discharge on account of misconduct or inefficiency".
  STATUS_CRUCIAL_DATE    "Crucial date for claim of ... ESM status ... will be the closing date" (SSC para 6.4 / 8.7).
  DEFINITION             "Explanation: An 'ex-serviceman' means a person: ..." with the release routes it lists.

Agniveer: no notice captured so far states an Agniveer reservation or relaxation, so nothing is parsed for it
(eligibility/esm.py reports CANNOT_DECIDE for a former Agniveer).
"""
import re
from typing import Callable, List

from govcareeros.backend.parsing.upsc_advertisement import ParsedRequirement, Provenance

REQ_TYPE = "ESM_RULE"
_ESM = r"Ex[\s-]*Servicem[ae]n"
_Q = r"\W{0,3}"  # the notices quote terms with assorted (and mis-encoded) quote marks

_REUSE_SSC_RE = re.compile(
    _ESM + r"\s*(?:\(ESM\)\s*)?who\s+have\s+already\s+secured\s+employment\s+in\s+(?:the\s+)?civil\s+side.*?"
    r"dated\s+(?:14(?:th)?\s+August,?\s+2014|14\.08\.2014)\s+issued\s+by\s+DoP&T\.", re.S | re.I)
_REUSE_UPSC_RE = re.compile(
    _ESM + r"\s+who\s+have\s+already\s+secured\s+regular\s+employment\s+under\s+the\s+Central\s+Govt\.?\s+in\s+a\s+Civil\s+Post.*?"
    r"not\s+be\s+eligible\s+for\s+the\s+benefit\s+of\s+reservation.*?jobs\.", re.S | re.I)
_RELEASE_RE = re.compile(
    r"For\s+any\s+serviceman\s+of\s+the\s+three\s+Armed\s+Forces\s+of\s+the\s+Union\s+to\s+be\s+treated\s+as\s+" + _ESM + r".*?"
    r"within\s+the\s+stipulated\s+period\s+of\s+one\s+year\s+from\s+the\s+closing\s+date.*?"
    r"must\s+also\s+acquire\s+the\s+status\s+of\s+an\s+" + _ESM + r"\s+within\s+the\s+stipulated\s+period\s+of\s+one\s+year\s+from\s+the\s+"
    r"closing\s+date\s+of\s+(?:receipt\s+of\s+)?applications?\.", re.S | re.I)
_CRUCIAL_RE = re.compile(
    r"Crucial\s+date\s+for\s+(?:the\s+)?claim\s+of\s+[A-Z/\s]{0,40}?ESM\s+status.*?will\s+be\s+the\s+closing\s+date\s+(?:for\s+receipt\s+of\s+"
    r"online\s+applications|of\s+the\s+Online\s+Application\s+Form)\.", re.S | re.I)
_DEFINITION_RE = re.compile(
    r"Explanation\s*:\s*An\s*" + _Q + r"\s*" + _ESM.replace("[ae]n", "an") + r"\s*" + _Q + r"\s*means\s+a\s+person\s*:.*?"
    r"Ex-?\s*recruits\s+boarded\s+out\s+or\s+relieved\s+on\s+medical\s+grounds?\s+and\s+granted\s+medical\s+disability\s+pension\.",
    re.S | re.I)
# Release routes the definition names (profile PersonalInfo.esm_release_type values)
_ROUTES = [
    ("RETIRED_WITH_PENSION", r"retired\s+or\s+relieved,?\s+or\s+discharged\s+from\s+such\s+service.*?after\s+earning\s+his(?:\s+or\s+her)?\s+pension"),
    ("MEDICAL_DISABILITY_PENSION", r"relieved\s+from\s+such\s+service\s+on\s+medical\s+grounds.*?disability\s+pension"),
    ("REDUCTION_IN_ESTABLISHMENT", r"reduction\s+in\s+establishment"),
    ("COMPLETED_ENGAGEMENT_GRATUITY", r"completing\s+the\s+specific\s+period\s+of\s+engagement.*?given\s+a\s+gratuity"),
    ("RECRUIT_BOARDED_OUT_MEDICAL", r"Ex-?\s*recruits\s+boarded\s+out.*?medical\s+disability\s+pension"),
]
_CENTRAL_GOVT_RE = re.compile(r"under\s+the\s+Central\s+Gov(?:ernmen)?t", re.I)
_DISMISSAL_EXCLUDED_RE = re.compile(r"status\s+of\s+" + _ESM + r"\s+otherwise\s+than\s+by\s+way\s+of\s+\W?dismissal\s+or\s+discharge\s+on\s+"
                                    r"account\s+of\s+misconduct\s+or\s+inefficiency", re.I)

def parse_esm_clauses(text: str, prov: Callable[[int, int, str, int], Provenance], warnings: List[str]) -> List[ParsedRequirement]:
    """`text` is the notice's concatenated page text and `prov(start, end, section, max_len)` its provenance helper."""
    rows: List[ParsedRequirement] = []

    def add(value: dict, m, section: str) -> None:
        rows.append(ParsedRequirement(REQ_TYPE, value, False, prov(m.start(), m.end(), section, 2500)))

    m = _REUSE_SSC_RE.search(text)
    if m:
        body = m.group(0)
        groups = re.search(r"Group\s*\W?C\W?\s*&\s*\W?D\W?\s*posts", body, re.I)
        add({"kind": "RESERVATION_REUSE_BAR", "trigger": "RESERVATION_AVAILED",
             "government": "CENTRAL" if _CENTRAL_GOVT_RE.search(body) else "ANY",
             "civil_post_groups": ["C", "D"] if groups else None,
             "undertaking_exception": bool(re.search(r"self-?\s*declaration\s*/\s*undertaking", body, re.I)),
             "bars": ["RESERVATION", "FEE_CONCESSION"] if re.search(r"fee\s+concession", body, re.I) else ["RESERVATION"],
             "om": "36034/1/2014-Estt (Res) dated 14.08.2014"}, m, "Ex-Servicemen: civil employment already secured")
    m = _REUSE_UPSC_RE.search(text)
    if m:
        add({"kind": "RESERVATION_REUSE_BAR", "trigger": "CIVIL_POST_SECURED", "government": "CENTRAL", "civil_post_groups": None,
             "undertaking_exception": False, "bars": ["RESERVATION"],
             "age_relaxation_kept": bool(re.search(r"permitted\s+the\s+benefit\s+of\s+age\s+relaxation", m.group(0), re.I))},
            m, "Concessions & Relaxations: NOTE")
    m = _RELEASE_RE.search(text)
    if m:
        add({"kind": "RELEASE_WITHIN_ONE_YEAR", "months_after_closing": 12,
             "excludes_dismissal_or_misconduct": bool(_DISMISSAL_EXCLUDED_RE.search(m.group(0)))},
            m, "Ex-Servicemen: serving personnel")
    m = _CRUCIAL_RE.search(text)
    if m:
        add({"kind": "STATUS_CRUCIAL_DATE", "date": "CLOSING_DATE"}, m, "Crucial date for ESM status")
    m = _DEFINITION_RE.search(text)
    if m:
        routes = [name for name, rx in _ROUTES if re.search(rx, m.group(0), re.S | re.I)]
        add({"kind": "DEFINITION", "release_types": routes}, m, "Explanation: ex-serviceman")
    if re.search(_ESM, text, re.I) and not rows:
        warnings.append("ex-servicemen clauses (re-use bar, release within one year, crucial date, definition) not found")
    return rows
