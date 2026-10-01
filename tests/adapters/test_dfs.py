"""DFS listing adapter against the live Vacancies pages captured on 2026-10-01 (see fixtures/adapters/dfs/meta.json)."""
import json
import re
from datetime import date
from html import unescape
from pathlib import Path
from urllib.parse import urljoin, urlparse

import pytest

from govcareeros.backend.sources.adapters import adapter_for, dfs
from govcareeros.backend.sources.url_guard import UrlPolicy

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "adapters" / "dfs"
PAGE_URL = "https://financialservices.gov.in/vacancies"
PAGE2_URL = "https://financialservices.gov.in/vacancies?page=1"
DOC = "https://financialservices.gov.in/sites/default/files/vacancy/"

def _read(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")

@pytest.fixture(scope="module")
def html():
    return _read("vacancies.html")

@pytest.fixture(scope="module")
def html2():
    return _read("vacancies_page2.html")

@pytest.fixture(scope="module")
def rows(html):
    return dfs.parse_listing(html, PAGE_URL)

@pytest.fixture(scope="module")
def rows2(html2):
    return dfs.parse_listing(html2, PAGE2_URL)

@pytest.fixture(scope="module")
def all_rows(rows, rows2):
    return rows + rows2

@pytest.fixture(scope="module")
def by_url(all_rows):
    return {r.url: r for r in all_rows}

def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()

def _entries(page: str):
    """The view's grid items, read independently of the adapter: (title, published, due, document href)."""
    out = []
    for block in page.split('<div class="views-view-responsive-grid__item-inner">')[1:]:
        def field(name):
            m = re.search(r'views-field-field-%s">(.*?)</div></div>' % name, block, re.S)
            value = re.sub(r'<span class="views-label[^"]*">.*?</span>', "", m.group(1), flags=re.S)
            return _text(value)
        href = re.search(r'views-field-field-v-document"><div class="field-content"><a href="([^"]+)"', block).group(1)
        title = _text(re.search(r'views-field-field-vacancy-name"><span class="field-content">(.*?)</span>', block, re.S).group(1))
        out.append((title, field("v-publish-date"), field("v-due-date"), href))
    return out

def test_fixture_meta_records_the_live_capture():
    meta = json.loads((FIXTURE_DIR / "meta.json").read_text(encoding="utf-8"))
    assert meta["source"] == "DFS"
    assert meta["captured_on"] == "2026-10-01"
    assert meta["fetch_mode"] == "HTTP"
    assert [(p["url"], p["file"], p["http_status"]) for p in meta["pages"]] == [
        (PAGE_URL, "vacancies.html", 200), (PAGE2_URL, "vacancies_page2.html", 200)]
    for p in meta["pages"]:
        assert (FIXTURE_DIR / p["file"]).is_file()

def test_adapter_is_auto_discovered_and_lists_the_current_pages():
    assert adapter_for("DFS") is dfs
    assert dfs.LISTING_URLS == [PAGE_URL, PAGE2_URL]
    policy = UrlPolicy.from_url(PAGE_URL)
    assert all(policy.allows(u) for u in dfs.LISTING_URLS)

def test_one_row_per_entry_in_page_order(html, html2, rows, rows2):
    page1, page2 = _entries(html), _entries(html2)
    assert len(page1) == 30 and len(page2) == 4
    assert [(r.title, r.url) for r in rows] == [(t, urljoin(PAGE_URL, h)) for t, _, _, h in page1]
    # page 2 repeats one entry (same title and document): reported once
    assert page2[1] == page2[2]
    assert [(r.title, r.url) for r in rows2] == [(t, urljoin(PAGE2_URL, h)) for t, _, _, h in (page2[0], page2[1], page2[3])]
    assert len({r.url for r in rows + rows2}) == 33

def test_all_urls_are_absolute_https_documents_on_the_official_domain(all_rows):
    for r in all_rows:
        parsed = urlparse(r.url)
        assert parsed.scheme == "https" and parsed.hostname == "financialservices.gov.in", r.url
        assert r.url.startswith(DOC) and r.url.endswith(".pdf"), r.url

def test_navigation_menu_search_and_pager_links_are_skipped(html, all_rows):
    urls = {r.url for r in all_rows}
    assert 'href="?page=1"' in html and 'action="/search"' in html
    assert not any("?page=" in u or u.rstrip("/") in (PAGE_URL, "https://financialservices.gov.in") for u in urls)
    menu_hrefs = set(re.findall(r'<a href="(/[^"]*)"', html.split('<div class="view-content">')[0]))
    assert menu_hrefs and not ({urljoin(PAGE_URL, h) for h in menu_hrefs} & urls)
    # the truncated description field never replaces the full title
    assert not any(r.title.endswith("…") for r in all_rows)

def test_newest_entry_registrar_deputation_extension(by_url):
    r = by_url[DOC + "2026-09/Extension-of-date.pdf"]
    assert r.title == ("Filling up of existing, anticipated and unforeseen vacancies upto 31.12.2026 for the post of "
                       "Registrar, Assistant Registrar and Recovery Officer on deputation basis in Debts Recovery "
                       "Appellate Tribunals (DRATs) and Debts Recovery Tribunals (DRTs) under DFS, MoF.")
    assert r.published_on == date(2026, 9, 8)
    assert r.last_date == date(2026, 12, 31)
    assert r.evidence["published_on"] == "PUBLISHED DATE 2026-09-08"
    assert r.evidence["last_date"] == "DUE DATE 2026-12-31"
    assert r.post_names == ["Registrar", "Assistant Registrar", "Recovery Officer"]
    assert r.evidence["post_names"] == "post of Registrar, Assistant Registrar and Recovery Officer"
    assert r.kind_hint == "VACANCY" and r.evidence["kind_hint"] == "vacancies"
    assert r.advertisement_number is None

def test_cvo_entry_with_due_date(by_url):
    r = by_url[DOC + "2026-07/Image456.pdf"]
    assert r.title == ("Appointment to the posts of Chief Vigilance Officers (CVOs) in the Public Sector Banks, "
                       "Public Sector Insurance Companies")
    assert (r.published_on, r.last_date) == (date(2026, 6, 22), date(2026, 8, 3))
    assert r.post_names == ["Chief Vigilance Officers (CVOs)"]
    assert r.kind_hint is None   # the title carries no label

def test_pfrda_whole_time_member(by_url):
    r = by_url[DOC + "2026-06/Advertisement-WTM--Eco--for-Website.pdf"]
    assert r.title == ("Inviting applications for the post of Whole-time Member (Economics) in Pension Fund "
                       "Regulatory and Development Authority (PFRDA), New Delhi")
    assert (r.published_on, r.last_date) == (date(2026, 6, 10), date(2026, 7, 10))
    assert r.post_names == ["Whole-time Member (Economics)"]
    assert r.kind_hint == "VACANCY" and r.evidence["kind_hint"] == "Inviting applications"

def test_drt_staff_list_of_posts(by_url):
    r = by_url[DOC + "2026-06/Image_2943.pdf"]
    assert r.post_names == ["Private Secretary", "Assistant", "Account Assistant", "Stenographer Grade-l",
                            "Upper Division Clerk"]
    assert (r.published_on, r.last_date) == (date(2026, 6, 2), date(2026, 6, 30))

def test_cersai_extension_states_the_closing_date_in_its_title(by_url):
    r = by_url[DOC + "2026-05/OM-extension-till-19-may-26.pdf"]
    assert r.title == ("Applications for the post of Central Registrar and MD&CEO of Central Registry (CERSAI). "
                       "The last date for receipt of applications has now been extended to 19.05.2026.")
    assert r.published_on == date(2026, 4, 30)
    assert r.last_date == date(2026, 5, 19)      # DUE DATE is empty; the title states it (dd.mm.yyyy)
    assert r.evidence["last_date"] == "last date for receipt of applications has now been extended to 19.05.2026"
    assert r.kind_hint == "CORRIGENDUM" and r.evidence["kind_hint"] == "extended"
    assert r.post_names == ["Central Registrar and MD&CEO of Central Registry (CERSAI)"]   # one post, not a list

    r = by_url[DOC + "2026-05/6543.pdf"]
    assert (r.published_on, r.last_date) == (date(2026, 4, 9), date(2026, 5, 4))

def test_ncgtc_extension_without_a_date(by_url):
    r = by_url[DOC + "Advertisement-for-appointment-of-CEO-NCGTC-Extension-in-ast-date-of-applications.pdf"]
    assert r.title == ("Applications invited for appointment to the post of Chief Executive Officer in the NCGTC "
                       "-Extension in last date of application")
    assert r.published_on == date(2025, 11, 11)
    assert r.last_date is None                   # "Extension in last date" states no date
    assert r.kind_hint == "CORRIGENDUM" and r.evidence["kind_hint"] == "Extension in"
    assert r.post_names == ["Chief Executive Officer"]

def test_compendium_is_rules_and_dates_in_titles_are_not_closing_dates(by_url):
    r = by_url[DOC + "2026-05/DFS-COMPENDIUM.pdf"]
    assert r.title == "Compendium of guidelines on reservation for Ex-servicemen"
    assert r.kind_hint == "RULES" and r.post_names == []
    # "vacancies upto 30.06.2026" is the vacancy period, not a last date; the DUE DATE field is empty
    r = by_url[DOC + "2026-05/Image_2164.pdf"]
    assert "upto 30.06.2026" in r.title
    assert r.published_on == date(2026, 3, 6) and r.last_date is None

def test_irdai_appointment_of(by_url):
    r = by_url[DOC + "Application-Member-Actuary.pdf"]
    assert r.title == "Applications are invited for appointment of Whole-time Member (Actuary) in the IRDAI, Hyderabad"
    assert r.post_names == ["Whole-time Member (Actuary)"]
    assert r.evidence["post_names"] == "appointment of Whole-time Member (Actuary)"
    assert r.published_on == date(2025, 11, 3) and r.last_date is None

def test_second_page_rows(by_url):
    r = by_url[DOC + "Circular-Steno.pdf"]
    assert r.title == ("Engagement of retired Central/State Govt./High Court/Dist Court officials for the post of "
                       "Stenographer Grade I (1 No.) on contract basis")
    assert r.published_on == date(2025, 1, 21) and r.last_date is None
    assert r.post_names == ["Stenographer Grade I"]
    assert r.kind_hint == "VACANCY"
    r = by_url[DOC + "Chairperson-IRDAI.pdf"]
    assert r.post_names == ["Chairperson"] and r.published_on == date(2025, 3, 10)

def test_dates_are_exactly_what_the_page_shows(html, html2, by_url):
    for page, base in ((html, PAGE_URL), (html2, PAGE2_URL)):
        for title, published, due, href in _entries(page):
            r = by_url[urljoin(base, href)]
            assert r.published_on == (date.fromisoformat(published) if published else None)
            if due:
                assert r.last_date == date.fromisoformat(due)
            elif r.last_date is not None:
                # only from the title's own "last date ... extended to dd.mm.yyyy"
                assert r.last_date.strftime("%d.%m.%Y") in title
                assert "last date" in r.evidence["last_date"]

def test_no_invented_dates_and_no_advertisement_numbers(all_rows):
    assert sum(r.last_date is not None for r in all_rows) == 9
    assert sum(r.published_on is not None for r in all_rows) == 33
    for r in all_rows:
        assert r.advertisement_number is None   # the page shows none
        for field_name, d in (("published_on", r.published_on), ("last_date", r.last_date)):
            if d is not None:
                ev = r.evidence[field_name]
                assert d.isoformat() in ev or d.strftime("%d.%m.%Y") in ev, (r.url, ev)

def test_evidence_present_for_every_filled_field_and_taken_from_the_page(html, html2, all_rows):
    page_text = _text(html) + " " + _text(html2)
    for r in all_rows:
        assert r.evidence["title"] == r.title and r.title in page_text
        assert urljoin(PAGE_URL, r.evidence["url"]) == r.url and f'href="{r.evidence["url"]}"' in html + html2
        for name, value in (("advertisement_number", r.advertisement_number), ("published_on", r.published_on),
                            ("last_date", r.last_date), ("kind_hint", r.kind_hint), ("post_names", r.post_names)):
            if value:
                assert r.evidence.get(name), (r.url, name)
                assert r.evidence[name] in page_text, (r.url, name, r.evidence[name])
            else:
                assert name not in r.evidence
        for post in r.post_names:
            assert post in r.evidence["post_names"]
        if r.kind_hint:
            assert r.evidence["kind_hint"] in r.title

def test_parse_is_deterministic(html):
    assert dfs.parse_listing(html, PAGE_URL) == dfs.parse_listing(html, PAGE_URL)
