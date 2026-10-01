"""STEEL listing adapter against the live vacancy pages captured on 2026-10-01 (see fixtures/adapters/steel/meta.json)."""
import hashlib
import json
import re
from datetime import date
from html import unescape
from pathlib import Path
from urllib.parse import urljoin, urlparse

import pytest

from govcareeros.backend.sources.adapters import adapter_for, steel
from govcareeros.backend.sources.registry import SOURCES
from govcareeros.backend.sources.url_guard import UrlPolicy

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "adapters" / "steel"
PAGE_URL = "https://steel.gov.in/vacancy"
PAGE2_URL = "https://steel.gov.in/vacancy?page=1"
FILES = "https://steel.gov.in/sites/default/files/"

def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", unescape(text)).strip()

def _load(name: str) -> str:
    return (FIXTURE_DIR / name).read_bytes().decode("utf-8")

@pytest.fixture(scope="module")
def html():
    return _load("vacancy.html")

@pytest.fixture(scope="module")
def html2():
    return _load("vacancy_page_1.html")

@pytest.fixture(scope="module")
def rows(html):
    return steel.parse_listing(html, PAGE_URL)

@pytest.fixture(scope="module")
def rows2(html2):
    return steel.parse_listing(html2, PAGE2_URL)

@pytest.fixture(scope="module")
def all_rows(rows, rows2):
    return rows + rows2

@pytest.fixture(scope="module")
def by_url(all_rows):
    return {r.url: r for r in all_rows}

def _table_rows(page: str):
    """(block heading, title text, title href, document hrefs, validity text) for every row of the
    recruitment_vacancy view tables, read with plain regexes independently of the adapter's parser."""
    page = re.sub(r"<!--.*?-->", "", page, flags=re.S)
    out = []
    blocks = re.split(r'(?=<div class="views-element-container[^"]*" id="block-steel-views-block-recruitment-vacancy)', page)
    for block in blocks[1:]:
        heading = _norm(re.search(r"<h2>(.*?)</h2>", block, re.S).group(1))
        tbody = re.search(r"<tbody>(.*?)</tbody>", block, re.S)
        if not tbody:
            continue
        for tr in re.findall(r"<tr>(.*?)</tr>", tbody.group(1), re.S):
            cells = dict(re.findall(r'<td headers="view-([a-z-]+?)-table-column(?:--\d+)?"[^>]*>(.*?)</td>', tr, re.S))
            title_href, title_inner = re.search(r'<a href="([^"]+)"[^>]*>(.*?)</a>', cells["title"], re.S).groups()
            docs = re.findall(r'<a href="([^"]+)"', cells["field-documents"])
            out.append((heading, _norm(title_inner), title_href, docs, _norm(re.sub(r"<[^>]+>", " ", cells["field-validity-date"]))))
    return out

@pytest.fixture(scope="module")
def table_rows(html, html2):
    return _table_rows(html), _table_rows(html2)

def test_fixture_meta_records_the_live_capture(html, html2):
    meta = json.loads((FIXTURE_DIR / "meta.json").read_text(encoding="utf-8"))
    assert meta["source"] == "STEEL"
    assert meta["captured_on"] == "2026-10-01"
    assert meta["fetch_mode"] == "HTTP"
    pages = {p["url"]: p for p in meta["pages"]}
    assert set(pages) == {PAGE_URL, PAGE2_URL}
    for text, url in ((html, PAGE_URL), (html2, PAGE2_URL)):
        page = pages[url]
        assert page["final_url"] == url and page["http_status"] == 200
        # git may rewrite line endings on checkout (the second page had four CRLFs inside inline
        # scripts); "sha256_lf" is the captured content with CRLF read as LF
        raw = text.replace("\r\n", "\n").encode("utf-8")
        assert hashlib.sha256(raw).hexdigest() == page["sha256_lf"]

def test_adapter_is_auto_discovered_under_the_registry_name():
    assert adapter_for("STEEL") is steel
    spec = next(s for s in SOURCES if s.name == "STEEL")
    assert urlparse(spec.recruitment_url).hostname == "steel.gov.in"

def test_listing_urls_are_the_vacancy_page_and_its_second_page():
    assert steel.LISTING_URLS == [PAGE_URL, PAGE2_URL]
    for url in steel.LISTING_URLS:
        UrlPolicy.from_url(PAGE_URL).check(url)

def test_one_row_per_table_row(rows, rows2, table_rows):
    t1, t2 = table_rows
    assert len(t1) == 19     # 2 rules + 8 CPSE + 8 MoS + 1 Young Professional
    assert len(t2) == 6      # second page of the CPSE and MoS tables
    assert [r.title for r in rows] == [t[1] for t in t1]
    assert [r.title for r in rows2] == [t[1] for t in t2]
    # each row points at its first document
    assert [r.url for r in rows] == [urljoin(PAGE_URL, t[3][0]) for t in t1]
    assert [r.url for r in rows2] == [urljoin(PAGE2_URL, t[3][0]) for t in t2]
    assert len({r.url for r in rows + rows2}) == 25

def test_section_headings_of_the_page(table_rows):
    t1, t2 = table_rows
    assert [h for h, *_ in t1].count("Recruitment Rules/Notification") == 2
    assert [h for h, *_ in t1].count("CPSE Vacancies") == 8
    assert [h for h, *_ in t1].count("MoS Vacancies") == 8
    assert [h for h, *_ in t1].count("Young Professional/ Consultant/Trainee etc.") == 1
    assert [h for h, *_ in t2] == ["CPSE Vacancies"] * 3 + ["MoS Vacancies"] * 3

def test_recruitment_rules_rows(rows):
    first, second = rows[0], rows[1]
    assert first.title == "Ministry of Steel (Technical Wing) Amendment Recruitment Rules, 2026"
    assert first.url == FILES + ("2026-06/Ministry%20of%20Steel%20%28Technical%20Wing%29%20Amendment%20Recruitment%20"
                                 "Rules%2C%202026.pdf")
    assert second.title == "Ministry of Steel (Technical Wing) Recruitment Rules, 2026"
    assert second.url == FILES + "2026-04/Recruitment%20Rules-2026_1.pdf"
    for r in (first, second):
        assert r.kind_hint == "RULES"
        assert r.evidence["kind_hint"] == "Recruitment Rules"
        # the rules' "Validity Date" (25-06-2026, 21-04-2026) is not an application closing date
        assert r.last_date is None and "last_date" not in r.evidence
        assert r.post_names == [] and r.advertisement_number is None

def test_cpse_bokaro_row(by_url):
    row = by_url[FILES + "2025-04/BSP%2C%20SAIL.pdf"]
    assert row.title == "Selection for the post of Director in-charge (Bokaro Steel Plant), SAIL Limited -reg."
    assert row.evidence["url"] == "/sites/default/files/2025-04/BSP%2C%20SAIL.pdf"
    assert row.last_date == date(2024, 11, 23)
    assert row.evidence["last_date"] == "23-11-2024"
    assert row.kind_hint == "VACANCY"
    assert row.evidence["kind_hint"] == "CPSE Vacancies"
    assert row.post_names == ["Director in-charge (Bokaro Steel Plant)"]
    assert row.evidence["post_names"] == "post of Director in-charge (Bokaro Steel Plant)"
    assert row.advertisement_number is None and row.published_on is None

def test_cpse_rows_on_the_second_page(by_url):
    row = by_url[FILES + "2025-04/upload%20TPRM.pdf"]
    assert row.title == ("Selection for the post of Director (Technical, Projects & Raw Materials), Steel Authority of "
                         "India Limited (SAIL) a Schedule 'A' CPSE")
    assert row.post_names == ["Director (Technical, Projects & Raw Materials)"]
    assert row.last_date == date(2024, 2, 15)
    nmdc = by_url[FILES + "2025-04/NMDC-CMD-Advt-JD-2-6.pdf"]
    assert nmdc.title == "Selection for the post of Chairman and Managing Director, NMDC Limited"
    assert nmdc.post_names == ["Chairman and Managing Director"]
    assert nmdc.last_date == date(2024, 10, 7) and nmdc.evidence["last_date"] == "07-10-2024"

def test_joint_industrial_advisor_vacancy_circular(by_url):
    row = by_url[FILES + "2026-06/Filling%20up%20of%20the%20post%20of%20Jt.IA%20in%20Min.%20of%20Steel%20%281%29_0.pdf"]
    assert row.title == ("Vacancy circular for filling up of one post of Joint Industrial Advisor (General Central Service "
                         "Group ‘A’ Gazetted Non Ministerial) in Pay Level – 12. [The tentative date for "
                         "publishing the Advertisement in Employment News/Rozgaar Samachar is 4.7.26]")
    assert row.post_names == ["Joint Industrial Advisor"]
    assert row.evidence["post_names"] == ("post of Joint Industrial Advisor (General Central Service Group ‘A’ "
                                          "Gazetted Non Ministerial)")
    assert row.kind_hint == "VACANCY" and row.evidence["kind_hint"] == "MoS Vacancies"
    assert row.last_date == date(2026, 9, 2) and row.evidence["last_date"] == "02-09-2026"
    # "tentative date for publishing ... is 4.7.26" is a plan, not the date the notice was published
    assert row.published_on is None and "published_on" not in row.evidence

def test_deputy_industrial_advisor_vacancy_circular(by_url):
    row = by_url[FILES + "2026-07/Filling%20up%20of%20Two%20Posts%20of%20Dy.IA%20in%20Min.%20of%20Steel.pdf"]
    assert row.title.startswith("Vacancy circular for filling up of two posts of Deputy Industrial Advisor")
    assert row.post_names == ["Deputy Industrial Advisor"]
    assert row.last_date == date(2026, 9, 2)

def test_nisst_rows_carry_their_advertisement_numbers(by_url):
    row = by_url[FILES + "2025-07/NISST%20recruitment.pdf"]
    assert row.title == "Recruitment in NISST for 5 posts on contractual basis: No.S-19022/10/2025-SD.I"
    assert row.advertisement_number == "S-19022/10/2025-SD.I"
    assert row.evidence["advertisement_number"] == "No.S-19022/10/2025-SD.I"
    assert row.last_date == date(2025, 7, 15) and row.evidence["last_date"] == "15-07-2025"
    assert row.kind_hint == "VACANCY"
    assert row.post_names == []          # the title names no post

    revised = by_url[FILES + "2025-07/Revised%20Advertisement%202025%20%281%29.pdf"]
    assert revised.title == "Revised Notification, Ref: VACCANCY NOTIFICATION NO. NISST/P&A/PERS/CON/2025-1"
    assert revised.advertisement_number == "NISST/P&A/PERS/CON/2025-1"
    assert revised.evidence["advertisement_number"] == "VACCANCY NOTIFICATION NO. NISST/P&A/PERS/CON/2025-1"
    assert revised.kind_hint == "CORRIGENDUM" and revised.evidence["kind_hint"] == "Revised Notification"
    assert revised.last_date == date(2025, 7, 6)

def test_only_the_nisst_rows_state_advertisement_numbers(all_rows):
    assert [r.advertisement_number for r in all_rows if r.advertisement_number] == [
        "S-19022/10/2025-SD.I", "NISST/P&A/PERS/CON/2025-1"]

def test_extension_of_last_date_is_a_corrigendum(by_url):
    row = by_url[FILES + "2025-04/ApplicationforthepostofDirectorNISST-Extensionoflastdate.pdf"]
    assert row.title == "Application for the post of Director, NISST - Extension of last date for applying"
    assert row.kind_hint == "CORRIGENDUM" and row.evidence["kind_hint"] == "Extension of last date"
    assert row.post_names == ["Director"]
    assert row.last_date == date(2023, 12, 17) and row.evidence["last_date"] == "17-12-2023"

def test_engagement_rows(by_url):
    srtmi = by_url[FILES + "2025-06/Advertisment%20_for_young%20Professional%20%20Consultant%20%20for%20SRTMI.pdf"]
    assert srtmi.title == "Engagement of Young Professionals/ Consultant in SRTMI"
    assert srtmi.post_names == ["Young Professionals", "Consultant"]
    assert srtmi.evidence["post_names"] == "Engagement of Young Professionals/ Consultant"
    # the advertisement itself says "Last date for sending completed application is 10.06.2025"
    assert srtmi.last_date == date(2025, 6, 10)

    trainers = by_url[FILES + "2026-02/BPNSI%20Notification.pdf"]
    assert trainers.title == "Notification for engagement of trainers by BPNSI on contract basis"
    assert trainers.post_names == ["trainers"]

    yp = by_url[FILES + "2025-04/Advertisement_YPs_CG1_Final.pdf"]
    assert yp.title == "Engagement of Young Professionals and Consultants Grade-1 in Ministry of Steel"
    # its block heading ("Young Professional/ Consultant/Trainee etc.") does not say vacancy; the title does
    assert yp.kind_hint == "VACANCY" and yp.evidence["kind_hint"] == "Engagement of"
    assert yp.post_names == ["Young Professionals and Consultants Grade-1"]
    assert yp.last_date == date(2024, 10, 14)

    bpnsi = by_url[FILES + "2025-04/BPNSI_YPP2023Notification.pdf"]
    assert bpnsi.title == "Advertisement for recruitment of Young Professionals in BPNSI"
    assert bpnsi.post_names == ["Young Professionals"]
    assert bpnsi.evidence["post_names"] == "recruitment of Young Professionals"

def test_kind_counts(all_rows):
    kinds = [r.kind_hint for r in all_rows]
    assert kinds.count("RULES") == 2
    assert kinds.count("CORRIGENDUM") == 2
    assert kinds.count("VACANCY") == 21
    assert kinds.count(None) == 0

def test_multi_document_row_uses_the_first_document(by_url, table_rows):
    t1, _ = table_rows
    nisst = next(t for t in t1 if t[1].startswith("Recruitment in NISST"))
    assert nisst[3] == ["/sites/default/files/2025-07/NISST%20recruitment.pdf",
                        "/sites/default/files/2025-07/NISST%20Addendum%20Notification.pdf",
                        "/sites/default/files/2025-07/ADDENDUM.pdf"]
    assert FILES + "2025-07/NISST%20recruitment.pdf" in by_url

def test_all_urls_are_https_on_the_official_domain(all_rows):
    policy = UrlPolicy.from_url(PAGE_URL)
    for r in all_rows:
        parts = urlparse(r.url)
        assert parts.scheme == "https", r.url
        assert parts.hostname == "steel.gov.in", r.url
        assert r.url.startswith(FILES) and r.url.endswith(".pdf"), r.url
        assert policy.allows(r.url)

def test_navigation_menus_and_pager_are_skipped(all_rows, html):
    titles = {r.title for r in all_rows}
    urls = {r.url for r in all_rows}
    assert 'id="superfish-main"' in html          # the site menu is on the page, the adapter leaves it out
    assert 'href="?page=1"' in html               # so is the pager
    assert not any("?page=" in u for u in urls)
    assert not any("/node/" in u for u in urls)
    for nav in ("Home", "Contact Us", "Next page", "Page 2", "Recruitment Rules/Notification"):
        assert nav not in titles

def test_evidence_for_every_filled_field_is_page_text(all_rows, html, html2):
    page_text = _norm(re.sub(r"<[^>]+>", " ", re.sub(r"<!--.*?-->", "", html + html2, flags=re.S)))
    for r in all_rows:
        assert r.evidence["title"] == r.title and r.title in page_text
        assert f'href="{r.evidence["url"]}"' in html + html2
        assert urljoin(PAGE_URL, r.evidence["url"]) == r.url
        for name in ("advertisement_number", "kind_hint", "published_on", "last_date"):
            if getattr(r, name) is not None:
                assert r.evidence.get(name), (r.url, name)
                assert r.evidence[name] in page_text
        if r.advertisement_number:
            assert r.evidence["advertisement_number"] in r.title
            assert r.advertisement_number in r.evidence["advertisement_number"]
        if r.post_names:
            assert r.evidence["post_names"] in r.title
            for post in r.post_names:
                assert post in r.evidence["post_names"]
        assert set(r.evidence) <= {"title", "url", "advertisement_number", "published_on", "last_date",
                                   "post_names", "kind_hint"}

def test_no_invented_dates(all_rows, table_rows):
    t1, t2 = table_rows
    validity = {urljoin(PAGE_URL, t[3][0]): (t[0], t[4]) for t in t1 + t2}
    for r in all_rows:
        # the page shows no publication date for any notice
        assert r.published_on is None and "published_on" not in r.evidence
        heading, shown = validity[r.url]
        assert re.fullmatch(r"\d{2}-\d{2}-\d{4}", shown)
        if heading == "Recruitment Rules/Notification":
            assert r.last_date is None
            continue
        # the last date is exactly this row's own Validity Date cell, read day-month-year
        assert r.evidence["last_date"] == shown
        dd, mm, yyyy = map(int, shown.split("-"))
        assert r.last_date == date(yyyy, mm, dd)

def test_parsing_is_deterministic(html):
    assert steel.parse_listing(html, PAGE_URL) == steel.parse_listing(html, PAGE_URL)

def test_page_without_the_vacancy_view_gives_no_rows(html):
    without = html.replace('id="block-steel-views-block-recruitment-vacancy', 'id="block-other')
    assert steel.parse_listing(without, PAGE_URL) == []
