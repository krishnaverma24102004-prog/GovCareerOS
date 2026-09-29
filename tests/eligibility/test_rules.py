"""Unit tests for deterministic eligibility rules (no database)."""
from datetime import date

import pytest

from govcareeros.backend.eligibility.rules import (
    EducationEntry, ExperienceEntry, ProfileSnapshot, Requirement, age_on, check_age, check_education,
    check_experience, overall_status, RuleOutcome,
)

CRUCIAL = date(2026, 10, 16)

def profile(**kw):
    base = dict(profile_version_id="v", date_of_birth=date(1996, 5, 1), category="OBC", is_pwbd=False, nationality="Indian")
    base.update(kw)
    return ProfileSnapshot(**base)

def edu_req(text):
    return [Requirement("r", "EDUCATION", text, 8)]

def outcome(results):
    assert len(results) == 1
    return results[0]

# ---------- age ----------

def test_age_on_counts_completed_years():
    assert age_on(date(1996, 10, 16), CRUCIAL) == 30
    assert age_on(date(1996, 10, 17), CRUCIAL) == 29

AGE = [Requirement("a1", "AGE_MAX", {"category": "UR", "max_age": 30}, 8),
       Requirement("a2", "AGE_MAX", {"category": "OBC", "max_age": 33}, 8),
       Requirement("a3", "AGE_RELAXATION", {"category": "PwBD", "years": 10}, 8)]

def test_age_within_category_limit():
    o = outcome(check_age(profile(date_of_birth=date(1994, 1, 1)), AGE, CRUCIAL))  # 32
    assert o.outcome == "MET" and "33" in o.rule and o.source_page == 8

def test_age_over_limit_is_not_met():
    o = outcome(check_age(profile(date_of_birth=date(1990, 1, 1)), AGE, CRUCIAL))  # 36
    assert o.outcome == "NOT_MET"

def test_pwbd_relaxation_requires_stated_status():
    over = date(1985, 1, 1)  # 41: over 33, within 43
    assert outcome(check_age(profile(date_of_birth=over, is_pwbd=True), AGE, CRUCIAL)).outcome == "CONDITIONAL"
    assert outcome(check_age(profile(date_of_birth=over, is_pwbd=None), AGE, CRUCIAL)).outcome == "UNKNOWN"
    assert outcome(check_age(profile(date_of_birth=over, is_pwbd=False), AGE, CRUCIAL)).outcome == "NOT_MET"

def test_age_unknown_without_dob_category_or_stated_limit():
    assert outcome(check_age(profile(date_of_birth=None), AGE, CRUCIAL)).outcome == "UNKNOWN"
    assert outcome(check_age(profile(category=None), AGE, CRUCIAL)).outcome == "UNKNOWN"
    # SC candidate, but the vacancy block states limits only for UR and OBC
    assert outcome(check_age(profile(category="SC"), AGE, CRUCIAL)).outcome == "UNKNOWN"

def test_general_category_maps_to_ur_limit():
    o = outcome(check_age(profile(category="General", date_of_birth=date(1996, 5, 1)), AGE, CRUCIAL))  # 30
    assert o.outcome == "MET" and o.requirement == "max age 30 for UR"

# ---------- education ----------

def test_bca_is_not_a_law_degree():
    o = outcome(check_education(profile(education=[EducationEntry("BCA", end_date=date(2018, 6, 1))]),
                                edu_req("Degree in Law from a recognized Institute/University."), CRUCIAL))
    assert (o.outcome, o.match_type) == ("NOT_MET", "MISMATCH")

def test_llb_matches_degree_in_law_exactly():
    o = outcome(check_education(profile(education=[EducationEntry("LL.B.", end_date=date(2019, 6, 1))]),
                                edu_req("Degree in Law from a recognized Institute/University."), CRUCIAL))
    assert (o.outcome, o.match_type) == ("MET", "EXACT_MATCH")

def test_bca_is_never_assumed_equal_to_btech():
    o = outcome(check_education(profile(education=[EducationEntry("BCA", end_date=date(2018, 6, 1))]),
                                edu_req("B.E./B.Tech in Computer Science from a recognized University."), CRUCIAL))
    assert (o.outcome, o.match_type) == ("NOT_MET", "MISMATCH")

def test_bca_vs_degree_in_science_is_unknown_equivalence():
    o = outcome(check_education(profile(education=[EducationEntry("BCA", end_date=date(2018, 6, 1))]),
                                edu_req("Degree in Science from a recognized University."), CRUCIAL))
    assert (o.outcome, o.match_type) == ("UNKNOWN", "UNKNOWN_EQUIVALENCE")

def test_explicit_equivalence_in_notification_text():
    o = outcome(check_education(profile(education=[EducationEntry("BCA", end_date=date(2018, 6, 1))]),
                                edu_req("B.Sc. (Computer Science) or BCA shall be treated as equivalent qualification."), CRUCIAL))
    assert o.match_type in ("EXPLICIT_EQUIVALENCE", "EXACT_MATCH") and o.outcome == "MET"

def test_any_discipline_accepts_any_degree():
    o = outcome(check_education(profile(education=[EducationEntry("B.Com", end_date=date(2017, 6, 1))]),
                                edu_req("Degree in any discipline from a recognized University."), CRUCIAL))
    assert (o.outcome, o.match_type) == ("MET", "EXACT_MATCH")

def test_extra_clause_makes_match_conditional():
    text = ("Degree in Science or Engineering from a recognized University or Institute, with: "
            "(i) Associateship in Sugar Technology from a recognized University")
    o = outcome(check_education(profile(education=[EducationEntry("B.Sc.", end_date=date(2016, 6, 1))]), edu_req(text), CRUCIAL))
    assert (o.outcome, o.match_type) == ("CONDITIONAL", "EXACT_MATCH") and "with" in o.note

def test_degree_completed_after_crucial_date_does_not_count():
    o = outcome(check_education(profile(education=[EducationEntry("LL.B.", end_date=date(2027, 5, 1))]),
                                edu_req("Degree in Law from a recognized University."), CRUCIAL))
    assert o.outcome == "NOT_MET"

def test_masters_requirement_rejects_bachelors():
    o = outcome(check_education(profile(education=[EducationEntry("B.Sc.", end_date=date(2016, 6, 1))]),
                                edu_req("Master's Degree in Science from a recognized University."), CRUCIAL))
    assert o.outcome == "NOT_MET"

def test_unrecognisable_requirement_goes_to_manual_review():
    o = outcome(check_education(profile(education=[EducationEntry("B.Sc.", end_date=date(2016, 6, 1))]),
                                edu_req("Qualification as prescribed in the Recruitment Rules of the Department."), CRUCIAL))
    assert (o.outcome, o.match_type) == ("UNKNOWN", "UNKNOWN_EQUIVALENCE")

# ---------- experience ----------

LEGAL_EXP = [Requirement("x", "EXPERIENCE",
                         "Two years’ experience in Legal work as a Legal Practitioner. Or Two years’ experience in Legal work in Govt. Office.", 8)]

def test_experience_too_short_is_not_met():
    p = profile(experience=[ExperienceEntry("Firm", "Legal Associate", "Legal", None, date(2026, 1, 1), None)])
    assert outcome(check_experience(p, LEGAL_EXP, CRUCIAL)).outcome == "NOT_MET"

def test_relevant_experience_is_met_with_evidence():
    p = profile(experience=[ExperienceEntry("Firm", "Legal Associate", "Legal", None, date(2022, 1, 1), None)])
    o = outcome(check_experience(p, LEGAL_EXP, CRUCIAL))
    assert o.outcome == "MET" and "Legal Associate @ Firm" in o.profile_evidence

def test_long_but_unrelated_experience_needs_review():
    p = profile(experience=[ExperienceEntry("Shop", "Sales Executive", "Retail", None, date(2015, 1, 1), date(2022, 1, 1))])
    assert outcome(check_experience(p, LEGAL_EXP, CRUCIAL)).outcome == "UNKNOWN"

# ---------- combination ----------

@pytest.mark.parametrize("outcomes,status", [
    (["MET", "MET", "NOT_EVALUATED"], "ELIGIBLE"),
    (["MET", "CONDITIONAL"], "CONDITIONAL"),
    (["MET", "UNKNOWN", "CONDITIONAL"], "MANUAL_REVIEW"),
    (["NOT_MET", "UNKNOWN"], "INELIGIBLE"),
])
def test_overall_status(outcomes, status):
    assert overall_status([RuleOutcome("X", o, "", "", "") for o in outcomes])[0] == status
