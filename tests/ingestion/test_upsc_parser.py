"""Parser tests against the real UPSC advertisement PDFs (Advt 11/2026 and 12/2026) stored as fixtures."""
from datetime import datetime
from pathlib import Path

import pytest

from govcareeros.backend.documents.pdf_text import extract_pages
from govcareeros.backend.parsing.upsc_advertisement import IST, parse_advertisement, words_to_number
from govcareeros.backend.sources.upsc import parse_listing

UPSC = Path(__file__).parents[1] / "fixtures" / "upsc"

@pytest.fixture(scope="module")
def advt12():
    return parse_advertisement(extract_pages((UPSC / "AdvtNo-12-2026-Engl-250926_0.pdf").read_bytes()))

@pytest.fixture(scope="module")
def advt11():
    return parse_advertisement(extract_pages((UPSC / "AdvtNo-11-2026-Engl-100926.pdf").read_bytes()))

def _reqs(vac, req_type):
    return [r.value for r in vac.requirements if r.req_type == req_type]

def test_words_to_number():
    assert words_to_number("One") == 1
    assert words_to_number("Sixty") == 60
    assert words_to_number("One hundred forty") == 140
    assert words_to_number("Twenty-five") == 25
    assert words_to_number("12") == 12
    assert words_to_number("several") is None

def test_advt12_advertisement_fields(advt12):
    assert advt12.warnings == []
    f = advt12.fields
    assert f["advertisement_number"].value == "12/2026"
    assert f["application_mode"].value == "ONLINE"
    assert f["application_start"].value == datetime(2026, 9, 26, 0, 0, tzinfo=IST)
    assert f["application_deadline"].value == datetime(2026, 10, 16, 18, 0, tzinfo=IST)
    # The advertisement states eligibility is judged on the closing date
    assert f["eligibility_crucial_date"].value == f["application_deadline"].value
    assert f["application_deadline"].provenance.page == 13
    assert "1800 HRS ON 16-10-2026" in f["application_deadline"].provenance.excerpt

def test_advt12_vacancies(advt12):
    by_ref = {v.external_ref: v for v in advt12.vacancies}
    assert list(by_ref) == ["26091201626", "26091202226", "26091203226", "26091204526"]

    jto = by_ref["26091201626"]
    assert jto.value("post_name") == "Junior Technical Officer (Sugar Technology)"
    assert jto.value("vacancy_count") == 1
    assert jto.value("ministry") == "Ministry Of Consumer Affairs Food & Public Distribution"
    assert jto.value("pay_scale") == "Level-10 in the Pay Matrix as per 7th CPC."
    assert _reqs(jto, "AGE_MAX") == [{"category": "OBC", "max_age": 38}]
    assert _reqs(jto, "AGE_RELAXATION") == [{"category": "PwBD", "years": 10}]
    assert _reqs(jto, "EDUCATION")[0].startswith("Degree in Science or Engineering from a recognized University")
    assert jto.fields["post_name"].provenance.page == 1

    alc = by_ref["26091202226"]
    assert alc.value("vacancy_count") == 8
    assert alc.value("department") == "Legislative Department"
    assert alc.value("ministry") == "Ministry Of Law and Justice"
    assert _reqs(alc, "AGE_MAX") == [
        {"category": "UR", "max_age": 40}, {"category": "EWS", "max_age": 40},
        {"category": "OBC", "max_age": 43}, {"category": "SC", "max_age": 45},
    ]
    assert {"category": "PwBD", "vacancies": 1} in _reqs(alc, "RESERVATION")

    law = by_ref["26091203226"]
    assert law.value("post_name") == "Law Officer"
    assert law.value("department") == "Land and Building Department, Government of NCT of Delhi"
    assert _reqs(law, "EDUCATION") == ["Degree in Law from a recognized Institute/University."]
    assert _reqs(law, "EXPERIENCE")[0].startswith("Two years’ experience in Legal work as a Legal Practitioner.")
    # Printed page numbers must not leak into values
    assert law.value("headquarters") == "Land & Building Department, Delhi."

def test_advt11_large_word_counts(advt11):
    assert advt11.warnings == []
    assert advt11.fields["advertisement_number"].value == "11/2026"
    counts = {v.external_ref: v.value("vacancy_count") for v in advt11.vacancies}
    assert len(counts) == 7
    assert counts["26091102112"] == 60
    assert counts["26091106212"] == 140
    app = next(v for v in advt11.vacancies if v.external_ref == "26091106212")
    assert app.value("post_name") == "Assistant Public Prosecutor"

def test_every_value_has_provenance(advt11, advt12):
    for ad in (advt11, advt12):
        for v in ad.vacancies:
            for name, f in v.fields.items():
                assert f.provenance.page >= 1 and f.provenance.excerpt, (v.external_ref, name)
            for r in v.requirements:
                assert r.provenance.page >= 1 and r.provenance.excerpt, (v.external_ref, r.req_type)

def test_listing_page_links():
    html = (UPSC / "recruitment-advertisement.html").read_text(encoding="utf-8")
    urls = [l.url for l in parse_listing(html)]
    assert urls == [
        "https://www.upsc.gov.in/sites/default/files/AdvtNo-12-2026-Engl-250926_0.pdf",
        "https://www.upsc.gov.in/sites/default/files/AdvtNo-11-2026-Engl-100926.pdf",
    ]
