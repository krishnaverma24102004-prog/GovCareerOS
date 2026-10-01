"""UKPSC listing adapter, against pages captured live from psc.uk.gov.in (see fixtures/adapters/ukpsc/meta.json)."""
import json
import re
from datetime import date
from html import unescape
from pathlib import Path
from urllib.parse import urlparse

import pytest

from govcareeros.backend.sources.adapters import adapter_for
from govcareeros.backend.sources.adapters import ukpsc
from govcareeros.backend.sources.registry import SOURCES
from govcareeros.backend.sources.url_guard import UrlPolicy

FIXTURES = Path(__file__).parent.parent / "fixtures" / "adapters" / "ukpsc"
META = json.loads((FIXTURES / "meta.json").read_text(encoding="utf-8"))["files"]
EN_PAGES = ["recruitment_en.html", "admitcards_en.html", "results_en.html", "answerkey_en.html"]

SPEC = next(s for s in SOURCES if s.name == "UKPSC")

def _html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")

def _rows(name: str):
    return ukpsc.parse_listing(_html(name), META[name]["url"])

def _page_text(name: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", _html(name))))

def _by_url(rows):
    return {r.url: r for r in rows}

def _dmy(text: str) -> date:
    d, m, y = (int(x) for x in text.split("-"))
    return date(y, m, d)

def test_module_is_discovered_for_the_registry_source():
    assert adapter_for("UKPSC") is ukpsc
    assert SPEC.recruitment_url == META["recruitment_en.html"]["url"]

def test_fixture_metadata_records_url_capture_date_and_fetch_mode():
    for name, info in META.items():
        assert (FIXTURES / name).exists()
        assert info["url"].startswith("https://psc.uk.gov.in/candidate-corner/")
        assert info["captured_at"].startswith("2026-10-01")
        assert info["fetch_mode"] == "HTTP"

def test_recruitment_rows():
    rows = _rows("recruitment_en.html")
    assert len(rows) == 20
    by_url = _by_url(rows)

    advt = by_url["https://psc.uk.gov.in/public/uploads/recruitment/1843959203.pdf"]
    assert advt.title == "Uttarakhand Combined State Civil/Upper Subordinate Services Examination-2026: Advertisement"
    assert advt.published_on == date(2026, 9, 9)
    assert advt.last_date == date(2026, 9, 29)
    assert advt.kind_hint == "VACANCY"
    assert advt.advertisement_number is None and advt.post_names == []
    assert advt.evidence == {
        "title": "Uttarakhand Combined State Civil/Upper Subordinate Services Examination-2026 | Advertisement",
        "url": "https://psc.uk.gov.in/public/uploads/recruitment/1843959203.pdf",
        "published_on": "09-09-2026",
        "last_date": "29-09-2026",
        "kind_hint": "Advertisement",
    }

    # Third document of the same examination: its own Publish Date, not the row's first one. Its End Date
    # (16-10-2026) closes the correction window, not applications, so it is not a last date
    correction = by_url["https://psc.uk.gov.in/public/uploads/recruitment/52805175.pdf"]
    assert correction.title == ("Uttarakhand Combined State Civil/Upper Subordinate Services Examination-2026: "
                                "Edit/Correction of Online Application Form")
    assert (correction.published_on, correction.last_date) == (date(2026, 10, 7), None)
    assert "last_date" not in correction.evidence
    assert correction.kind_hint is None and "kind_hint" not in correction.evidence

    judicial = by_url["https://psc.uk.gov.in/public/uploads/recruitment/1304545086.pdf"]
    assert judicial.title == ("Uttarakhand Judicial Service Civil Judge (Junior Division) Exam-2023: "
                              "Notification (Required Document List)")
    assert (judicial.published_on, judicial.last_date) == (date(2026, 9, 29), None)

    lecturer = by_url["https://psc.uk.gov.in/public/uploads/recruitment/1433940324.pdf"]
    assert lecturer.title == ("Uttarakhand Special Subordinate Education (Lecturer-Group 'C') Services "
                              "General/Women Branch Exam-2025: Notification")
    assert (lecturer.published_on, lecturer.last_date) == (date(2026, 9, 24), None)

    vet = by_url["https://psc.uk.gov.in/public/uploads/recruitment/1339347899.pdf"]
    assert vet.title == "Veterinary Officers Exam - 2026: Advertisement"
    assert (vet.published_on, vet.last_date, vet.kind_hint) == (date(2026, 8, 25), date(2026, 9, 14), "VACANCY")

    rejection = by_url["https://psc.uk.gov.in/public/uploads/recruitment/1904885097.pdf"]
    assert rejection.title == "Chief Fire Officer Exam-2026: Final Rejection List"
    assert rejection.kind_hint is None
    assert rejection.last_date is None  # its End Date (10-10-2026) is not an application deadline

def test_admit_card_rows_take_the_kind_from_their_label_or_the_page_heading():
    by_url = _by_url(_rows("admitcards_en.html"))
    assert len(by_url) == 17
    labelled = by_url["https://psc.uk.gov.in/public/uploads/admitcard/2084419480.pdf"]
    assert labelled.title == ("Uttarakhand Combined State Civil Lower Subordinate Services Examination-2024: Notification "
                              "Regarding Admit Card for Physical Ability Test (Deputy Jailor & Excise Inspector)")
    assert labelled.published_on == date(2026, 8, 31)
    assert labelled.last_date is None  # this page has no End Date column
    assert (labelled.kind_hint, labelled.evidence["kind_hint"]) == ("ADMIT_CARD", "Admit Card")

    scribe = by_url["https://psc.uk.gov.in/public/uploads/admitcard/1848590155.pdf"]
    assert scribe.title.endswith(": Notification regarding Scribe")
    assert scribe.published_on == date(2026, 9, 23)
    assert (scribe.kind_hint, scribe.evidence["kind_hint"]) == ("ADMIT_CARD", "Admit Card")  # page heading

def test_result_rows():
    by_url = _by_url(_rows("results_en.html"))
    assert len(by_url) == 28
    selection = by_url["https://psc.uk.gov.in/public/uploads/results/162017235.pdf"]
    assert selection.title == "Uttarakhand Combined State Civil Lower Subordinate Services Examination-2024: Selection Result"
    assert (selection.published_on, selection.last_date) == (date(2026, 9, 25), None)
    assert (selection.kind_hint, selection.evidence["kind_hint"]) == ("RESULT", "Result")

    corrigendum = by_url["https://psc.uk.gov.in/public/uploads/results/259564318.pdf"]
    assert corrigendum.title == ("Uttarakhand Combined State Junior Engineer Service Examination-2023: "
                                 "Corrigendum (Revised Selection Result of PH Candidates)")
    assert corrigendum.published_on == date(2026, 6, 11)
    assert (corrigendum.kind_hint, corrigendum.evidence["kind_hint"]) == ("CORRIGENDUM", "Corrigendum")

    final_key = by_url["https://psc.uk.gov.in/public/uploads/results/772837217.pdf"]
    assert final_key.title == "Additional Private Secretary Exam-2024: Final Answer Key (General Studies)"
    assert final_key.kind_hint == "ANSWER_KEY"

def test_answer_key_rows_and_a_blank_end_date():
    by_url = _by_url(_rows("answerkey_en.html"))
    assert len(by_url) == 26
    history = by_url["https://psc.uk.gov.in/public/uploads/answerkey/1570008564.pdf"]
    assert history.title == ("Uttarakhand Special Subordinate Education (Lecturer-Group 'C') Services General/Women "
                             "Branch Exam-2025: Provisional Answer Key (History-I)")
    # End Date 24-09-2026 closes online objections to the key, not applications
    assert (history.published_on, history.last_date) == (date(2026, 9, 15), None)
    assert "last_date" not in history.evidence
    assert history.kind_hint == "ANSWER_KEY"

    # The page leaves this row's End Date cells blank: no last date, no evidence for one
    regulation = by_url["https://psc.uk.gov.in/public/uploads/answerkey/1820208787.pdf"]
    assert regulation.title == ("Answer Key Related Notification: "
                                "Pariksha Parinam Nirman Prakriya Viniyamavali-2022(Fifth Amendment-2026)")
    assert regulation.published_on == date(2026, 7, 8)
    assert regulation.last_date is None and "last_date" not in regulation.evidence

def test_default_locale_page_has_no_titles_so_no_rows():
    # Same table, but every Title cell is an untranslated placeholder and Advertisement Name is empty
    html = _html("recruitment_default_locale.html")
    assert "1843959203.pdf" in html and "meta_value" in html
    assert _rows("recruitment_default_locale.html") == []

@pytest.mark.parametrize("name", EN_PAGES)
def test_urls_are_official_documents_and_menus_are_skipped(name):
    rows = _rows(name)
    assert rows
    policy = UrlPolicy.from_url(SPEC.recruitment_url)
    for r in rows:
        assert policy.allows(r.url), r.url
        assert urlparse(r.url).path.startswith("/public/uploads/") and r.url.endswith(".pdf"), r.url
        assert "candidate-corner" not in r.url  # side menu / "Link To Apply" targets
    assert len({r.url for r in rows}) == len(rows)

@pytest.mark.parametrize("name", EN_PAGES)
def test_every_filled_field_has_exact_source_evidence(name):
    page = _page_text(name)
    raw = _html(name)
    for r in _rows(name):
        assert r.title and r.evidence["title"]
        for part in r.evidence["title"].split(" | "):
            assert part in page, part
        assert r.title == r.evidence["title"].replace(" | ", ": ", 1)
        assert r.evidence["url"] in raw
        assert r.advertisement_number is None and "advertisement_number" not in r.evidence
        assert r.post_names == [] and "post_names" not in r.evidence
        if r.kind_hint:
            assert r.evidence["kind_hint"].lower() in (r.title + " " + page[:20000]).lower()
        else:
            assert "kind_hint" not in r.evidence

@pytest.mark.parametrize("name", EN_PAGES)
def test_no_invented_dates(name):
    raw = _html(name)
    for r in _rows(name):
        for field in ("published_on", "last_date"):
            value = getattr(r, field)
            if value is None:
                assert field not in r.evidence
                continue
            if field == "last_date":
                assert r.kind_hint == "VACANCY"  # only an advertisement's End Date closes applications
            text = r.evidence[field]
            assert re.fullmatch(r"\d{2}-\d{2}-\d{4}", text)
            assert text in raw
            assert value == _dmy(text)

def test_listing_urls_are_official_candidate_corner_pages():
    policy = UrlPolicy.from_url(SPEC.recruitment_url)
    assert ukpsc.LISTING_URLS
    for url in ukpsc.LISTING_URLS:
        assert policy.allows(url)
        assert url in {info["url"] for info in META.values()}
