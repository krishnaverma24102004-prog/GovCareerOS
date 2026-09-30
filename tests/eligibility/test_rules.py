"""Unit tests for deterministic eligibility rules (no database)."""
from datetime import date

import pytest

from govcareeros.backend.eligibility.rules import (
    check_citizenship, EducationEntry, ExperienceEntry, ProfileSnapshot, Requirement, age_on, check_age, check_education,
    check_experience, overall_status, RuleOutcome,
)
from govcareeros.backend.extraction.validator import age_limits_in

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
    answered = dict(is_pwbd=False, is_ex_serviceman=False, is_central_govt_employee=False)
    o = outcome(check_age(profile(date_of_birth=date(1990, 1, 1), **answered), AGE, CRUCIAL))  # 36
    assert o.outcome == "NOT_MET"

def test_over_age_stays_open_when_relaxation_rules_and_answers_are_missing():
    # No General Instructions extracted and ex-serviceman / govt-servant status not answered
    o = outcome(check_age(profile(date_of_birth=date(1990, 1, 1)), AGE, CRUCIAL))
    assert o.outcome == "UNKNOWN" and "relaxation rules were not extracted" in o.rule

def test_single_limit_for_all_categories():
    all_limit = [Requirement("a", "AGE_MAX", {"category": "ALL", "max_age": 56}, 1)]
    answered = dict(is_pwbd=False, is_ex_serviceman=False, is_central_govt_employee=False)
    assert outcome(check_age(profile(date_of_birth=date(1972, 1, 1), **answered), all_limit, CRUCIAL)).outcome == "MET"  # 54
    ur_over = outcome(check_age(profile(date_of_birth=date(1968, 1, 1), category="UR", **answered), all_limit, CRUCIAL))  # 58
    assert ur_over.outcome == "NOT_MET" and ur_over.requirement == "max age 56 for ALL"
    # An SC candidate over a single "ALL" limit may still have a concession the notice text states elsewhere
    sc_over = outcome(check_age(profile(date_of_birth=date(1968, 1, 1), category="SC", **answered), all_limit, CRUCIAL))
    assert sc_over.outcome == "UNKNOWN"

def test_pwbd_relaxation_requires_stated_status():
    over = date(1985, 1, 1)  # 41: over 33, within 43
    assert outcome(check_age(profile(date_of_birth=over, is_pwbd=True, disability_percentage=45), AGE, CRUCIAL)).outcome == "CONDITIONAL"
    # The General Instructions require at least 40% disability; without a percentage it can't be settled
    assert outcome(check_age(profile(date_of_birth=over, is_pwbd=True), AGE, CRUCIAL)).outcome == "UNKNOWN"
    assert outcome(check_age(profile(date_of_birth=over, is_pwbd=None), AGE, CRUCIAL)).outcome == "UNKNOWN"
    assert outcome(check_age(profile(date_of_birth=over, is_pwbd=False, is_ex_serviceman=False, is_central_govt_employee=False),
                             AGE, CRUCIAL)).outcome == "NOT_MET"

def test_age_unknown_without_dob_category_or_stated_limit():
    assert outcome(check_age(profile(date_of_birth=None), AGE, CRUCIAL)).outcome == "UNKNOWN"
    assert outcome(check_age(profile(category=None), AGE, CRUCIAL)).outcome == "UNKNOWN"
    # SC candidate, but the vacancy block states limits only for UR and OBC
    assert outcome(check_age(profile(category="SC"), AGE, CRUCIAL)).outcome == "UNKNOWN"

def test_general_category_maps_to_ur_limit():
    o = outcome(check_age(profile(category="General", date_of_birth=date(1996, 5, 1)), AGE, CRUCIAL))  # 30
    assert o.outcome == "MET" and o.requirement == "max age 30 for UR"

def test_below_minimum_age_is_not_met():
    # ITBP Head Constable, verbatim: "Between 18 and 25 years" -> {"category": "ALL", "min_age": 18, "max_age": 25}
    itbp = [Requirement("a", "AGE_MAX", limit, 3) for limit in age_limits_in("Between 18 and 25 years")]
    o = outcome(check_age(profile(date_of_birth=date(2010, 1, 1)), itbp, CRUCIAL))  # 16
    assert o.outcome == "NOT_MET" and o.rule == "age 16 < minimum 18" and o.source_page == 3
    assert outcome(check_age(profile(date_of_birth=date(2008, 10, 16)), itbp, CRUCIAL)).outcome == "MET"  # 18 on the day

def test_dob_after_crucial_date_is_never_scored():
    # Hand-built to reproduce the reported bug (DOB 2030 stored before input validation existed); no real document shows it
    o = outcome(check_age(profile(date_of_birth=date(2030, 1, 1)), AGE, CRUCIAL))
    assert o.outcome == "UNKNOWN" and "after the crucial date" in o.rule

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

# ---------- General Instructions relaxations (values as extracted from UPSC Advt 12/2026, pp.14, 21-22) ----------

GENERAL = [
    Requirement("g1", "AGE_RELAXATION_RULE", {"basis": "EX_SERVICEMEN", "years": 5, "min_service_months_after_attestation": 6}, 21),
    Requirement("g2", "AGE_RELAXATION_RULE", {"basis": "CENTRAL_GOVT_SERVANT", "years": 5,
                                              "years_if_post_reserved_for": {"SC": 10, "ST": 10, "OBC": 8},
                                              "min_continuous_service_years": 3, "decided_by_commission": True}, 22),
    Requirement("g3", "AGE_RELAXATION_RULE", {"basis": "PWBD", "years": 10, "max_age": 56, "min_disability_percent": 40,
                                              "requires_post_suitability": True, "not_cumulative_with": ["CENTRAL_GOVT_SERVANT"]}, 22),
    Requirement("g4", "CITIZENSHIP", {"accepted": ["INDIAN_CITIZEN", "NEPAL_SUBJECT", "BHUTAN_SUBJECT", "TIBETAN_REFUGEE_PRE_1962", "PIO_MIGRANT"],
                                      "certificate_of_eligibility_required": ["NEPAL_SUBJECT", "BHUTAN_SUBJECT", "TIBETAN_REFUGEE_PRE_1962", "PIO_MIGRANT"]}, 14),
]
OBC_ONLY = [Requirement("a", "AGE_MAX", {"category": "OBC", "max_age": 38}, 2),
            Requirement("r", "RESERVATION", {"category": "OBC", "vacancies": 1}, 1),
            Requirement("p", "PWBD_SUITABILITY", {"OL": {"suitable": True, "reserved": False}, "LV": {"suitable": False, "reserved": False}}, 1)]
OVER_BY_3 = date(1985, 1, 1)   # 41 on 2026-10-16, limit 38

def settled(**kw):
    """A profile that has answered every relaxation question (unanswered ones stay open, correctly, as UNKNOWN)."""
    base = dict(is_pwbd=False, is_ex_serviceman=False, is_central_govt_employee=False)
    base.update(kw)
    return profile(**base)

def test_ex_serviceman_relaxation_needs_six_months_service():
    ok = outcome(check_age(settled(date_of_birth=OVER_BY_3, is_ex_serviceman=True, armed_forces_service_months=60), OBC_ONLY + GENERAL, CRUCIAL))
    assert ok.outcome == "CONDITIONAL" and "EX_SERVICEMEN" in ok.rule and "p.21" in ok.requirement
    short = outcome(check_age(settled(date_of_birth=OVER_BY_3, is_ex_serviceman=True, armed_forces_service_months=4), OBC_ONLY + GENERAL, CRUCIAL))
    assert short.outcome == "NOT_MET"
    unknown = outcome(check_age(settled(date_of_birth=OVER_BY_3, is_ex_serviceman=True), OBC_ONLY + GENERAL, CRUCIAL))
    assert unknown.outcome == "UNKNOWN"

def test_central_govt_servant_gets_8_years_on_post_reserved_for_obc():
    p = settled(date_of_birth=date(1980, 1, 1), is_central_govt_employee=True, central_govt_service_start=date(2015, 4, 1))  # 46
    o = outcome(check_age(p, OBC_ONLY + GENERAL, CRUCIAL))
    assert o.outcome == "CONDITIONAL" and "+8 CENTRAL_GOVT_SERVANT" in o.requirement
    new_joiner = settled(date_of_birth=OVER_BY_3, is_central_govt_employee=True, central_govt_service_start=date(2025, 1, 1))
    assert outcome(check_age(new_joiner, OBC_ONLY + GENERAL, CRUCIAL)).outcome == "NOT_MET"  # < 3 years service

def test_pwbd_relaxation_follows_post_suitability_and_age_cap():
    suitable = settled(date_of_birth=OVER_BY_3, is_pwbd=True, disability_percentage=50, disability_types=["OL"])
    assert outcome(check_age(suitable, OBC_ONLY + GENERAL, CRUCIAL)).outcome == "CONDITIONAL"
    unsuitable = settled(date_of_birth=OVER_BY_3, is_pwbd=True, disability_percentage=50, disability_types=["LV"])
    assert outcome(check_age(unsuitable, OBC_ONLY + GENERAL, CRUCIAL)).outcome == "NOT_MET"
    low = settled(date_of_birth=OVER_BY_3, is_pwbd=True, disability_percentage=30, disability_types=["OL"])
    assert outcome(check_age(low, OBC_ONLY + GENERAL, CRUCIAL)).outcome == "NOT_MET"
    over_cap = settled(date_of_birth=date(1969, 1, 1), is_pwbd=True, disability_percentage=50, disability_types=["OL"])  # 57
    assert outcome(check_age(over_cap, OBC_ONLY + GENERAL, CRUCIAL)).outcome == "NOT_MET"

def test_relaxations_are_not_silently_added_together():
    # 50: needs +12; PwBD (10) or govt service (8) alone is not enough, and the text makes them alternatives
    p = settled(date_of_birth=date(1976, 1, 1), is_pwbd=True, disability_percentage=50, disability_types=["OL"],
                is_central_govt_employee=True, central_govt_service_start=date(2010, 1, 1), is_ex_serviceman=False)
    o = outcome(check_age(p, OBC_ONLY + GENERAL, CRUCIAL))
    assert o.outcome == "UNKNOWN" and "combined" in o.rule

@pytest.mark.parametrize("status,nationality,expected", [
    ("INDIAN_CITIZEN", None, "MET"),
    (None, "Indian", "MET"),
    ("NEPAL_SUBJECT", None, "CONDITIONAL"),
    ("OTHER", None, "NOT_MET"),
    (None, None, "UNKNOWN"),
])
def test_citizenship(status, nationality, expected):
    assert outcome(check_citizenship(profile(citizenship_status=status, nationality=nationality), GENERAL)).outcome == expected

def test_experience_after_postgraduate_degree_only():
    req = [Requirement("x", "EXPERIENCE", "Three years’ experience in the concerned speciality after obtaining the post graduate degree.", 10)]
    p = profile(education=[EducationEntry("MBBS", end_date=date(2018, 3, 1)), EducationEntry("MD (Cardiology)", education_level="MASTERS", end_date=date(2024, 6, 1))],
                experience=[ExperienceEntry("AIIMS", "Senior Resident Cardiology", "Cardiology", None, date(2019, 1, 1), None)])
    o = outcome(check_experience(p, req, CRUCIAL))
    # 7.8 years in total, but only 2.4 after the MD: not enough
    assert o.outcome == "NOT_MET" and "counted from 2024-06-01" in o.profile_evidence
    no_pg_date = profile(education=[EducationEntry("MD (Cardiology)", education_level="MASTERS")],
                         experience=[ExperienceEntry("AIIMS", "Senior Resident", "Cardiology", None, date(2019, 1, 1), None)])
    assert outcome(check_experience(no_pg_date, req, CRUCIAL)).outcome == "UNKNOWN"
