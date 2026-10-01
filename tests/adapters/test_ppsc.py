"""PPSC listing adapter, against the home page captured live from https://ppsc.gov.in/ (see meta.json)."""
import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import pytest

from govcareeros.backend.sources.adapters import adapter_for, all_adapters
from govcareeros.backend.sources.adapters import ppsc
from govcareeros.backend.sources.adapters.base import ListingRow

FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "adapters" / "ppsc"
META = json.loads((FIXTURE_DIR / "meta.json").read_text(encoding="utf-8"))
HTML = (FIXTURE_DIR / META["file"]).read_text(encoding="utf-8")
BASE_URL = META["final_url"]


@pytest.fixture(scope="module")
def rows():
    return ppsc.parse_listing(HTML, BASE_URL)


def _by_url(rows, url) -> ListingRow:
    found = [r for r in rows if r.url == url]
    assert len(found) == 1, f"expected exactly one row for {url}, got {len(found)}"
    return found[0]


def test_fixture_meta_records_the_capture():
    assert META["url"] == "https://ppsc.gov.in/"
    assert META["final_url"] == "https://ppsc.gov.in/"
    assert META["capture_date"] == "2026-10-01"
    assert META["fetch_mode"] == "HTTP"
    assert META["http_status"] == 200


def test_adapter_is_auto_discovered():
    assert all_adapters()["PPSC"] is ppsc
    assert adapter_for("PPSC") is ppsc
    assert ppsc.SOURCE == "PPSC"


def test_reads_the_announcements_board(rows):
    # 645 items on the board; 37 tender / quotation notices skipped; the rest are distinct pages
    assert len(rows) == 608
    assert rows[0].title == (
        "FINAL RESULT FOR RECRUITMENT TO 101 POSTS OF HORTICULTURE DEVELOPMENT OFFICER IN THE DEPARTMENT "
        "OF HORTICULTURE GOVERNMENT OF PUNJAB UPLOADED ON 28-09-2026"
    )
    assert len({r.url for r in rows}) == len(rows)


def test_final_result_row(rows):
    r = _by_url(rows, "https://ppsc.gov.in/index.aspx?page=2802&tabid=10&tablinkid=6841")
    assert r.title.startswith("FINAL RESULT FOR RECRUITMENT TO 101 POSTS OF HORTICULTURE DEVELOPMENT OFFICER")
    assert r.published_on == date(2026, 9, 28)
    assert r.evidence["published_on"] == "UPLOADED ON 28-09-2026"
    assert r.kind_hint == "RESULT"
    assert r.evidence["kind_hint"] == "RESULT"
    assert r.post_names == ["HORTICULTURE DEVELOPMENT OFFICER"]
    assert r.evidence["post_names"] == "101 POSTS OF HORTICULTURE DEVELOPMENT OFFICER"
    assert r.advertisement_number is None
    assert r.last_date is None


def test_advertisement_number_row(rows):
    r = _by_url(rows, "https://ppsc.gov.in/index.aspx?page=2779&tabid=10&tablinkid=6818")
    assert r.title == (
        "PUBLIC NOTICE REGARDING CALCULATORS FOR COMPETITIVE EXAMINATION FOR SR ASSISTANT ACCOUNTS IN THE "
        "DEPARTMENT OF HOUSING URBAN DEVELOPMENT PUDA ADVT NO 202270 UPLOADED ON 17-07-2026"
    )
    assert r.advertisement_number == "202270"
    assert r.evidence["advertisement_number"] == "ADVT NO 202270"
    assert r.published_on == date(2026, 7, 17)


def test_several_advertisement_numbers_in_one_title(rows):
    r = _by_url(rows, "https://ppsc.gov.in/index.aspx?page=2768&tabid=10&tablinkid=6807")
    assert r.advertisement_number == "202270, 20261, 20262"
    assert r.evidence["advertisement_number"].startswith("Advt No 202270 IN THE DEPARTMENT")
    assert r.evidence["advertisement_number"].endswith("Advt No 20262")
    assert r.published_on == date(2026, 7, 2)
    assert r.evidence["published_on"] == "uploaded on 02-07-2026"
    assert r.post_names == ["SENIOR ASSISTANT ACCOUNTS"]


def test_advertisement_range_and_post(rows):
    r = _by_url(rows, "https://ppsc.gov.in/index.aspx?page=2797&tabid=10&tablinkid=6836")
    assert r.advertisement_number == "202229 TO 202236"
    assert r.evidence["advertisement_number"] == "ADVERTISEMENT NOS 202229 TO 202236"
    assert r.post_names == ["SENIOR ASSISTANT"]
    assert r.published_on == date(2026, 9, 16)


def test_corrigendum_row(rows):
    r = _by_url(rows, "https://ppsc.gov.in/index.aspx?page=2369&tabid=10&tablinkid=6408")
    assert r.title == (
        "CORRIGENDUM REGARDING THE RECRUITMENT TO 11 POSTS OF LEGAL ASSISTANT HEAD OFFICE GROUP-B IN THE "
        "DEPARTMENT OF SCHOOL EDUCATION GOVERNMENT OF PUNJAB ADVT No 202218 Updated 26-07-2024"
    )
    assert r.kind_hint == "CORRIGENDUM"
    assert r.evidence["kind_hint"] == "CORRIGENDUM"
    assert r.advertisement_number == "202218"
    assert r.published_on == date(2024, 7, 26)
    assert r.evidence["published_on"] == "Updated 26-07-2024"
    assert r.post_names == ["LEGAL ASSISTANT HEAD OFFICE GROUP-B"]


def test_admit_card_row(rows):
    r = _by_url(rows, "https://ppsc.gov.in/index.aspx?page=2662&tabid=10&tablinkid=6701")
    assert r.kind_hint == "ADMIT_CARD"
    assert r.evidence["kind_hint"] == "ADMIT CARD"
    assert r.post_names == ["SENIOR SCALE STENOGRAPHER"]
    assert r.published_on == date(2025, 10, 17)


def test_new_recruitment_row_without_a_date(rows):
    r = _by_url(rows, "https://ppsc.gov.in/index.aspx?page=2761&tabid=10&tablinkid=6800")
    assert r.title == (
        "NEW RECRUITMENT TO TWENTY ONE POSTS OF PUNJAB CIVIL SERVICES EXECUTIVE BRANCH TO BE FILLED FROM "
        "REGISTER A-2 FOR RECRUITMENT PROCESS YEAR 2026"
    )
    assert r.kind_hint == "VACANCY"
    assert r.post_names == ["PUNJAB CIVIL SERVICES EXECUTIVE BRANCH"]
    assert r.evidence["post_names"] == "TWENTY ONE POSTS OF PUNJAB CIVIL SERVICES EXECUTIVE BRANCH"
    # the page gives no upload date for this item, so none is invented
    assert r.published_on is None
    assert "published_on" not in r.evidence


def test_upload_time_before_date(rows):
    r = _by_url(rows, "https://ppsc.gov.in/index.aspx?page=2445&tabid=10&tablinkid=6484")
    assert r.published_on == date(2024, 10, 21)
    assert r.evidence["published_on"] == "updated at 6 P M on 21-10-2024"


def test_glued_department_is_cut_from_post_name(rows):
    r = _by_url(rows, "https://ppsc.gov.in/index.aspx?page=2280&tabid=10&tablinkid=6319")
    assert "GROUP-AIN THE DEPARTMENT" in r.title
    assert r.post_names == ["DISTRICT DEFENCE SERVICES WELFARE OFFICER GROUP-A"]


def test_exam_dates_and_unlabelled_dates_are_not_publication_dates(rows):
    exam = _by_url(rows, "https://ppsc.gov.in/index.aspx?page=1942&tabid=10&tablinkid=5981")
    assert exam.title == "Seating Plan For Draftsmen-head Draftsmen Examination To Be Conducted On 25-09-2022"
    assert exam.published_on is None and exam.last_date is None
    unlabelled = _by_url(rows, "https://ppsc.gov.in/index.aspx?page=2502&tabid=10&tablinkid=6541")
    assert unlabelled.title.endswith("SCIENCE TECHNOLOGY AND ENVIRONMENT 27-11-2024")
    assert unlabelled.published_on is None
    assert unlabelled.kind_hint == "RESULT"


def test_navigation_and_tender_links_are_skipped(rows):
    titles = {r.title.upper() for r in rows}
    for nav in ("OPEN ADVERTISEMENT", "CLOSED ADVERTISMENT", "LATEST RESULTS", "HISTORY", "DOWNLOAD ADMIT CARD",
                "VIEW ALL..", "CONTACT US", "PRIVACY POLICY"):
        assert nav not in titles
    assert not any(re.search(r"\btender\b|\bquotations?\b", r.title, re.I) for r in rows)
    # every row is an Announcements item (tabid=10); menu (tabid=3, 15, 24, 48 ...) and logo-strip (tabid=21) links are not
    assert all("tabid=10&" in r.url for r in rows)
    assert "/index.aspx?page=68&tabid=21&tablinkid=107" in HTML


def test_all_urls_are_on_the_official_domain(rows):
    for r in rows:
        parsed = urlparse(r.url)
        assert parsed.scheme == "https"
        assert parsed.hostname == "ppsc.gov.in"


def test_evidence_for_every_filled_field(rows):
    for r in rows:
        assert r.evidence["title"] == r.title
        assert r.evidence["url"] in HTML
        assert r.url.endswith(r.evidence["url"].lstrip("/"))
        for field in ("published_on", "last_date", "advertisement_number", "kind_hint"):
            if getattr(r, field) is not None:
                assert r.evidence.get(field), f"{field} without evidence: {r.title}"
        if r.post_names:
            assert r.evidence.get("post_names")
            for name in r.post_names:
                assert name in r.evidence["post_names"]
        for field, text in r.evidence.items():
            if field != "url":
                assert text in r.title, f"evidence for {field} is not source text: {text!r}"


def test_no_invented_dates(rows):
    for r in rows:
        # the board never states a closing date
        assert r.last_date is None
        if r.published_on is None:
            continue
        ev = r.evidence["published_on"]
        assert re.search(r"up(?:loaded|dated)", ev, re.I)
        assert r.title.endswith(ev)
        d, m, y = re.search(r"(\d{1,2})[-./](\d{1,2})[-./](\d{2,4})$", ev).groups()
        assert r.published_on == date(int(y) if len(y) == 4 else 2000 + int(y), int(m), int(d))
    assert sum(r.published_on is not None for r in rows) == 600
