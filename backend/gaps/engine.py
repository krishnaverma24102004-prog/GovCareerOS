"""
Gap engine: turns one eligibility decision (+ relevance components) into concrete gaps.

Pure functions over the same plain inputs as eligibility.rules — no database, no LLM.

  - Every NOT_MET / UNKNOWN outcome yields at least one BLOCKING gap; CONDITIONAL outcomes yield
    non-blocking gaps (a certificate to produce, a clause to confirm, a completion date to add).
  - A gap names what the notification requires (its own words + page, filled in by the caller from
    the requirement row), what the profile has, and one deterministic next step.
  - Dates are computed with the eligibility engine's own arithmetic, so a projected date is exactly
    the first day the engine would say MET: experience years are days / 365.25, age is completed years
    (a 29 Feb birthday is reached on 1 Mar in non-leap years). Projections assume ongoing roles continue.
  - An UNKNOWN that a profile field would settle becomes PROFILE_MISSING_INFO, naming the field and
    why it is asked (e.g. disability percentage, because a PwBD age relaxation could apply).
  - Relevance components never block; a component under RELEVANCE_THRESHOLD, or one the profile can't
    inform, becomes a RELEVANCE_<COMPONENT> gap.
"""
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from math import ceil
from typing import Dict, List, Optional, Sequence

from govcareeros.backend.eligibility import rules
from govcareeros.backend.relevance.scoring import VacancyText, terms

ENGINE_VERSION = "gaps/1"
RELEVANCE_THRESHOLD = 50

@dataclass
class Gap:
    gap_type: str
    blocking: bool
    requirement: str
    profile_has: str
    next_step: str
    reason_code: str
    requirement_id: object = None
    requirement_page: Optional[int] = None
    requirement_quote: Optional[str] = None
    profile_field: Optional[str] = None
    due_date: Optional[date] = None

def fmt(d: Optional[date]) -> str:
    return f"{d.day} {d.strftime('%b %Y')}" if d else "-"

def add_years(d: date, years: int) -> date:
    """The first date on which rules.age_on(d, ...) reaches `years`."""
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # 29 Feb in a non-leap year
        return date(d.year + years, 3, 1)

# ---------- experience projection ----------

def _counted_years(entries: Sequence[rules.ExperienceEntry], counted_from: Optional[date], until: date) -> float:
    """Same sum as rules.check_experience, evaluated up to `until`."""
    total = 0.0
    for e in entries:
        if e.start_date:
            start = max(e.start_date, counted_from) if counted_from else e.start_date
            total += rules._years_between(start, min(e.end_date or until, until))
    return total

def earliest_experience_date(entries: Sequence[rules.ExperienceEntry], required: float, counted_from: Optional[date],
                             after: date) -> Optional[date]:
    """First date on which the counted years reach `required`, if the roles continue as recorded; None if they never do."""
    if _counted_years(entries, counted_from, after) >= required:
        return after
    lo = after
    hi = max(after, counted_from or after) + timedelta(days=ceil(required * 366) + 1)
    if _counted_years(entries, counted_from, hi) < required:
        return None  # every role has an end date; no further accrual
    while (hi - lo).days > 1:
        mid = lo + timedelta(days=(hi - lo).days // 2)
        if _counted_years(entries, counted_from, mid) >= required:
            hi = mid
        else:
            lo = mid
    return hi

def _relevant(profile: rules.ProfileSnapshot, keywords: Sequence[str]) -> List[rules.ExperienceEntry]:
    return [e for e in profile.experience
            if keywords and any(k in f"{e.designation} {e.domain or ''} {e.responsibilities or ''}".lower() for k in keywords)]

# ---------- per check ----------

_MISSING_LABELS = {
    "is_pwbd": "whether you are a person with benchmark disability (PwBD)",
    "disability_percentage": "disability percentage",
    "disability_types": "disability sub-category (e.g. OL, LV, HH)",
    "is_ex_serviceman": "ex-serviceman status",
    "armed_forces_service_months": "months of armed forces service",
    "is_central_govt_employee": "whether you are a Central Government servant",
    "central_govt_service_start": "start date of continuous Central Government service",
}

def _gap(o: rules.RuleOutcome, gap_type: str, blocking: bool, next_step: str, reason: str, **kw) -> Gap:
    return Gap(gap_type, blocking, o.requirement, kw.pop("profile_has", o.profile_evidence), next_step, reason,
               o.requirement_id, o.source_page, **kw)

def _age_gaps(o: rules.RuleOutcome, profile: rules.ProfileSnapshot, reqs: Sequence[rules.Requirement],
              crucial: Optional[date]) -> List[Gap]:
    blocking = o.outcome in ("NOT_MET", "UNKNOWN")
    if o.outcome == "CONDITIONAL":
        return [_gap(o, "CERTIFICATE", False, f"age fits only with a relaxation: {o.note or 'keep the supporting certificate ready'}",
                     "RELAXATION_CERTIFICATE")]
    if not blocking:
        return []
    if crucial is None:
        return [_gap(o, "AGE", True, "the notification's crucial date for age was not extracted; read it in the notification and "
                     "compute your age on that date", "CRUCIAL_DATE_MISSING")]
    if profile.date_of_birth is None:
        return [_gap(o, "PROFILE_MISSING_INFO", True, "profile is missing date of birth (age is judged on the crucial date "
                     f"{fmt(crucial)})", "MISSING_FIELD", profile_field="date_of_birth")]
    category = rules.normalize_category(profile.category)
    if category is None:
        return [_gap(o, "PROFILE_MISSING_INFO", True, "profile is missing a recognised category (UR/EWS/OBC/SC/ST); "
                     "the age limit depends on it", "MISSING_FIELD", profile_field="category")]
    age = rules.age_on(profile.date_of_birth, crucial)
    if age < 0:
        return [_gap(o, "PROFILE_MISSING_INFO", True, "date of birth is after the crucial date; correct it in the profile",
                     "INVALID_FIELD", profile_field="date_of_birth")]
    limits = [r for r in reqs if r.req_type == "AGE_MAX"]
    limit = rules.age_limit_for(limits, category)
    if limit is None:
        return [_gap(o, "AGE", True, f"the notification states no age limit for {category} in this vacancy's block; check its "
                     "General Instructions for the rule that applies to you", "NO_LIMIT_FOR_CATEGORY")]
    max_age, min_age = limit.value["max_age"], limit.value.get("min_age")
    if min_age is not None and age < min_age:
        reach = add_years(profile.date_of_birth, min_age)
        return [_gap(o, "AGE", True, f"minimum age {min_age}; you are {age} on the crucial date {fmt(crucial)} — you reach "
                     f"{min_age} on {fmt(reach)}, so notifications with a crucial date on or after that can count you",
                     "BELOW_MIN_AGE", due_date=reach)]

    options = rules.relaxation_options(profile, reqs, category, crucial, age)
    fitting = [x for x in options if x.status != "NO" and age <= max_age + x.years]
    unsettled = [x for x in fitting if x.status == "UNKNOWN" and x.missing]
    if unsettled:
        return [_gap(o, "PROFILE_MISSING_INFO", True,
                     f"profile is missing {_MISSING_LABELS.get(x.missing, x.missing)} (asked because the {x.basis} age relaxation "
                     f"of {x.years} years could apply: you are {age}, the {limit.value['category']} limit is {max_age}"
                     + (f", relaxation on p.{x.page}" if x.page else "") + ")",
                     "MISSING_FIELD", profile_field=x.missing) for x in unsettled]
    last_ok = add_years(profile.date_of_birth, max_age + 1) - timedelta(days=1)
    if o.outcome == "UNKNOWN":
        return [_gap(o, "AGE", True, f"you are {age}, above the {limit.value['category']} limit of {max_age}; {o.rule}"
                     + (f" ({o.note})" if o.note else "") + " — confirm with the recruiting body before applying",
                     "RELAXATION_UNSETTLED", due_date=last_ok)]
    ruled_out = "; ".join(f"{x.basis}: {x.evidence}" for x in options) or "the notification states no relaxation that applies"
    return [_gap(o, "AGE", True, f"you are {age} on the crucial date {fmt(crucial)}, above the {limit.value['category']} limit of "
                 f"{max_age}; you were last within it for crucial dates up to {fmt(last_ok)}. Relaxations considered: {ruled_out}",
                 "AGED_OUT", due_date=last_ok)]

def _education_gaps(o: rules.RuleOutcome, crucial: Optional[date]) -> List[Gap]:
    d = o.data or {}
    reason = d.get("reason")
    if o.outcome == "CONDITIONAL":
        out = []
        if d.get("end_date") is None:
            out.append(_gap(o, "PROFILE_MISSING_INFO", False, f"degree completion date missing for {d.get('degree')}; the degree must be "
                            f"complete by the crucial date {fmt(crucial)}", "MISSING_FIELD", profile_field="education.end_date"))
        if d.get("extra_clause"):
            out.append(_gap(o, "EDUCATION", False, f"the qualification text adds a condition ('{d['extra_clause']} ...'); confirm you meet "
                            "it from the quoted requirement before applying", "EXTRA_CLAUSE"))
        return out
    if o.outcome not in ("NOT_MET", "UNKNOWN"):
        return []
    families = ", ".join(f.lower().replace("_", " ") for f in d.get("required_families") or []) or "the stated qualification"
    if reason == "NO_EDUCATION":
        return [_gap(o, "PROFILE_MISSING_INFO", True, "profile has no education entries; add your qualifications with completion dates",
                     "MISSING_FIELD", profile_field="education")]
    if reason == "NOT_COMPLETED":
        end = d["end_date"]
        return [_gap(o, "EDUCATION", True, f"{d['degree']} completes on {fmt(end)}, after this vacancy's crucial date {fmt(crucial)}; "
                     f"notifications with a crucial date on or after {fmt(end)} can count it", reason, due_date=end)]
    if reason == "BELOW_MASTERS":
        return [_gap(o, "EDUCATION", True, f"a Master's-level qualification is required; your highest matching entry is {d['degree']}",
                     reason)]
    if reason in ("EQUIVALENCE_NOT_STATED", "UNRECOGNISED_REQUIREMENT"):
        return [_gap(o, "EDUCATION", True, f"the notification does not state that {d.get('degree')} is equivalent to {families}; "
                     "equivalence is never assumed — hold an equivalence order from the competent authority or treat this post as "
                     "not open to you", reason)]
    return [_gap(o, "EDUCATION", True, f"requires {families}; {d.get('degree')} is not in that family and the notification declares "
                 "no equivalence", reason or "WRONG_FAMILY")]

def _experience_gaps(o: rules.RuleOutcome, profile: rules.ProfileSnapshot, crucial: Optional[date]) -> List[Gap]:
    d = o.data or {}
    reason = d.get("reason")
    if o.outcome not in ("NOT_MET", "UNKNOWN"):
        return []
    if reason == "YEARS_UNREADABLE":
        return [_gap(o, "EXPERIENCE", True, "the required years could not be read from the notification; read the quoted requirement "
                     "and compare your dated experience by hand", reason)]
    if reason == "QUALIFICATION_DATE_MISSING":
        what = "post-graduate degree" if d.get("level") == 3 else "qualifying degree"
        return [_gap(o, "PROFILE_MISSING_INFO", True, f"{what} completion date missing (only experience after it counts)",
                     "MISSING_FIELD", profile_field="education.end_date")]
    if reason == "UNDATED_ENTRIES":
        return [_gap(o, "PROFILE_MISSING_INFO", True, f"experience start/end dates missing; {d['years']:.1f} dated years are short of the "
                     f"{d['required_years']} required, and undated roles cannot be counted", "MISSING_FIELD",
                     profile_field="experience.start_date")]

    required, counted_from, until = d["required"], d.get("counted_from"), d["until"]
    keywords, fields = d.get("keywords") or [], d.get("fields") or []
    field_text = " or ".join(fields) if fields else "the stated field"
    relevant = _relevant(profile, keywords)
    entries = relevant if keywords else profile.experience
    have = _counted_years(entries, counted_from, until)
    names = ", ".join(f"{e.designation} @ {e.organization}" for e in entries) or "none"
    since = f" counted from {fmt(counted_from)}" if counted_from else ""
    has = f"{have:.1f} years{since} to {fmt(until)} ({names})"
    base = f"need {required:g} years in {field_text}; you have {has}"

    if keywords and not relevant:
        return [_gap(o, "EXPERIENCE", True, f"{base}. None of your roles mention {keywords}: if a role was in this field, say so in its "
                     "domain/responsibilities; otherwise this post is not open to you yet", "NO_RELEVANT_EXPERIENCE", profile_has=has)]
    earliest = earliest_experience_date(entries, required, counted_from, until)
    if earliest is None:
        step = f"{base}; your recorded roles have all ended, so the gap only closes with new experience in {field_text}"
    elif earliest <= until:
        # only reached when relevance, not duration, is the open question
        step = f"{base}, but the notification's field ({field_text}) could not be matched to your roles automatically; " \
               "state the domain in each role so it can be checked"
    else:
        step = f"{base} — earliest date you'd meet it: {fmt(earliest)} if your current role continues. This vacancy counts " \
               f"experience only to {fmt(until)}, so later notifications with a crucial date on or after {fmt(earliest)} can count it"
    if not keywords:
        step += "; relevance to the field will still need confirming"
    return [_gap(o, "EXPERIENCE", True, step, reason or "TOO_FEW_YEARS", profile_has=has,
                 due_date=earliest if earliest and earliest > until else None)]

def _citizenship_gaps(o: rules.RuleOutcome) -> List[Gap]:
    if o.outcome == "UNKNOWN":
        return [_gap(o, "PROFILE_MISSING_INFO", True, "profile is missing citizenship status", "MISSING_FIELD",
                     profile_field="citizenship_status")]
    if o.outcome == "NOT_MET":
        return [_gap(o, "CITIZENSHIP", True, f"{o.rule}; only {o.requirement[len('accepted: '):]} may apply", "NOT_ACCEPTED")]
    if o.outcome == "CONDITIONAL":
        return [_gap(o, "CERTIFICATE", False, f"obtain the {o.note or 'certificate of eligibility'} before applying", "ELIGIBILITY_CERTIFICATE")]
    return []

def _notice_gaps(o: rules.RuleOutcome) -> List[Gap]:
    if o.outcome != "CONDITIONAL":
        return []
    page = f" (p.{o.source_page})" if o.source_page else ""
    return [_gap(o, "NOTICE_CONDITION", False, f"an official notice{page} adds a condition for your category; meet it as quoted", "NOTICE")]

# ---------- relevance ----------

_PROFILE_HINTS = {
    "skills": ("skills", "add skills and certifications you can evidence"),
    "domain": ("experience.domain", "add the domain of each role, your specialisation, or project domains"),
    "location": ("preferred_locations", "add preferred posting locations"),
    "role": ("career_interests", "add the posts or fields you are aiming for"),
}

# Advertisement boilerplate that sits next to the duties text and says nothing about the work
_BOILERPLATE = {"candidates", "candidate", "commission", "union", "note", "discretion", "reasons", "recorded", "experience",
                "qualifications", "qualification", "relaxable", "otherwise", "additional", "nct", "writing", "well", "should"}

def _top_terms(text: str, exclude: set, n: int = 8) -> List[str]:
    keep = terms(text) - exclude - _BOILERPLATE
    counts = Counter(w for w in re.findall(r"[a-z]{3,}", text.lower()) if w in keep)
    return [w for w, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:n]]

def relevance_gaps(components: Sequence[dict], vac: Optional[VacancyText], profile_terms: set,
                   duties_req: Optional[rules.Requirement] = None, covered: Sequence[str] = ()) -> List[Gap]:
    """components: stored relevance breakdown dicts ({name, score, weight, evidence}); `covered` names components an
    eligibility gap already explains (e.g. experience), which are not repeated."""
    out = []
    for c in components:
        name, score, evidence = c["name"], c.get("score"), c.get("evidence") or []
        if name in covered:
            continue
        gap_type = f"RELEVANCE_{name.upper()}"
        if score is None:
            if name in _PROFILE_HINTS and evidence and "in profile" in evidence[0]:
                field, hint = _PROFILE_HINTS[name]
                out.append(Gap(gap_type, False, f"{name} relevance", evidence[0], f"{hint} so {name} relevance can be scored",
                               "NOT_SCORED", profile_field=field))
            continue
        if score >= RELEVANCE_THRESHOLD:
            continue
        step = f"{name} relevance {score:g}/100: {evidence[0] if evidence else ''}"
        req_id = page = None
        if name in ("skills", "domain") and vac is not None:
            text = " ".join([vac.duties, vac.desirable] + ([vac.post_name, vac.department or ""] if name == "domain" else []))
            missing = _top_terms(text, profile_terms)
            if missing:
                step += f"; the duties centre on {missing} — only add these if you have real, evidenced work in them"
            if duties_req is not None:
                req_id, page = duties_req.id, duties_req.source_page
        out.append(Gap(gap_type, False, f"{name} relevance", "; ".join(evidence), step, "LOW_SCORE", req_id, page))
    return out

# ---------- entry point ----------

def compute_gaps(profile: rules.ProfileSnapshot, reqs: Sequence[rules.Requirement], crucial: Optional[date],
                 outcomes: Sequence[rules.RuleOutcome], relevance_components: Sequence[dict] = (),
                 vac: Optional[VacancyText] = None, skills: Sequence[str] = ()) -> List[Gap]:
    gaps: List[Gap] = []
    for o in outcomes:
        if o.check == "AGE":
            gaps += _age_gaps(o, profile, reqs, crucial)
        elif o.check == "EDUCATION":
            gaps += _education_gaps(o, crucial)
        elif o.check == "EXPERIENCE":
            gaps += _experience_gaps(o, profile, crucial)
        elif o.check == "CITIZENSHIP":
            gaps += _citizenship_gaps(o)
        elif o.check == "NOTICE":
            gaps += _notice_gaps(o)
    profile_terms = terms(" ".join([f"{e.designation} {e.domain or ''} {e.responsibilities or ''}" for e in profile.experience]
                                   + [f"{e.degree} {e.specialization or ''}" for e in profile.education] + profile.certifications + list(skills)))
    duties = next((r for r in reqs if r.req_type == "DUTIES"), None)
    covered = {g.gap_type.lower() for g in gaps if g.gap_type in ("EDUCATION", "EXPERIENCE")}
    gaps += relevance_gaps(relevance_components, vac, profile_terms, duties, covered)
    # Blocking first, then by type, so the list reads as "what stops you" before "what would help"
    return sorted(gaps, key=lambda g: (not g.blocking, g.gap_type))
