"""
APSC listing adapter, against pages captured live from apsc.nic.in on 2026-10-01
(tests/fixtures/adapters/apsc/, see meta.json there for URL, capture time and fetch mode).
"""
import json
import re
from datetime import date
from html import unescape
from pathlib import Path
from urllib.parse import urlparse

import pytest

from govcareeros.backend.sources.adapters import adapter_for
from govcareeros.backend.sources.adapters import apsc
from govcareeros.backend.sources.registry import SOURCES

FIXTURES = Path(__file__).parents[1] / "fixtures" / "adapters" / "apsc"
META = json.loads((FIXTURES / "meta.json").read_text(encoding="utf-8"))
PAGES = {p["file"]: p for p in META["pages"]}
ALL_PAGES = list(PAGES)
DATE_FIELDS = ("published_on", "last_date")

def _html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")

def _rows(name: str):
    return apsc.parse_listing(_html(name), PAGES[name]["final_url"])

def _by_url(rows, url: str):
    found = [r for r in rows if r.url == url]
    assert len(found) == 1, f"expected exactly one row for {url}, got {len(found)}"
    return found[0]

def _visible_text(html: str) -> str:
    """The page's text with comments and tags removed and whitespace collapsed, as a reader sees it."""
    html = re.sub(r"<!--.*?-->", " ", html, flags=re.S)
    html = re.sub(r"</?(?:span|font|a|b|i|em|strong)\b[^>]*>", "", html, flags=re.I)  # inline: no break
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html))).strip()

def test_fixture_meta_records_live_capture():
    assert META["source"] == "APSC"
    assert META["user_agent"].startswith("GovCareerOS/")
    assert set(PAGES) == {"index.html", "advt_2026.html", "notifications_2026.html", "corig_2026.html",
                          "scr_tst_result_2026.html", "final-results-2026.html"}
    for page in META["pages"]:
        assert (FIXTURES / page["file"]).is_file()
        assert page["url"].startswith("https://apsc.nic.in/")
        assert page["final_url"] == page["url"]
        assert page["capture_date"] == "2026-10-01"
        assert page["fetch_mode"] == "HTTP"
        assert page["http_status"] == 200

def test_discovered_and_listing_urls_on_official_domain():
    assert adapter_for("APSC") is apsc
    spec = next(s for s in SOURCES if s.name == "APSC")
    assert spec.recruitment_url == "https://apsc.nic.in/"
    captured = {p["url"] for p in META["pages"]}
    for url in apsc.LISTING_URLS:
        assert urlparse(url).scheme == "https" and urlparse(url).hostname == "apsc.nic.in"
        assert url in captured  # every listing page the adapter asks for has a live fixture

def test_row_counts_match_the_pages():
    # Each count is the page's own documents on apsc.nic.in plus, on every page but advt_2026 (where it
    # is already a table row), the footer's "Latest Recruitment Advertisement" item (Advt. No. 12/2026).
    # advt: 12 table rows. notifications: 41 table rows, one of them holding three numbered lists (43).
    # corrigendum: 4. screening results: 11. final results: 23 rows, 5 with a cut-off notification beside
    # the result and the CCE-2024 row with three documents (30). Home page: 35 "Latest Updates" items, 29 of
    # them led by a document on apsc.nic.in (the 6 admit-card items point at apscrecruitment.in).
    assert {name: len(_rows(name)) for name in ALL_PAGES} == {
        "index.html": 30,
        "advt_2026.html": 12,
        "notifications_2026.html": 44,
        "corig_2026.html": 5,
        "scr_tst_result_2026.html": 12,
        "final-results-2026.html": 31,
    }

def test_advertisement_rows():
    rows = _rows("advt_2026.html")
    assert [r.advertisement_number for r in rows] == [f"{n:02d}/2026" for n in range(12, 0, -1)]

    ra = rows[0]
    assert ra.title == "Research Assistant under Labour Welfare Department"
    assert ra.url == "https://apsc.nic.in/advt_2026/Advt_no_12-2026_website.pdf"
    assert ra.published_on == date(2026, 8, 4) and ra.last_date == date(2026, 9, 10)
    assert ra.post_names == ["Research Assistant"]
    assert ra.kind_hint == "VACANCY"
    assert ra.evidence == {
        "title": "Research Assistant under Labour Welfare Department",
        "url": "advt_2026/Advt_no_12-2026_website.pdf",
        "advertisement_number": "12/2026",
        "published_on": "04-08-2026",
        "last_date": "10-09-2026",
        "kind_hint": "Recruitment Advertisements >> 2026",
        "post_names": "Research Assistant under Labour Welfare Department",
    }

    je = _by_url(rows, "https://apsc.nic.in/advt_2026/JE_Elec_PWD_03_2026.pdf")
    assert je.title == "Junior Engineer (Electrical), Public Works (Building & NH) Department"
    assert je.advertisement_number == "03/2026"
    assert (je.published_on, je.last_date) == (date(2026, 5, 6), date(2026, 6, 10))
    assert je.post_names == ["Junior Engineer (Electrical)"]

    vo = _by_url(rows, "https://apsc.nic.in/advt_2026/Advt_no_11-2026_website.pdf")
    assert vo.post_names == ["Veterinary Officer/Block Veterinary Officer (VO/BVO)"]
    assert (vo.published_on, vo.last_date) == (date(2026, 8, 3), date(2026, 9, 6))

    hg = _by_url(rows, "https://apsc.nic.in/advt_2026/Class_I_Home_Guard_02_2026.pdf")
    assert hg.post_names == ["Class-I (Junior Grade) Home Guards"]

    assert {r.post_names[0] for r in rows if r.post_names} >= {
        "Junior Manager", "Assistant Manager", "Geophysicist", "Assistant Mining Engineer",
        "Drilling Engineer", "Chemist", "Assistant Geologist",
    }

    # The CCE row names an examination, not a post; its payment-guidelines button is not a document row
    cce = rows[-1]
    assert cce.title == "Combined Competitive (Preliminary) Examination, 2025"
    assert cce.url == "https://apsc.nic.in/advt_2026/Advt_CCE-2025.pdf"
    assert cce.advertisement_number == "01/2026"
    assert (cce.published_on, cce.last_date) == (date(2026, 4, 10), date(2026, 5, 3))
    assert cce.post_names == []
    assert not any("Guidelines" in r.url or "apscrecruitment" in r.url for r in rows)

def test_application_start_date_is_not_read_as_a_listing_date():
    # Row 12/2026 shows "APPLICATION START DATE:11-08-2026"; ListingRow has no field for it
    ra = _rows("advt_2026.html")[0]
    assert "11-08-2026" in _visible_text(_html("advt_2026.html"))
    assert date(2026, 8, 11) not in (ra.published_on, ra.last_date)
    assert not any("11-08-2026" in v for v in ra.evidence.values())

def test_notification_rows_use_the_date_column_not_dates_inside_titles():
    rows = _rows("notifications_2026.html")
    je = rows[0]
    assert je.title == ("Notification regarding Screening Test (OMR Based) for recruitment to the post of "
                        "Junior Engineer (Electrical) under Public Works (Building & NH) Department "
                        "(Advt. No. 03/2026, 05-05-2026)")
    assert je.url == "https://apsc.nic.in/notif_2026/JE_Elec_PWD_Screening_Test_Notifi_03_2026.pdf"
    assert je.advertisement_number == "03/2026"
    assert je.evidence["advertisement_number"] == "Advt. No. 03/2026"
    assert je.published_on == date(2026, 9, 25)  # the Date column, not the advertisement's 05-05-2026
    assert je.last_date is None
    assert je.kind_hint is None and je.post_names == []

    cce = _by_url(rows, "https://apsc.nic.in/notif_2026/CCE_Mains_2025_Schedule_01_2026.pdf")
    assert cce.title == "NOTIFICATION REGARDING THE SCHEDULE FOR COMBINED COMPETITIVE(MAIN) EXAMINATION, 2025"
    assert cce.advertisement_number is None  # the title names none
    assert cce.published_on == date(2026, 9, 5)

    cottage = _by_url(rows, "https://apsc.nic.in/notif_2026/Asst_Director_Cottage_Interview_Notifi_06_2025.pdf")
    assert cottage.advertisement_number == "06/2025"
    assert cottage.evidence["advertisement_number"] == "Advt. No.06/2025"
    assert cottage.published_on == date(2026, 8, 17)

    assert all(r.last_date is None for r in rows)

def test_corrigendum_rows():
    rows = _rows("corig_2026.html")
    first = rows[0]
    assert first.title == ("Corrigendum regarding syllabus for Screening Test (OMR based) for recruitment to the "
                           "post of Junior Engineer (Electrical) under Public Works (B & NH) Department "
                           "(Advt No. 03/2026 dated 05/05/2026)")
    assert first.url == "https://apsc.nic.in/misc_2026/Corrigendum_03_2026.pdf"
    assert first.advertisement_number == "03/2026"
    assert first.evidence["advertisement_number"] == "Advt No. 03/2026"
    assert first.published_on == date(2026, 8, 27)
    assert first.kind_hint == "CORRIGENDUM" and first.evidence["kind_hint"] == "Corrigendums 2026"

    by_number = _by_url(rows, "https://apsc.nic.in/misc_2026/CORRIGENDUM_24-2024.pdf")
    assert by_number.advertisement_number is None  # a notification number, not an advertisement number
    assert by_number.published_on == date(2026, 3, 2)

    assert [r.kind_hint for r in rows[:4]] == ["CORRIGENDUM"] * 4

def test_screening_result_rows_take_the_advt_column():
    rows = _rows("scr_tst_result_2026.html")
    cce = _by_url(rows, "https://apsc.nic.in/misc_2026/CCE_PRE_ADVT_10_2026_Result.pdf")
    assert cce.title == "Result of the Combined Competitive (Preliminary) Examination, 2025"
    assert cce.advertisement_number == "01/2026" and cce.evidence["advertisement_number"] == "01/2026"
    assert cce.published_on == date(2026, 7, 30)
    assert cce.kind_hint == "RESULT"
    assert cce.evidence["kind_hint"] == "Written/Screening Test Results-2026"
    assert all(r.kind_hint == "RESULT" for r in rows[:11])

def test_final_result_cell_with_two_documents_gives_two_rows():
    rows = _rows("final-results-2026.html")
    result = _by_url(rows, "https://apsc.nic.in/misc_2026/JE_PHE_Result_27_2025.pdf")
    cutoff = _by_url(rows, "https://apsc.nic.in/misc_2026/JE_PHE_Cut_Off_27_2025.pdf")
    assert result.title == ("Result of Junior Engineer (Civil/Mechanical/Chemical/Electrical) under Public Health "
                            "Engineering Department [Advt. No. 27/2025, dtd. 11-08-2025]")
    assert cutoff.title.startswith("Notification - Category wise Cut-off marks and marks secured for Junior Engineer")
    for row in (result, cutoff):
        assert row.advertisement_number == "27/2025"
        assert row.published_on == date(2026, 7, 7)
        assert row.kind_hint == "RESULT"
    # The marks-portal buttons beside them are off-domain and not documents
    assert not any("nbapps" in r.url for r in rows)

    cce = _by_url(rows, "https://apsc.nic.in/misc_2026/Result_CCE_2024_17022026.pdf")
    assert cce.title == "Final result of the Combined Competitive Examination, 2024"
    assert cce.advertisement_number == "12/2025" and cce.published_on == date(2026, 2, 17)

def test_home_page_lists_have_no_dates():
    rows = _rows("index.html")
    assert all(r.published_on is None and r.last_date is None for r in rows)

    key = rows[0]
    assert key.title == ("Notification regarding provisional Answer Keys, Assistant Engineer "
                         "(Civil/Mechanical/Chemical), Public Health Engineering Department (Advt. No. 31/2025)")
    assert key.url == "https://apsc.nic.in/misc_2026/AE_PHE_Answerkey_Notifi_31_2025.pdf"
    assert key.advertisement_number == "31/2025"
    assert key.kind_hint == "ANSWER_KEY"
    assert key.evidence["kind_hint"] == "Notification regarding provisional Answer Keys"
    # The per-paper answer-key buttons in the same item are not separate rows
    assert not any(r.url.endswith("AE_PHE_GS_Answerkey_31_2025.pdf") for r in rows)

    corr = _by_url(rows, "https://apsc.nic.in/misc_2026/Corrigendum_03_2026.pdf")
    assert corr.kind_hint == "CORRIGENDUM" and corr.evidence["kind_hint"] == "Corrigendum"

    # The footer box: "ADVT. NO. 12/2026" label under "Latest Recruitment Advertisement"
    latest = _by_url(rows, "https://apsc.nic.in/advt_2026/Advt_no_12-2026_website.pdf")
    assert latest.title == "Research Assistant under Labour Welfare Department"
    assert latest.advertisement_number == "12/2026"
    assert latest.evidence["advertisement_number"] == "ADVT. NO. 12/2026"
    assert latest.kind_hint == "VACANCY"
    assert latest.evidence["kind_hint"] == "Latest Recruitment Advertisement"
    assert latest.post_names == []  # the list does not label a post column

    # Admit-card items link to apscrecruitment.in only
    assert not any("Admit Card" in r.title for r in rows)

def test_navigation_and_commented_out_links_are_not_rows():
    for name in ALL_PAGES:
        for row in _rows(name):
            path = urlparse(row.url).path
            assert not path.endswith((".php", ".html", ".asp", "/")), (name, row.url)
            assert path.lower().endswith(".pdf"), (name, row.url)
        # cce_2018.php sits inside an HTML comment in the footer of every page
        assert not any("cce_2018" in r.url for r in _rows(name))

@pytest.mark.parametrize("name", ALL_PAGES)
def test_urls_absolute_and_on_official_domain(name):
    for row in _rows(name):
        parts = urlparse(row.url)
        assert parts.scheme == "https" and parts.hostname == "apsc.nic.in", row.url
        assert " " not in row.url

@pytest.mark.parametrize("name", ALL_PAGES)
def test_every_filled_field_has_source_evidence(name):
    html = _html(name)
    text = _visible_text(html)
    for row in _rows(name):
        assert row.title and row.evidence["title"] == row.title and row.title in text
        assert row.evidence["url"] in unescape(html)
        for field in ("advertisement_number", "published_on", "last_date", "kind_hint"):
            if getattr(row, field) is not None:
                assert row.evidence.get(field), (row.url, field)
                assert row.evidence[field] in text, (row.url, field, row.evidence[field])
            else:
                assert field not in row.evidence
        if row.advertisement_number:
            assert row.advertisement_number in row.evidence["advertisement_number"]
        if row.post_names:
            assert row.evidence["post_names"] in text
            assert all(p in row.evidence["post_names"] for p in row.post_names)
        else:
            assert "post_names" not in row.evidence

@pytest.mark.parametrize("name", ALL_PAGES)
def test_no_invented_dates(name):
    html = _html(name)
    cells = {re.sub(r"\s+", " ", c).strip() for c in re.findall(r"<td>([^<]*)</td>", html)}
    for row in _rows(name):
        for field in DATE_FIELDS:
            value = getattr(row, field)
            if value is None:
                continue
            ev = row.evidence[field]
            # A whole table cell of the page, in its dd-mm-yyyy format, that reads as exactly this date
            assert ev in cells, (row.url, field, ev)
            assert ev == value.strftime("%d-%m-%Y"), (row.url, field, ev)
