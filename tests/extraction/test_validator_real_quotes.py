"""Validator rules on quotes taken verbatim from real notices (CRPF, ITBP, MHA, BSF, UPSC) seen on 30-09-2026."""
from datetime import datetime
from pathlib import Path

import pytest

from govcareeros.backend.documents.pdf_text import PageText, extract_pages
from govcareeros.backend.extraction.validator import IST, age_limits_in, dates_in, find_quote, verify_field

UPSC = Path(__file__).parents[1] / "fixtures" / "upsc"

@pytest.mark.parametrize("quote,expected", [
    ("maximum age limit for appointment on contractual basis shall not be exceeding fifty-two (52) years on the date of walk-in-interview",
     [{"category": "ALL", "max_age": 52}]),                                                         # CRPF Commandant (Architect)
    ("Between 18 and 25 years", [{"category": "ALL", "min_age": 18, "max_age": 25}]),                # ITBP Head Constable
    ("Note-3: The upper age-limit for appointment by deputation (including short-term contract) shall not be exceeding 56 years",
     [{"category": "ALL", "max_age": 56}]),                                                         # MHA / SSB
    ("The maximum age limit for appointment on deputation shall be 56 years.", [{"category": "ALL", "max_age": 56}]),
    ("Not more than 67 years", [{"category": "ALL", "max_age": 67}]),                                # BSF specialists
    ("55 years or less than 55 years", [{"category": "ALL", "max_age": 55}]),                        # CRPF physiotherapist
    ("40 years for UR/EWS, 43 years for OBC and 45 years for SC",                                    # UPSC Advt 12/2026
     [{"category": "UR", "max_age": 40}, {"category": "EWS", "max_age": 40}, {"category": "OBC", "max_age": 43}, {"category": "SC", "max_age": 45}]),
    ("Holding analogous posts on regular basis; OR With five years' regular service in Level-10", []),   # experience, not age
    # Relaxation amounts are not age limits (UPSC Advt 11 and 12/2026); these used to become AGE_MAX rows
    ("The age is further relaxable upto 10 years for PwBD", []),
    ("AGE: 40 years for UR/EWS, 43 years for OBC and 45 years for SC/ST. The age is further relaxable upto 10 years for PwBD.", []),
    ("The upper age limit is relaxable for Central/U.T. Govt. Servants up to 5 years", []),
    ("Similarly OBC candidates would get maximum upto 13 years including 3 years age relaxation meant for OBC category", []),
    ("For certain age concessions admissible to various categories please go through the instruction regarding Concessions & Relaxations", []),
    # Hand-built, no real fixture has these shapes: a relaxation table row, the same row without the word, and a
    # minimum-and-maximum sentence whose minimum used to be stored as the maximum
    ("Age relaxation: 05 years for SC/ST and 03 years for OBC", []),
    ("05 years for SC/ST and 03 years for OBC", []),
    ("Age should not be below 21 years and not exceeding 30 years", [{"category": "ALL", "min_age": 21, "max_age": 30}]),
])
def test_age_limits_from_real_quotes(quote, expected):
    assert age_limits_in(quote) == expected

def test_dates_from_real_quotes():
    assert [d.isoformat() for d in dates_in("01 Sept’ 2025 to 03 Sept’ 2025")] == ["2025-09-01", "2025-09-03"]
    assert [d.isoformat() for d in dates_in("1800 HRS ON 16-10-2026")] == ["2026-10-16"]
    # A relative deadline has no date to extract
    assert dates_in("within a period of 6O davs from the date of pubiication of this vacancy circular in the Employrnent News") == []

@pytest.fixture(scope="module")
def advt11():
    return extract_pages((UPSC / "AdvtNo-11-2026-Engl-100926.pdf").read_bytes())

@pytest.fixture(scope="module")
def advt12():
    return extract_pages((UPSC / "AdvtNo-12-2026-Engl-250926_0.pdf").read_bytes())

# UPSC Advt 12/2026 page 13, verbatim: opens 26-09-2026, closes 16-10-2026 at 1800 hrs
OPEN_CLOSE = ("ONLINE RECRUITMENT APPLICATIONS ARE INVITED FOR DIRECT RECRUITMENT BY SELECTION THROUGH WEBSITE https://upsconline.nic.in "
              "TO THE ABOVE POSTS FROM 26-09-2026. CLOSING DATE FOR SUBMISSION OF ONLINE RECRUITMENT APPLICATION THROUGH WEBSITE IS 1800 HRS ON 16-10-2026.")
LAW_OFFICER = "Four vacancies for the post of Public Law Officer/District Litigation Officer/Law Officer"   # Advt 11/2026 page 18
SUGAR_EXPERIENCE = "Three years' experience in teaching or research or advisory experience in the field of Sugar Technology"  # Advt 12 p2

@pytest.mark.parametrize("doc,name,value,quote,accepted,expect", [
    # Every rejection branch of verify_field
    ("12", "experience", None, "", False, "not stated"),
    ("12", "experience", "3 years", "", False, "no supporting quote"),
    ("12", "experience", "5 years", SUGAR_EXPERIENCE.replace("Three", "Five"), False, "quote not found in document"),
    ("12", "pay_level", "Level-10", "PAY SCALE: Level-1 0 in the Pay Matrix as per 7th CPC", False, "quote not found in document"),  # "1 0" is not "10"
    ("12", "pay_level", "Level-10", "AY SCALE: Level-10 in the Pay Matrix as per 7th CP", False, "quote not found in document"),    # starts/ends mid-word
    ("12", "pay_level", "Level-10", "Level-10", False, "fewer than 5 words"),
    ("12", "experience", "teaching", "research or advisory experience", False, "fewer than 5 words"),
    ("11", "post_name", "Law Off", LAW_OFFICER, False, "not whole words"),
    ("11", "post_name", "a", LAW_OFFICER, False, "too short"),
    ("11", "vacancy_count", "four", LAW_OFFICER, False, "not an integer"),
    ("11", "vacancy_count", "5", LAW_OFFICER, False, "5 does not appear"),
    ("12", "last_date", "16-10-2026", "CLOSING DATE FOR SUBMISSION OF ONLINE RECRUITMENT APPLICATION", False, "no date in the quote"),
    ("12", "last_date", "17-10-2026", OPEN_CLOSE, False, "not one of the dates"),
    ("12", "last_date", "as notified", OPEN_CLOSE, False, "not one of the dates"),
    ("12", "age_limit", "10 years", "The age is further relaxable upto 10 years for PwBD", False, "age relaxation"),
    ("12", "age_limit", "35 years", "AGE: 38 years for OBC.", False, "disagrees"),
    ("12", "age_limit", "Level-10", "PAY SCALE: Level-10 in the Pay Matrix as per 7th CPC", False, "no upper age limit"),
    # Accepted: values come from the document's words
    ("11", "post_name", "law officer", LAW_OFFICER, True, "Law Officer"),                     # the document's casing is stored
    ("11", "vacancy_count", "4", LAW_OFFICER, True, 4),
    ("12", "last_date", "16-10-2026", OPEN_CLOSE, True, datetime(2026, 10, 16, 18, 0, tzinfo=IST)),
    ("12", "last_date", "26-09-2026", OPEN_CLOSE, True, datetime(2026, 9, 26, 23, 59, tzinfo=IST)),  # 1800 HRS belongs to the other date
    ("12", "age_limit", "38 years", "AGE: 38 years for OBC.", True, [{"category": "OBC", "max_age": 38}]),
    ("12", "experience", "3 years", SUGAR_EXPERIENCE, True, SUGAR_EXPERIENCE),
    ("12", "pay_level", "Level-10", "PAY SCALE Level 10 in the Pay Matrix, as per 7th CPC", True,
     "PAY SCALE: Level-10 in the Pay Matrix as per 7th CPC"),                                  # EXACT_WORDS keeps the page's own text
])
def test_verify_field_on_real_pages(advt11, advt12, doc, name, value, quote, accepted, expect):
    v = verify_field(name, {"value": value, "quote": quote, "page": 0}, advt11 if doc == "11" else advt12)
    assert v.accepted is accepted, v.reason
    if accepted:
        assert v.value == expect
    else:
        assert expect in v.reason and v.value is None

def test_missing_field_is_rejected(advt12):
    assert verify_field("post_name", None, advt12).reason == "missing"

def test_word_matching_keeps_word_boundaries(advt12):
    assert find_quote("PAY SCALE Level 10 in the Pay Matrix, as per 7th CPC", advt12, None)[2] == "EXACT_WORDS"
    assert find_quote("PAY SCALE Level 1 0 in the Pay Matrix, as per 7th CPC", advt12, None) is None
    assert find_quote("CALE: Level-10 in the Pay Matrix as per 7th CPC", advt12, None) is None

def test_last_date_takes_the_date_the_llm_named_not_the_latest():
    # Hand-built: the reported bug needs a closing date followed by a later interview date in one quote;
    # no fixture notice words it this way
    pages = [PageText(1, "Last date for receipt of applications is 10-10-2025. Interview will be held on 20-10-2025 at 10 AM", False)]
    quote = "Last date for receipt of applications is 10-10-2025. Interview will be held on 20-10-2025 at 10 AM"
    v = verify_field("last_date", {"value": "10-10-2025", "quote": quote, "page": 1}, pages)
    assert v.accepted and v.value == datetime(2025, 10, 10, 23, 59, tzinfo=IST)
    assert "time not stated" in v.reason
