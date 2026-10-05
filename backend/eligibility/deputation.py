"""
Deterministic deputation checks for a serving government employee against one post of a parsed
deputation circular (backend/parsing/deputation_circular.py). Pure functions — no database, no LLM.

Outcomes use the rules.py vocabulary plus NEEDS_HUMAN_REVIEW: the circular's own words decide nothing the
code cannot read for certain, so these always go to a reviewer:
  - "holding analogous post on regular basis": whether a post is analogous is a judgement on duties and
    scale; it is never auto-decided
  - cooling-off after a previous deputation: see COOLING_OFF below
  - a level/years clause the layout broke apart, or service that may combine with earlier levels

Age: "not exceeding N years as on the closing date". Completed years below N pass and above N fail; an
officer who is N on the closing date (but past the N-th birthday) is left to the reviewer, because circulars
word this both ways ("not exceeding fifty-four years" vs "persons who will be crossing the age of 54 years
... need not apply", both in the NATGRID circular).
"""
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, List, Optional

from govcareeros.backend.eligibility.rules import (
    ProfileSnapshot, Requirement, RuleOutcome, age_on, check_govt_service, overall_status,
)
from govcareeros.backend.parsing.deputation_circular import DeputationCircular, DeputationPost, Quoted, ServiceClause

NEEDS_HUMAN_REVIEW = "NEEDS_HUMAN_REVIEW"

@dataclass(frozen=True)
class CoolingOffRule:
    years: int
    applies_up_to: str
    authority: str
    verified_quote: Optional[str]  # the rule's words from an official copy of the authority; None = not yet verified

# The 3-year cooling-off is commonly attributed to the OM below, but no official copy of it has been read
# into this project, so the rule is never applied on its own: a reviewer confirms it per case.
COOLING_OFF = CoolingOffRule(3, "Joint Secretary level", "DoPT OM No. 6/8/2009-Estt.(Pay II) dated 17.06.2010", None)

def add_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # 29 February
        return d.replace(year=d.year + years, day=28)

def closing_date(circular: DeputationCircular, employment_news_date: Optional[date] = None) -> Optional[date]:
    """The closing date the circular states, or Employment News publication + N days when it closes that way
    ("within 45 days from the date of publication": counted from the issue date, so the issue date + 45).

    `employment_news_date` must come from the Employment News issue itself; without it the date is unknown.
    """
    c = circular.closing
    if c is None:
        return None
    if c.value["kind"] == "DATE":
        return c.value["date"]
    if c.value["kind"] == "EMPLOYMENT_NEWS_DAYS" and employment_news_date is not None:
        return employment_news_date + timedelta(days=c.value["days"])
    return None

def _req(q: Optional[Quoted]) -> str:
    return q.quote if q else "-"

def check_age(profile: ProfileSnapshot, limit: Optional[Quoted], closing: Optional[date]) -> RuleOutcome:
    if limit is None:
        return RuleOutcome("DEPUTATION_AGE", "NOT_EVALUATED", "the circular states no maximum age", "-", "-")
    page, requirement, max_age = limit.page, limit.quote, limit.value["years"]
    if closing is None:
        return RuleOutcome("DEPUTATION_AGE", "UNKNOWN", "age is counted on the closing date, which is not known yet",
                           requirement, "-", source_page=page)
    if profile.date_of_birth is None:
        return RuleOutcome("DEPUTATION_AGE", "UNKNOWN", "date of birth missing in profile", requirement,
                           "date_of_birth: not provided", source_page=page)
    age = age_on(profile.date_of_birth, closing)
    evidence = f"DOB {profile.date_of_birth.isoformat()}, age {age} on closing date {closing.isoformat()}"
    data = {"age": age, "max_age": max_age, "closing_date": closing.isoformat()}
    if age < max_age or closing == add_years(profile.date_of_birth, max_age):
        return RuleOutcome("DEPUTATION_AGE", "MET", f"age {age} within the limit of {max_age}", requirement, evidence,
                           source_page=page, data=data)
    if age > max_age:
        return RuleOutcome("DEPUTATION_AGE", "NOT_MET", f"age {age} is above the limit of {max_age}", requirement, evidence,
                           source_page=page, data=data)
    return RuleOutcome("DEPUTATION_AGE", NEEDS_HUMAN_REVIEW, f"past the {max_age}th birthday on the closing date",
                       requirement, evidence, source_page=page, data=data,
                       note=f"circulars read 'not exceeding {max_age} years' both as completed years and as not crossing {max_age}")

def _clause_outcome(profile: ProfileSnapshot, clause: ServiceClause, closing: Optional[date]) -> Dict[str, object]:
    out: Dict[str, object] = {"years": clause.years, "levels": list(clause.levels), "page": clause.page, "quote": clause.quote}
    if not clause.levels:
        return {**out, "outcome": NEEDS_HUMAN_REVIEW, "rule": "the clause's pay level could not be read from the circular"}
    level, since = profile.current_pay_level, profile.current_level_regular_since
    if level is None or since is None or closing is None:
        missing = "closing date" if closing is None else "current pay level / regular service start in profile"
        return {**out, "outcome": "UNKNOWN", "rule": f"{missing} not known"}
    if level not in clause.levels:
        # Service in a listed level held before the current one is not in the profile
        return {**out, "outcome": "NOT_MET", "rule": f"current level {level} is not " + "/".join(f"Level-{l}" for l in clause.levels)}
    due = add_years(since, clause.years)
    if closing >= due:
        return {**out, "outcome": "MET", "rule": f"{clause.years} years in Level-{level} completed on {due.isoformat()}"}
    if clause.combined and len(clause.levels) > 1:
        return {**out, "outcome": NEEDS_HUMAN_REVIEW,
                "rule": f"Level-{level} service reaches {clause.years} years only on {due.isoformat()}; earlier service in "
                        + "/".join(f"Level-{l}" for l in clause.levels if l != level) + " may be counted together"}
    return {**out, "outcome": "NOT_MET", "rule": f"{clause.years} years in Level-{level} complete only on {due.isoformat()}"}

def check_service(profile: ProfileSnapshot, post: DeputationPost, closing: Optional[date]) -> RuleOutcome:
    """The level + years route. An analogous-post route, when the circular gives one, keeps a NOT_MET open."""
    evidence = f"current level: {profile.current_pay_level or 'not stated'}, regular since: " \
               f"{profile.current_level_regular_since.isoformat() if profile.current_level_regular_since else 'not stated'}"
    if not post.service:
        outcome = NEEDS_HUMAN_REVIEW if post.analogous else "NOT_EVALUATED"
        return RuleOutcome("DEPUTATION_SERVICE", outcome, "the circular states no level/years clause for this post",
                           _req(post.analogous), evidence, source_page=post.analogous.page if post.analogous else None)
    clauses = [_clause_outcome(profile, c, closing) for c in post.service]
    results = [c["outcome"] for c in clauses]
    best = next((c for c in clauses if c["outcome"] == "MET"), None)
    if best:
        outcome, rule = "MET", best["rule"]
    elif NEEDS_HUMAN_REVIEW in results:
        outcome, rule = NEEDS_HUMAN_REVIEW, next(c["rule"] for c in clauses if c["outcome"] == NEEDS_HUMAN_REVIEW)
    elif "UNKNOWN" in results:
        outcome, rule = "UNKNOWN", next(c["rule"] for c in clauses if c["outcome"] == "UNKNOWN")
    elif post.analogous:
        outcome, rule = NEEDS_HUMAN_REVIEW, "no level/years clause is met; only the analogous-post route remains"
    else:
        outcome, rule = "NOT_MET", "; ".join(c["rule"] for c in clauses)
    shown = best or clauses[0]
    return RuleOutcome("DEPUTATION_SERVICE", outcome, rule, " OR ".join(c.quote for c in post.service), evidence,
                       source_page=shown["page"], data={"clauses": clauses})

def check_analogous(profile: ProfileSnapshot, post: DeputationPost) -> Optional[RuleOutcome]:
    if post.analogous is None:
        return None
    evidence = f"organisation/cadre: {profile.organisation_cadre or 'not stated'}, current level: {profile.current_pay_level or 'not stated'}"
    return RuleOutcome("ANALOGOUS_POST", NEEDS_HUMAN_REVIEW, "whether a post is analogous is never decided automatically",
                       post.analogous.quote, evidence, source_page=post.analogous.page,
                       note=f"post: {post.name.value}" + (f", Level-{post.pay_level.value}" if post.pay_level else ""))

def check_cooling_off(profile: ProfileSnapshot, circular: DeputationCircular, as_of: date) -> RuleOutcome:
    rule = COOLING_OFF
    requirement = f"{rule.years}-year cooling-off after a deputation (up to {rule.applies_up_to}), {rule.authority}"
    asked = circular.asks_last_deputation
    data: Dict[str, object] = {"rule_verified": rule.verified_quote is not None}
    if profile.last_deputation_end is None:
        evidence = "last deputation end: not stated (none, or not entered)"
    else:
        gap = (as_of - profile.last_deputation_end).days
        data.update(last_deputation_end=profile.last_deputation_end.isoformat(), days_since=gap,
                    cooling_off_ends=add_years(profile.last_deputation_end, rule.years).isoformat())
        evidence = f"returned from last deputation on {profile.last_deputation_end.isoformat()} ({gap} days before {as_of.isoformat()})"
    why = "cooling-off rule not verified from an official copy; reviewer must confirm" if rule.verified_quote is None \
        else "reviewer confirms the officer's level and deputation history"
    return RuleOutcome("COOLING_OFF", NEEDS_HUMAN_REVIEW, why, requirement, evidence,
                       source_page=asked.page if asked else None, note=asked.quote if asked else None, data=data)

def check_feeder_bar(post: DeputationPost, circular: DeputationCircular) -> Optional[RuleOutcome]:
    bar = circular.feeder_bar
    if bar is None:
        return None
    return RuleOutcome("FEEDER_BAR", NEEDS_HUMAN_REVIEW, "departmental feeder-grade officers are barred; the profile cannot show this",
                       bar.quote, "-", source_page=bar.page, note=f"post: {post.name.value}")

# ---------- forwarding checklist ----------

CHECKLIST_LABELS = {
    "PROPER_CHANNEL": "Application forwarded through proper channel (cadre controlling authority)",
    "APAR_LAST_5_YEARS": "APAR/ACR copies for the last 5 years, attested",
    "VIGILANCE_CLEARANCE": "Vigilance clearance",
    "INTEGRITY_CERTIFICATE": "Integrity certificate",
    "PENALTY_STATEMENT": "Statement of major/minor penalties (last 10 years)",
    "CADRE_CLEARANCE": "Cadre clearance",
    "NOC": "No objection from the parent department / cadre controlling authority",
}

def forwarding_checklist(circular: DeputationCircular) -> List[Dict[str, object]]:
    """Only what the circular itself asks for, each with its page and words; nothing is added from practice."""
    items = [{"code": code, "item": CHECKLIST_LABELS[code], "page": q.page, "quote": q.quote}
             for code, q in circular.documents.items()]
    order = list(CHECKLIST_LABELS)
    items.sort(key=lambda i: order.index(i["code"]))
    if circular.closing:
        c = circular.closing
        when = c.value["date"].isoformat() if c.value["kind"] == "DATE" else f"{c.value['days']} days from Employment News publication"
        items.append({"code": "CLOSING", "item": f"Forwarded application must reach by: {when}", "page": c.page, "quote": c.quote})
    return items

# ---------- one post ----------

def assess_post(profile: ProfileSnapshot, circular: DeputationCircular, post: DeputationPost, *,
                as_of: date, employment_news_date: Optional[date] = None,
                govt_service: Optional[Requirement] = None) -> Dict[str, object]:
    closing = closing_date(circular, employment_news_date)
    outcomes: List[RuleOutcome] = []
    if govt_service is not None:
        outcomes += check_govt_service(profile, [govt_service], "DEPUTATION")
    outcomes.append(check_age(profile, post.max_age or circular.max_age, closing))
    outcomes.append(check_service(profile, post, closing))
    outcomes += [o for o in (check_analogous(profile, post), check_feeder_bar(post, circular)) if o]
    outcomes.append(check_cooling_off(profile, circular, as_of))
    status, decided = overall_status(outcomes)
    return {"post": post.name.value, "closing_date": closing.isoformat() if closing else None,
            "status": status, "decided_share": decided, "outcomes": outcomes,
            "forwarding_checklist": forwarding_checklist(circular)}

def assess(profile: ProfileSnapshot, circular: DeputationCircular, *, as_of: date,
           employment_news_date: Optional[date] = None, govt_service: Optional[Requirement] = None) -> List[Dict[str, object]]:
    return [assess_post(profile, circular, p, as_of=as_of, employment_news_date=employment_news_date, govt_service=govt_service)
            for p in circular.posts]
