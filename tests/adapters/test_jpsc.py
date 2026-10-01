"""JPSC listing adapter, against pages captured live from www.jpsc.gov.in (see the fixture meta.json)."""
import json
import re
from datetime import date
from html import unescape
from pathlib import Path
from urllib.parse import urlparse

import pytest

from govcareeros.backend.sources.adapters import adapter_for
from govcareeros.backend.sources.adapters import jpsc
from govcareeros.backend.sources.registry import SOURCES

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "adapters" / "jpsc"
META = json.loads((FIXTURES / "meta.json").read_text(encoding="utf-8"))
PAGES = sorted(META["files"])

def _html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")

def _rows(name: str):
    return jpsc.parse_listing(_html(name), META["files"][name]["final_url"])

def _by_url(name: str):
    return {r.url: r for r in _rows(name)}

def _squash(text: str) -> str:
    return re.sub(r"\s+", "", unescape(text))

def _page_text(name: str) -> str:
    return _squash(re.sub(r"<[^>]+>", " ", _html(name)))

def _parse_dmy(text: str) -> date:
    d, m, y = re.search(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})", text).groups()
    return date(int(y), int(m), int(d))

# --- discovery / fixtures -------------------------------------------------------------------

def test_module_is_discovered_for_the_registry_source():
    assert adapter_for("JPSC") is jpsc
    spec = next(s for s in SOURCES if s.name == jpsc.SOURCE)
    assert spec.official_domain == "jpsc.gov.in"
    for url in jpsc.LISTING_URLS:
        assert urlparse(url).scheme == "https" and urlparse(url).hostname == "www.jpsc.gov.in"

def test_fixtures_are_live_captures():
    assert META["source"] == "JPSC"
    for name, m in META["files"].items():
        assert (FIXTURES / name).exists()
        assert m["url"].startswith("https://www.jpsc.gov.in/") and m["http_status"] == 200
        assert m["fetch_mode"] == "HTTP" and m["captured_at"].startswith("2026-")

# --- homepage (registry page) ---------------------------------------------------------------

def test_homepage_news_tiles():
    rows = _by_url("home.html")
    r = rows["https://www.jpsc.gov.in/data/Press_Release_04_24_dated_14_07_2026.pdf"]
    assert r.title == "Press Release regarding Forest Range Officer Advt.No.04/2024 uploaded date :14-07-2026"
    assert r.advertisement_number == "04/2024"
    assert r.evidence["advertisement_number"] == "Advt.No.04/2024"
    assert r.published_on == date(2026, 7, 14)
    assert r.evidence["published_on"] == "uploaded date :14-07-2026"
    assert r.last_date is None and r.kind_hint is None

    r = rows["https://www.jpsc.gov.in/data/jharkhand%20gazette_29_11_2023.pdf"]
    assert r.published_on == date(2023, 11, 29) and r.advertisement_number is None

def test_homepage_tickers_read_only_labelled_dates():
    rows = _by_url("home.html")
    r = rows["https://www.jpsc.gov.in/data/Press_Release_03_24_dated_10_08_2026.pdf"]
    assert r.title == ("Press release regarding Recruitment of Forest Range Officer and Assistant Conservator "
                       "of Forest (Advt.No.-04/2024 and 03/2024) Dtd.10-08-2026")
    assert r.advertisement_number == "04/2024"
    assert r.published_on == date(2026, 8, 10) and r.evidence["published_on"] == "Dtd.10-08-2026"
    assert r.post_names == ["Forest Range Officer and Assistant Conservator of Forest"]

    # "(29-07-2026)" is a bare date in brackets: not labelled as a publication date, so not read
    r = rows["https://www.jpsc.gov.in/data/Notice.pdf"]
    assert r.title == ("Notice regarding recruitment of Jharkhand Combined Civil Services Examination-2025 "
                       "Advt.No.-01/2026 (29-07-2026)")
    assert r.advertisement_number == "01/2026"
    assert r.published_on is None and r.post_names == []

    r = rows["https://www.jpsc.gov.in/Civil_judge_junior_division_22_2023/login.php"]
    assert r.advertisement_number == "22/2023" and r.published_on is None

def test_homepage_latest_recruitments_list():
    rows = _by_url("home.html")
    listed = [r for r in rows.values() if "exam_files.php?id=" in r.url]
    assert len(listed) == 40

    r = rows["https://www.jpsc.gov.in/exam_files.php?id=13038"]
    assert r.title == "Recruitment of Drug Inspector Advt.No.-12/2025"
    assert r.advertisement_number == "12/2025"
    assert r.post_names == ["Drug Inspector"]
    assert r.kind_hint == "VACANCY" and r.evidence["kind_hint"] == "Latest Recruitments/Openings"
    assert r.published_on is None and r.last_date is None  # the list states no dates

    r = rows["https://www.jpsc.gov.in/exam_files.php?id=13042"]
    assert r.title == "Recruitment of Lecturer in Govt. Polytechnic/Govt. Women's Polytechnic(Regular), Advt.No.-04/2026"
    assert r.advertisement_number == "04/2026"
    assert r.post_names == ["Lecturer in Govt. Polytechnic/Govt. Women's Polytechnic"]

    r = rows["https://www.jpsc.gov.in/exam_files.php?id=13037"]
    assert r.advertisement_number == "11/2025" and r.evidence["advertisement_number"] == "Advt. No. 11/2025"
    assert r.post_names == ["Assistant Director/Senior Scientific Officer"]

    # An examination is not a post name
    r = rows["https://www.jpsc.gov.in/exam_files.php?id=13039"]
    assert r.title == "Recruitment of Jharkhand Combined Civil Services Examination-2025 Advt.No.-01/2026"
    assert r.advertisement_number == "01/2026" and r.post_names == []
    r = rows["https://www.jpsc.gov.in/exam_files.php?id=13034"]
    assert r.title == "Jharkhand Eligibility Test 2024 Advt.No.-08/2025" and r.post_names == []

def test_homepage_skips_navigation_and_off_domain_links():
    urls = set(_by_url("home.html"))
    for nav in ("index.php", "advertisement.php", "Results.php", "Gallery.php", "Address.php",
                "list_rti.php", "Exam_Calendar_2026_dtd_09_07_2026.pdf", "JPSC%20Annual%20Report"):
        assert not any(nav in u for u in urls), nav
    assert not any("jpscotr.com" in u or "upsc.gov.in" in u or ":2096" in u for u in urls)
    assert len(urls) == 50

# --- per-advertisement pages (exam_files.php?id=N) ------------------------------------------

def test_advertisement_page_documents():
    rows = _by_url("exam_files_13045.html")
    heading = "Recruitment of Assistant Professor in Medical Colleges(Super Specialist)Department Advt.No.-07/2026"
    r = rows["https://www.jpsc.gov.in/data/Advertisement_07_26_dated_15_04_2026.pdf"]
    assert r.title == f"{heading}: Advertisement dtd.15-04-2026"
    assert r.evidence["title"] == "Advertisement dtd.15-04-2026" and r.evidence["heading"] == heading
    assert r.advertisement_number == "07/2026"
    assert r.published_on == date(2026, 4, 15) and r.evidence["published_on"] == "dtd.15-04-2026"
    assert r.kind_hint == "VACANCY" and r.evidence["kind_hint"] == "Advertisement"
    assert r.post_names == ["Assistant Professor in Medical Colleges(Super Specialist)Department"]

    r = rows["https://www.jpsc.gov.in/data/Press_Release_07_26_dated_15_04_2026.pdf"]
    assert r.published_on == date(2026, 4, 15) and r.kind_hint is None

def test_advertisement_page_kinds_and_dates():
    rows = _by_url("exam_files_13020.html")
    r = rows["https://www.jpsc.gov.in/data/Press_Release_and_Result_22_23_dated_02_07_2024.pdf"]
    assert r.evidence["title"] == "Press Release and P.T Result dtd.02-07-2024"
    assert r.kind_hint == "RESULT" and r.published_on == date(2024, 7, 2)
    assert r.advertisement_number == "22/2023" and r.post_names == ["Civil Judge (Junior Division)"]

    r = rows["https://www.jpsc.gov.in/data/Press_Release_and_Model_Answer_Key_dated_28_03_2024.pdf"]
    # the listing says 29-03-2024 (the file name says 28); the listing text is what counts
    assert r.published_on == date(2024, 3, 29) and r.kind_hint == "RESULT"

    # "Order Dated 09-02-2026 ... dtd.01-06-2026": the listing's own date is the last label
    r = rows["https://www.jpsc.gov.in/data/Court_Order_dated_09_02_2026-2.pdf"]
    assert r.published_on == date(2026, 6, 1) and r.evidence["published_on"] == "dtd.01-06-2026"

    # Admit-card row: its date is not labelled ("..., 05-03-2024"), so none is read
    r = rows["https://www.jpsc.gov.in/admit_card_civil_Judge_Junior_Division_P_T_22_2023/"]
    assert r.evidence["title"] == "Click here to download Admit Card for P.T. Examination, 05-03-2024"
    assert r.kind_hint == "ADMIT_CARD" and r.published_on is None

    # The SBI Collect payment link is off the official domain
    assert not any("onlinesbi" in u for u in rows)

def test_advertisement_page_rules_rows():
    rows = _by_url("exam_files_13039.html")
    r = rows["https://www.jpsc.gov.in/data/2ND_AMENDMENT_Rules.pdf"]
    assert r.published_on == date(2026, 1, 31) and r.evidence["published_on"] == "dated:-31-01-2026"
    r = rows["https://www.jpsc.gov.in/data/Press_Release_and_P.T_Result_01_26_dated_02_07_2026.pdf"]
    assert r.kind_hint == "RESULT" and r.published_on == date(2026, 7, 2) and r.post_names == []
    assert len([u for u in rows if u.startswith("https://www.jpsc.gov.in/data/")]) == 26

# --- section tables (advertisement.php, Results.php) ----------------------------------------

def test_advertisement_table():
    rows = _by_url("advertisement.html")
    r = rows["https://www.jpsc.gov.in/data/Advertisement%20CJ(JD)RE-2013%20(Advt%20no.%2004-2013).pdf"]
    assert r.title == "CIVIL JUDGE (Junior Division) Recruitment-2013"
    assert r.advertisement_number == "04/2013" and r.evidence["advertisement_number"] == "04/2013"
    assert r.last_date == date(2014, 1, 6) and r.evidence["last_date"] == "06/01/2014"
    assert r.published_on is None  # the table gives only the closing date
    assert r.kind_hint == "VACANCY" and r.evidence["kind_hint"] == "Advertisement"

    r = rows["https://www.jpsc.gov.in/data/Principal_Govt_Polytechnic_Govt_Womens_%20Polytechnic_Colleges_01_2014.pdf"]
    assert r.title == "Principal, Govt. Polytechnic / Govt. Women’s Polytechnic Colleges"
    assert r.advertisement_number == "01/2014" and r.last_date == date(2014, 10, 24)

def test_corrigendum_table():
    rows = _by_url("advertisement.html")
    r = rows["https://www.jpsc.gov.in/data/Corrigendum_CJ_JD_-Cut-off_Date__21-12-2013_.pdf"]
    assert r.title == "Corrigendum Civil Judge (JD)-2013 (Cut off Date)"
    assert r.published_on == date(2013, 12, 21) and r.evidence["published_on"] == "21-12-2013"
    assert r.kind_hint == "CORRIGENDUM" and r.last_date is None and r.advertisement_number is None

    r = rows["https://www.jpsc.gov.in/data/Press_Release_CJ_JD_PT-2014.pdf"]
    assert r.kind_hint == "CORRIGENDUM" and r.evidence["kind_hint"] == "Corrigendum"  # filed under the section
    r = rows["https://www.jpsc.gov.in/data/Civil%20Judge%20(JD)%20PT-2014%20Admit%20Advertisement.pdf"]
    assert r.kind_hint == "ADMIT_CARD" and r.published_on == date(2014, 2, 23)

def test_results_tables():
    rows = _by_url("results.html")
    r = rows["https://www.jpsc.gov.in/data/mo(bc)%20backlog.pdf"]
    assert r.title == "Medical Officer(Basic Cadre) Backlig"
    assert r.advertisement_number == "05/2013" and r.published_on == date(2015, 6, 22)
    assert r.kind_hint == "RESULT" and r.evidence["kind_hint"] == "Result"

    r = rows["https://www.jpsc.gov.in/fishree%20Office/"]
    assert r.advertisement_number == "16/2011" and r.published_on == date(2014, 10, 21)

    r = rows["https://www.jpsc.gov.in/data/Revised%20Model%20Answer%20Key%20CJ(JD)PT-2014.pdf"]
    assert r.published_on == date(2014, 6, 2) and r.evidence["published_on"] == "02-06-2014"
    assert r.kind_hint == "RESULT"

# --- invariants over every captured page ----------------------------------------------------

@pytest.mark.parametrize("name", PAGES)
def test_every_row_is_official_and_evidenced(name):
    rows = _rows(name)
    assert rows
    page = _page_text(name)
    assert len({r.url for r in rows}) == len(rows)
    for r in rows:
        parts = urlparse(r.url)
        assert parts.scheme == "https" and parts.hostname == "www.jpsc.gov.in", r.url
        assert " " not in r.url
        assert r.title and _squash(r.evidence["title"]) in page
        filled = {
            "advertisement_number": r.advertisement_number, "published_on": r.published_on,
            "last_date": r.last_date, "post_names": r.post_names, "kind_hint": r.kind_hint,
        }
        for field, value in filled.items():
            if value:
                assert field in r.evidence, (r.title, field)
                assert _squash(r.evidence[field]) in page, (r.title, field, r.evidence[field])
        if r.advertisement_number:
            assert r.advertisement_number in r.evidence["advertisement_number"]
        for field in ("published_on", "last_date"):
            value = getattr(r, field)
            if value is not None:
                # the date is exactly the one in the cited text, never derived from elsewhere
                assert _parse_dmy(r.evidence[field]) == value, (r.title, field)
        for post in r.post_names:
            assert post in r.evidence["post_names"]

def test_no_date_without_page_text():
    # Dates appear only where a page states one: the homepage list states none
    for r in _rows("home.html"):
        if "exam_files.php" in r.url:
            assert r.published_on is None and r.last_date is None
    # Bare, unlabelled dates in a caption are never read as publication dates
    for name in PAGES:
        for r in _rows(name):
            if r.published_on is not None and "published_on" in r.evidence:
                ev = r.evidence["published_on"]
                labelled = re.fullmatch(r"(?i)(uploaded\s*date|dtd|dated)\s*[.:\-–]*\s*\d{1,2}[-/.]\d{1,2}[-/.]\d{4}", ev)
                table_cell = re.fullmatch(r"\d{1,2}[-/.]\d{1,2}[-/.]\d{4}", ev)
                assert labelled or table_cell, ev
