"""
Deterministic hard-eligibility rules. Pure functions over plain data — no database, no LLM.

Each check returns a RuleOutcome with one of:
  MET            requirement satisfied, with evidence
  NOT_MET        requirement definitely not satisfied -> INELIGIBLE
  CONDITIONAL    satisfied subject to something the data cannot confirm (named in `note`)
  UNKNOWN        the requirement or the profile is not machine-decidable -> MANUAL_REVIEW
  NOT_EVALUATED  informational; the advertisement text for this criterion was not extracted

Education equivalence is never assumed: BCA is not B.Tech/BE, and a degree family only
matches when the requirement text names it (EXACT_MATCH) or explicitly declares the
candidate's qualification equivalent (EXPLICIT_EQUIVALENCE).
"""
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional, Sequence, Set, Tuple

from govcareeros.backend.parsing.upsc_advertisement import words_to_number

ENGINE_VERSION = "hard_eligibility/2"

# ---------- plain inputs ----------

@dataclass
class EducationEntry:
    degree: str
    specialization: Optional[str] = None
    education_level: Optional[str] = None
    end_date: Optional[date] = None
    equivalence: Optional[str] = None

@dataclass
class ExperienceEntry:
    organization: str
    designation: str
    domain: Optional[str] = None
    responsibilities: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None

@dataclass
class ProfileSnapshot:
    profile_version_id: object
    date_of_birth: Optional[date]
    category: Optional[str]
    is_pwbd: Optional[bool]
    nationality: Optional[str]
    education: List[EducationEntry] = field(default_factory=list)
    experience: List[ExperienceEntry] = field(default_factory=list)
    certifications: List[str] = field(default_factory=list)
    disability_percentage: Optional[int] = None
    disability_types: List[str] = field(default_factory=list)
    citizenship_status: Optional[str] = None
    is_ex_serviceman: Optional[bool] = None
    armed_forces_service_months: Optional[int] = None
    is_central_govt_employee: Optional[bool] = None
    central_govt_service_start: Optional[date] = None

@dataclass
class Requirement:
    id: object
    req_type: str
    value: object  # dict for AGE_MAX / AGE_RELAXATION / RESERVATION, text otherwise
    source_page: Optional[int] = None

@dataclass
class RuleOutcome:
    check: str
    outcome: str
    rule: str
    requirement: str
    profile_evidence: str
    requirement_id: object = None
    source_page: Optional[int] = None
    match_type: Optional[str] = None
    note: Optional[str] = None
    data: Dict[str, object] = field(default_factory=dict)  # structured facts behind the rule, e.g. years

# ---------- categories ----------

_CATEGORY_ALIASES = {
    "GEN": "UR", "GENERAL": "UR", "UR": "UR", "UNRESERVED": "UR",
    "EWS": "EWS", "OBC": "OBC", "OBC-NCL": "OBC", "OBC(NCL)": "OBC",
    "SC": "SC", "ST": "ST",
}

def normalize_category(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return _CATEGORY_ALIASES.get(re.sub(r"\s+", "", value).upper())

# ---------- age ----------

def age_on(dob: date, on: date) -> int:
    """Completed years on a date."""
    return on.year - dob.year - ((on.month, on.day) < (dob.month, dob.day))

def age_limit_for(reqs: Sequence[Requirement], category: Optional[str]) -> Optional[Requirement]:
    """A category-specific AGE_MAX wins; "ALL" is a single limit the notice states for everyone."""
    limits = [r for r in reqs if r.req_type == "AGE_MAX" and isinstance(r.value, dict)]
    return next((r for r in limits if r.value.get("category") == category), None) or \
        next((r for r in limits if r.value.get("category") == "ALL"), None)

def check_age(profile: ProfileSnapshot, reqs: Sequence[Requirement], crucial: Optional[date]) -> List[RuleOutcome]:
    limits = [r for r in reqs if r.req_type == "AGE_MAX"]
    if not limits:
        return [RuleOutcome("AGE", "NOT_EVALUATED", "no upper age limit extracted for this vacancy", "-", "-")]
    stated = ", ".join(f"{r.value['category']} {r.value['max_age']}" for r in limits)
    page = limits[0].source_page
    if crucial is None:
        return [RuleOutcome("AGE", "UNKNOWN", "age is judged on the crucial date, which was not extracted",
                            f"max age: {stated}", "-", source_page=page)]
    if profile.date_of_birth is None:
        return [RuleOutcome("AGE", "UNKNOWN", "date of birth missing in profile", f"max age: {stated}", "date_of_birth: not provided", source_page=page)]
    category = normalize_category(profile.category)
    if category is None:
        return [RuleOutcome("AGE", "UNKNOWN", "category missing or not recognised in profile",
                            f"max age: {stated}", f"category: {profile.category!r}", source_page=page)]

    age = age_on(profile.date_of_birth, crucial)
    evidence = f"DOB {profile.date_of_birth.isoformat()}, category {category}, age {age} on crucial date {crucial.isoformat()}"
    if age < 0:
        # Only a stored row that predates DOB validation can get here; never score impossible data
        return [RuleOutcome("AGE", "UNKNOWN", "date of birth is after the crucial date", f"max age: {stated}", evidence, source_page=page)]
    limit = age_limit_for(limits, category)
    if limit is None:
        # e.g. a post whose block states limits for OBC only; the general relaxation rules are not parsed
        return [RuleOutcome("AGE", "UNKNOWN", f"no age limit stated for category {category} in this vacancy",
                            f"max age: {stated}", evidence, source_page=page)]

    max_age = limit.value["max_age"]
    req_text = f"max age {max_age} for {limit.value['category']}"
    min_age = limit.value.get("min_age")
    if min_age is not None and age < min_age:
        # Relaxations only raise the upper limit
        return [RuleOutcome("AGE", "NOT_MET", f"age {age} < minimum {min_age}", f"age {min_age}-{max_age} for {limit.value['category']}",
                            evidence, limit.id, limit.source_page)]
    if age <= max_age:
        return [RuleOutcome("AGE", "MET", f"age {age} <= {max_age}", req_text, evidence, limit.id, limit.source_page)]

    options = relaxation_options(profile, reqs, category, crucial, age)
    fitting = [o for o in options if o.status != "NO" and age <= max_age + o.years]
    usable = [o for o in fitting if o.status == "OK"]
    if usable:
        best = max(usable, key=lambda o: o.years)
        return [RuleOutcome("AGE", "CONDITIONAL", f"age {age} within {best.basis}-relaxed limit {max_age + best.years}",
                            f"{req_text} (+{best.years} {best.basis}, p.{best.page})", f"{evidence}; {best.evidence}",
                            limit.id, limit.source_page, note=best.note)]
    if fitting:
        missing = "; ".join(f"{o.basis}: {o.evidence}" for o in fitting)
        return [RuleOutcome("AGE", "UNKNOWN", f"age {age} > {max_age}; a relaxation could apply but the profile doesn't settle it",
                            req_text, f"{evidence}; {missing}", limit.id, limit.source_page)]
    open_options = [o for o in options if o.status != "NO"]
    if len(open_options) > 1 and age <= max_age + sum(o.years for o in open_options):
        return [RuleOutcome("AGE", "UNKNOWN", f"age {age} fits only if relaxations {[o.basis for o in open_options]} are combined",
                            req_text, evidence, limit.id, limit.source_page,
                            note="the notification does not state whether these relaxations are cumulative")]
    rules_known = any(r.req_type == "AGE_RELAXATION_RULE" for r in reqs)
    # A category-specific limit already includes that category's concession; only a single "ALL" limit leaves it open
    may_have_relaxation = (limit.value["category"] == "ALL" and category != "UR") or any(
        flag is not False for flag in (profile.is_pwbd, profile.is_ex_serviceman, profile.is_central_govt_employee))
    if not rules_known and may_have_relaxation:
        # e.g. a notice that states one limit for everyone; its relaxation rules were not extracted
        return [RuleOutcome("AGE", "UNKNOWN", f"age {age} > {max_age}; the notice's age relaxation rules were not extracted",
                            req_text, evidence, limit.id, limit.source_page)]
    ruled_out = "; ".join(f"{o.basis}: {o.evidence}" for o in options) or "no relaxation rules found"
    return [RuleOutcome("AGE", "NOT_MET", f"age {age} > {max_age} and no applicable relaxation", req_text,
                        f"{evidence}; {ruled_out}", limit.id, limit.source_page)]

@dataclass
class RelaxationOption:
    basis: str
    years: int
    status: str  # OK (profile qualifies, certificate still needed), UNKNOWN (profile silent), NO
    evidence: str
    page: Optional[int]
    note: Optional[str] = None

def _service_years(start: Optional[date], until: date) -> Optional[float]:
    return None if start is None else (until - start).days / 365.25

def relaxation_options(profile: ProfileSnapshot, reqs: Sequence[Requirement], category: str,
                       crucial: date, age: int) -> List[RelaxationOption]:
    """Age relaxations stated in the notification (vacancy block + General Instructions), each judged separately."""
    general = {r.value.get("basis"): r for r in reqs if r.req_type == "AGE_RELAXATION_RULE"}
    options: List[RelaxationOption] = []

    # PwBD: years from the vacancy block if stated there, otherwise from the General Instructions
    vac_pwbd = next((r for r in reqs if r.req_type == "AGE_RELAXATION" and r.value.get("category") == "PwBD"), None)
    gen_pwbd = general.get("PWBD")
    if vac_pwbd or gen_pwbd:
        years = (vac_pwbd or gen_pwbd).value["years"]
        page = (vac_pwbd or gen_pwbd).source_page
        rule = gen_pwbd.value if gen_pwbd else {}
        suitability = next((r.value for r in reqs if r.req_type == "PWBD_SUITABILITY"), None)
        if profile.is_pwbd is False:
            options.append(RelaxationOption("PwBD", years, "NO", "profile: not PwBD", page))
        elif profile.is_pwbd is None:
            options.append(RelaxationOption("PwBD", years, "UNKNOWN", "PwBD status not stated", page))
        elif rule.get("max_age") and age > rule["max_age"]:
            options.append(RelaxationOption("PwBD", years, "NO", f"age {age} above PwBD cap {rule['max_age']}", gen_pwbd.source_page))
        elif profile.disability_percentage is None:
            options.append(RelaxationOption("PwBD", years, "UNKNOWN", "disability percentage not stated", page))
        elif profile.disability_percentage < rule.get("min_disability_percent", 40):
            options.append(RelaxationOption("PwBD", years, "NO",
                                            f"disability {profile.disability_percentage}% < {rule.get('min_disability_percent', 40)}%", page))
        elif suitability and not profile.disability_types:
            options.append(RelaxationOption("PwBD", years, "UNKNOWN", "disability sub-category not stated; post suitability depends on it", page))
        elif suitability:
            ok = [t for t in profile.disability_types if suitability.get(t, {}).get("suitable")]
            if ok:
                options.append(RelaxationOption("PwBD", years, "OK", f"PwBD {profile.disability_percentage}%, {','.join(ok)} suitable for this post", page,
                                                "needs a disability certificate in the prescribed format"))
            else:
                options.append(RelaxationOption("PwBD", years, "NO",
                                                f"sub-category {','.join(profile.disability_types)} not identified suitable for this post", page))
        else:
            options.append(RelaxationOption("PwBD", years, "OK", f"PwBD {profile.disability_percentage}%", page,
                                            "post suitability table not extracted; confirm the sub-category is suitable"))

    exsm = general.get("EX_SERVICEMEN")
    if exsm:
        need = exsm.value.get("min_service_months_after_attestation")
        if profile.is_ex_serviceman is False:
            options.append(RelaxationOption("EX_SERVICEMEN", exsm.value["years"], "NO", "profile: not an ex-serviceman", exsm.source_page))
        elif profile.is_ex_serviceman is None:
            options.append(RelaxationOption("EX_SERVICEMEN", exsm.value["years"], "UNKNOWN", "ex-serviceman status not stated", exsm.source_page))
        elif need and profile.armed_forces_service_months is None:
            options.append(RelaxationOption("EX_SERVICEMEN", exsm.value["years"], "UNKNOWN", "armed forces service months not stated", exsm.source_page))
        elif need and profile.armed_forces_service_months < need:
            options.append(RelaxationOption("EX_SERVICEMEN", exsm.value["years"], "NO",
                                            f"{profile.armed_forces_service_months} months service < {need}", exsm.source_page))
        else:
            options.append(RelaxationOption("EX_SERVICEMEN", exsm.value["years"], "OK",
                                            f"ex-serviceman, {profile.armed_forces_service_months} months service", exsm.source_page,
                                            "needs a release certificate from the Defence Forces"))

    cgs = general.get("CENTRAL_GOVT_SERVANT")
    if cgs:
        reserved_for_me = any(r.req_type == "RESERVATION" and r.value.get("category") == category and r.value.get("vacancies", 0) > 0 for r in reqs)
        years = cgs.value.get("years_if_post_reserved_for", {}).get(category, cgs.value["years"]) if reserved_for_me else cgs.value["years"]
        need = cgs.value.get("min_continuous_service_years")
        served = _service_years(profile.central_govt_service_start, crucial)
        if profile.is_central_govt_employee is False:
            options.append(RelaxationOption("CENTRAL_GOVT_SERVANT", years, "NO", "profile: not a Central Govt servant", cgs.source_page))
        elif profile.is_central_govt_employee is None:
            options.append(RelaxationOption("CENTRAL_GOVT_SERVANT", years, "UNKNOWN", "Central Govt service status not stated", cgs.source_page))
        elif need and served is None:
            options.append(RelaxationOption("CENTRAL_GOVT_SERVANT", years, "UNKNOWN", "Central Govt service start date not stated", cgs.source_page))
        elif need and served < need:
            options.append(RelaxationOption("CENTRAL_GOVT_SERVANT", years, "NO", f"{served:.1f} years continuous service < {need}", cgs.source_page))
        else:
            options.append(RelaxationOption("CENTRAL_GOVT_SERVANT", years, "OK", f"{served:.1f} years Central Govt service", cgs.source_page,
                                            "only for posts in the same line/allied cadre; decision rests with the Commission"))

    # "whichever may be more beneficial": PwBD and Central Govt servant are alternatives, never added
    return options

# ---------- citizenship ----------

def check_citizenship(profile: ProfileSnapshot, reqs: Sequence[Requirement]) -> List[RuleOutcome]:
    req = next((r for r in reqs if r.req_type == "CITIZENSHIP"), None)
    if req is None:
        return [RuleOutcome("CITIZENSHIP", "NOT_EVALUATED", "citizenship conditions not extracted for this vacancy", "-", "-")]
    accepted = req.value.get("accepted", [])
    needs_cert = req.value.get("certificate_of_eligibility_required", [])
    status = profile.citizenship_status
    derived = ""
    if status is None and profile.nationality and re.fullmatch(r"\s*indian?\s*", profile.nationality, re.I):
        status, derived = "INDIAN_CITIZEN", " (from nationality field)"
    requirement = f"accepted: {', '.join(accepted)}"
    if status is None:
        return [RuleOutcome("CITIZENSHIP", "UNKNOWN", "citizenship status not stated", requirement,
                            f"nationality: {profile.nationality!r}", req.id, req.source_page)]
    evidence = f"citizenship: {status}{derived}"
    if status not in accepted:
        return [RuleOutcome("CITIZENSHIP", "NOT_MET", f"{status} is not an accepted category", requirement, evidence, req.id, req.source_page)]
    if status in needs_cert:
        return [RuleOutcome("CITIZENSHIP", "CONDITIONAL", f"{status} accepted", requirement, evidence, req.id, req.source_page,
                            note="requires a certificate of eligibility issued by the Government of India")]
    return [RuleOutcome("CITIZENSHIP", "MET", f"{status} accepted", requirement, evidence, req.id, req.source_page)]

# ---------- education ----------

# Degree families: how a profile degree is recognised, and how a requirement names the family
_FAMILIES: Dict[str, Tuple[str, str]] = {
    "LAW": (r"\bLL\.?\s?B\b|\bB\.?A\.?\s?LL\.?B|\bLL\.?\s?M\b|bachelor of laws?|master of laws?",
            r"degree in law|law degree|\bLL\.?\s?B\b|\bLL\.?\s?M\b|master'?s degree in law"),
    "MEDICAL": (r"\bMBBS\b|bachelor of medicine|\bM\.?D\.?\b(?!\w)|\bM\.?S\.?\s*\(|\bDNB\b",
                r"medical qualification|\bMBBS\b|medical council"),
    "ENGINEERING": (r"\bB\.?\s?Tech\b|\bB\.?\s?E\.?\b(?!\w)|\bM\.?\s?Tech\b|\bM\.?\s?E\.?\b(?!\w)|bachelor of (?:engineering|technology)",
                    r"engineering|\bB\.?\s?Tech\b|\bB\.?\s?E\.?(?!\w)"),
    "SCIENCE": (r"\bB\.?\s?Sc\b|\bM\.?\s?Sc\b|bachelor of science|master of science",
                # "B.Tech in Computer Science" names a subject, not a science degree
                r"degree in (?:[A-Za-z]+\s){0,2}science|science (?:degree|graduate)|\bB\.?\s?Sc\b|\bM\.?\s?Sc\b"),
    "COMPUTER_APPLICATIONS": (r"\bBCA\b|\bMCA\b|computer applications",
                              r"computer applications|\bBCA\b|\bMCA\b"),
    "COMMERCE": (r"\bB\.?\s?Com\b|\bM\.?\s?Com\b|bachelor of commerce", r"commerce|\bB\.?\s?Com\b"),
    "ARTS": (r"\bB\.?\s?A\b(?!\.?\s?LL)|\bM\.?\s?A\b|bachelor of arts", r"\bdegree in arts\b|\bB\.?\s?A\.?(?!\w)"),
}
_ANY_DEGREE_RE = re.compile(r"degree in any discipline|graduat(?:e|ion) in any|any graduate|bachelor'?s degree(?!\s+in)|degree from a recogni[sz]ed university(?!\s+in)", re.I)
_MASTERS_RE = re.compile(r"\bmaster'?s degree|post[\s-]?graduate degree|\bM\.?\s?(?:Sc|A|Com|Tech)\b", re.I)
# Clauses that add conditions the degree family alone cannot satisfy
_EXTRA_CLAUSE_RE = re.compile(r"\bwith\b|\bunless\b|associateship|diploma in|registration|registered|practi(?:c|s)e|experience", re.I)
# Families that sit close enough that a mismatch is not certain (still never treated as a match)
_ADJACENT = {frozenset({"COMPUTER_APPLICATIONS", "SCIENCE"})}
_LEVEL = {"DOCTORATE": 4, "MASTERS": 3, "POSTGRADUATE": 3, "BACHELORS": 2, "GRADUATE": 2, "DIPLOMA": 1}

def degree_families(degree_text: str) -> Set[str]:
    return {name for name, (profile_re, _) in _FAMILIES.items() if re.search(profile_re, degree_text, re.I)}

def _degree_level(e: EducationEntry) -> int:
    if e.education_level and e.education_level.upper() in _LEVEL:
        return _LEVEL[e.education_level.upper()]
    if re.search(r"\bph\.?\s?d\b|doctor", e.degree, re.I):
        return 4
    if re.search(r"\bM\.?\s?(?:Sc|A|Com|Tech|E|CA|BA)\b|\bMCA\b|\bLL\.?\s?M\b|master|\bM\.?D\b|\bM\.?S\.?\s*\(", e.degree, re.I):
        return 3
    if re.search(r"diploma", e.degree, re.I):
        return 1
    return 2

def _abbrev_pattern(degree: str) -> str:
    return r"\b" + r"\.?\s?".join(re.escape(c) for c in re.sub(r"[^A-Za-z]", "", degree)) + r"\b"

def check_education(profile: ProfileSnapshot, reqs: Sequence[Requirement], crucial: Optional[date]) -> List[RuleOutcome]:
    edu_reqs = [r for r in reqs if r.req_type == "EDUCATION"]
    if not edu_reqs:
        return [RuleOutcome("EDUCATION", "NOT_EVALUATED", "no essential educational qualification extracted", "-", "-")]
    out = []
    for req in edu_reqs:
        text = str(req.value)
        short = text[:300]
        if not profile.education:
            out.append(RuleOutcome("EDUCATION", "NOT_MET", "profile has no education entries", short, "education: none",
                                   req.id, req.source_page, "MISMATCH"))
            continue

        required = {name for name, (_, req_re) in _FAMILIES.items() if re.search(req_re, text, re.I)}
        any_degree = bool(_ANY_DEGREE_RE.search(text))
        needs_masters = bool(_MASTERS_RE.search(text[:120]))  # a Master's requirement is stated up front
        extra = _EXTRA_CLAUSE_RE.search(text)

        best: Optional[Tuple[int, str, EducationEntry, str]] = None  # (rank, match_type, entry, why)
        for e in profile.education:
            fams = degree_families(f"{e.degree} {e.specialization or ''}")
            completed = e.end_date is None or crucial is None or e.end_date <= crucial
            if not completed:
                cand = (1, "MISMATCH", e, f"{e.degree} completes {e.end_date} after crucial date {crucial}")
            elif needs_masters and _degree_level(e) < 3:
                cand = (1, "MISMATCH", e, f"{e.degree} is below the Master's level required")
            elif re.search(_abbrev_pattern(e.degree) + r".{0,60}\bequivalent\b|\bequivalent\b.{0,60}" + _abbrev_pattern(e.degree), text, re.I) and len(re.sub(r"[^A-Za-z]", "", e.degree)) >= 2:
                cand = (4, "EXPLICIT_EQUIVALENCE", e, f"notification text declares {e.degree} equivalent")
            elif any_degree and _degree_level(e) >= 2:
                cand = (5, "EXACT_MATCH", e, f"{e.degree} is a degree; requirement accepts any discipline")
            elif fams & required:
                cand = (5, "EXACT_MATCH", e, f"{e.degree} is in required family {sorted(fams & required)}")
            elif not required and not any_degree:
                cand = (2, "UNKNOWN_EQUIVALENCE", e, "requirement text does not name a recognisable degree family")
            elif any(frozenset({f, r}) in _ADJACENT for f in fams for r in required):
                cand = (2, "UNKNOWN_EQUIVALENCE", e, f"{e.degree} ({sorted(fams)}) is not named; equivalence to {sorted(required)} is not stated")
            else:
                cand = (1, "MISMATCH", e, f"{e.degree} ({sorted(fams) or 'unrecognised'}) not in required {sorted(required) or ['any degree']}")
            if best is None or cand[0] > best[0]:
                best = cand

        rank, match_type, entry, why = best
        evidence = f"{entry.degree}" + (f" ({entry.specialization})" if entry.specialization else "") + \
                   (f", completed {entry.end_date}" if entry.end_date else ", completion date not recorded")
        if match_type in ("EXACT_MATCH", "EXPLICIT_EQUIVALENCE"):
            notes = []
            if extra:
                notes.append(f"requirement has an additional clause ('{extra.group(0)} ...') that must also be satisfied")
            if entry.end_date is None:
                notes.append("degree completion date not recorded in profile")
            outcome = "CONDITIONAL" if notes else "MET"
            out.append(RuleOutcome("EDUCATION", outcome, why, short, evidence, req.id, req.source_page, match_type, "; ".join(notes) or None))
        elif match_type == "UNKNOWN_EQUIVALENCE":
            out.append(RuleOutcome("EDUCATION", "UNKNOWN", why, short, evidence, req.id, req.source_page, match_type))
        else:
            out.append(RuleOutcome("EDUCATION", "NOT_MET", why, short, evidence, req.id, req.source_page, match_type))
    return out

# ---------- experience ----------

_YEARS_RE = re.compile(r"([A-Za-z]+|\d+)\s*(?:\(\s*\d+\s*\))?\s*years?['’]?\s+(?:of\s+)?(?:[\w-]+\s+){0,4}?experience", re.I)
_DOMAIN_RE = re.compile(r"experience\s+(?:in|of)\s+(?:the\s+)?(?:field\s+of\s+)?([A-Za-z][A-Za-z\s&/-]{2,60})", re.I)
_POST_QUALIFICATION_RE = re.compile(r"after\s+obtaining\s+(?:the\s+)?(post[\s-]?graduate\s+)?(?:degree|qualification)", re.I)
_STOPWORDS = {"the", "work", "working", "field", "of", "in", "and", "or", "as", "a", "an", "concerned", "teaching", "research", "advisory", "govt", "office"}

def _years_between(start: date, end: date) -> float:
    return max(0.0, (end - start).days / 365.25)

def check_experience(profile: ProfileSnapshot, reqs: Sequence[Requirement], crucial: Optional[date]) -> List[RuleOutcome]:
    exp_reqs = [r for r in reqs if r.req_type == "EXPERIENCE"]
    out = []
    for req in exp_reqs:
        text = str(req.value)
        short = text[:300]
        years = [n for n in (words_to_number(m.group(1)) for m in _YEARS_RE.finditer(text)) if n]
        if not years:
            out.append(RuleOutcome("EXPERIENCE", "UNKNOWN", "required years not stated in a machine-readable form", short, "-", req.id, req.source_page))
            continue
        required = min(years)  # alternatives joined by "Or": the smallest is the most lenient
        until = crucial or date.today()
        # "... after obtaining the post graduate degree": only experience after that qualification counts
        after = _POST_QUALIFICATION_RE.search(text)
        counted_from: Optional[date] = None
        if after:
            level = 3 if after.group(1) else 2
            dated = [e.end_date for e in profile.education if _degree_level(e) >= level and e.end_date]
            if not dated:
                what = "post-graduate degree" if level == 3 else "qualifying degree"
                out.append(RuleOutcome("EXPERIENCE", "UNKNOWN", f"only experience after the {what} counts, but its completion date is not in the profile",
                                       short, "-", req.id, req.source_page))
                continue
            counted_from = min(dated)
        spans = []
        for e in profile.experience:
            if e.start_date:
                start = max(e.start_date, counted_from) if counted_from else e.start_date
                spans.append((e, _years_between(start, min(e.end_date or until, until))))
        total = sum(y for _, y in spans)
        undated = [e for e in profile.experience if not e.start_date]
        evidence = (f"{total:.1f} years dated experience"
                    + (f" counted from {counted_from.isoformat()} (after qualification)" if counted_from else "")
                    + f" up to {until.isoformat()}" + (f"; {len(undated)} undated entries" if undated else ""))

        if total < required and not undated:
            out.append(RuleOutcome("EXPERIENCE", "NOT_MET", f"{total:.1f} years < {required} years required (all domains counted)",
                                   short, evidence, req.id, req.source_page,
                                   data={"years": round(total, 2), "required": required, "relevance_confirmed": False}))
            continue
        if total < required:
            out.append(RuleOutcome("EXPERIENCE", "UNKNOWN", "dated experience is short; undated entries may cover the gap", short, evidence, req.id, req.source_page))
            continue

        domains = [d.strip().lower() for d in _DOMAIN_RE.findall(text)]
        keywords = {w for d in domains for w in re.findall(r"[a-z]{4,}", d)} - _STOPWORDS
        relevant = [(e, y) for e, y in spans
                    if keywords and any(k in f"{e.designation} {e.domain or ''} {e.responsibilities or ''}".lower() for k in keywords)]
        relevant_years = sum(y for _, y in relevant)
        notes = []
        if keywords and relevant_years >= required:
            names = ", ".join(f"{e.designation} @ {e.organization}" for e, _ in relevant)
            outcome = "CONDITIONAL" if notes else "MET"
            out.append(RuleOutcome("EXPERIENCE", outcome, f"{relevant_years:.1f} years matching {sorted(keywords)} >= {required}",
                                   short, f"{names}; {evidence}", req.id, req.source_page, note="; ".join(notes) or None,
                                   data={"years": round(relevant_years, 2), "required": required, "relevance_confirmed": True}))
        else:
            out.append(RuleOutcome("EXPERIENCE", "UNKNOWN",
                                   f"{total:.1f} years total >= {required}, but relevance to the required field could not be confirmed",
                                   short, evidence, req.id, req.source_page, note="; ".join(notes) or None,
                                   data={"years": round(relevant_years, 2), "total_years": round(total, 2), "required": required, "relevance_confirmed": False}))
    return out

# ---------- combination ----------

# ---------- conditions added by later official notices ----------

_NOTICE_CATEGORY_RE = {
    "OBC": r"\bOBC\b", "SC": r"\bSC\b|scheduled\s+caste", "ST": r"\bST\b|scheduled\s+tribe",
    "EWS": r"\bEWS\b", "UR": r"\bUR\b|unreserved",
}

def check_notice_conditions(profile: ProfileSnapshot, reqs: Sequence[Requirement]) -> List[RuleOutcome]:
    conds = [r for r in reqs if r.req_type == "NOTICE_CONDITION"]
    if not conds:
        return []
    category = normalize_category(profile.category)
    mine = []
    for r in conds:
        text = str(r.value)
        hit = bool(category and re.search(_NOTICE_CATEGORY_RE.get(category, "$^"), text, re.I))
        hit = hit or bool(profile.is_pwbd and re.search(r"PwBD|disabilit", text, re.I))
        hit = hit or bool(profile.is_ex_serviceman and re.search(r"ex-?servicem", text, re.I))
        if hit:
            mine.append(r)
    if not mine:
        return [RuleOutcome("NOTICE", "NOT_EVALUATED", f"{len(conds)} notice condition(s) do not mention your category", "-", "-")]
    return [RuleOutcome("NOTICE", "CONDITIONAL", "an official notice adds a condition for your category", str(r.value)[:300],
                        f"category {category}" + (", PwBD" if profile.is_pwbd else ""), r.id, r.source_page,
                        note=str(r.value)[:300]) for r in mine]

def evaluate(profile: ProfileSnapshot, reqs: Sequence[Requirement], crucial: Optional[date]) -> List[RuleOutcome]:
    return check_age(profile, reqs, crucial) + check_education(profile, reqs, crucial) + \
        check_experience(profile, reqs, crucial) + check_citizenship(profile, reqs) + check_notice_conditions(profile, reqs)

def overall_status(outcomes: Sequence[RuleOutcome]) -> Tuple[str, float]:
    decisive = [o for o in outcomes if o.outcome != "NOT_EVALUATED"]
    if any(o.outcome == "NOT_MET" for o in decisive):
        status = "INELIGIBLE"
    elif any(o.outcome == "UNKNOWN" for o in decisive):
        status = "MANUAL_REVIEW"
    elif any(o.outcome == "CONDITIONAL" for o in decisive):
        status = "CONDITIONAL"
    else:
        status = "ELIGIBLE"
    # Share of checks decided outright (MET / NOT_MET); not a probability
    settled = sum(o.outcome in ("MET", "NOT_MET") for o in decisive)
    return status, round(settled / len(decisive), 2) if decisive else 0.0

def to_date(value) -> Optional[date]:
    if value is None:
        return None
    return value.date() if isinstance(value, datetime) else value
