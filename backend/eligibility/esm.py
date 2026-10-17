"""
Ex-servicemen (ESM) and Agniveer section of an eligibility result (vacancy or exam), kept apart from the hard verdict.

Pure functions over the profile snapshot and the notice's rows; nothing is decided that the notice does not state:

  ESM_STATUS             the candidate's release route (PersonalInfo.esm_release_type) against the notice's definition
                         of "ex-serviceman" (ESM_RULE kind DEFINITION). Dismissal / discharge for misconduct FAILS only
                         when the notice excludes it in so many words (SSC JE 2026 para 7.6); otherwise a reviewer
                         decides, since the definition has other routes (e.g. gallantry award winners).
  ESM_RELEASE            released on or before the crucial date (STATUS_CRUCIAL_DATE: the closing date); a serving
                         candidate only when the notice allows completing the term of engagement within one year from
                         the closing date (RELEASE_WITHIN_ONE_YEAR), and then only up to that day.
  ESM_AGE                the notice's own ESM age relaxation (AGE_RELAXATION_RULE): SSC "N years after deduction of the
                         military service rendered from the actual age" -> actual age - service <= limit + N; UPSC
                         "relaxed by N years" with at least M months' service after attestation (rules.attested_service:
                         the stated service months; enrolment dates can only show it is too short). No such row ->
                         CANNOT_DECIDE. SSC service = armed_forces_service_months, else enrolment -> release
                         (rules.military_service_months).
  ESM_RESERVATION_REUSE  the re-use bar exactly as the notice quotes it (RESERVATION_REUSE_BAR: SSC paras 5.4 / 7.4 with
                         DoP&T OM 36034/1/2014-Estt (Res) of 14.08.2014, UPSC p.22 NOTE). A notice without it ->
                         NEEDS_HUMAN_REVIEW: the 2014 OM itself is not a document held here.
  ESM_AGNIVEER           no captured notice states an Agniveer reservation or relaxation: CANNOT_DECIDE for a former
                         Agniveer, or NEEDS_HUMAN_REVIEW quoting a requirement row that mentions Agniveers.

Outcomes: OK / FAILS / CANNOT_DECIDE (the profile or the notice text does not say) / NEEDS_HUMAN_REVIEW (a judgement
the code must not make) / NOT_APPLICABLE. The section only appears when the profile claims ex-serviceman or Agniveer
service; it never changes `status` or `confidence` (the hard AGE check already counts the ESM relaxation). Every row
names the notice page and quotes its words; the official notification prevails.
"""
from datetime import date
from typing import Dict, List, Optional, Sequence

from govcareeros.backend.eligibility import rules
from govcareeros.backend.parsing.esm_clauses import REQ_TYPE

PREFIX = "ESM_"
OK, FAILS, CANNOT_DECIDE, NEEDS_HUMAN_REVIEW, NOT_APPLICABLE = "OK", "FAILS", "CANNOT_DECIDE", "NEEDS_HUMAN_REVIEW", "NOT_APPLICABLE"
ESM_AGE_BASES = ("EX_SERVICEMEN", "EX_SERVICEMEN_SERVICE_DEDUCTED")
_MISCONDUCT = ("DISMISSED", "DISCHARGED_MISCONDUCT_INEFFICIENCY")
_GROUPS = {"C", "D"}

def is_esm_check(name: str) -> bool:
    return name.startswith(PREFIX)

def _out(check: str, outcome: str, rule: str, req: Optional[rules.Requirement], evidence: str, note: Optional[str] = None) -> rules.RuleOutcome:
    quote = (req.source_quote or str(req.value)) if req else "-"
    return rules.RuleOutcome(check, outcome, rule, quote, evidence, req.id if req else None, req.source_page if req else None, note=note)

def _esm_rules(reqs: Sequence[rules.Requirement]) -> Dict[str, rules.Requirement]:
    """First row of each kind; an exam judged post-wise repeats its exam-level rows."""
    out: Dict[str, rules.Requirement] = {}
    for r in reqs:
        if r.req_type == REQ_TYPE and isinstance(r.value, dict) and r.value.get("kind"):
            out.setdefault(r.value["kind"], r)
    return out

def _plus_one_year(d: date) -> date:
    try:
        return d.replace(year=d.year + 1)
    except ValueError:  # 29 February
        return d.replace(year=d.year + 1, day=28)

def _months(m: Optional[int]) -> str:
    return "not stated" if m is None else f"{m // 12} years {m % 12} months"

# ---------- status (definition) ----------

def check_status(profile: rules.ProfileSnapshot, by_kind: Dict[str, rules.Requirement]) -> rules.RuleOutcome:
    t = profile.esm_release_type
    defn, release = by_kind.get("DEFINITION"), by_kind.get("RELEASE_WITHIN_ONE_YEAR")
    evidence = f"release type: {t or 'not stated'}" + (f", {profile.esm_service_branch}" if profile.esm_service_branch else "") + \
        (f", rank {profile.esm_rank}" if profile.esm_rank else "")
    if t is None:
        return _out("ESM_STATUS", CANNOT_DECIDE, "how you left the Armed Forces is not stated in the profile", defn, evidence)
    if t == "STILL_SERVING":
        return _out("ESM_STATUS", NOT_APPLICABLE, "still serving: the release check decides whether you may apply as an ex-serviceman",
                    release or defn, evidence)
    if t in _MISCONDUCT:
        if release and release.value.get("excludes_dismissal_or_misconduct"):
            return _out("ESM_STATUS", FAILS, "the notice excludes ex-serviceman status acquired by dismissal or discharge on account of "
                        "misconduct or inefficiency", release, evidence)
        return _out("ESM_STATUS", NEEDS_HUMAN_REVIEW, "the definition's engagement route excludes dismissal and discharge for misconduct "
                    "or inefficiency; whether another route of the definition applies needs a reviewer", defn, evidence)
    if defn is None:
        return _out("ESM_STATUS", CANNOT_DECIDE, "the notice text used does not define 'ex-serviceman'", None, evidence)
    if t in defn.value.get("release_types", []):
        return _out("ESM_STATUS", OK, f"release route {t} is one the notice's definition lists", defn, evidence,
                    "needs the release/discharge certificate stating the period of service, in the prescribed format")
    return _out("ESM_STATUS", NEEDS_HUMAN_REVIEW, f"release route {t} is not one of the routes the definition lists "
                f"({', '.join(defn.value.get('release_types', []))}); a reviewer must read the definition", defn, evidence)

# ---------- release date against the crucial date ----------

def check_release(profile: rules.ProfileSnapshot, by_kind: Dict[str, rules.Requirement], closing: Optional[date]) -> rules.RuleOutcome:
    crucial_rule, window = by_kind.get("STATUS_CRUCIAL_DATE"), by_kind.get("RELEASE_WITHIN_ONE_YEAR")
    release, t = profile.esm_release_date, profile.esm_release_type
    evidence = f"release type: {t or 'not stated'}; release date: {release.isoformat() if release else 'not stated'}"
    if closing is None:
        return _out("ESM_RELEASE", CANNOT_DECIDE, "the closing date of this notice is not known", crucial_rule or window, evidence)
    serving = t == "STILL_SERVING" or (release is not None and release > closing)
    if not serving:
        if release is None:
            return _out("ESM_RELEASE", CANNOT_DECIDE, "release/discharge date not stated in the profile", crucial_rule, evidence)
        if crucial_rule:
            return _out("ESM_RELEASE", OK, f"released on {release.isoformat()}, by the crucial date for ESM status (closing date "
                        f"{closing.isoformat()})", crucial_rule, evidence)
        return _out("ESM_RELEASE", OK, f"released on {release.isoformat()}, before the closing date {closing.isoformat()}; the notice "
                    "text used states no separate crucial date for ESM status", None, evidence)
    if window is None:
        return _out("ESM_RELEASE", CANNOT_DECIDE, "not yet released on the closing date, and the notice text used does not say whether "
                    "serving personnel due for release may apply as ex-servicemen", crucial_rule, evidence)
    if release is None:
        return _out("ESM_RELEASE", CANNOT_DECIDE, "expected date of completing the term of engagement not stated in the profile", window, evidence)
    last = _plus_one_year(closing)
    if release <= last:
        return _out("ESM_RELEASE", OK, f"term of engagement completes on {release.isoformat()}, within one year from the closing date "
                    f"{closing.isoformat()} (by {last.isoformat()})", window, evidence,
                    "needs documentary evidence from the competent authority, and ex-serviceman status must be acquired by "
                    f"{last.isoformat()}")
    return _out("ESM_RELEASE", FAILS, f"term of engagement completes on {release.isoformat()}, more than one year after the closing "
                f"date {closing.isoformat()} (last allowed {last.isoformat()})", window, evidence)

# ---------- age relaxation ----------

def check_age(profile: rules.ProfileSnapshot, reqs: Sequence[rules.Requirement], on: Optional[date]) -> rules.RuleOutcome:
    seen, age_rules = set(), []
    for r in reqs:
        if r.req_type == "AGE_RELAXATION_RULE" and isinstance(r.value, dict) and r.value.get("basis") in ESM_AGE_BASES and r.id not in seen:
            seen.add(r.id)
            age_rules.append(r)
    if not age_rules:
        return _out("ESM_AGE", CANNOT_DECIDE, "no ex-servicemen age relaxation was extracted from this notice; read it in the notice", None, "-")
    rule = age_rules[0]
    category = rules.normalize_category(profile.category)
    months = rules.military_service_months(profile, on)
    service = f"military service {_months(months)}" + \
        (" (from enrolment/release dates)" if profile.armed_forces_service_months is None and months is not None else "")
    if profile.date_of_birth is None or on is None or category is None:
        missing = "date of birth" if profile.date_of_birth is None else "category" if category is None else "the crucial date for age"
        return _out("ESM_AGE", CANNOT_DECIDE, f"{missing} not known", rule, service)
    # Category limits (an SSC category limit already includes the category relaxation); post-wise notices give several
    own = [r for r in reqs if r.req_type == "AGE_MAX" and isinstance(r.value, dict) and r.value.get("category") == category]
    limits = own or [r for r in reqs if r.req_type == "AGE_MAX" and isinstance(r.value, dict) and r.value.get("category") == "ALL"]
    # A birth-date window ("born not earlier than ...") means the limit must not have been reached: at most limit - 1 years
    caps = sorted({r.value["max_age"] - (1 if r.value.get("born_not_earlier_than") else 0) for r in limits})
    age = rules.age_on(profile.date_of_birth, on)
    evidence = f"DOB {profile.date_of_birth.isoformat()}, age {age} on {on.isoformat()}, {service}"
    if not caps:
        return _out("ESM_AGE", CANNOT_DECIDE, f"no upper age limit extracted for category {category}", rule, evidence)
    if age <= caps[0]:
        return _out("ESM_AGE", NOT_APPLICABLE, f"age {age} is within the limit {caps[0]} without the ex-servicemen relaxation", rule, evidence)
    v = rule.value
    if v["basis"] == "EX_SERVICEMEN_SERVICE_DEDUCTED":
        if months is None:
            return _out("ESM_AGE", CANNOT_DECIDE, "length of military service not stated (service months, or enrolment and release dates)",
                        rule, evidence)
        effective = age - months / 12
        how = f"actual age {age} minus {_months(months)} military service = {effective:.1f}"
    else:
        need = v.get("min_service_months_after_attestation")
        served, served_text = rules.attested_service(profile, on, need) if need else ("OK", None)
        if served == "NO":
            return _out("ESM_AGE", FAILS, f"{served_text}: the relaxation requires {need} months' continuous service after attestation",
                        rule, evidence)
        if served == "UNKNOWN":
            return _out("ESM_AGE", CANNOT_DECIDE, f"{served_text}; the relaxation requires {need} months' continuous service after "
                        "attestation (state the service months)", rule, evidence)
        effective = age
        how = f"age {age}"
    fits = [c for c in caps if effective <= c + v["years"]]
    limits_text = ", ".join(f"{c} + {v['years']} = {c + v['years']}" for c in caps)
    if not fits:
        return _out("ESM_AGE", FAILS, f"{how} is above the relaxed limit ({limits_text})", rule, evidence)
    note = "needs the ex-serviceman certificate stating the period of service"
    if len(fits) < len(caps):
        note += f"; fits only posts whose limit is {fits[0]} or more"
    return _out("ESM_AGE", OK, f"{how} is within the relaxed limit ({limits_text})", rule, evidence, note)

# ---------- reservation already used for a civil post ----------

def check_reuse(profile: rules.ProfileSnapshot, by_kind: Dict[str, rules.Requirement]) -> rules.RuleOutcome:
    bar = by_kind.get("RESERVATION_REUSE_BAR")
    secured, used = profile.esm_civil_post_secured, profile.esm_reservation_used
    group, undertaking = profile.esm_civil_post_group, profile.esm_undertaking_given
    evidence = "; ".join(f"{k}: {'not stated' if x is None else x}" for k, x in (
        ("civil post held", secured), ("secured through ESM reservation", used), ("group", group), ("self-declaration given", undertaking)))
    if bar is None:
        if secured is False:
            return _out("ESM_RESERVATION_REUSE", NOT_APPLICABLE, "no civil post held after release", None, evidence)
        return _out("ESM_RESERVATION_REUSE", NEEDS_HUMAN_REVIEW, "the notice text used does not state the rule on using the ESM reservation "
                    "again after a civil post (DoP&T OM 36034/1/2014-Estt (Res) is not a document held here)", None, evidence)
    v = bar.value
    if v.get("trigger") == "CIVIL_POST_SECURED":
        if secured is None:
            return _out("ESM_RESERVATION_REUSE", CANNOT_DECIDE, "whether you hold a regular civil post under the Central Government is not stated",
                        bar, evidence)
        if not secured:
            return _out("ESM_RESERVATION_REUSE", OK, "no regular civil post under the Central Government held", bar, evidence)
        return _out("ESM_RESERVATION_REUSE", FAILS, "you hold a regular civil post under the Central Government: no ESM reservation for "
                    "this post", bar, evidence, "the same note keeps the ex-servicemen age relaxation" if v.get("age_relaxation_kept") else None)
    # RESERVATION_AVAILED: only a civil post secured through the ESM reservation counts
    lost = " and ".join(b.lower().replace("_", " ") for b in v.get("bars", ["RESERVATION"]))
    # "No civil post" settles it before any follow-up answer about that post is read
    if secured is False or used is False:
        return _out("ESM_RESERVATION_REUSE", OK, "no civil post secured through the ESM reservation", bar, evidence)
    if used is None:
        return _out("ESM_RESERVATION_REUSE", CANNOT_DECIDE, "whether a civil post was secured through the ESM reservation is not stated", bar, evidence)
    groups = set(v.get("civil_post_groups") or [])
    if groups and group is None:
        return _out("ESM_RESERVATION_REUSE", CANNOT_DECIDE, "the Group of the civil post held is not stated; the bar is for Group "
                    f"{' & '.join(sorted(groups))} posts", bar, evidence)
    if groups and group not in groups:
        return _out("ESM_RESERVATION_REUSE", NEEDS_HUMAN_REVIEW, f"the notice states the bar for Group {' & '.join(sorted(groups))} posts; "
                    f"your civil post is Group {group}", bar, evidence)
    if v.get("undertaking_exception"):
        if undertaking is None:
            return _out("ESM_RESERVATION_REUSE", CANNOT_DECIDE, "whether the self-declaration of earlier applications was given to the "
                        "employer on joining is not stated", bar, evidence)
        if undertaking:
            return _out("ESM_RESERVATION_REUSE", NEEDS_HUMAN_REVIEW, "self-declaration given: the reservation stays open only for the "
                        "vacancies applied for before joining the civil post, as listed in it; a reviewer must check this application",
                        bar, evidence)
    return _out("ESM_RESERVATION_REUSE", FAILS, f"civil post secured through the ESM reservation: not eligible for ESM {lost}", bar, evidence)

# ---------- Agniveer ----------

def check_agniveer(reqs: Sequence[rules.Requirement]) -> rules.RuleOutcome:
    mention = next((r for r in reqs if "agniveer" in f"{r.source_quote or ''} {r.value}".lower()), None)
    if mention:
        return _out("ESM_AGNIVEER", NEEDS_HUMAN_REVIEW, "the notice mentions Agniveers; their provision is not machine-read yet", mention,
                    "former Agniveer")
    return _out("ESM_AGNIVEER", CANNOT_DECIDE, "the notice text used states no reservation or age relaxation for former Agniveers; "
                "check the official notification", None, "former Agniveer")

# ---------- section ----------

def claims_esm(profile: rules.ProfileSnapshot) -> bool:
    return bool(profile.is_ex_serviceman) or (profile.is_ex_serviceman is None and profile.esm_release_type is not None)

def check_esm(profile: rules.ProfileSnapshot, reqs: Sequence[rules.Requirement], closing: Optional[date],
              age_on: Optional[date]) -> List[rules.RuleOutcome]:
    """`closing`: closing date of applications; `age_on`: the date the notice reckons age on."""
    out: List[rules.RuleOutcome] = []
    if claims_esm(profile):
        by_kind = _esm_rules(reqs)
        out += [check_status(profile, by_kind), check_release(profile, by_kind, closing), check_age(profile, reqs, age_on),
                check_reuse(profile, by_kind)]
    if profile.is_agniveer:
        out.append(check_agniveer(reqs))
    return out
