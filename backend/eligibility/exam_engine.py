"""
Hard eligibility for an examination (optionally one post of it), per immutable profile version.

Reuses rules.check_age / check_education / check_citizenship on the exam's ExamRequirement rows and
adds only what exam notices state differently:

  * Age relaxations that are not "+N years": ex-servicemen get N years after deducting military
    service from their age; Central Government civilian employees with M years' service and
    widows/divorced women are allowed "up to X years of age". Where the profile cannot say
    (marital status is not recorded, disablement in operations is not recorded) the result is UNKNOWN,
    never MET.
  * School-level qualifications ("passed 12th Standard or equivalent"). A 12th/Class XII entry
    completed by the cut-off date meets it. A degree holder is CONDITIONAL: the notice still wants
    the 12th pass itself proved at document verification. A required stream ("Science stream with
    Mathematics") must be named in the entry's specialization.
  * Several education paragraphs for different posts are alternatives: without a post, the best one
    decides (can the candidate sit the exam at all?) and the others are reported, not counted.

Post-level requirement rows replace exam-level rows of the same type. Post-wise notices (CGL, JE) also give:

  * The birth-date window of the notice ("born not earlier than 02-08-1996 and not later than 01-08-2008"
    for an 18-30 post) decides age when a post's AGE_MAX row carries it: the upper limit must not have been
    reached on the crucial date. Older candidates go through the same relaxation rules, read that way.
  * Relaxation rules the notice states for Group 'C' posts only apply to posts classified Group "C"; rules stated
    for named posts only (CAPF 2026: widows and Delhi Police departmental candidates, "For the post of SI in Delhi
    Police") apply to those posts (post_keys). A departmental-candidate cap is never OK: the profile does not
    record departmental service, so it is UNKNOWN up to the cap and NO above it.
  * Without a post, every post is evaluated and the best one decides ("can I sit this exam for some post?");
    the result names that post and how many posts the profile is eligible for.
"""
import hashlib
import re
import uuid
from dataclasses import replace
from datetime import date, timezone
from typing import List, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from govcareeros.backend.eligibility import rules
from govcareeros.backend.eligibility import certificate_check
from govcareeros.backend.eligibility.engine import EligibilityInputError, load_profile_snapshot, requirement_value
from govcareeros.backend.models.exam import Exam, ExamEligibilityEvidence, ExamEligibilityResult, ExamPost, ExamRequirement
from govcareeros.backend.models.user import PersonalInfo, ProfileVersion
from govcareeros.backend.parsing.upsc_advertisement import IST

ENGINE_VERSION = f"exam_eligibility/2+{rules.ENGINE_VERSION}"

async def load_requirements(session: AsyncSession, exam_id: uuid.UUID, post_id: Optional[uuid.UUID]) -> List[rules.Requirement]:
    rows = (await session.execute(select(ExamRequirement).where(
        ExamRequirement.exam_id == exam_id, ExamRequirement.superseded_at.is_(None),
        ExamRequirement.post_id.is_(None) | (ExamRequirement.post_id == post_id),
    ))).scalars().all()
    post_types = {r.req_type for r in rows if r.post_id is not None}
    out = []
    for r in rows:
        if r.post_id is None and r.req_type in post_types:
            continue
        out.append(rules.Requirement(r.id, r.req_type, requirement_value(r), r.source_page))
    return out

# ---------- age ----------

def _applies(rule: dict, category: str) -> bool:
    return category in rule.get("categories", [category])

def exam_age_relaxations(profile: rules.ProfileSnapshot, reqs: Sequence[rules.Requirement], category: str, age: int,
                         limit: rules.Requirement, closing: Optional[date], gender: Optional[str] = None) -> List[rules.RelaxationOption]:
    """Relaxations stated as a cap or as years after service deduction; each judged for this candidate."""
    options = []
    base_max = limit.value.get("base_max_age", limit.value["max_age"])
    for r in reqs:
        v = r.value if r.req_type == "AGE_RELAXATION_RULE" and isinstance(r.value, dict) else None
        if not v or not _applies(v, category):
            continue
        basis = v.get("basis")
        if basis == "EX_SERVICEMEN_SERVICE_DEDUCTED":
            allowed = limit.value["max_age"] + v["years"]
            if profile.is_ex_serviceman is False:
                options.append(rules.RelaxationOption(basis, v["years"], "NO", "profile: not an ex-serviceman", r.source_page))
            elif profile.is_ex_serviceman is None or profile.armed_forces_service_months is None:
                options.append(rules.RelaxationOption(basis, v["years"], "UNKNOWN",
                                                      "ex-serviceman status or length of military service not stated", r.source_page))
            else:
                effective = age - profile.armed_forces_service_months / 12
                status = "OK" if effective <= allowed else "NO"
                options.append(rules.RelaxationOption(basis, v["years"], status,
                                                      f"age {age} minus {profile.armed_forces_service_months} months service = {effective:.1f} vs {allowed}",
                                                      r.source_page, "needs the ex-serviceman certificate and undertaking"))
        elif basis == "DISABLED_DEFENCE_PERSONNEL":
            status = "NO" if profile.is_ex_serviceman is False or age > base_max + v["years"] else "UNKNOWN"
            options.append(rules.RelaxationOption(basis, v["years"], status, "the profile does not record disablement in operations", r.source_page))
        elif basis == "CENTRAL_GOVT_CIVILIAN_EMPLOYEE":
            years, need = v["max_age"] - limit.value["max_age"], v.get("min_service_years")
            served = None if profile.central_govt_service_start is None or closing is None else (closing - profile.central_govt_service_start).days / 365.25
            if profile.is_central_govt_employee is False or age > v["max_age"]:
                options.append(rules.RelaxationOption(basis, years, "NO", "profile: not a Central Govt civilian employee"
                                                      if profile.is_central_govt_employee is False else f"age {age} > {v['max_age']}", r.source_page))
            elif profile.is_central_govt_employee is None or (need and served is None):
                options.append(rules.RelaxationOption(basis, years, "UNKNOWN", "Central Govt service status or start date not stated", r.source_page))
            elif need and served < need:
                options.append(rules.RelaxationOption(basis, years, "NO", f"{served:.1f} years service < {need} by the closing date", r.source_page))
            else:
                options.append(rules.RelaxationOption(basis, years, "OK", f"Central Govt civilian employee, up to {v['max_age']} years",
                                                      r.source_page, "regular and continuous service must be certified by the employer"))
        elif basis == "WIDOW_DIVORCED_SEPARATED":
            years = v["max_age"] - limit.value["max_age"]
            male = (gender or "").strip().lower() in ("male", "m")
            status = "NO" if male or age > v["max_age"] else "UNKNOWN"
            options.append(rules.RelaxationOption(basis, years, status, "the profile does not record marital status", r.source_page))
        elif basis == "DEPARTMENTAL_CANDIDATE":
            # e.g. Delhi Police constables/head constables/ASIs with 3 years' service; the profile does not record that service
            years = v["max_age"] - limit.value["max_age"]
            status = "NO" if age > v["max_age"] else "UNKNOWN"
            options.append(rules.RelaxationOption(basis, years, status, f"departmental service ({v.get('min_service_years') or '?'} years "
                                                  f"regular and continuous) is not recorded in the profile; up to {v['max_age']} years",
                                                  r.source_page))
    return options

def _post_group(classification: Optional[str]) -> Optional[str]:
    m = re.search(r"Group\s*\W?\s*([ABC])\b", classification or "")
    return m.group(1) if m else None

def scope_to_post(reqs: Sequence[rules.Requirement], post: ExamPost) -> List[rules.Requirement]:
    """Drops relaxation rules the notice states for another group of posts ("for Group 'C' posts") or for named
    posts only ("For the post of SI in Delhi Police": post_keys)."""
    group = _post_group(post.classification)

    def other_posts(v) -> bool:
        return bool((v.get("post_group") and v["post_group"] != group) or (v.get("post_keys") and post.post_key not in v["post_keys"]))
    return [r for r in reqs if not (r.req_type == "AGE_RELAXATION_RULE" and isinstance(r.value, dict) and other_posts(r.value))]

def check_exam_age(profile: rules.ProfileSnapshot, reqs: Sequence[rules.Requirement], crucial: Optional[date],
                   closing: Optional[date], gender: Optional[str] = None) -> List[rules.RuleOutcome]:
    category = rules.normalize_category(profile.category)
    limit = rules.age_limit_for(reqs, category) if category else None
    if limit is not None and limit.value.get("born_not_earlier_than") and profile.date_of_birth and crucial:
        return _check_birth_window(profile, reqs, limit, crucial, closing, gender)
    return _check_exam_age_years(profile, reqs, crucial, closing, gender)

def _check_birth_window(profile: rules.ProfileSnapshot, reqs: Sequence[rules.Requirement], limit: rules.Requirement, crucial: date,
                        closing: Optional[date], gender: Optional[str]) -> List[rules.RuleOutcome]:
    v = limit.value
    earliest, latest = date.fromisoformat(v["born_not_earlier_than"]), date.fromisoformat(v["born_not_later_than"])
    dob, age = profile.date_of_birth, rules.age_on(profile.date_of_birth, crucial)
    stated = f"born {earliest.isoformat()} to {latest.isoformat()} for {v['category']} (age limit {v.get('min_age')}-{v['max_age']})"
    evidence = f"DOB {dob.isoformat()}, category {v['category']}, age {age} on crucial date {crucial.isoformat()}"
    if dob > latest:
        return [rules.RuleOutcome("AGE", "NOT_MET", f"born after {latest.isoformat()}: below the minimum age on {crucial.isoformat()}",
                                  stated, evidence, limit.id, limit.source_page)]
    if dob >= earliest:
        return [rules.RuleOutcome("AGE", "MET", f"born on or after {earliest.isoformat()} and not after {latest.isoformat()}",
                                  stated, evidence, limit.id, limit.source_page)]
    # Born before the window: the upper limit was reached. Relaxations are judged on completed years with the limit
    # read as the notice states it (not reached = at most limit - 1 completed years)
    def reached(r: rules.Requirement) -> rules.Requirement:
        if r.req_type != "AGE_MAX" or not isinstance(r.value, dict) or not r.value.get("born_not_earlier_than"):
            return r
        value = dict(r.value, max_age=r.value["max_age"] - 1)
        if "base_max_age" in value:
            value["base_max_age"] -= 1
        return rules.Requirement(r.id, r.req_type, value, r.source_page)
    o = _check_exam_age_years(profile, [reached(r) for r in reqs], crucial, closing, gender)[0]
    return [replace(o, requirement=stated, rule=f"born before {earliest.isoformat()} (limit {v['max_age']} reached); {o.rule}")]

def _check_exam_age_years(profile: rules.ProfileSnapshot, reqs: Sequence[rules.Requirement], crucial: Optional[date],
                          closing: Optional[date], gender: Optional[str] = None) -> List[rules.RuleOutcome]:
    out = rules.check_age(profile, reqs, crucial)
    o = out[0]
    if o.outcome != "NOT_MET" or "no applicable relaxation" not in o.rule:
        return out
    category = rules.normalize_category(profile.category)
    age = rules.age_on(profile.date_of_birth, crucial)
    limit = rules.age_limit_for([r for r in reqs if r.req_type == "AGE_MAX"], category)
    options = exam_age_relaxations(profile, reqs, category, age, limit, closing, gender)
    usable = [x for x in options if x.status == "OK"]
    if usable:
        best = usable[0]
        return [rules.RuleOutcome("AGE", "CONDITIONAL", f"age {age} within the {best.basis} relaxation", o.requirement,
                                  f"{o.profile_evidence}; {best.evidence}", o.requirement_id, o.source_page, note=best.note)]
    open_ = [x for x in options if x.status == "UNKNOWN"]
    if open_:
        return [rules.RuleOutcome("AGE", "UNKNOWN", f"age {age} > {limit.value['max_age']}; a relaxation could apply but the profile doesn't settle it",
                                  o.requirement, o.profile_evidence + "; " + "; ".join(f"{x.basis}: {x.evidence}" for x in open_),
                                  o.requirement_id, o.source_page)]
    ruled_out = "; ".join(f"{x.basis}: {x.evidence}" for x in options)
    return [rules.RuleOutcome("AGE", "NOT_MET", o.rule, o.requirement, o.profile_evidence + (f"; {ruled_out}" if ruled_out else ""),
                              o.requirement_id, o.source_page)]

# ---------- education ----------

_SCHOOL_12 = re.compile(r"\b12th\b|\bXII\b|10\s*\+\s*2|higher\s+secondary|senior\s+secondary|intermediate", re.I)
_SCHOOL_10 = re.compile(r"\b10th\b|matricul|\bmatric\b|secondary\s+school|high\s+school|\bclass\s+x\b", re.I)
_DEGREE_REQ = re.compile(r"degree|graduat|diploma|bachelor", re.I)
_STREAM_RE = re.compile(r"Science\s+stream\s+with\s+Mathematics", re.I)
_HIGHER_LEVELS = {"BACHELORS", "GRADUATE", "MASTERS", "POSTGRADUATE", "DOCTORATE"}

def _school_level(text: str) -> int:
    return 12 if _SCHOOL_12.search(text) else 10 if _SCHOOL_10.search(text) else 0

def check_school_education(profile: rules.ProfileSnapshot, req: rules.Requirement, cutoff: Optional[date]) -> Optional[rules.RuleOutcome]:
    """None when the requirement is not school-level (then rules.check_education decides)."""
    text = str(req.value)
    need = _school_level(text)
    if not need or _DEGREE_REQ.search(text):
        return None
    short = text[:300]
    stream = bool(_STREAM_RE.search(text))
    best: Optional[rules.RuleOutcome] = None
    rank = {"MET": 4, "CONDITIONAL": 3, "UNKNOWN": 2, "NOT_MET": 1}
    for e in profile.education:
        label = f"{e.degree}" + (f" ({e.specialization})" if e.specialization else "")
        level = _school_level(e.degree)
        higher = (e.education_level or "").upper() in _HIGHER_LEVELS or (not level and rules.degree_families(label) and "diploma" not in e.degree.lower())
        late = e.end_date is not None and cutoff is not None and e.end_date > cutoff
        if late:
            o = rules.RuleOutcome("EDUCATION", "NOT_MET", f"{e.degree} completes {e.end_date} after the cut-off {cutoff}", short, label, req.id, req.source_page, "MISMATCH")
        elif level >= need:
            if stream and not e.specialization:
                o = rules.RuleOutcome("EDUCATION", "UNKNOWN", "the notice requires the Science stream with Mathematics; the stream is not recorded",
                                      short, label, req.id, req.source_page, "UNKNOWN_EQUIVALENCE")
            elif stream and not (re.search(r"science|\bPCM\b", e.specialization, re.I) and re.search(r"math|\bPCM\b", e.specialization, re.I)):
                o = rules.RuleOutcome("EDUCATION", "NOT_MET", f"stream '{e.specialization}' is not Science with Mathematics", short, label,
                                      req.id, req.source_page, "MISMATCH")
            else:
                o = rules.RuleOutcome("EDUCATION", "MET" if e.end_date else "CONDITIONAL", f"{e.degree} is a class {level} pass", short, label,
                                      req.id, req.source_page, "EXACT_MATCH", None if e.end_date else "completion date not recorded in profile")
        elif higher:
            o = rules.RuleOutcome("EDUCATION", "UNKNOWN" if stream else "CONDITIONAL",
                                  f"{e.degree} is above class {need}; the class {need} pass itself is not in the profile", short, label,
                                  req.id, req.source_page, "HIGHER_QUALIFICATION",
                                  "the notice asks for the class " + str(need) + " certificate at document verification")
        else:
            o = rules.RuleOutcome("EDUCATION", "UNKNOWN" if "diploma" in e.degree.lower() else "NOT_MET",
                                  f"{e.degree} is not recognised as a class {need} pass", short, label, req.id, req.source_page,
                                  "UNKNOWN_EQUIVALENCE" if "diploma" in e.degree.lower() else "MISMATCH")
        if best is None or rank[o.outcome] > rank[best.outcome]:
            best = o
    return best or rules.RuleOutcome("EDUCATION", "NOT_MET", "profile has no education entries", short, "education: none",
                                     req.id, req.source_page, "MISMATCH")

def check_exam_education(profile: rules.ProfileSnapshot, reqs: Sequence[rules.Requirement], cutoff: Optional[date]) -> List[rules.RuleOutcome]:
    edu = [r for r in reqs if r.req_type == "EDUCATION"]
    if not edu:
        return rules.check_education(profile, reqs, cutoff)
    outcomes = [check_school_education(profile, r, cutoff) or rules.check_education(profile, [r], cutoff)[0] for r in edu]
    if len(outcomes) == 1:
        return outcomes
    rank = {"MET": 4, "CONDITIONAL": 3, "UNKNOWN": 2, "NOT_MET": 1}
    best = max(outcomes, key=lambda o: rank.get(o.outcome, 0))
    # The other paragraphs are for other posts: reported for the reader, not counted in the status
    others = [rules.RuleOutcome("EDUCATION_ALTERNATIVE", "NOT_EVALUATED", f"{o.outcome}: {o.rule}", o.requirement, o.profile_evidence,
                                o.requirement_id, o.source_page, o.match_type, o.note) for o in outcomes if o is not best]
    return [best] + others

# ---------- evaluation ----------

def _day(dt) -> Optional[date]:
    return dt.astimezone(IST).date() if dt else None

def input_fingerprint(exam: Exam, post_id, reqs: List[rules.Requirement], as_of: Optional[date] = None) -> str:
    parts = [ENGINE_VERSION, str(post_id or "-"), *(d.astimezone(timezone.utc).isoformat() if d else "-" for d in
             (exam.age_crucial_date, exam.education_cutoff_date, exam.application_end)), *sorted(str(r.id) for r in reqs),
             *certificate_check.fingerprint_part(reqs, as_of)]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()

_STATUS_RANK = {"ELIGIBLE": 4, "CONDITIONAL": 3, "MANUAL_REVIEW": 2, "INELIGIBLE": 1}

async def _posts_with_own_rows(session: AsyncSession, exam_id: uuid.UUID) -> List[ExamPost]:
    """Posts that carry their own age/education rows (post-wise notices), in the notice's order."""
    ids = set((await session.execute(select(ExamRequirement.post_id).where(
        ExamRequirement.exam_id == exam_id, ExamRequirement.post_id.is_not(None), ExamRequirement.superseded_at.is_(None),
        ExamRequirement.req_type.in_(("AGE_MAX", "EDUCATION"))))).scalars())
    posts = (await session.execute(select(ExamPost).where(ExamPost.id.in_(ids)))).scalars().all() if ids else []
    return sorted(posts, key=lambda p: (p.source_page or 0, p.created_at, p.post_key))

async def _best_post(posts: List[ExamPost], judge) -> Tuple[List[rules.RuleOutcome], List[rules.Requirement]]:
    """No post chosen: every post is judged with its own rows and the best one decides; the others are counted, not shown."""
    judged = []
    for i, p in enumerate(posts):
        outcomes, reqs = await judge(p)
        status, confidence = rules.overall_status(outcomes)
        judged.append((_STATUS_RANK[status], confidence, -i, p, outcomes, reqs, status))
    best = max(judged, key=lambda j: j[:3])
    counts = {}
    for j in judged:
        counts[j[6]] = counts.get(j[6], 0) + 1
    p = best[3]
    summary = rules.RuleOutcome(
        "POST", "NOT_EVALUATED", f"the notice states age/qualification post-wise; best of {len(posts)} posts shown",
        f"{p.post_name} ({p.department or '-'}, {p.pay_level or '-'})",
        ", ".join(f"{k}: {v} posts" for k, v in sorted(counts.items(), key=lambda kv: -_STATUS_RANK[kv[0]])), source_page=p.source_page)
    return best[4] + [summary], [r for j in judged for r in j[5]]

async def evaluate_exam(session: AsyncSession, user_id: uuid.UUID, profile_version_id: uuid.UUID, exam_id: uuid.UUID,
                        post_id: Optional[uuid.UUID] = None) -> ExamEligibilityResult:
    version = await session.get(ProfileVersion, profile_version_id)
    if version is None or version.user_id != user_id:
        raise EligibilityInputError("profile version not found")
    exam = await session.get(Exam, exam_id)
    if exam is None:
        raise EligibilityInputError("exam not found")
    post = None
    if post_id is not None:
        post = await session.get(ExamPost, post_id)
        if post is None or post.exam_id != exam_id:
            raise EligibilityInputError("post not found in this exam")

    snapshot = await load_profile_snapshot(session, profile_version_id)
    gender = (await session.execute(select(PersonalInfo.gender).where(PersonalInfo.profile_version_id == profile_version_id))).scalar_one_or_none()
    closing = _day(exam.application_end)
    age_day = _day(exam.age_crucial_date) or closing
    edu_day = _day(exam.education_cutoff_date) or closing

    async def judge(p: Optional[ExamPost]) -> Tuple[List[rules.RuleOutcome], List[rules.Requirement]]:
        reqs = await load_requirements(session, exam_id, p.id if p else None)
        if p is not None:
            reqs = scope_to_post(reqs, p)
        return (check_exam_age(snapshot, reqs, age_day, closing, gender) + check_exam_education(snapshot, reqs, edu_day)
                + rules.check_citizenship(snapshot, reqs)), reqs

    post_wise = [] if post is not None else await _posts_with_own_rows(session, exam_id)
    if post_wise:
        outcomes, reqs = await _best_post(post_wise, judge)
    else:
        outcomes, reqs = await judge(post)
    if exam.age_crucial_date is None and closing is not None:
        outcomes.append(rules.RuleOutcome("CRUCIAL_DATE", "NOT_EVALUATED", "the notice text used does not state a crucial date for age; the closing date was used",
                                          "-", f"closing date {closing.isoformat()}"))
    status, confidence = rules.overall_status(outcomes)
    # Separate section, after the verdict so it cannot change status/confidence (see certificate_check)
    as_of = certificate_check.today()
    outcomes += certificate_check.check_certificates(snapshot, reqs, await certificate_check.load_held(session, profile_version_id),
                                                     closing, as_of)
    result = ExamEligibilityResult(
        id=uuid.uuid4(), user_id=user_id, exam_id=exam_id, post_id=post_id, profile_version_id=profile_version_id,
        engine_version=ENGINE_VERSION, crucial_date=exam.age_crucial_date or exam.application_end, status=status,
        confidence=confidence, input_fingerprint=input_fingerprint(exam, post_id, reqs, as_of),
    )
    session.add(result)
    for o in outcomes:
        session.add(ExamEligibilityEvidence(
            result_id=result.id, requirement_id=o.requirement_id, check_name=o.check, match_type=o.match_type,
            requirement=o.requirement, profile_evidence=o.profile_evidence,
            rule=o.rule + (f" [note: {o.note}]" if o.note else ""), outcome=o.outcome,
            source_page=str(o.source_page) if o.source_page else None,
        ))
    await session.flush()
    return await get_exam_result(session, user_id, result.id)

async def get_exam_result(session: AsyncSession, user_id: uuid.UUID, result_id: uuid.UUID) -> Optional[ExamEligibilityResult]:
    res = await session.execute(
        select(ExamEligibilityResult).options(selectinload(ExamEligibilityResult.evidence))
        .where(ExamEligibilityResult.id == result_id, ExamEligibilityResult.user_id == user_id)
        .execution_options(populate_existing=True)
    )
    return res.scalar_one_or_none()
