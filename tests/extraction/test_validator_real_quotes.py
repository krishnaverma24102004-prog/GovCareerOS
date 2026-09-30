"""Validator rules on quotes taken verbatim from real notices (CRPF, ITBP, MHA, BSF, UPSC) seen on 30-09-2026."""
import pytest

from govcareeros.backend.extraction.validator import age_limits_in, dates_in

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
])
def test_age_limits_from_real_quotes(quote, expected):
    assert age_limits_in(quote) == expected

def test_dates_from_real_quotes():
    assert [d.isoformat() for d in dates_in("01 Sept’ 2025 to 03 Sept’ 2025")] == ["2025-09-01", "2025-09-03"]
    assert [d.isoformat() for d in dates_in("1800 HRS ON 16-10-2026")] == ["2026-10-16"]
    # A relative deadline has no date to extract
    assert dates_in("within a period of 6O davs from the date of pubiication of this vacancy circular in the Employrnent News") == []
