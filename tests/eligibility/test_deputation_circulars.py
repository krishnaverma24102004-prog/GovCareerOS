"""
Deputation circulars parsed and checked, on real circulars captured on 2026-10-05 through the guarded fetcher
(tests/fixtures/deputation/meta.json has each file's URL, capture time and sha256):
  - CBSE No. CBSE/RC/ADVT./01/2026/2417 (Addl. IAFA, Senior Accounts Officer, Under Secretary (Legal))
  - NATGRID (MHA) No. A/4/2025-AD ESTT, "deputation (including short term contract)", 45 days from Employment News
  - DoPT No. A-12026/1/2023-AD-III, Despatch Rider on deputation/absorption, closing 30.09.2026
  - ICSIL deputation advertisement dated 25/09/2026 (a PSU; its post table is not parsed into posts)
Profiles below are inputs to the rules, not document facts.
"""
import json
from datetime import date
from functools import lru_cache
from pathlib import Path

import pytest

from govcareeros.backend.documents.pdf_text import extract_pages
from govcareeros.backend.eligibility import deputation as dep
from govcareeros.backend.eligibility.rules import ProfileSnapshot, Requirement, RuleOutcome, overall_status
from govcareeros.backend.parsing.deputation_circular import classify_mode, parse_circular
from govcareeros.backend.parsing.recruitment_mode import detect_in_pages, govt_service_requirement
from govcareeros.backend.sources.adapters import adapter_for

FIXTURES = Path(__file__).parents[1] / "fixtures" / "deputation"
CBSE = "Details_Advertisement_Deputation_15012026.pdf"
NATGRID = "NATGRIDVACAN_13062025.pdf"
DOPT = "dopt_despatch_rider_A-12026-1-2023-AD-III.pdf"
ICSIL = "Advertisement_-_Deputation_Posts25_9_2026.pdf"

@lru_cache(maxsize=None)
def _pages(name):
    return tuple(extract_pages((FIXTURES / name).read_bytes()))

def _circular(name):
    return parse_circular(_pages(name))

def _officer(**kw):
    base = dict(profile_version_id=None, date_of_birth=None, category=None, is_pwbd=None, nationality=None)
    return ProfileSnapshot(**{**base, **kw})

def _by_check(result):
    return {o.check: o for o in result["outcomes"]}

def test_fixtures_match_their_recorded_hashes():
    import hashlib
    meta = json.loads((FIXTURES / "meta.json").read_text(encoding="utf-8"))
    for name, m in meta.items():
        assert hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest() == m["sha256"], name
        assert m["url"].startswith("https://")

# ---------- parsing ----------

def test_cbse_circular_terms():
    c = _circular(CBSE)
    assert c.mode.value == ("DEPUTATION",) and c.mode.quote == "Mode of Recruitment : Deputation basis"
    assert c.max_age.value == {"years": 56, "on": "CLOSING_DATE"} and c.max_age.page == 1
    assert c.max_age.quote.endswith("is 56 years as on the closing date of receipt of application")
    assert (c.tenure_initial_years.value, c.tenure_max_years.value) == (3, 5)
    assert c.tenure_max_years.quote == "subject to a maximum period of five years"
    assert c.closing.value == {"kind": "DATE", "date": date(2026, 2, 13)}
    assert c.closing.quote == "submitted w.e.f. 15.01.2026 to 13.02.2026" and c.closing.page == 1
    assert c.feeder_bar.page == 1 and "direct line of promotion shall not be eligible" in c.feeder_bar.quote
    docs = {k: (q.page, q.quote) for k, q in c.documents.items()}
    assert docs["APAR_LAST_5_YEARS"] == (3, "ACRs of last 5 years")
    assert docs["VIGILANCE_CLEARANCE"] == (3, "Vigilance clearance")
    assert docs["PENALTY_STATEMENT"] == (3, "Major and Minor penalties imposed during last 10 years")
    assert docs["PROPER_CHANNEL"] == (3, "submitted by the applicant to his/her Cadre Controlling Authority")
    assert "INTEGRITY_CERTIFICATE" not in docs and "CADRE_CLEARANCE" not in docs

def test_cbse_posts_with_levels_and_service_clauses():
    posts = _circular(CBSE).posts
    assert [(p.name.value, p.name.page, p.pay_level.value) for p in posts] == [
        ("ADDITIONAL INTERNAL AUDITOR & FINANCIAL ADVISOR", 5, 12), ("SENIOR ACCOUNTS OFFICER", 6, 11), ("UNDER SECRETARY (LEGAL)", 7, 11)]
    iafa, sao, us = posts
    assert iafa.pay_level.quote == "Scale of Pay: Level-12"
    assert iafa.analogous.quote == "Holding analogous post on regular basis" and iafa.analogous.page == 5
    assert [(s.years, s.levels, s.regular, s.combined) for s in iafa.service] == [(5, (11,), True, False), (9, (9, 10), True, False)]
    assert iafa.service[0].quote == "05 years’ regular service in Pay Level-11 of 7th CPC or equivalent pay grade"
    # "either in Pay Level- 7/Level-8 ... or combined regular service in Level-7 & Level-8"
    assert [(s.years, s.levels, s.combined) for s in sao.service] == [(4, (9, 10), False), (8, (7, 8), True)]
    assert [(s.years, s.levels, s.combined) for s in us.service] == [(4, (10,), False), (8, (8, 7), True)]
    assert us.service[1].quote.endswith("or taken together.")

def test_natgrid_circular_terms():
    c = _circular(NATGRID)
    assert c.mode.value[0] == "DEPUTATION_STC" and c.mode.page == 2
    assert c.mode.quote == "Mode of Recruitment: By Deputation (including Short-term contract)"
    assert c.max_age.value == {"years": 54, "on": "CLOSING_DATE"}
    assert "not exceeding fifty-four years as on the last date of receipt of applications" in c.max_age.quote
    assert c.closing.value == {"kind": "EMPLOYMENT_NEWS_DAYS", "days": 45} and c.closing.page == 1
    assert c.closing.quote == "within a period of 45 days from the date of publication of the advertisement in Employment News"
    assert c.tenure_max_years.value == 5 and c.tenure_max_years.quote == "shall ordinarily not exceed five years"
    docs = {k: (q.page, q.quote) for k, q in c.documents.items()}
    assert docs["APAR_LAST_5_YEARS"] == (13, "APARs of last five years")
    assert docs["CADRE_CLEARANCE"] == (13, "cadre clearance")
    # Pro forma note 9.1: officers already on deputation send "Cadre Clearance, Vigilance Clearance and Integrity Certificate"
    assert docs["VIGILANCE_CLEARANCE"] == (15, "Vigilance Clearance") and docs["INTEGRITY_CERTIFICATE"] == (15, "Integrity Certificate")
    assert docs["PENALTY_STATEMENT"][0] == 17
    assert c.asks_last_deputation.page == 15 and c.asks_last_deputation.quote == "date of return from the last deputation"

def test_natgrid_posts_read_through_the_three_column_layout():
    posts = _circular(NATGRID).posts
    assert [p.name.value for p in posts[:6]] == [
        "Deputy Director (Relationship Management)", "Deputy Director (Solution Security)", "Deputy Director (Project Management)",
        "Assistant Director (IT Infra and Security)", "Assistant Director (BMS-Data Centre)", "Assistant Director (BMS-Disaster Recovery Centre)"]
    assert [p.pay_level.value for p in posts[:6]] == [11, 11, 11, 8, 8, 8]
    assert posts[2].pay_level.quote == "Pay Scale: Level-1 1"  # the PDF's own text layer splits "11"
    assert all(p.mode.value == ("DEPUTATION_STC",) for p in posts[:6])
    first = posts[0]
    assert first.analogous.quote == "holding analogous post on regular basis in the parent cadre or department"
    assert [(s.years, s.levels, s.regular) for s in first.service] == [(5, (10,), True)]
    assert first.service[0].quote.startswith("with five years regular service in the grade rendered after appointment thereto")
    # Post 4's clause runs into the duties column and its level ("7") lands after the next field: no level is guessed
    assert [(s.years, s.levels) for s in posts[3].service] == [(2, ())]
    assert [(s.years, s.levels) for s in posts[4].service] == [(2, (7,))]
    assert posts[4].max_age.value["years"] == 54 and posts[4].max_age.page == 7

def test_dopt_despatch_rider_circular():
    c = _circular(DOPT)
    assert c.mode.value == ("DEPUTATION", "ABSORPTION", "RE_EMPLOYMENT") and c.mode.quote == "Deputation/absorption:"
    # The text layer has the "ﬁ" ligature; NFKC folds it
    assert c.max_age.value == {"years": 56, "on": "CLOSING_DATE"} and c.max_age.page == 2
    assert "‘not exceeding fifty six years’ as on the closing date of receipt of applications" in c.max_age.quote
    assert c.closing.value == {"kind": "DATE", "date": date(2026, 9, 30)}
    assert c.closing.quote == "closing date of receipt of applications i.e. 30.09.2026"
    assert c.tenure_max_years.value == 3
    assert c.documents["APAR_LAST_5_YEARS"].quote == "ACR/APAR dossiers of last 5 years"
    assert c.documents["VIGILANCE_CLEARANCE"].quote == "vigilance clearance certificate"
    [post] = c.posts
    assert post.name.value == "Despatch Rider (DR)"
    # The post's own level, not the Level 1 the feeder grade is named in
    assert post.pay_level.value == 2 and post.pay_level.quote == "Level 2 (Rs. 19,900 - 63,200) in the pay matrix"
    assert post.analogous.quote == "holding the post of Despatch Rider or Multi Tasking Staff on regular basis"
    assert post.service == []

def test_icsil_psu_advertisement_terms():
    c = _circular(ICSIL)
    assert c.mode.value == ("DEPUTATION",) and c.mode.quote == "posts in ICSIL on deputation basis"
    assert c.closing.value == {"kind": "DATE", "date": date(2026, 10, 19)} and c.closing.page == 7
    assert (c.tenure_initial_years.value, c.tenure_extension_years.value) == (3, 2)
    assert c.tenure_extension_years.quote == "extended for a further period of two years"
    assert c.documents["PROPER_CHANNEL"].quote == "through proper channel" and c.documents["PROPER_CHANNEL"].page == 1
    assert c.documents["APAR_LAST_5_YEARS"].quote == "APARs attested copies for the last five years"
    assert c.max_age is None  # ages are per post in a table the text layer scrambles

def test_mode_phrases():
    assert classify_mode("deputation (including short-term contract)") == ("DEPUTATION_STC",)
    assert classify_mode("Deputation/absorption:") == ("DEPUTATION", "ABSORPTION")

# ---------- rules ----------

def _cbse_post(i):
    c = _circular(CBSE)
    return c, c.posts[i]

def test_age_56_on_the_closing_date():
    c, iafa = _cbse_post(0)
    closing = dep.closing_date(c)  # 13.02.2026
    within = dep.check_age(_officer(date_of_birth=date(1975, 3, 1)), c.max_age, closing)
    assert (within.outcome, within.data["age"], within.source_page) == ("MET", 50, 1)
    assert dep.check_age(_officer(date_of_birth=date(1970, 2, 13)), c.max_age, closing).outcome == "MET"  # 56 that day
    edge = dep.check_age(_officer(date_of_birth=date(1969, 6, 1)), c.max_age, closing)
    assert edge.outcome == dep.NEEDS_HUMAN_REVIEW and edge.data["age"] == 56
    over = dep.check_age(_officer(date_of_birth=date(1969, 1, 10)), c.max_age, closing)
    assert over.outcome == "NOT_MET" and over.requirement == c.max_age.quote

def test_level_and_years_route():
    c, iafa = _cbse_post(0)
    closing = dep.closing_date(c)
    met = dep.check_service(_officer(current_pay_level=11, current_level_regular_since=date(2020, 1, 1)), iafa, closing)
    assert met.outcome == "MET" and met.source_page == 5
    assert met.data["clauses"][0]["quote"] == iafa.service[0].quote
    # Level 11 for under 5 years and not Level 9/10: no clause is met, but the analogous route stays with a reviewer
    short = dep.check_service(_officer(current_pay_level=11, current_level_regular_since=date(2022, 6, 1)), iafa, closing)
    assert short.outcome == dep.NEEDS_HUMAN_REVIEW
    assert [cl["outcome"] for cl in short.data["clauses"]] == ["NOT_MET", "NOT_MET"]
    unknown = dep.check_service(_officer(), iafa, closing)
    assert unknown.outcome == "UNKNOWN"

def test_combined_levels_go_to_review():
    c, sao = _cbse_post(1)
    o = dep.check_service(_officer(current_pay_level=8, current_level_regular_since=date(2020, 1, 1)), sao, dep.closing_date(c))
    assert o.outcome == dep.NEEDS_HUMAN_REVIEW and "Level-7" in o.rule

def test_analogous_post_is_never_auto_decided():
    for name in (CBSE, NATGRID, DOPT):
        c = _circular(name)
        for post in c.posts:
            if post.analogous is None:
                continue
            o = dep.check_analogous(_officer(current_pay_level=11, organisation_cadre="CGA"), post)
            assert o.outcome == dep.NEEDS_HUMAN_REVIEW and o.requirement == post.analogous.quote

def test_broken_clause_and_missing_clause_go_to_review():
    natgrid = _circular(NATGRID)
    o = dep.check_service(_officer(current_pay_level=7, current_level_regular_since=date(2015, 1, 1)), natgrid.posts[3], date(2026, 1, 1))
    assert o.outcome == dep.NEEDS_HUMAN_REVIEW and "could not be read" in o.rule
    [rider] = _circular(DOPT).posts
    o = dep.check_service(_officer(current_pay_level=1, current_level_regular_since=date(2015, 1, 1)), rider, date(2026, 9, 30))
    assert o.outcome == dep.NEEDS_HUMAN_REVIEW and o.requirement == rider.analogous.quote

def test_employment_news_closing_needs_the_issue_date():
    c = _circular(NATGRID)
    assert dep.closing_date(c) is None
    o = dep.check_age(_officer(date_of_birth=date(1980, 1, 1)), c.max_age, dep.closing_date(c))
    assert o.outcome == "UNKNOWN" and "closing date" in o.rule
    assert dep.closing_date(c, employment_news_date=date(2025, 6, 21)) == date(2025, 8, 5)  # issue date + 45 days

def test_cooling_off_is_reviewer_confirmed():
    c = _circular(NATGRID)
    o = dep.check_cooling_off(_officer(last_deputation_end=date(2024, 3, 31)), c, as_of=date(2026, 10, 5))
    assert o.outcome == dep.NEEDS_HUMAN_REVIEW and o.data["rule_verified"] is False
    assert o.data["cooling_off_ends"] == "2027-03-31" and "6/8/2009-Estt.(Pay II)" in o.requirement
    assert (o.source_page, o.note) == (15, "date of return from the last deputation")
    never = dep.check_cooling_off(_officer(), _circular(CBSE), as_of=date(2026, 10, 5))
    assert never.outcome == dep.NEEDS_HUMAN_REVIEW and never.source_page is None

def test_forwarding_checklist_lists_only_what_the_circular_asks():
    natgrid = dep.forwarding_checklist(_circular(NATGRID))
    assert [i["code"] for i in natgrid] == ["APAR_LAST_5_YEARS", "VIGILANCE_CLEARANCE", "INTEGRITY_CERTIFICATE",
                                            "PENALTY_STATEMENT", "CADRE_CLEARANCE", "CLOSING"]
    assert natgrid[-1]["item"] == "Forwarded application must reach by: 45 days from Employment News publication"
    cbse = dep.forwarding_checklist(_circular(CBSE))
    assert [i["code"] for i in cbse] == ["PROPER_CHANNEL", "APAR_LAST_5_YEARS", "VIGILANCE_CLEARANCE", "PENALTY_STATEMENT", "NOC", "CLOSING"]
    assert all(i["quote"] and i["page"] for i in cbse)
    assert cbse[-1]["item"].endswith("2026-02-13")

def test_assess_cbse_post_end_to_end():
    c = _circular(CBSE)
    found = detect_in_pages(_pages(CBSE))
    req = Requirement("r1", "GOVT_SERVICE", govt_service_requirement(found), found.page)
    officer = _officer(date_of_birth=date(1975, 3, 1), is_central_govt_employee=True, current_pay_level=11,
                       current_level_regular_since=date(2020, 1, 1), organisation_cadre="Indian Audit & Accounts Department")
    [iafa, sao, us] = dep.assess(officer, c, as_of=date(2026, 1, 20), govt_service=req)
    checks = _by_check(iafa)
    assert checks["DEPUTATION_AGE"].outcome == "MET" and checks["DEPUTATION_SERVICE"].outcome == "MET"
    assert checks["ANALOGOUS_POST"].outcome == dep.NEEDS_HUMAN_REVIEW
    assert checks["COOLING_OFF"].outcome == dep.NEEDS_HUMAN_REVIEW
    assert checks["FEEDER_BAR"].outcome == dep.NEEDS_HUMAN_REVIEW and checks["FEEDER_BAR"].source_page == 1
    assert checks["GOVT_SERVICE"].outcome == "CONDITIONAL"
    # Reviewer items keep it out of ELIGIBLE; nothing here is INELIGIBLE
    assert iafa["status"] == "MANUAL_REVIEW" and iafa["closing_date"] == "2026-02-13"
    assert iafa["forwarding_checklist"][0]["code"] == "PROPER_CHANNEL"
    too_old = dep.assess_post(_officer(**{**officer.__dict__, "date_of_birth": date(1968, 1, 1)}), c, c.posts[0], as_of=date(2026, 1, 20))
    assert too_old["status"] == "INELIGIBLE"

def test_overall_status_treats_needs_human_review_as_manual_review():
    o = RuleOutcome("ANALOGOUS_POST", dep.NEEDS_HUMAN_REVIEW, "r", "q", "e")
    assert overall_status([RuleOutcome("A", "MET", "r", "q", "e"), o])[0] == "MANUAL_REVIEW"

# ---------- DoPT listing ----------

def test_dopt_listing_capture_links_the_captured_circular():
    meta = json.loads((FIXTURES / "meta.json").read_text(encoding="utf-8"))
    listing = meta["dopt_vacancy_circulars.html"]["url"]
    rows = adapter_for("DOPT").parse_listing((FIXTURES / "dopt_vacancy_circulars.html").read_text(encoding="utf-8"), listing)
    assert len(rows) == 90 and all(r.url.startswith("https://documents.doptcirculars.nic.in/") for r in rows)
    rider = next(r for r in rows if r.url == meta[DOPT]["url"])
    assert rider.title == "Filling up of 01 post of Despatch Rider (DR) on deputation basis in DoPT, New Delhi-reg."
    assert rider.advertisement_number == "A-12026/1/2023-Ad-III" and rider.published_on == date(2026, 8, 27)
    assert "No. A-12026/1/2023-AD-III" in _pages(DOPT)[0].text
