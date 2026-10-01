"""HPSC listing adapter against the live pages captured on 2026-10-01 (see fixtures/adapters/hpsc/meta.json)."""
import json
import re
from datetime import date
from html import unescape
from pathlib import Path
from urllib.parse import urljoin, urlparse

import pytest

from govcareeros.backend.sources.adapters import adapter_for, hpsc
from govcareeros.backend.sources.registry import SOURCES

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "adapters" / "hpsc"
HOME = "https://hpsc.gov.in/en-us/"
ADVERTISEMENTS = "https://hpsc.gov.in/en-us/Instructions"
RESULTS = "https://hpsc.gov.in/en-us/Examination/Results"
ADMIT_CARD = "https://hpsc.gov.in/en-us/Examination/Admit-card"
PAGES = {"home.html": HOME, "advertisements.html": ADVERTISEMENTS, "results.html": RESULTS, "admit-card.html": ADMIT_CARD}
DOC = "https://hpsc.gov.in/Portals/0/"

def _html(name):
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")

@pytest.fixture(scope="module")
def parsed():
    return {name: hpsc.parse_listing(_html(name), url) for name, url in PAGES.items()}

def _by_url(rows):
    return {r.url: r for r in rows}

def _cell_text(fragment):
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()

def _table_rows(html):
    """Date-table rows read independently of the adapter: (date cell, title cell text, first href in the title cell)."""
    out = []
    for table in re.findall(r"<table\b.*?</table>", html, re.S):
        for tr in re.findall(r"<tr\b.*?</tr>", table, re.S):
            cells = re.findall(r"<td\b[^>]*>(.*?)</td>", tr, re.S)
            if len(cells) < 2:
                continue
            href = re.search(r'href="([^"]+)"', cells[1])
            out.append((_cell_text(cells[0]), _cell_text(cells[1]), unescape(href.group(1)) if href else None))
    return out

def _whats_new(html):
    block = html[html.index('class="sidemmenu whats_new"'):]
    block = block[:block.index("</ul>")]
    return [(unescape(h), _cell_text(t)) for h, t in re.findall(r'<li><a href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)]

# --- capture and registration ---------------------------------------------------------------------------------

def test_fixture_meta_records_the_live_capture():
    meta = json.loads((FIXTURE_DIR / "meta.json").read_text(encoding="utf-8"))
    assert meta["source"] == "HPSC"
    assert meta["captured_on"] == "2026-10-01"
    assert meta["fetch_mode"] == "HTTP"
    assert meta["user_agent"] == "GovCareerOS/0.1 (personal government-job tracker)"
    pages = {p["file"]: p for p in meta["pages"]}
    assert set(pages) == set(PAGES)
    for name, url in PAGES.items():
        assert pages[name]["url"] == pages[name]["final_url"] == url
        assert pages[name]["http_status"] == 200
        assert (FIXTURE_DIR / name).exists()

def test_adapter_is_auto_discovered_under_the_registry_name():
    assert adapter_for("HPSC") is hpsc
    spec = next(s for s in SOURCES if s.name == "HPSC")
    assert spec.recruitment_url == HOME
    assert hpsc.LISTING_URLS == [ADVERTISEMENTS, RESULTS, ADMIT_CARD]
    for url in hpsc.LISTING_URLS:
        assert urlparse(url).scheme == "https" and urlparse(url).hostname == "hpsc.gov.in"

# --- Recruitment > Advertisement/ Corrigendum ----------------------------------------------------------------

def test_one_row_per_official_notice_in_the_advertisement_tables(parsed):
    rows = parsed["advertisements.html"]
    table = _table_rows(_html("advertisements.html"))
    assert len(table) == 256
    # One corrigendum links a PDF on www.digialm.com (the exam agency), outside the official domain
    offsite = [t for t in table if "digialm.com" in t[2]]
    assert [t[1] for t in offsite] == [
        "Corrigendum for Advertisement No. 4/2018 - for the posts of Deputy Director Fisheries in Fisheries Department"]
    assert len(rows) == 255 == len({r.url for r in rows})
    assert not any("digialm" in r.url for r in rows)

def test_current_advertisement_row(parsed):
    r = _by_url(parsed["advertisements.html"])[DOC + "Advt_25_2026_FSO_18_09_2026.pdf"]
    assert r.title == "Advt. No. 25 of 2026 - Food Safety Officer (Group-B) in Food and Drugs Administration Department, Haryana"
    assert r.advertisement_number == "25/2026"
    assert r.published_on == date(2026, 9, 18)
    assert r.last_date is None  # the table shows no closing date for it
    assert r.kind_hint == "VACANCY"
    assert r.post_names == ["Food Safety Officer (Group-B)"]
    assert r.evidence == {
        "title": r.title,
        "url": "/Portals/0/Advt_25_2026_FSO_18_09_2026.pdf",
        "published_on": "18.09.2026",
        "advertisement_number": "Advt. No. 25 of 2026",
        "kind_hint": "Advt.",
        "post_names": "Advt. No. 25 of 2026 - Food Safety Officer (Group-B) in Food and Drugs Administration Department, Haryana",
    }

@pytest.mark.parametrize("pdf, title, advt, advt_text, published", [
    ("Advt_22_2026_HCS_Ex_Br_30_01_2026_1.pdf", "Advt No. 22 of 2026 - HCS (Ex. Br.) & Other Allied Services",
     "22/2026", "Advt No. 22 of 2026", date(2026, 1, 30)),
    ("Advt_05_to_17_2026_Various_post_HSIIDC_12_01_2026.pdf",
     "Advt No. 05 to 17 of 2026 - Various Posts (Group-B) of Haryana State Industrial & Infrastructure Development "
     "Corporation Department, Haryana", "05 to 17 of 2026", "Advt No. 05 to 17 of 2026", date(2026, 1, 12)),
    ("Advt_18_19_2026_Lect_FI_IT_13_01_2026.pdf",
     "Advt No. 18 & 19 of 2026 - (i) Lecturer (Financial Management) and (ii) Lecturer (Information & Technology) in "
     "Development & Panchayats Department", "18 & 19 of 2026", "Advt No. 18 & 19 of 2026", date(2026, 1, 13)),
])
def test_advertisement_numbers_as_the_page_writes_them(parsed, pdf, title, advt, advt_text, published):
    r = _by_url(parsed["advertisements.html"])[DOC + pdf]
    assert r.title == title
    assert r.advertisement_number == advt
    assert r.evidence["advertisement_number"] == advt_text
    assert r.published_on == published
    assert r.kind_hint == "VACANCY"

def test_post_names_only_as_listed(parsed):
    by_url = _by_url(parsed["advertisements.html"])
    assert by_url[DOC + "Advt_22_2026_HCS_Ex_Br_30_01_2026_1.pdf"].post_names == ["HCS (Ex. Br.) & Other Allied Services"]
    # "(i) ... and (ii) ..." enumerates two posts
    assert by_url[DOC + "Advt_18_19_2026_Lect_FI_IT_13_01_2026.pdf"].post_names == [
        "Lecturer (Financial Management)", "Lecturer (Information & Technology)"]
    # "Various Posts" names none
    various = by_url[DOC + "Advt_05_to_17_2026_Various_post_HSIIDC_12_01_2026.pdf"]
    assert various.post_names == [] and "post_names" not in various.evidence
    # The cadre is not part of the post
    pgt = by_url[DOC + "Advt_23_2026_PGT_CS_05_02_2026.pdf"]
    assert pgt.title == ("Advt No. 23 of 2026 - Post Graduate Teachers (PGTs) in the subject of Computer Science for "
                         "Rest of Haryana Cadre & Mewat Cadre")
    assert pgt.post_names == ["Post Graduate Teachers (PGTs) in the subject of Computer Science"]

def test_corrigendum_rows(parsed):
    by_url = _by_url(parsed["advertisements.html"])
    r = by_url[DOC + "Corr_ADA_27_05_2026.pdf"]
    assert r.title == "Corrigendum for Advt. No. 18 of 2025 - Assistant District Attorney in Prosecution Department, Haryana"
    assert r.kind_hint == "CORRIGENDUM" and r.evidence["kind_hint"] == "Corrigendum"
    assert r.advertisement_number == "18/2025"
    assert r.published_on == date(2026, 5, 27)
    assert r.post_names == ["Assistant District Attorney"]

    # From the separate "CORRIGENDUM :-" table
    old = by_url[DOC + "Sr_%20Manager%20IA_Corrigendum.pdf"]
    assert old.title == ("Corrigendum for Advertisement No. 1(4) of 2018 - for the posts of Senior Manager (IA) in "
                         "Haryana State Industrial and Infrastructure Development Corporation Ltd.")
    assert old.advertisement_number == "1(4)/2018"
    assert old.evidence["url"] == "/Portals/0/Sr_%20Manager%20IA_Corrigendum.pdf"
    assert old.published_on == date(2021, 10, 21)
    assert old.post_names == ["Senior Manager (IA)"]

def test_unlabelled_announcements_get_no_kind(parsed):
    r = _by_url(parsed["advertisements.html"])[DOC + "Ann_withdrawn_Advt_15_2022_ADO_06_02_2026.pdf"]
    assert r.title.startswith("Announcement regarding withdrawn of Advt No 15/2022")
    assert r.kind_hint is None and "kind_hint" not in r.evidence
    assert r.advertisement_number == "15/2022"
    assert r.evidence["advertisement_number"] == "Advt No 15/2022"
    assert r.published_on == date(2026, 2, 6)

def test_closing_date_is_read_only_from_the_page_remark(parsed):
    by_url = _by_url(parsed["advertisements.html"])
    # Legacy absolute http:// link to the same site, served over https
    r = by_url["https://hpsc.gov.in/en-us/Recruitment/Advertisement-Corrigendum/Advertisement-No-7-of-2015-for-Various-Posts"]
    assert r.evidence["url"] == ("http://hpsc.gov.in/en-us/Recruitment/Advertisement-Corrigendum/"
                                 "Advertisement-No-7-of-2015-for-Various-Posts")
    assert r.title == "Advertisement No. 7 of 2015 for Various Posts"
    assert r.advertisement_number == "7/2015"
    assert r.published_on == date(2015, 12, 16)
    assert r.last_date == date(2016, 1, 18)
    assert r.evidence["last_date"] == "Closing date for Online Submission: 18.01.2016"

    # The hard-copy date next to it ("... is 09-09-2016") is not the closing date
    r = by_url["https://hpsc.gov.in/en-us/Recruitment/Advertisement-Corrigendum/"
               "Advertisement-No-1-of-2016-for-Various-posts-Closing-date-for-Online-Submission-29082016"]
    assert r.title == "Advertisement No. 1 of 2016 for Various posts"
    assert r.last_date == date(2016, 8, 29)
    assert r.evidence["last_date"] == "Closing date for Online Submission: 29.08.2016"

    rows = parsed["advertisements.html"]
    assert sum(1 for r in rows if r.last_date) == 35

def test_kind_counts_on_the_advertisement_page(parsed):
    kinds = [r.kind_hint for r in parsed["advertisements.html"]]
    assert kinds.count("VACANCY") == 188
    assert kinds.count("CORRIGENDUM") == 53
    assert kinds.count(None) == 14

# --- Examination > Results / Admit card ----------------------------------------------------------------------

def test_results_table(parsed):
    rows = parsed["results.html"]
    table = _table_rows(_html("results.html"))
    assert len(table) == 541
    # Four PDFs are listed twice; each document is one row (the first listing)
    assert len(rows) == 537 == len({r.url for r in rows})
    assert all(r.kind_hint == "RESULT" for r in rows)

    by_url = _by_url(rows)
    r = by_url[DOC + "Result_II_Lect_Fashion_Design_28_09_2026.pdf"]
    assert r.title == ("Result-II (In continuation of result dated 24.09.2026) for the posts of Lecturer in Fashion Design "
                       "(Advt. No. 11(Viii)/2021)")
    assert r.published_on == date(2026, 9, 28)
    assert r.evidence["published_on"] == "28.09.2026"
    assert r.advertisement_number == "11(Viii)/2021"
    assert r.evidence["kind_hint"] == "Result"
    assert r.post_names == ["Lecturer in Fashion Design"]

    twice = by_url[DOC + "IMG_0001_1.pdf"]
    assert twice.published_on == date(2025, 5, 18)
    assert twice.title.startswith("In Continuation of dated 04.01.2025 Final Result for the posts of PGT - Home Science")

def test_result_row_without_its_own_label_takes_the_page_heading(parsed):
    r = _by_url(parsed["results.html"])[DOC + "sociol.pdf"]
    assert r.title == "Subject Knowledge Test - PGT Sociology"
    assert r.kind_hint == "RESULT"
    assert r.evidence["kind_hint"] == "Results"  # the page's own heading
    assert r.published_on == date(2024, 9, 26)
    assert r.advertisement_number is None and r.post_names == []

def test_admit_card_table(parsed):
    rows = parsed["admit-card.html"]
    assert len(_table_rows(_html("admit-card.html"))) == 44 == len(rows)
    assert all(r.kind_hint == "ADMIT_CARD" for r in rows)
    r = _by_url(rows)[DOC + "Ann_Admitcard_SKT_ADA_07_08_2026.pdf"]
    assert r.title == ("Announcement regarding provisional admit card for the posts of Assistant District Attorney in "
                       "Prosecution Department, Haryana (Advt. No. 18/2025)")
    assert r.published_on == date(2026, 8, 7)
    assert r.evidence["kind_hint"] == "admit card"
    assert r.advertisement_number == "18/2025"
    assert r.evidence["advertisement_number"] == "Advt. No. 18/2025"
    assert r.post_names == ["Assistant District Attorney"]
    assert r.evidence["post_names"] == "for the posts of Assistant District Attorney in Prosecution Department, Haryana"

# --- Homepage "What's New" ------------------------------------------------------------------------------------

def test_homepage_whats_new_list(parsed):
    rows = parsed["home.html"]
    items = _whats_new(_html("home.html"))
    assert len(items) == 45 == len(rows)
    assert [r.evidence["url"] for r in rows] == [h for h, _ in items]

    r = _by_url(rows)[DOC + "Ann_Int_ADA_30_09_2026.pdf"]
    assert r.title == ("Announcement dated 30.09.2026 regarding schedule of Interview for the posts of Assistant District "
                       "Attorney in Prosecution Department, Haryana (Advt. No. 18/2025)")
    assert r.published_on == date(2026, 9, 30)
    assert r.evidence["published_on"] == "dated 30.09.2026"
    assert r.kind_hint is None
    assert r.advertisement_number == "18/2025"
    assert r.post_names == ["Assistant District Attorney"]

def test_homepage_date_is_the_notices_own_not_one_it_refers_to(parsed):
    r = _by_url(parsed["home.html"])[DOC + "Result_II_Lect_Fashion_Design_28_09_2026.pdf"]
    assert r.title.startswith("Result-II dated 28.09.2026 (In continuation of result dated 24.09.2026)")
    assert r.published_on == date(2026, 9, 28)
    assert r.kind_hint == "RESULT"

def test_one_advertisement_per_post_followed_by_the_department(parsed):
    # The department after the last "(Advt. No. ...)" is not a post and does not drop the posts before it
    r = _by_url(parsed["home.html"])[DOC + "Ann_postpond_SM_IA_M_IA_03_09_2026.pdf"]
    assert r.title == ("Announcement dated 03.09.2026 regarding postponement of Subject Knowledge Test for the posts of "
                       "Senior Manager (IA) (Advt. No. 08/2026) & Manager (IA) (Advt. No. 09/2026) in HSIIDC, scheduled "
                       "to be held on 27.09.2026")
    assert r.advertisement_number == "08/2026"
    assert r.post_names == ["Senior Manager (IA)", "Manager (IA)"]

    r = _by_url(parsed["home.html"])[DOC + "Ann_Scheme_Pattern_Syllabus_SASP_JSA_P_24_08_2026.pdf"]
    assert r.post_names == ["System Analyst/Senior Programmer", "Jr. System Analyst/Programmer"]

def test_sidebar_is_not_read_on_pages_with_their_own_table(parsed):
    # "What's New" is a sidebar on every page; on listing pages only the page's table is read
    sidebar_only = DOC + "Rollwise_Marks_SKT_AP_Commerce_29_09_2026.pdf"
    assert sidebar_only in _by_url(parsed["home.html"])
    for name in ("advertisements.html", "results.html", "admit-card.html"):
        assert sidebar_only not in _by_url(parsed[name])

def test_navigation_and_menu_links_are_skipped(parsed):
    urls = {r.url for rows in parsed.values() for r in rows}
    for menu in ("https://hpsc.gov.in/en-us/About-Us", "https://hpsc.gov.in/en-us/RTI", "https://hpsc.gov.in/en-us/Tenders",
                 "https://hpsc.gov.in/en-us/Previous-Question-Papers", "https://hpsc.gov.in/en-us/Screen-Reader",
                 "https://hpsc.gov.in/en-us/Examination/Results", "https://hpsc.gov.in/en-us/Instructions",
                 "https://hpsc.gov.in/en-us/Sitemap", "https://regn.hpsc.gov.in/", "https://hpsc.gov.in/"):
        assert menu not in urls
    # The "Instructions" column of the advertisement table is not a separate notice
    assert ("https://hpsc.gov.in/en-us/Advertisments-and-Instructions/Advertisement-No-7-of-2015-for-Various-Posts-instruction26"
            not in urls)

# --- invariants over every row --------------------------------------------------------------------------------

def test_all_urls_are_on_the_official_domain(parsed):
    for rows in parsed.values():
        for r in rows:
            parsed_url = urlparse(r.url)
            assert parsed_url.scheme == "https", r.url
            assert parsed_url.hostname == "hpsc.gov.in", r.url

def test_evidence_for_every_filled_field_is_page_text(parsed):
    for name, rows in parsed.items():
        html = _html(name)
        # Block tags separate text; an inline tag may split a cell's text mid-word ("08/2023</a>)")
        spaced = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html)))
        joined = re.sub(r"\s+", " ", unescape(re.sub(r"<(?!/?(?:td|th|tr|li|br|p|div)\b)[^>]+>", "",
                                                     re.sub(r"<(?:/?(?:td|th|tr|li|p|div)|br)\b[^>]*>", " ", html))))
        hrefs = {unescape(h) for h in re.findall(r'href="([^"]+)"', html)}
        for r in rows:
            filled = {"title", "url"}
            filled |= {f for f in ("advertisement_number", "published_on", "last_date", "kind_hint") if getattr(r, f) is not None}
            if r.post_names:
                filled.add("post_names")
            assert set(r.evidence) == filled, r
            assert r.evidence["title"] == r.title
            assert r.evidence["url"] in hrefs
            assert urljoin(PAGES[name], r.evidence["url"]).split("://", 1)[1] == r.url.split("://", 1)[1]
            for field in filled - {"url"}:
                assert r.evidence[field] in spaced or r.evidence[field] in joined, (name, field, r.evidence[field])
            if r.advertisement_number:
                assert r.evidence["advertisement_number"] in r.title
            if r.post_names:
                assert r.evidence["post_names"] in r.title
                for post in r.post_names:
                    assert post in r.evidence["post_names"] or post in r.title

def test_no_invented_dates(parsed):
    for name in ("advertisements.html", "results.html", "admit-card.html"):
        shown = {}
        for date_text, cell, href in _table_rows(_html(name)):
            shown.setdefault(urljoin(PAGES[name], href).split("://", 1)[1], (date_text, cell))
        for r in parsed[name]:
            date_text, cell = shown[r.url.split("://", 1)[1]]
            assert r.evidence["published_on"] == date_text
            day, month, year = (int(x) for x in date_text.split("."))
            assert r.published_on == date(year, month, day)
            closing = re.search(r"Closing date for Online Submission: (\d\d)\.(\d\d)\.(\d{4})", cell)
            if closing:
                d, m, y = (int(x) for x in closing.groups())
                assert r.last_date == date(y, m, d)
            else:
                assert r.last_date is None and "last_date" not in r.evidence

    items = dict(_whats_new(_html("home.html")))
    for r in parsed["home.html"]:
        own = re.match(r"^[^()]*?\bdated (\d\d)\.(\d\d)\.(\d{4})", items[r.evidence["url"]])
        assert own, r.title  # every "What's New" item states its own date
        d, m, y = (int(x) for x in own.groups())
        assert r.published_on == date(y, m, d)
        assert r.last_date is None
