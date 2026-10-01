"""Shape check for the certificate-validity research fixture (tests/fixtures/certificate_cases.json)."""
import json
import re
from datetime import date
from pathlib import Path

import pytest

DATA = json.loads((Path(__file__).parents[1] / "fixtures" / "certificate_cases.json").read_text(encoding="utf-8"))
CASES, RULES = DATA["cases"], DATA["rules"]
TYPES = {"obc_ncl", "ews", "sc_st", "pwbd", "ex_serviceman", "domicile"}
FY = re.compile(r"^\d{4}-\d{2}$")

def test_case_and_rule_ids_are_unique():
    ids = [c["id"] for c in CASES] + [r["id"] for r in RULES]
    assert len(ids) == len(set(ids))
    assert 15 <= len(CASES) <= 25 and RULES

@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_case_shape(case):
    assert case["citation_url"].startswith("https://")
    assert case["outcome"] in ("accepted", "rejected")
    assert case["certificate_type"] in TYPES
    assert case["court"] and case["case_name"] and case["recruitment"] and case["rule_applied"]
    assert case["quote"].strip() and len(case["quote"].split()) <= 25
    assert case["source_kind"] in ("judgment", "news_report")
    assert isinstance(case["uncertain"], bool)
    if case["uncertain"] or case["date"] is None:
        assert case["uncertain"] and case["uncertain_reason"]
    if case["date"] is not None:
        date.fromisoformat(case["date"])
    f = case["facts"]
    for d in f["certificate_issue_dates"] + [x for x in (f["cut_off_date"], f["advertisement_date"]) if x]:
        date.fromisoformat(d)
    for fy in (f["certificate_financial_year"], f["required_financial_year"]):
        assert fy is None or FY.match(fy)

@pytest.mark.parametrize("rule", RULES, ids=lambda r: r["id"])
def test_rule_shape(rule):
    assert rule["source_url"].startswith("https://")
    assert rule["certificate_type"] in TYPES | {"all"}
    assert rule["rule"] and rule["reference"] and rule["issuer"]
    assert rule["quote"].strip() and len(rule["quote"].split()) <= 25
    assert rule["source_kind"] in ("official_document", "judgment")
    if rule["uncertain"]:
        assert rule["uncertain_reason"]

def test_every_certificate_type_has_a_case():
    assert {c["certificate_type"] for c in CASES} == TYPES
