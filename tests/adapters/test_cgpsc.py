"""
CGPSC listing adapter, against pages captured live from psc.cg.gov.in on 2026-10-01
(tests/fixtures/adapters/cgpsc/, see meta.json there for URL, capture time and fetch mode).
"""
import json
import re
from datetime import date
from html import unescape
from pathlib import Path
from urllib.parse import urlparse

import pytest

from govcareeros.backend.sources.adapters import adapter_for
from govcareeros.backend.sources.adapters import cgpsc
from govcareeros.backend.sources.registry import SOURCES

FIXTURES = Path(__file__).parents[1] / "fixtures" / "adapters" / "cgpsc"
META = json.loads((FIXTURES / "meta.json").read_text(encoding="utf-8"))
PAGES = {p["file"]: p for p in META["pages"]}
DATE_IN_PARENS = re.compile(r"\((\d{2})-(\d{2})-(\d{4})\)")

def _html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")

def _rows(name: str):
    return cgpsc.parse_listing(_html(name), PAGES[name]["final_url"])

def _by_title_evidence(rows, caption: str):
    found = [r for r in rows if r.evidence["title"] == caption]
    assert len(found) == 1, f"expected exactly one row for {caption!r}, got {len(found)}"
    return found[0]

ALL_PAGES = ["Advertisement.html", "Notifications.html", "Result.html"]

def test_fixture_meta_records_live_capture():
    assert META["source"] == "CGPSC"
    for page in META["pages"]:
        assert (FIXTURES / page["file"]).is_file()
        assert page["url"].startswith("https://psc.cg.gov.in/")
        assert page["capture_date"] == "2026-10-01"
        assert page["fetch_mode"] == "HTTP"
        assert page["http_status"] == 200

def test_discovered_and_listing_urls_on_official_domain():
    assert adapter_for("CGPSC") is cgpsc
    spec = next(s for s in SOURCES if s.name == "CGPSC")
    assert spec.recruitment_url == "https://psc.cg.gov.in/Advertisement.php"
    for url in cgpsc.LISTING_URLS:
        assert urlparse(url).scheme == "https" and urlparse(url).hostname == spec.official_domain

def test_row_counts_match_the_pages():
    # Advertisement: every <li> link is an official PDF. Notifications: 125 links, of which 49 point at
    # the e-service portal (online.ecgpsconline.in) and 1 at cgpolice.gov.in, so 75 remain. Result: 240.
    assert len(_rows("Advertisement.html")) == 128
    assert len(_rows("Notifications.html")) == 75
    assert len(_rows("Result.html")) == 240

def test_advertisement_rows():
    rows = _rows("Advertisement.html")

    first = rows[0]
    assert first.title == "MICRO BIOLOGIST ADVERTISEMENT (08-09-2026)"
    assert first.url == "https://psc.cg.gov.in/PDFs/advertisement/MICRO_BIOLOGIST_ADVERTISEMENT%20(08-09-2026).pdf"
    assert first.published_on == date(2026, 9, 8)
    assert first.kind_hint == "VACANCY"
    assert first.evidence == {
        "title": "MICRO_BIOLOGIST_ADVERTISEMENT (08-09-2026)",
        "url": "PDFs/advertisement/MICRO_BIOLOGIST_ADVERTISEMENT (08-09-2026).pdf",
        "published_on": "(08-09-2026)",
        "kind_hint": "ADVERTISEMENT",
    }

    corr = rows[1]
    assert corr.title == "PSYCHIATRIST-2026 CORRIGENDUM (21-08-2026)"
    assert corr.url == "https://psc.cg.gov.in/PDFs/advertisement/PSYCHIATRIST-2026_CORRIGENDUM%20(21-08-2026).pdf"
    assert corr.published_on == date(2026, 8, 21)
    assert corr.kind_hint == "CORRIGENDUM"

    # no space before the date on the page
    psy = _by_title_evidence(rows, "PSYCHIATRIST-2026_ADVERTISEMENT(17-07-2026)")
    assert psy.published_on == date(2026, 7, 17) and psy.kind_hint == "VACANCY"

    # brackets in the file name are percent-encoded
    ap = _by_title_evidence(rows, "ASSISTANT_PROFESSOR_[DEPT._OF_MEDICAL_EDUCATION]-2025_ADVERTISEMENT (17-11-2025)")
    assert ap.url == ("https://psc.cg.gov.in/PDFs/advertisement/"
                      "ASSISTANT_PROFESSOR_%5BDEPT._OF_MEDICAL_EDUCATION%5D-2025_ADVERTISEMENT%20(17-11-2025).pdf")
    assert ap.published_on == date(2025, 11, 17)

    # numbered corrigendum: the label is kept as written
    mi = _by_title_evidence(rows, "MINING_INSPECTOR-2022_CORRIGENDUM-2_(02-04-2025)")
    assert mi.kind_hint == "CORRIGENDUM" and mi.evidence["kind_hint"] == "CORRIGENDUM-2"
    assert mi.published_on == date(2025, 4, 2)

    # two dates: the earlier names the advertisement it refers to, the last is the notice's own date;
    # "SUCHANA_PATRA" (notice) is not a vacancy/corrigendum label, so no kind is claimed
    hostel = _by_title_evidence(rows, "HOSTEL_SUPERINTENDENT_GRADE-'D'-2023_(13-05-2023)_SUCHANA_PATRA_(20-05-2023)")
    assert hostel.published_on == date(2023, 5, 20)
    assert hostel.evidence["published_on"] == "(20-05-2023)"
    assert hostel.kind_hint is None
    assert hostel.url == ("https://psc.cg.gov.in/PDFs/advertisement/"
                          "HOSTEL_SUPERINTENDENT_GRADE-'D'-2023_(13-05-2023)_SUCHANA_PATRA_(20-05-2023).pdf")

    # entity in the page (&amp;) is decoded
    sub = _by_title_evidence(rows, "SUBEDAR,_SUB_INSPECTOR_CADRE_&_PLATOON_COMMANDER_RECRUITMENT-2024_ADVERTISEMENT (21-10-2024)")
    assert sub.title == "SUBEDAR, SUB INSPECTOR CADRE & PLATOON COMMANDER RECRUITMENT-2024 ADVERTISEMENT (21-10-2024)"
    assert sub.url == ("https://psc.cg.gov.in/PDFs/advertisement/"
                       "SUBEDAR,_SUB_INSPECTOR_CADRE_&_PLATOON_COMMANDER_RECRUITMENT-2024_ADVERTISEMENT%20(21-10-2024).pdf")

    last = rows[-1]
    assert last.evidence["title"] == "ASSISTANT_PROFESSOR_(MEDICAL)-2021_ADVERTISEMENT (24-03-2021)"
    assert last.published_on == date(2021, 3, 24)

def test_notification_rows():
    rows = _rows("Notifications.html")

    admit = rows[0]
    assert admit.title == "SAHAYAK JILA LOK ABHIYOJAN ADHIKARI-2026 ABOUT ADMIT CARD (16-09-2026)"
    assert admit.url == ("https://psc.cg.gov.in/PDFs/Notifications/"
                         "SAHAYAK_JILA_LOK_ABHIYOJAN_ADHIKARI-2026_ABOUT_ADMIT_CARD%20(16-09-2026).pdf")
    assert admit.published_on == date(2026, 9, 16)
    assert admit.kind_hint == "ADMIT_CARD" and admit.evidence["kind_hint"] == "ADMIT_CARD"

    call = _by_title_evidence(rows, "ASSISTANT_PROFESSOR_[DEPT._OF_MEDICAL_EDUCATION]-2025_DOWNLOAD_CALL_LETTER_REQUIRED_PROFORMAS (06-03-2026)")
    assert call.kind_hint == "ADMIT_CARD" and call.evidence["kind_hint"] == "CALL_LETTER"
    assert call.published_on == date(2026, 3, 6)

    # a schedule/notification carries no result/corrigendum/admit-card label
    sched = _by_title_evidence(rows, "STATE_SERVICE_EXAMINATION-2025_DOCUMENT_VERIFICATION_INTERVIEW_SCHEDULE (02-09-2026)")
    assert sched.kind_hint is None and sched.published_on == date(2026, 9, 2)

    # the page shows no date for this one, so none is set
    undated = _by_title_evidence(rows, "COURT_MANAGER_EXAM-2025_PRELIMS_FEE_REFUND_NOTIFICATION")
    assert undated.published_on is None and "published_on" not in undated.evidence
    assert undated.url == "https://psc.cg.gov.in/PDFs/Notifications/COURT_MANAGER_EXAM-2025_PRELIMS_FEE_REFUND_NOTIFICATION.pdf"

    # e-service portal and other-site links are not official-domain documents
    assert not any("CLICK_HERE" in r.evidence["title"] for r in rows)
    assert not any("PST_DV_RESULT" in r.evidence["title"] for r in rows)

def test_result_rows():
    rows = _rows("Result.html")

    first = rows[0]
    assert first.title == "STATE ENGINEERING SERVICE EXAM-2026 WRITTEN EXAM RESULT (03-09-2026)"
    assert first.url == ("https://psc.cg.gov.in/PDFs/Result/"
                         "STATE_ENGINEERING_SERVICE_EXAM-2026_WRITTEN_EXAM_RESULT%20(03-09-2026).pdf")
    assert first.published_on == date(2026, 9, 3)
    assert first.kind_hint == "RESULT" and first.evidence["kind_hint"] == "RESULT"

    # the page's own misspelling
    dental = _by_title_evidence(rows, "DENTAL_SURGEON_EXAM-2022_SELCTION_LIST (26-08-2022)")
    assert dental.kind_hint == "RESULT" and dental.evidence["kind_hint"] == "SELCTION_LIST"
    assert dental.published_on == date(2022, 8, 26)

    cut = _by_title_evidence(rows, "STATE_SERVICE_EXAM-2024_MAINS_CUT_OFF_MARKS")
    assert cut.kind_hint == "RESULT" and cut.published_on is None

    # stray characters after the date on the page do not hide it
    merit = _by_title_evidence(rows, "STATE_SERVICE_EXAMINATION-2021_\u00a0DETAILED_MERIT_LIST (11-05-2023))")
    assert merit.published_on == date(2023, 5, 11) and merit.kind_hint == "RESULT"

    # a date inside the caption that is not in parentheses (a court order date) is not the document date
    court = _by_title_evidence(
        rows, "RESULT_OF_PETITIONERS_AS_PER_ORDER_BY_HON'BLE_HIGH_COURT_DATED_28-07-2022_[ASSISTANT_PROFESSOR_EXAM-2019](06-08-2022)")
    assert court.published_on == date(2022, 8, 6)

@pytest.mark.parametrize("name", ALL_PAGES)
def test_urls_are_official_and_unique(name):
    rows = _rows(name)
    assert len({r.url for r in rows}) == len(rows)
    for r in rows:
        parsed = urlparse(r.url)
        assert parsed.scheme == "https" and parsed.hostname == "psc.cg.gov.in", r.url
        assert " " not in r.url
        assert "Home_Welcome.php" not in r.url  # breadcrumb is not a row

@pytest.mark.parametrize("name", ALL_PAGES)
def test_evidence_for_every_filled_field_is_page_text(name):
    page = unescape(_html(name))
    for r in _rows(name):
        assert r.title and r.evidence["title"] in page
        assert r.evidence["url"] in page
        for field in ("advertisement_number", "published_on", "last_date", "kind_hint"):
            if getattr(r, field) is not None:
                assert r.evidence.get(field), (field, r.title)
                assert r.evidence[field] in r.evidence["title"], (field, r.title)
        if r.post_names:
            assert r.evidence.get("post_names")

@pytest.mark.parametrize("name", ALL_PAGES)
def test_no_invented_dates(name):
    for r in _rows(name):
        # these pages state no closing dates or advertisement numbers for official-domain documents
        assert r.last_date is None and r.advertisement_number is None
        stated = DATE_IN_PARENS.findall(r.evidence["title"])
        if not stated:
            assert r.published_on is None, r.title
            continue
        d, m, y = (int(x) for x in stated[-1])
        assert r.published_on == date(y, m, d), r.title
        assert r.evidence["published_on"] == f"({stated[-1][0]}-{stated[-1][1]}-{stated[-1][2]})"
