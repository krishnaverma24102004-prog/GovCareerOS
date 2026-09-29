"""
Career relevance: how well a vacancy fits a candidate, separate from eligibility.

Transparent and deterministic. Six components, each 0-100 with a weight and the evidence
used. A component the profile can't inform (e.g. no preferred locations) is reported as
not scored and its weight is redistributed, so a missing field never counts as a zero.
"""
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Sequence

from govcareeros.backend.eligibility import rules

ENGINE_VERSION = "career_relevance/1"

DEFAULT_WEIGHTS: Dict[str, float] = {
    "education": 25, "skills": 15, "experience": 20, "domain": 20, "location": 10, "role": 10,
}

# Education match -> score. Eligibility decides; relevance only reflects how close the fit is.
_EDUCATION_SCORES = {"EXACT_MATCH": 100, "EXPLICIT_EQUIVALENCE": 90, "UNKNOWN_EQUIVALENCE": 40, "MISMATCH": 0}

_STOP = {
    "with", "from", "that", "this", "their", "have", "will", "shall", "other", "work", "works", "officer", "officers",
    "post", "posts", "grade", "junior", "senior", "scale", "government", "department", "ministry", "india", "delhi",
    "national", "institute", "recognized", "recognised", "university", "degree", "assigned", "duties", "duty", "including",
    "such", "etc", "also", "under", "into", "time", "cases", "case", "competent", "authority", "concerned", "related",
    "matters", "matter", "any", "and", "the", "for", "all", "various", "general", "public", "service", "services",
    # common three-letter words; meaningful ones such as "law" or "tax" are kept
    "not", "are", "was", "may", "per", "its", "who", "has", "his", "her", "can", "one", "two", "out", "own", "use",
    "via", "new", "got", "but", "him", "our", "etc", "b.a", "iii", "viii",
}

def terms(text: Optional[str]) -> set:
    return {w for w in re.findall(r"[a-z]{3,}", (text or "").lower()) if w not in _STOP}

@dataclass
class VacancyText:
    post_name: str
    department: Optional[str] = None
    ministry: Optional[str] = None
    headquarters: Optional[str] = None
    duties: str = ""
    desirable: str = ""

@dataclass
class ProfileExtras:
    skills: List[str] = field(default_factory=list)
    project_domains: List[str] = field(default_factory=list)
    preferred_locations: Optional[str] = None
    career_interests: Optional[str] = None

@dataclass
class Component:
    name: str
    score: Optional[float]  # None = not scored (profile lacks the input)
    weight: float
    evidence: List[str]

@dataclass
class RelevanceResult:
    score: float
    components: List[Component]
    weights_used: Dict[str, float]

def _education(profile: rules.ProfileSnapshot, reqs: Sequence[rules.Requirement], crucial: Optional[date], vac: VacancyText) -> Component:
    outcomes = [o for o in rules.check_education(profile, reqs, crucial) if o.outcome != "NOT_EVALUATED"]
    if not outcomes:
        return Component("education", None, 0, ["no essential qualification extracted"])
    best = max(outcomes, key=lambda o: _EDUCATION_SCORES.get(o.match_type or "", 0))
    score = _EDUCATION_SCORES.get(best.match_type or "", 0)
    evidence = [f"{best.match_type}: {best.rule}"]
    if best.outcome == "CONDITIONAL":
        score = min(score, 80)
        evidence.append(f"capped at 80: {best.note}")
    candidate = terms(" ".join(f"{e.degree} {e.specialization or ''}" for e in profile.education))
    overlap = sorted(candidate & terms(vac.desirable))
    if overlap and score > 0:
        score = min(100, score + 10)
        evidence.append(f"+10 desirable qualification terms matched: {overlap}")
    return Component("education", score, 0, evidence)

def _skills(extras: ProfileExtras, profile: rules.ProfileSnapshot, vac: VacancyText, reqs: Sequence[rules.Requirement]) -> Component:
    names = [s for s in extras.skills + profile.certifications if s.strip()]
    if not names:
        return Component("skills", None, 0, ["no skills or certifications in profile"])
    vacancy_terms = terms(" ".join([vac.duties, vac.desirable] + [str(r.value) for r in reqs if r.req_type in ("EDUCATION", "EXPERIENCE")]))
    matched = [n for n in names if terms(n) & vacancy_terms]
    denominator = min(len(names), 5)
    score = round(100 * min(len(matched), denominator) / denominator, 1)
    return Component("skills", score, 0, [f"{len(matched)} of {len(names)} skills/certifications appear in duties or requirements: {matched or 'none'}",
                                          f"formula: matched / min(skills, 5) = {min(len(matched), denominator)}/{denominator}"])

def _experience(profile: rules.ProfileSnapshot, reqs: Sequence[rules.Requirement], crucial: Optional[date], vac: VacancyText) -> Component:
    if not profile.experience:
        return Component("experience", 0.0, 0, ["no experience in profile"])
    outcomes = rules.check_experience(profile, reqs, crucial)
    if outcomes:
        o = outcomes[0]
        if o.outcome in ("MET", "CONDITIONAL"):
            return Component("experience", 100.0, 0, [o.rule])
        if "required" in o.data:
            required = o.data["required"]
            if o.data.get("relevance_confirmed"):
                have, factor = o.data["years"], 1.0
            else:
                # Relevance unconfirmed: count total experience at half weight
                have, factor = o.data.get("total_years", o.data["years"]), 0.5
            score = round(100 * min(1.0, factor * have / required), 1)
            return Component("experience", score, 0, [o.rule, f"formula: min(1, {factor} x {have} / {required} years)"])
        return Component("experience", 0.0, 0, [o.rule, "requirement could not be measured against the profile"])
    # No stated experience requirement: reward experience whose field overlaps the duties
    duty_terms = terms(vac.duties + " " + vac.post_name)
    until = crucial or date.today()
    related = [(e, rules._years_between(e.start_date, min(e.end_date or until, until)))
               for e in profile.experience if e.start_date and terms(f"{e.designation} {e.domain or ''} {e.responsibilities or ''}") & duty_terms]
    years = sum(y for _, y in related)
    score = round(100 * min(1.0, years / 3), 1)
    return Component("experience", score, 0, [f"no experience requirement stated; {years:.1f} years in roles overlapping the duties",
                                              "formula: min(1, related years / 3)"])

def _domain(profile: rules.ProfileSnapshot, extras: ProfileExtras, vac: VacancyText) -> Component:
    candidate = terms(" ".join([f"{e.designation} {e.domain or ''}" for e in profile.experience]
                               + [e.specialization or "" for e in profile.education] + extras.project_domains))
    if not candidate:
        return Component("domain", None, 0, ["no experience domains, specialisations or project domains in profile"])
    vacancy = terms(" ".join([vac.post_name, vac.department or "", vac.ministry or "", vac.duties]))
    overlap = sorted(candidate & vacancy)
    denominator = min(len(vacancy), 8) or 1
    score = round(100 * min(len(overlap), denominator) / denominator, 1)
    return Component("domain", score, 0, [f"shared field terms: {overlap or 'none'}",
                                          f"formula: shared / min(vacancy terms, 8) = {min(len(overlap), denominator)}/{denominator}"])

def _location(extras: ProfileExtras, vac: VacancyText) -> Component:
    if not extras.preferred_locations:
        return Component("location", None, 0, ["no preferred locations in profile"])
    if not vac.headquarters:
        return Component("location", None, 0, ["headquarters not stated in the notification"])
    wanted = [w.strip() for w in re.split(r"[,;/]", extras.preferred_locations) if w.strip()]
    hits = [w for w in wanted if re.search(r"\b" + re.escape(w) + r"\b", vac.headquarters, re.I)]
    anywhere = bool(re.search(r"anywhere\s+in\s+India", vac.headquarters, re.I))
    if hits:
        score = 70.0 if anywhere else 100.0
        note = "headquarters matches; notification says posting can be anywhere in India" if anywhere else "headquarters matches"
        return Component("location", score, 0, [f"{note}: {hits} in '{vac.headquarters}'"])
    return Component("location", 30.0 if anywhere else 0.0, 0,
                     [f"preferred {wanted} not in '{vac.headquarters}'" + ("; transferable anywhere in India" if anywhere else "")])

def _role(profile: rules.ProfileSnapshot, extras: ProfileExtras, vac: VacancyText) -> Component:
    target = terms(vac.post_name)
    if not target:
        return Component("role", None, 0, ["post name has no distinctive terms"])
    interests = terms(extras.career_interests)
    held = terms(" ".join(e.designation for e in profile.experience))
    if not interests and not held:
        return Component("role", None, 0, ["no career interests or past designations in profile"])
    from_interests = sorted(target & interests)
    from_held = sorted(target & held)
    score = round(100 * len(set(from_interests) | set(from_held)) / len(target), 1)
    return Component("role", score, 0, [f"post terms {sorted(target)}; matched in stated interests {from_interests or 'none'}, "
                                        f"in past designations {from_held or 'none'}"])

def score_relevance(profile: rules.ProfileSnapshot, extras: ProfileExtras, reqs: Sequence[rules.Requirement],
                    vac: VacancyText, crucial: Optional[date], weights: Optional[Dict[str, float]] = None) -> RelevanceResult:
    weights = dict(weights or DEFAULT_WEIGHTS)
    components = [
        _education(profile, reqs, crucial, vac), _skills(extras, profile, vac, reqs), _experience(profile, reqs, crucial, vac),
        _domain(profile, extras, vac), _location(extras, vac), _role(profile, extras, vac),
    ]
    scored = [c for c in components if c.score is not None]
    total_weight = sum(weights[c.name] for c in scored)
    for c in components:
        c.weight = round(100 * weights[c.name] / total_weight, 2) if c.score is not None and total_weight else 0.0
    final = round(sum(c.score * c.weight for c in scored) / 100, 1) if total_weight else 0.0
    return RelevanceResult(final, components, weights)
