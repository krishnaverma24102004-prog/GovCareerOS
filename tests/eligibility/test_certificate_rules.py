"""
Certificate-validity rules against the REAL decided cases in tests/fixtures/certificate_cases.json.

Each case's facts become the notice requirement (crucial date = the case's cut-off date, income year = the year the
rules/advertisement required, quote = the case's quote) and the certificate the candidate held (issue date and income
year as stated). The expected status is what the court decided, read through the engine's statuses:
rejected -> FAILS; accepted -> anything but FAILS. Where the case facts do not state what the rule needs, or the
courts are split (README "Known conflicts"), the engine must say CANNOT_DECIDE, never guess; those are named below.
Cases marked uncertain or reported only in the news are skipped with that reason.
"""
import json
from datetime import date
from pathlib import Path

import pymupdf
import pytest

from govcareeros.backend.eligibility import certificates
from govcareeros.backend.eligibility.certificate_types import CertificateKind, CertificateRequirement, HeldCertificate

FIXTURES = Path(__file__).parents[1] / "fixtures"
DATA = json.loads((FIXTURES / "certificate_cases.json").read_text(encoding="utf-8"))
CASES, RULES = DATA["cases"], {r["id"]: r for r in DATA["rules"]}

KIND = {"ews": CertificateKind.EWS, "obc_ncl": CertificateKind.OBC_NCL, "pwbd": CertificateKind.PWBD,
        "sc_st": CertificateKind.SC, "ex_serviceman": CertificateKind.ESM, "domicile": CertificateKind.DOMICILE}
CLAIM = {"ews": "EWS", "obc_ncl": "OBC", "pwbd": "PWBD", "sc_st": "SC", "ex_serviceman": "ESM", "domicile": "DOMICILE"}

# Notice terms the case facts do not carry but a judgment reproduces verbatim: case id -> rule id in the fixture.
# CAPF (AC) 2023 Rule 21.2 (reproduced in Raghvendra Singh, para 7) is the window Bharath Singarappa's certificate missed.
NOTICE_TERMS = {"dhc-2026-bharath-singarappa": "capf-ac-obc-ncl-issue-window"}
CAPF_2023_WINDOW_START = date(2023, 4, 1)  # "issued on/after 01.04.2023 (after completion of FY 2022-23)"

# case id -> (expected status, why it follows from the facts and the court's decision)
EXPECTED = {
    "sc-2023-divya-uoi": (certificates.FAILS, "income FY 2019-20 held, FY 2020-21 required"),
    "sc-2023-divya-ashwani-dubey": (certificates.FAILS, "income FY 2021-22 held, FY 2020-21 required"),
    "sc-2022-gaurav-singh-wp2-wp4": (certificates.FAILS, "income FY 2019-20 held, FY 2018-19 required"),
    "sc-2022-gaurav-singh-wp1": (certificates.CANNOT_DECIDE, "accepted; the facts do not state the certificate's income year"),
    "sc-2026-poonam-dwivedi": (certificates.FAILS, "FY 2020-21 certificate issued 12.01.2021, before that year ended"),
    "sc-2026-poonam-dwivedi-sunita-kumari": (certificates.FAILS, "later FY and issued after the last date"),
    "sc-2026-poonam-dwivedi-archana-saxena": (certificates.FAILS, "income FY 2019-20 held, FY 2020-21 required"),
    "sc-2026-poonam-dwivedi-shanu-tiwari": (certificates.FAILS, "income FY 2019-20 held, FY 2020-21 required"),
    "dhc-2022-aashima-goyal": (certificates.FAILS, "income FY 2018-19 held, FY 2017-18 required"),
    # Rejected by the court, but the case facts state no issue date and no cited general rule fixes how recent an
    # OBC-NCL certificate must be (the Rajasthan one-year rule is marked uncertain): the engine refuses to decide
    "sc-2025-sakshi-arha": (certificates.CANNOT_DECIDE, "rejected; no issue date stated, Rajasthan validity rule uncertain"),
    "sc-2025-sakshi-arha-jyoti-beniwal": (certificates.CANNOT_DECIDE, "rejected; only 'of the year 2016' in the notes"),
    "dhc-2026-bharath-singarappa": (certificates.CANNOT_DECIDE, "accepted; outside the notice window, courts split"),
    "dhc-2025-amjad-khan": (certificates.CANNOT_DECIDE, "accepted; issued after the cut-off, courts split"),
    "sc-2022-karn-singh-yadav": (certificates.CANNOT_DECIDE, "accepted; no cut-off or issue date stated"),
    "sc-2026-rohith-nathan": (certificates.CANNOT_DECIDE, "accepted; turned on the creamy-layer income test, no dates"),
    "cat-2026-samit-kumar": (certificates.CANNOT_DECIDE, "accepted; no cut-off date stated"),
    "bomhc-2026-shritij-mohan-shrivastava": (certificates.CANNOT_DECIDE, "accepted; no cut-off date stated"),
}

def _d(v):
    return date.fromisoformat(v) if v else None

def _skip_reason(case):
    if case["uncertain"]:
        return f"uncertain: {case['uncertain_reason']}"
    if case["source_kind"] == "news_report":
        return "news_report: judgment text not read"
    return None

def requirement_for(case, *, with_crucial=True, with_fy=True) -> CertificateRequirement:
    f = case["facts"]
    window = {}
    if case["id"] in NOTICE_TERMS:
        rule = RULES[NOTICE_TERMS[case["id"]]]
        assert "issued on/after 01.04.2023" in rule["quote"] and "not later than the closing date" in rule["quote"]
        window = {"issued_on_or_after": CAPF_2023_WINDOW_START, "issued_on_or_before": _d(f["cut_off_date"])}
    return CertificateRequirement(
        kind=KIND[case["certificate_type"]], crucial_date=_d(f["cut_off_date"]) if with_crucial else None,
        financial_year=f["required_financial_year"] if with_fy and case["certificate_type"] == "ews" else None,
        source_page=None, source_quote=case["quote"], **window)

def held_for(case) -> HeldCertificate:
    f = case["facts"]
    issued = f["certificate_issue_dates"]
    assert len(issued) <= 1, "one certificate per case; several dates need one HeldCertificate each"
    return HeldCertificate(kind=KIND[case["certificate_type"]], issue_date=_d(issued[0]) if issued else None,
                           financial_year=f["certificate_financial_year"])

def test_every_usable_case_has_an_expectation():
    usable = {c["id"] for c in CASES if _skip_reason(c) is None}
    assert usable == set(EXPECTED)

@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_real_case(case):
    reason = _skip_reason(case)
    if reason:
        pytest.skip(reason)
    expected, why = EXPECTED[case["id"]]
    res = certificates.evaluate(requirement_for(case), held_for(case), CLAIM[case["certificate_type"]])
    assert res.status == expected, (why, res.reason)
    # Never contradict the court: a rejected certificate is never OK, an accepted one never FAILS
    assert res.status != (certificates.OK if case["outcome"] == "rejected" else certificates.FAILS)
    # The requirement's own words travel with the decision
    assert res.source_quote == case["quote"] and res.kind == KIND[case["certificate_type"]]
    assert res.reason and not res.verified

def _case(case_id):
    return next(c for c in CASES if c["id"] == case_id)

def test_cited_rules_match_the_fixture_word_for_word():
    for rule in certificates.CITED_RULES:
        src = RULES[rule.id]
        assert (rule.reference, rule.quote) == (src["reference"], src["quote"])
        assert src["uncertain"] is False and "jurisdiction" not in src

@pytest.mark.parametrize("case", [c for c in CASES if c["certificate_type"] == "ews" and _skip_reason(c) is None
                                  and c["facts"]["cut_off_date"] and c["facts"]["required_financial_year"]], ids=lambda c: c["id"])
def test_dopt_income_year_rule_gives_the_year_each_court_applied(case):
    """Notice silent on the year: the DoPT rule (FY before the year of application) derives exactly the year required."""
    res = certificates.evaluate(requirement_for(case, with_fy=False), held_for(case), "EWS")
    assert res.status == EXPECTED[case["id"]][0]
    assert certificates.EWS_FY_PRIOR.id in res.rules
    required = case["facts"]["required_financial_year"]
    assert f"FY {required}" in res.reason or res.status == certificates.CANNOT_DECIDE

def test_dopt_worked_example_year():
    # FAQ 23: closing date 1 August 2022 -> income of 2021-2022 (quoted in the rule)
    assert "shall be 2021-2022" in certificates.EWS_FY_EXAMPLE.quote
    assert certificates.fy_label(certificates.fy_containing(date(2022, 8, 1)) - 1) == "2021-22"
    assert certificates.fy_start("2021-2022") == certificates.fy_start("2021-22") == 2021
    assert certificates.fy_start("2021-23") is None

def test_closing_date_stands_in_for_an_unstated_crucial_date():
    divya = _case("sc-2023-divya-uoi")
    res = certificates.evaluate(requirement_for(divya, with_crucial=False), held_for(divya), "EWS",
                                closing_date=_d(divya["facts"]["cut_off_date"]))
    assert res.status == certificates.FAILS and certificates.EWS_CRUCIAL_DATE.id in res.rules
    # Without any date or year the notice is silent: no guess
    res = certificates.evaluate(requirement_for(divya, with_crucial=False, with_fy=False), held_for(divya), "EWS")
    assert res.status == certificates.CANNOT_DECIDE and not res.rules

def test_before_the_crucial_date_a_wrong_certificate_can_still_be_replaced():
    """Divya on the day CSE-2022 was advertised (02.02.2022) held only the FY 2019-20 certificate: then it was still
    curable (NEEDS_DOCUMENT); judged after the cut-off (as the Supreme Court did) it FAILS."""
    divya = _case("sc-2023-divya-uoi")
    advertised = _d(divya["facts"]["advertisement_date"])
    live = certificates.evaluate(requirement_for(divya), held_for(divya), "EWS", as_of=advertised)
    assert live.status == certificates.NEEDS_DOCUMENT and "2022-02-22" in live.reason
    after = certificates.evaluate(requirement_for(divya), held_for(divya), "EWS", as_of=date(2022, 2, 23))
    assert after.status == certificates.FAILS

def test_claim_decides_whether_the_certificate_is_needed():
    divya = _case("sc-2023-divya-uoi")
    req = requirement_for(divya)
    assert certificates.evaluate(req, held_for(divya), "UR").status == certificates.NOT_APPLICABLE
    assert certificates.evaluate(req, None, "GEN").status == certificates.NOT_APPLICABLE
    assert certificates.evaluate(req, held_for(divya), None).status == certificates.CANNOT_DECIDE
    with pytest.raises(ValueError):
        certificates.evaluate(req, HeldCertificate(kind=CertificateKind.OBC_NCL), "EWS")

def test_outside_window_is_a_notice_condition_for_non_caste_certificates():
    """The CAPF (AC) 2023 window applied to an EWS-style check would fail outright; for OBC-NCL the courts are split."""
    bharath = _case("dhc-2026-bharath-singarappa")
    req = requirement_for(bharath)
    res = certificates.evaluate(req, held_for(bharath), "OBC")
    assert res.status == certificates.CANNOT_DECIDE and "sc-2025-sakshi-arha" in res.reason
    assert "outside the notice's window" in res.reason

# ---------- real notice clauses (captured PDFs) ----------

def _page_text(pdf, page):
    return " ".join(pymupdf.open(FIXTURES / pdf)[page - 1].get_text().split())

IES_OBC = ("must produce OBC (Non-Creamy Layer) certificate based on the income for the Financial Year (FY) 2024-25, 2023- 24 "
           "and 2022-23 and issued on/after 01.04.2025 (after completion of FY 2024-25) but not later than the closing date")
IES_EWS = ("Income & Asset Certificate based on the income for Financial Year (FY) 2024-2025 and issued on/after 01.04.2025 "
           "(after the completion of FY 2024-25) but not later than the closing date")
IES_PDF = "upsc/prep/Exam_Notification_IES_ISS_Eng_11022026.pdf"
IES_CLOSING = "LAST DATE FOR SUBMISSION OF APPLICATION: 03.03.2026"  # page 1

def test_notice_quotes_are_verbatim():
    assert IES_CLOSING in _page_text(IES_PDF, 1)
    assert IES_OBC in _page_text(IES_PDF, 5)
    assert IES_EWS in _page_text(IES_PDF, 6)

def test_ies_iss_2026_windows():
    """UPSC IES/ISS 2026 states the window itself, so an in-window certificate is OK and an earlier one is not."""
    closing = date(2026, 3, 3)  # IES_CLOSING
    obc = CertificateRequirement(CertificateKind.OBC_NCL, crucial_date=closing, issued_on_or_after=date(2025, 4, 1),
                                 issued_on_or_before=closing, source_page=5, source_quote=IES_OBC)
    ews = CertificateRequirement(CertificateKind.EWS, crucial_date=closing, issued_on_or_after=date(2025, 4, 1),
                                 issued_on_or_before=closing, financial_year="2024-2025", source_page=6, source_quote=IES_EWS)
    ok = certificates.evaluate(obc, HeldCertificate(CertificateKind.OBC_NCL, issue_date=date(2025, 6, 2)), "OBC", verified=True)
    assert (ok.status, ok.verified, ok.source_page) == (certificates.OK, True, 5)
    early = certificates.evaluate(obc, HeldCertificate(CertificateKind.OBC_NCL, issue_date=date(2025, 3, 20)), "OBC")
    assert early.status == certificates.CANNOT_DECIDE  # courts split on OBC-NCL windows
    ews_ok = certificates.evaluate(ews, HeldCertificate(CertificateKind.EWS, issue_date=date(2025, 6, 2), financial_year="2024-25"), "EWS")
    assert ews_ok.status == certificates.OK and not ews_ok.verified and "not yet verified" in ews_ok.reason
    ews_early = certificates.evaluate(ews, HeldCertificate(CertificateKind.EWS, issue_date=date(2025, 3, 20), financial_year="2024-25"), "EWS")
    assert ews_early.status == certificates.FAILS and "the notice wants it issued between 2025-04-01" in ews_early.reason
