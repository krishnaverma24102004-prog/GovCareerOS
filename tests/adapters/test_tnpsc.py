"""TNPSC listing adapter against live pages captured on 2026-10-01 (see fixtures/adapters/tnpsc/meta.json)."""
import json
import re
from datetime import date
from html import unescape
from pathlib import Path

import pytest

from govcareeros.backend.sources.adapters import adapter_for, tnpsc
from govcareeros.backend.sources.registry import SOURCES
from govcareeros.backend.sources.url_guard import UrlPolicy

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "adapters" / "tnpsc"
PAGE_URL = "https://www.tnpsc.gov.in/English/notification.aspx"
DASHBOARD_URL = "https://tnpsc.gov.in/web/examdashboard/index.aspx"
DOC = "https://www.tnpsc.gov.in/Document/"
POLICY = UrlPolicy.from_url(PAGE_URL)

PAGES = {
    "notification.html": PAGE_URL,
    "examdashboard.html": DASHBOARD_URL,
    "examdashboard_01_2026.html": DASHBOARD_URL + "?notid=16f23df0-a669-4855-a718-8083a5745077",
    "examdashboard_04_2026.html": DASHBOARD_URL + "?notid=024fc5d2-a6b0-4c29-93db-979bf485d544",
    "examdashboard_05_2026.html": DASHBOARD_URL + "?notid=33aa5415-6205-4ae9-842e-212d62d088f1",
}
FIELDS = ("title", "url", "advertisement_number", "published_on", "last_date", "post_names", "kind_hint")

def _html(name):
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")

def _page_text(html):
    """The page's visible text, whitespace-collapsed, read independently of the adapter."""
    text = re.sub(r"<br\s*/?>", " ", html, flags=re.I)
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", text)))

@pytest.fixture(scope="module")
def parsed():
    return {name: tnpsc.parse_listing(_html(name), url) for name, url in PAGES.items()}

@pytest.fixture(scope="module")
def table_rows(parsed):
    return {r.url: r for r in parsed["notification.html"]}

def _filled(row, field):
    value = getattr(row, field)
    return value not in (None, "", [])

# ---------------------------------------------------------------- fixture + discovery

def test_fixture_meta_records_the_live_capture():
    meta = json.loads((FIXTURE_DIR / "meta.json").read_text(encoding="utf-8"))
    assert meta["url"] == PAGE_URL
    assert meta["captured_on"] == "2026-10-01"
    assert meta["fetch_mode"] == "HTTP"
    assert {p["file"]: p["url"] for p in meta["pages"]} == PAGES
    assert all(p["http_status"] == 200 for p in meta["pages"])

def test_adapter_is_auto_discovered_under_the_registry_name():
    assert adapter_for("TNPSC") is tnpsc
    spec = next(s for s in SOURCES if s.name == "TNPSC")
    assert spec.recruitment_url == PAGE_URL
    # The extra listing page is on the same official domain, over https
    assert tnpsc.LISTING_URLS == [DASHBOARD_URL]
    assert all(POLICY.allows(u) for u in tnpsc.LISTING_URLS)

def test_notification_page_points_to_the_exam_dashboard_for_later_years():
    # Why LISTING_URLS exists: the registry page itself says so
    text = _page_text(_html("notification.html"))
    assert "The notifications for the year 2025 are available in the Exam Dashboard page" in text

# ---------------------------------------------------------------- notification.aspx table

def test_one_row_per_notification_and_per_follow_up_document(parsed):
    rows = parsed["notification.html"]
    html = _html("notification.html")
    table = html[html.index('id="noti_table"'):]
    table = table[:table.index("</table>")]
    notifications = re.findall(r'class="viewlink"', table)
    assert len(notifications) == 291
    assert sum(r.kind_hint == "VACANCY" for r in rows) == 291
    assert len(rows) == 545
    assert len({r.url for r in rows}) == len(rows)

def test_latest_notification_row(table_rows):
    row = table_rows[DOC + "english/SCE English Final_.pdf"]
    assert row.title == "SPECIAL COMPETITIVE EXAMINATION (2024)"
    assert row.advertisement_number == "18/2024"
    assert row.last_date == date(2024, 12, 24)
    assert row.evidence["last_date"] == "24/12/2024"
    # "Online Registration From" (25/11/2024) is when applications open, not a publication date
    assert row.published_on is None
    assert row.kind_hint == "VACANCY"
    assert row.evidence["kind_hint"] == "Notification Number"
    assert row.post_names == []  # an examination, not a post
    assert row.evidence["url"] == "../Document/english/SCE English Final_.pdf"

def test_post_row_with_addenda(table_rows):
    row = table_rows[DOC + "english/APP GRADE II ENGLISH_.pdf"]
    assert row.title == "ASSISTANT PUBLIC PROSECUTOR (GRADE II)"
    assert row.advertisement_number == "13/2024"
    assert row.last_date == date(2024, 10, 12)
    assert row.post_names == ["ASSISTANT PUBLIC PROSECUTOR (GRADE II)"]
    assert row.evidence["post_names"] == "ASSISTANT PUBLIC PROSECUTOR (GRADE II)"

    a = table_rows[DOC + "English/Addendum_13A.pdf"]
    b = table_rows[DOC + "English/APP Addendum 13B.pdf"]
    assert a.title == "ASSISTANT PUBLIC PROSECUTOR (GRADE II) - ADDENDUM_13A"
    assert b.title == "ASSISTANT PUBLIC PROSECUTOR (GRADE II) - ADDENDUM_13B"
    for doc in (a, b):
        assert doc.kind_hint == "CORRIGENDUM"
        assert doc.advertisement_number == "13/2024"
        # the closing date belongs to the notification, not to its addenda
        assert doc.last_date is None and doc.published_on is None
        assert doc.post_names == []

def test_service_group_is_dropped_from_post_names(table_rows):
    row = table_rows[DOC + "english/09_23_Asst_Jailor_ENG.pdf"]
    assert row.advertisement_number == "09/2023"
    assert row.title == "ASSISTANT JAILOR (MEN & WOMEN) (TAMIL NADU JAIL SUBORDINATE SERVICE)"
    assert row.post_names == ["ASSISTANT JAILOR (MEN & WOMEN)"]
    assert row.last_date == date(2023, 5, 11)

def test_letter_suffixed_notification_numbers(table_rows):
    numbers = {r.advertisement_number for r in table_rows.values() if r.kind_hint == "VACANCY"}
    assert {"19a/2012", "19b/2012", "19c/2012", "19d/2012", "19e/2012"} <= numbers
    assert None not in numbers

def test_rows_without_registration_dates_get_no_dates(table_rows):
    rimc = [r for r in table_rows.values() if r.advertisement_number in ("14/2015", "03/2015", "01/2016")
            and r.kind_hint == "VACANCY"]
    assert len(rimc) == 3
    for row in rimc:
        assert row.title == "Admission to the Rashtriya Indian Military College (DEHRADUN)"
        assert row.last_date is None and row.published_on is None
        assert row.post_names == []

def test_follow_up_kinds_come_from_the_link_text(table_rows):
    rules = [r for r in table_rows.values() if r.kind_hint == "RULES"]
    assert {r.evidence["kind_hint"] for r in rules} == {
        "CIVIL JUDGE-TAMIL NADU STATE JUDICIAL SERVICE-RULES", "CIVIL JUDGE - TAMIL NADU STATE JUDICIAL SERVICE - RULES"}
    erratum = table_rows[DOC + "English/CHEMIST ERRATUM.pdf"]
    assert erratum.kind_hint == "CORRIGENDUM" and erratum.evidence["kind_hint"] == "ERRATUM"
    distribution = table_rows[DOC + "English/RI_DOV.pdf"]
    assert distribution.evidence["title"] == "DISTRIBUTION OF VACANCIES"
    assert distribution.kind_hint is None  # not labelled as an amendment

def test_hidden_portal_links_and_navigation_are_skipped(parsed):
    urls = [r.url for r in parsed["notification.html"]]
    assert not any("tnpscexams.in" in u for u in urls)  # APPLY ONLINE / Download Hall Ticket (display:none)
    assert not any(u.lower().endswith(("home.aspx", "instructions-to-candidates_2025.pdf")) for u in urls)
    assert not any("examdashboard" in u.lower() for u in urls)
    assert all(u.startswith(DOC) for u in urls)

# ---------------------------------------------------------------- Exam Dashboard

def test_dashboard_index_cards(parsed):
    rows = parsed["examdashboard.html"]
    assert [(r.advertisement_number, r.title) for r in rows] == [
        ("01/2026", "RASHTRIYA INDIAN MILITARY COLLEGE, DEHRADUN (JANUARY-2027 TERM)"),
        ("04/2026", "COMBINED TECHNICAL SERVICES EXAMINATION (NON – INTERVIEW POSTS)"),
        ("05/2026", "COMBINED CIVIL SERVICES EXAMINATION – I (GROUP I SERVICES)"),
        ("06/2026", "COMBINED TECHNICAL SERVICES EXAMINATION (DIPLOMA / ITI LEVEL)"),
        ("07/2026", "COMBINED CIVIL SERVICES EXAMINATION - II (GROUP II AND IIA SERVICES)"),
        ("08/2026", "COMBINED TECHNICAL SERVICES EXAMINATION (INTERVIEW POSTS)"),
    ]
    assert rows[0].url == DASHBOARD_URL + "?notid=16f23df0-a669-4855-a718-8083a5745077"
    assert rows[0].evidence["title"] == "1. RASHTRIYA INDIAN MILITARY COLLEGE, DEHRADUN (JANUARY-2027 TERM) - 01/2026"
    # The cards show no dates
    assert all(r.published_on is None and r.last_date is None for r in rows)

def test_dashboard_notification_page(parsed):
    rows = parsed["examdashboard_04_2026.html"]
    assert len(rows) == 7
    notification = rows[0]
    assert notification.title == ("COMBINED TECHNICAL SERVICES EXAMINATION (NON – INTERVIEW POSTS)"
                                  " - Notification No. 04/2026")
    assert notification.url == "https://tnpsc.gov.in/document/english/CTS(Non Interview) English Final_.pdf"
    assert notification.advertisement_number == "04/2026"
    assert notification.kind_hint == "VACANCY"
    assert notification.published_on == date(2026, 5, 20)
    assert notification.evidence["published_on"] == "20-05-2026"
    assert notification.last_date == date(2026, 6, 25)
    assert notification.evidence["last_date"] == (
        "Last date and time for submission of online application : 25.06.2026")

    addendum = next(r for r in rows if r.url == "https://tnpsc.gov.in/Document/english/Addendum 4B.pdf")
    assert addendum.kind_hint == "CORRIGENDUM"
    assert addendum.published_on == date(2026, 7, 16)
    assert addendum.last_date is None
    assert addendum.evidence["title"] == "Addendum for Notification No. 04/2026 ADDENDUM (4B/2026)"

    press = next(r for r in rows if r.url.endswith("Press_Release_51_2026.pdf"))
    assert press.kind_hint is None and press.published_on == date(2026, 5, 20)

    # "Apply Online" and "Hall Ticket" open apply.tnpscexams.in (off the official domain): skipped
    assert not any("tnpscexams.in" in r.url for r in rows)

def test_dashboard_page_closing_date_only_when_stated(parsed):
    group1 = parsed["examdashboard_05_2026.html"][0]
    assert group1.advertisement_number == "05/2026"
    assert group1.published_on == date(2026, 6, 23)
    assert group1.last_date == date(2026, 7, 29)
    assert "29.07.2026" in group1.evidence["last_date"]

    rimc = parsed["examdashboard_01_2026.html"]
    assert len(rimc) == 1
    assert rimc[0].url == "https://tnpsc.gov.in/document/english/NOTIFICATION-RIMC-English.pdf"
    assert rimc[0].published_on == date(2026, 2, 13)
    assert rimc[0].last_date is None  # this page has no "Apply Online" row

# ---------------------------------------------------------------- invariants over every page

@pytest.mark.parametrize("name", list(PAGES))
def test_every_url_is_on_the_official_domain(parsed, name):
    rows = parsed[name]
    assert rows
    for row in rows:
        assert row.url.startswith("https://")
        assert POLICY.allows(row.url), row.url

@pytest.mark.parametrize("name", list(PAGES))
def test_every_filled_field_has_page_evidence(parsed, name):
    html = _html(name)
    text = _page_text(html)
    raw = unescape(html)
    for row in parsed[name]:
        for field in FIELDS:
            if _filled(row, field):
                assert row.evidence.get(field), (row.url, field)
                source = raw if field == "url" else text
                assert row.evidence[field] in source, (row.url, field, row.evidence[field])

@pytest.mark.parametrize("name", list(PAGES))
def test_no_date_is_invented(parsed, name):
    for row in parsed[name]:
        for field in ("published_on", "last_date"):
            value = getattr(row, field)
            if value is None:
                continue
            day, month, year = f"{value.day:02d}", f"{value.month:02d}", str(value.year)
            shown = {f"{day}{sep}{month}{sep}{year}" for sep in "/-."}
            assert any(s in row.evidence[field] for s in shown), (row.url, field, value, row.evidence[field])

def test_unknown_page_gives_no_rows():
    assert tnpsc.parse_listing("<html><body><a href='/x.pdf'>Recruitment</a></body></html>", PAGE_URL) == []
