"""RPSC listing adapter against the live advertisements page captured on 2026-10-01 (see fixtures/adapters/rpsc/meta.json)."""
import json
import re
from datetime import date
from html import unescape
from pathlib import Path
from urllib.parse import urlparse

import pytest

from govcareeros.backend.sources.adapters import adapter_for, rpsc
from govcareeros.backend.sources.registry import SOURCES

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "adapters" / "rpsc"
PAGE_URL = "https://rpsc.rajasthan.gov.in/advertisements"
DOC = "https://rpsc.rajasthan.gov.in/Static/RecruitmentAdvertisements/"

@pytest.fixture(scope="module")
def html():
    return (FIXTURE_DIR / "advertisements.html").read_text(encoding="utf-8")

@pytest.fixture(scope="module")
def rows(html):
    return rpsc.parse_listing(html, PAGE_URL)

@pytest.fixture(scope="module")
def by_url(rows):
    return {r.url: r for r in rows}

@pytest.fixture(scope="module")
def page_rows(html):
    """The table's own rows, read independently of the adapter: (release date, description, href)."""
    table = html[html.index("<table"):html.index("</table>")]
    out = []
    for tr in re.findall(r"<tr\b[^>]*>(.*?)</tr>", table, re.S):
        cells = re.findall(r"<td\b[^>]*>(.*?)</td>", tr, re.S)
        if not cells:
            continue
        text = [re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", c))).strip() for c in cells]
        out.append((text[1], text[3], re.search(r"href='([^']+)'", tr).group(1)))
    return out

def test_fixture_meta_records_the_live_capture():
    meta = json.loads((FIXTURE_DIR / "meta.json").read_text(encoding="utf-8"))
    assert meta["url"] == PAGE_URL
    assert meta["captured_on"] == "2026-10-01"
    assert meta["fetch_mode"] == "HTTP"

def test_adapter_is_auto_discovered_under_the_registry_name():
    assert adapter_for("RPSC") is rpsc
    spec = next(s for s in SOURCES if s.name == "RPSC")
    assert spec.recruitment_url == PAGE_URL

def test_one_row_per_document_in_the_table(rows, page_rows):
    assert len(page_rows) == 620
    # 620 table rows link 571 distinct PDFs: one corrigendum PDF is listed under 24 exams
    assert len(rows) == len({href for _, _, href in page_rows}) == 571
    assert len({r.url for r in rows}) == len(rows)
    assert sum(1 for _, _, href in page_rows if href.endswith("81E2774EDE38492E8098DE2600D5ABDA.pdf")) == 24

def test_latest_corrigendum_row(by_url):
    r = by_url[DOC + "12E5561DA6634F6A9548306FFA667C12.pdf"]
    assert r.title == "Corrigendum No. 15/2026-27 for Sub Inspector Comb. Comp. Exam - 2021"
    assert r.published_on == date(2026, 9, 9)
    assert r.kind_hint == "CORRIGENDUM"
    # A corrigendum's own number is not an advertisement number
    assert r.advertisement_number is None
    assert r.last_date is None
    assert r.evidence == {
        "title": "Corrigendum No. 15/2026-27 for Sub Inspector Comb. Comp. Exam - 2021",
        "url": "Static/RecruitmentAdvertisements/12E5561DA6634F6A9548306FFA667C12.pdf",
        "published_on": "09/09/2026",
        "kind_hint": "Corrigendum",
    }

@pytest.mark.parametrize("pdf, title, advt, published, kind_text", [
    ("C80D8376B256408692F17AAA61B754DE.pdf", "Advt. No. 05/2026-27 for Physiotherapist - 2026",
     "05/2026-27", date(2026, 7, 21), "Advt."),
    ("76C02A7D61A54708AFFCDD7A2A60DBB2.pdf", "Advt. No. 02/2026-27 for Raj. State And Sub. Services Comb. Comp Exam - 2026",
     "02/2026-27", date(2026, 5, 27), "Advt."),
    ("52C8C180FC8444E397C1FAAEF0947C96.pdf", "Advt. No. 22/2024-25 for Sr. Teacher GR-II Comp. Exam 2024",
     "22/2024-25", date(2024, 12, 11), "Advt."),
    ("86D89644B12048FFBA3ACF57C9DE1453.pdf", "Advt. 01/2008-09 For Teacher Gr. III(Sanskrit Edu. Dept.) Comp. Exam 2008",
     "01/2008-09", date(2008, 6, 20), "Advt."),
])
def test_advertisement_rows(by_url, pdf, title, advt, published, kind_text):
    r = by_url[DOC + pdf]
    assert r.title == title
    assert r.advertisement_number == advt
    assert r.published_on == published
    assert r.kind_hint == "VACANCY"
    assert r.evidence["kind_hint"] == kind_text
    assert r.evidence["published_on"] == published.strftime("%d/%m/%Y")
    assert r.evidence["advertisement_number"] in title

def test_page_typing_inside_advertisement_numbers(by_url):
    spaced = by_url[DOC + "40B86E277B8A4CDD8D3F0BFB457F6085.pdf"]
    assert spaced.title == "Advt No. 08 /2017-18 for Physiotherapist(Non-TSP) Exam 2018"
    assert spaced.advertisement_number == "08/2017-18"
    assert spaced.evidence["advertisement_number"] == "Advt No. 08 /2017-18"

    corr = by_url[DOC + "779BBBB0FE3B4841B919A243C4FD1420.pdf"]
    assert corr.title == "Corrigendum No.07/2014-15 Of Advt.01/2014 -15 For Assistant Professor Biochemistry"
    # The corrigendum names the advertisement it amends
    assert corr.kind_hint == "CORRIGENDUM"
    assert corr.advertisement_number == "01/2014-15"
    assert corr.evidence["advertisement_number"] == "Advt.01/2014 -15"
    assert corr.published_on == date(2014, 7, 31)

def test_unusual_advertisement_number_formats(rows):
    numbers = {r.advertisement_number for r in rows}
    for expected in ("08/EXAM/SI-PC/EP-I/2020-21", "E-01/2020-21", "EC-6/2011-12", "04/E-A/2011-12", "07/2021-2022"):
        assert expected in numbers
    # "Advt.R8/Assistant Town Planner 11-12" and "Advertisement No. F7()/SET/RPSC/ Noti./2013-14/208" state no clean number
    for r in rows:
        if r.title in ("Advt.R8/Assistant Town Planner 11-12",
                       "Advertisement No. F7()/SET/RPSC/ Noti./2013-14/208 for SET Exam 2013"):
            assert r.advertisement_number is None and "advertisement_number" not in r.evidence

def test_post_names_only_where_the_page_names_the_post(by_url, rows):
    r = by_url[DOC + "2538690ED02B4CB6AC3E8266910B9DC5.pdf"]
    assert r.title == "Joint Recruitment Advt. No. 09/2020-21 for the post of Asst. Testing Officer and Supdt. Garden"
    assert r.post_names == ["Asst. Testing Officer", "Supdt. Garden"]
    assert r.evidence["post_names"] == "for the post of Asst. Testing Officer and Supdt. Garden"
    assert r.advertisement_number == "09/2020-21"
    assert r.published_on == date(2021, 2, 11)
    for row in rows:
        if row.post_names:
            assert re.search(r"\bpost\s+of\b", row.title, re.I)

def test_unlabelled_rows_get_no_kind(by_url):
    r = by_url[DOC + "A625ED018C3C4536902F5A76F53292B9.pdf"]
    assert r.title == "Report of Equivalency Committee as per UGC Regulation 2018 for Asst. Prof. (College Education) - 2023"
    assert r.kind_hint is None and "kind_hint" not in r.evidence
    assert r.advertisement_number is None
    assert r.published_on == date(2023, 6, 22)

def test_kind_counts(rows):
    kinds = [r.kind_hint for r in rows]
    assert kinds.count("CORRIGENDUM") == 315
    assert kinds.count("VACANCY") == 241
    assert kinds.count(None) == 15

def test_all_urls_are_official_documents(rows):
    for r in rows:
        parsed = urlparse(r.url)
        assert parsed.scheme == "https"
        assert parsed.hostname == "rpsc.rajasthan.gov.in"
        # The page mixes ".pdf" and ".PDF"
        assert parsed.path.startswith("/Static/RecruitmentAdvertisements/") and parsed.path.lower().endswith(".pdf")

def test_navigation_and_menu_links_are_skipped(rows):
    urls = {r.url for r in rows}
    for menu in ("Static/Others/RPSC_RTI.pdf", "Static/InformationForCandidates/FAQ.pdf", "Static/Others/innovations.pdf",
                 "results", "home", "sitemap"):
        assert "https://rpsc.rajasthan.gov.in/" + menu not in urls

def test_evidence_for_every_filled_field_is_page_text(rows, html):
    page_text = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html)))
    for r in rows:
        filled = {"title", "url"}
        filled |= {f for f in ("advertisement_number", "published_on", "last_date", "kind_hint")
                   if getattr(r, f) is not None}
        if r.post_names:
            filled.add("post_names")
        assert set(r.evidence) == filled, r
        assert r.evidence["title"] == r.title
        assert f"href='{r.evidence['url']}'" in html
        for name in filled - {"url"}:
            assert r.evidence[name] in page_text, (name, r.evidence[name])
        if r.advertisement_number:
            assert r.evidence["advertisement_number"] in r.title
            assert re.sub(r"\s+", "", r.advertisement_number) in re.sub(r"\s+", "", r.evidence["advertisement_number"])
        if r.kind_hint:
            assert r.title.startswith(r.evidence["kind_hint"])

def test_no_invented_dates(rows, page_rows):
    release_by_href = {}
    for release, _, href in page_rows:
        release_by_href.setdefault(href, release)
    for r in rows:
        # The listing shows no closing date column
        assert r.last_date is None
        shown = release_by_href[r.evidence["url"]]
        assert r.evidence["published_on"] == shown
        day, month, year = (int(x) for x in shown.split("/"))
        assert r.published_on == date(year, month, day)
