"""MHA listing adapter against the live www.mha.gov.in vacancies pages captured on 2026-10-01 (see fixtures/adapters/mha/meta.json)."""
import json
import re
from html import unescape
from pathlib import Path
from urllib.parse import urljoin, urlparse

import pytest

from govcareeros.backend.sources.adapters import adapter_for, mha
from govcareeros.backend.sources.registry import SOURCES

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "adapters" / "mha"
LISTING_URL = "https://www.mha.gov.in/en/notifications/vacancies"
PAGE1_URL = LISTING_URL + "?page=1"
PAGE2_URL = LISTING_URL + "?page=2"
DOC = "https://www.mha.gov.in/sites/default/files/"
PAGES = [("vacancies.html", LISTING_URL), ("vacancies_page1.html", PAGE1_URL), ("vacancies_page2.html", PAGE2_URL)]

def _read(name):
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")

def _text(fragment):
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()

def _table_rows(html):
    """The listing table's own rows, read independently of the adapter: (title cell text, [href, ...])."""
    out = []
    for tr in re.findall(r"<tr>(.*?)</tr>", html, re.S):
        title = re.search(r'<td headers="view-title-table-column"[^>]*>(.*?)</td>', tr, re.S)
        if title:
            out.append((_text(title.group(1)), [unescape(h) for h in re.findall(r'href="([^"]+)"', tr)]))
    return out

@pytest.fixture(scope="module")
def pages():
    return {url: _read(name) for name, url in PAGES}

@pytest.fixture(scope="module")
def rows_by_page(pages):
    return {url: mha.parse_listing(html, url) for url, html in pages.items()}

@pytest.fixture(scope="module")
def all_rows(rows_by_page):
    return [r for url in (LISTING_URL, PAGE1_URL, PAGE2_URL) for r in rows_by_page[url]]

@pytest.fixture(scope="module")
def by_url(all_rows):
    return {r.url: r for r in all_rows}

def test_fixture_meta_records_the_live_capture():
    meta = json.loads((FIXTURE_DIR / "meta.json").read_text(encoding="utf-8"))
    assert meta["url"] == LISTING_URL
    assert meta["captured_on"] == "2026-10-01"
    assert meta["fetch_mode"] == "HTTP"
    assert [(p["url"], p["file"], p["http_status"], p["fetch_mode"]) for p in meta["pages"]] == [
        (url, name, 200, "HTTP") for name, url in PAGES]

def test_adapter_is_auto_discovered_under_the_registry_name():
    assert adapter_for("MHA") is mha
    spec = next(s for s in SOURCES if s.name == "MHA")
    assert spec.recruitment_url == LISTING_URL

def test_listing_urls_are_the_pages_own_pager_links(pages):
    pager = {urljoin(LISTING_URL, unescape(h)) for h in re.findall(r'<a href="(\?page=\d+)"', pages[LISTING_URL])}
    assert mha.LISTING_URLS == [PAGE1_URL, PAGE2_URL]
    assert set(mha.LISTING_URLS) <= pager
    assert all(urlparse(u).scheme == "https" and urlparse(u).hostname == "www.mha.gov.in" for u in mha.LISTING_URLS)

def test_one_row_per_table_row_on_every_page(pages, rows_by_page):
    for url, expected in ((LISTING_URL, 20), (PAGE1_URL, 20), (PAGE2_URL, 9)):
        table_rows = _table_rows(pages[url])
        assert len(table_rows) == expected
        rows = rows_by_page[url]
        assert [r.title for r in rows] == [title for title, _ in table_rows]
        assert [r.url for r in rows] == [urljoin(url, hrefs[0]) for _, hrefs in table_rows]

def test_first_row_on_the_current_page(rows_by_page):
    first = rows_by_page[LISTING_URL][0]
    assert first.title == ("Filling up the 01 post of Senior Instructor (Mountaineering in Sashastra Seema Bal (SSB) "
                           "on deputation(Including short-term contract) basis.")
    assert first.url == DOC + "SSB_vacancy_29092026.PDF"
    assert first.evidence["url"] == "/sites/default/files/SSB_vacancy_29092026.PDF"
    assert first.kind_hint == "VACANCY" and first.evidence["kind_hint"] == "Filling up"
    # "(Mountaineering ..." never closes in the title: the post name is left out rather than guessed
    assert first.post_names == [] and "post_names" not in first.evidence
    assert first.advertisement_number is None

def test_specific_rows(by_url):
    r = by_url[DOC + "PostMember%28PD%29LPAI_15092026.pdf"]
    assert r.title == "Appointment to the Post of Member (P&D) of the LPAI on deputation basis- inviting applications"
    assert r.post_names == ["Member (P&D)"] and r.evidence["post_names"] == "Post of Member (P&D)"
    assert r.kind_hint == "VACANCY"

    r = by_url[DOC + "DDVacancy_10092026.pdf"]
    assert r.title.startswith("Vacancy circular for One (01) post of Deputy Director (Implementation) in the "
                              "Department of Official Language, Ministry of Home Affairs on Deputation basis")
    assert r.post_names == ["Deputy Director (Implementation)"]
    assert (r.kind_hint, r.evidence["kind_hint"]) == ("VACANCY", "Vacancy circular")

    r = by_url[DOC + "VacancyCircular_21082026.pdf"]
    assert r.post_names == ["Consultants"]
    assert r.evidence["post_names"].startswith("Engagement of 30 Nos. retired government Official")
    assert r.evidence["post_names"].endswith("as Consultants")

    r = by_url[DOC + "VacancyUDC_31072026.PDF"]
    assert r.title == ("Filling up of two (02) posts of Upper Division Clerk (UDC) on deputation basis in the Office "
                       "of the Custodian of Enemy Property for India (CEPI) at New Delhi (01) and Mumbai (01)-reg.")
    assert r.post_names == ["Upper Division Clerk (UDC)"]

    r = by_url[DOC + "Vacancies_15062026.pdf"]
    assert r.title == ("Engagement of two Technical Resource persons - IT Support Engineer (01) and Website "
                       "Administrator (01) for NIC: Department of Justice-regarding.")
    assert r.post_names == ["IT Support Engineer", "Website Administrator"]
    assert r.evidence["post_names"] == "persons - IT Support Engineer (01) and Website Administrator (01)"

    r = by_url[DOC + "Vacancy_29052026.pdf"]
    assert r.post_names == ["Deputy Director General (DDG)"]
    assert r.evidence["post_names"] == "vacancies of Deputy Director General (DDG)"

    r = by_url[DOC + "SI_Non_GD_08042026.pdf"]
    assert r.title == ("Advertisement for the Posts of Sub-Inspector (Staff Nurse),Sub-Inspector(Pioneer),and "
                       "Sub-Inspector(Communication) in SSB.")
    assert r.post_names == ["Sub-Inspector (Staff Nurse)", "Sub-Inspector(Pioneer)", "Sub-Inspector(Communication)"]
    assert (r.kind_hint, r.evidence["kind_hint"]) == ("VACANCY", "Advertisement")

    r = by_url[DOC + "RecruitmentNotice_07052026.pdf"]
    assert r.title == "Hiring of 195 Technical Manpower Under I4C -Reg"
    assert r.post_names == ["Technical Manpower"] and r.evidence["post_names"] == "Hiring of 195 Technical Manpower"

def test_advertisement_number_only_where_the_title_states_one(all_rows, by_url):
    r = by_url[DOC + "UPSCDOletterdated_18062026.pdf"]
    assert r.title == "Vacancy for various post for direct recruitment through UPSC Adv no. 05/2026"
    assert r.advertisement_number == "05/2026"
    assert r.evidence["advertisement_number"] == "Adv no. 05/2026"
    assert [x.url for x in all_rows if x.advertisement_number] == [r.url]
    # A "File No." of a referenced circular is not an advertisement number
    assert by_url[DOC + "VacancyCircular_19062026.PDF"].advertisement_number is None

def test_corrigenda_and_extensions_are_labelled(by_url):
    expected = {
        "VacancyCircular_07082026.pdf": "Extension of last date",
        "AirVacancy_19062026.PDF": "Extension of Last Date",
        "VacancyCircular_19062026.PDF": "Corrigendum",
        "ProfessorExtension_21042026_0.pdf": "Extension of Date",
    }
    for name, words in expected.items():
        r = by_url[DOC + name]
        assert (r.kind_hint, r.evidence["kind_hint"]) == ("CORRIGENDUM", words)
    assert by_url[DOC + "VacancyCircular_19062026.PDF"].title == (
        "Corrigendum to vacancy circular File No. CEPI/DEL/ 36/25/2025/725 Dated: 09.06.2026")
    assert by_url[DOC + "AirVacancy_19062026.PDF"].post_names == ["Consultant"]

def test_no_row_has_an_invented_date(all_rows, pages):
    # The table has no date column and the page states no publication or closing date for any row
    for html in pages.values():
        header = re.search(r"<thead>(.*?)</thead>", html, re.S).group(1)
        assert [_text(h) for h in re.findall(r"<th\b[^>]*>(.*?)</th>", header, re.S)] == [
            "SR-No", "Keyword", "Download/Link"]
    assert all(r.published_on is None and r.last_date is None for r in all_rows)
    assert not any(k in r.evidence for r in all_rows for k in ("published_on", "last_date"))

def test_all_urls_are_official_documents_and_unique(all_rows):
    assert len(all_rows) == 49
    assert len({r.url for r in all_rows}) == 49
    for r in all_rows:
        parsed = urlparse(r.url)
        assert parsed.scheme == "https" and parsed.hostname == "www.mha.gov.in"
        assert r.url.startswith(DOC) and r.url.lower().endswith(".pdf")

def test_navigation_and_pager_links_are_skipped(all_rows, pages):
    urls = {r.url for r in all_rows}
    hrefs = set(re.findall(r'href="([^"]+)"', pages[LISTING_URL]))
    assert "/en/division_of_mha/AGMUT_Cadre_Management" in hrefs and "?page=1" in hrefs
    assert urljoin(LISTING_URL, "/en/division_of_mha/AGMUT_Cadre_Management") not in urls
    assert PAGE1_URL not in urls and "https://ips.gov.in/" not in urls

def test_same_title_listed_again_with_a_new_document_is_kept(by_url):
    a = by_url[DOC + "AppointmentMemberLPAI_21092026.pdf"]
    b = by_url[DOC + "MemberLPI_12052026.pdf"]
    assert a.title == b.title == "Appointment to the Post of Member (Finance) of the LPAI on deputation basis- inviting applications-reg."
    assert a.post_names == b.post_names == ["Member (Finance)"]

def test_every_filled_field_has_exact_source_evidence(all_rows, pages):
    html_all = "".join(pages.values())
    for r in all_rows:
        assert r.evidence["title"] == r.title
        assert f'href="{r.evidence["url"]}"' in html_all
        assert urljoin(LISTING_URL, r.evidence["url"]) == r.url
        for name in ("advertisement_number", "kind_hint", "post_names"):
            value = getattr(r, name)
            if value:
                assert r.evidence.get(name), (r.url, name)
                assert r.evidence[name] in r.title, (r.url, name)
            else:
                assert name not in r.evidence
        if r.advertisement_number:
            assert r.evidence["advertisement_number"].endswith(r.advertisement_number)
        for post in r.post_names:
            assert post in r.evidence["post_names"]
    # Rows whose title names no post ("vacant posts at ...", "various post", "posts on ...") or whose
    # post name never closes its brackets carry no post names
    assert sorted(r.url[len(DOC):] for r in all_rows if not r.post_names) == sorted([
        "SSB_vacancy_29092026.PDF", "VacancyCircular_07082026.pdf", "VacancyCircular_24062026.pdf",
        "VacancyCircular_19062026.PDF", "UPSCDOletterdated_18062026.pdf", "LetterBPRD_05062026.pdf"])
    assert all(r.kind_hint for r in all_rows)
