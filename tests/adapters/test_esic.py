"""ESIC listing adapter against live pages captured on 2026-10-01 (see fixtures/adapters/esic/meta.json)."""
import json
import re
from datetime import date
from html import unescape
from pathlib import Path
from urllib.parse import urlparse

import pytest

from govcareeros.backend.sources.adapters import adapter_for, esic
from govcareeros.backend.sources.registry import SOURCES
from govcareeros.backend.sources.url_guard import UrlPolicy

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "adapters" / "esic"
REGISTRY_URL = "https://www.esic.gov.in/recruitments"
FINAL_URL = "https://esic.gov.in/recruitments"
DOC = "https://esic.gov.in/attachments/recruitmentfile/"
POLICY = UrlPolicy.from_url(REGISTRY_URL)

# file -> (URL requested, URL the page was served from = the base_url the fetcher hands the adapter)
PAGES = {
    "recruitments.html": (REGISTRY_URL, FINAL_URL),
    "recruitments_page2.html": ("https://esic.gov.in/recruitments/index/page:2",) * 2,
    "recruitments_page3.html": ("https://esic.gov.in/recruitments/index/page:3",) * 2,
}
FIELDS = ("title", "url", "advertisement_number", "published_on", "last_date", "post_names", "kind_hint")


def _html(name):
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


def _page_text(html):
    """The page's visible text, whitespace-collapsed, read independently of the adapter."""
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html)))


@pytest.fixture(scope="module")
def parsed():
    return {name: esic.parse_listing(_html(name), base) for name, (_, base) in PAGES.items()}


@pytest.fixture(scope="module")
def by_url(parsed):
    return {r.url: r for rows in parsed.values() for r in rows}


def _filled(row, field):
    return getattr(row, field) not in (None, "", [])


def _all(parsed):
    return [(name, r) for name, rows in parsed.items() for r in rows]


# ---------------------------------------------------------------- fixture + discovery

def test_fixture_meta_records_the_live_capture():
    meta = json.loads((FIXTURE_DIR / "meta.json").read_text(encoding="utf-8"))
    assert meta["url"] == REGISTRY_URL
    assert meta["captured_on"] == "2026-10-01"
    assert meta["fetch_mode"] == "HTTP"
    assert {p["file"]: (p["url"], p["final_url"]) for p in meta["pages"]} == PAGES
    assert all(p["http_status"] == 200 for p in meta["pages"])


def test_adapter_is_auto_discovered_under_the_registry_name():
    assert adapter_for("ESIC") is esic
    spec = next(s for s in SOURCES if s.name == "ESIC")
    assert spec.recruitment_url == REGISTRY_URL


def test_extra_listing_urls_are_the_tables_own_pagination_on_the_official_domain():
    page1 = _html("recruitments.html")
    for url in esic.LISTING_URLS:
        assert POLICY.allows(url)
        assert f'href="{urlparse(url).path}"' in page1


# ---------------------------------------------------------------- rows

def test_one_row_per_document_link_in_the_table(parsed):
    assert len(parsed["recruitments.html"]) == 11      # 10 table rows, S.No. 4 has two documents
    assert len(parsed["recruitments_page2.html"]) == 30  # S.No. 13 lists 21 regional documents
    assert len(parsed["recruitments_page3.html"]) == 13  # S.No. 25, 28, 29 have two documents each


def test_first_row_result(parsed):
    row = parsed["recruitments.html"][0]
    assert row.title == (
        "RESULT OF WALK IN INTERVIEW FOR THE RECRUITMENT OF FULL TIME /PART TIME SPECIALIST, PART TIME/FULL TIME "
        "SUPER SPECIALIST, SENIOR RESIDENT -1 YEAR /45 DAYS ON CONTRACT BASIS HELD ON 25.09.2026")
    assert row.url == DOC + (
        "RESULT_OF_WALK_IN_INTERVIEW_FOR_THE_RECRUITMENT_OF_FULL_TIME_PART_TIME_SPECIALIST_PART_TIME_FULL_TIME_"
        "SUPER_SPECIALIST_SENIOR_RESIDENT_1_YEAR_45_DAYS_ON_CONTRACT_BASIS_HELD_ON_25_09_2026_1790832876.pdf")
    assert row.published_on == date(2026, 10, 1)
    assert row.evidence["published_on"] == "2026-10-01"
    assert row.kind_hint == "RESULT" and row.evidence["kind_hint"] == "Result"
    # "Result" in the Last Date column carries no date, and the "25.09.2026" in the title is not one
    assert row.last_date is None
    assert row.advertisement_number is None


def test_walk_in_row_takes_the_date_shown_in_the_last_date_column(by_url):
    row = by_url[DOC + "WALK_IN_INTERVIEW_FOR_POST_OF_SPECIALIST_PGMO_amp_SENIOR_RESIDENT_on_contractual_Basis_1790828886.pdf"]
    assert row.title == "WALK IN INTERVIEW FOR POST OF SPECIALIST. PGMO & SENIOR RESIDENT on contractual Basis"
    assert row.published_on == date(2026, 10, 1)
    assert row.last_date == date(2026, 10, 7)
    assert row.evidence["last_date"] == "Interview (2026-10-07)"
    assert row.kind_hint is None


def test_both_documents_of_a_row_carry_that_rows_dates(parsed):
    lucknow = [r for r in parsed["recruitments.html"] if r.published_on == date(2026, 9, 30) and r.last_date == date(2026, 10, 13)]
    assert [(r.title, r.url) for r in lucknow] == [
        ("Walk in Interview for recruitment of the post of full time/part time Specialists and Senior Residents on "
         "contract basis for ESIC Hospital, Sarojini Nagar, Lucknow",
         DOC + "Walk_in_Interview_for_recruitment_of_the_post_of_full_time_part_time_Specialists_and_Senior_Residents_"
               "on_contract_basis_for_ESIC_Hospital_Sarojini_Nagar_Lucknow_1790758629.pdf"),
        ("Department-wise Interview Schedule & Vacancy Chart",
         DOC + "Department_wise_Interview_Schedule_amp_Vacancy_Chart_1790758629.pdf"),
    ]
    assert all(r.evidence["last_date"] == "Interview (2026-10-13)" for r in lucknow)


def test_cancellation_of_an_advertisement(by_url):
    row = by_url[DOC + (
        "NOTICE_Cancellation_of_Advertisement_No_06_of_2026_for_Recruitment_to_the_Posts_of_Professor_Associate_"
        "Professor_Assistant_Professor_and_Senior_Resident_on_Contract_Basis_at_ESIC_Medical_College_amp_Hospital_"
        "Namkum_Ranchi_1790673613.pdf")]
    assert row.title.startswith("NOTICE : Cancellation of Advertisement No. 06 of 2026 for Recruitment")
    assert row.title.endswith("at ESIC Medical College & Hospital, Namkum, Ranchi")
    assert row.advertisement_number == "06 of 2026"
    assert row.evidence["advertisement_number"] == "Advertisement No. 06 of 2026"
    assert row.kind_hint == "CORRIGENDUM"
    assert row.evidence["kind_hint"] == "Cancellation of Advertisement"
    assert row.published_on == date(2026, 9, 29)
    assert row.last_date == date(2026, 10, 1)
    assert row.evidence["last_date"] == "Last Date (2026-10-01)"


@pytest.mark.parametrize("file, title, advt, evidence", [
    ("Result_of_Walk_in_Interview_for_the_contractual_recruitment_Teaching_Faculty_SR_Advt_No_01_of_2026_1790327780.pdf",
     "Result of Walk-in-Interview for the contractual recruitment - Teaching Faculty SR - Advt. No. 01 of 2026",
     "01 of 2026", "Advt. No. 01 of 2026"),
    ("Signed_result_for_Advertisement_No_07_2026_1790316497.pdf",
     "Signed result for Advertisement No. - 07-2026", "07-2026", "Advertisement No. - 07-2026"),
    ("Declaration_of_Result_for_Advt_No_11_of_2026_1790245844.pdf",
     "Declaration of Result for Advt. No. 11 of 2026", "11 of 2026", "Advt. No. 11 of 2026"),
])
def test_advertisement_numbers_stated_in_the_link_text(parsed, file, title, advt, evidence):
    row = next(r for r in parsed["recruitments_page2.html"] if r.url == DOC + file)
    assert row.title == title
    assert row.advertisement_number == advt
    assert row.evidence["advertisement_number"] == evidence
    assert row.kind_hint == "RESULT"
    assert row.last_date is None


def test_regional_results_row_gives_one_row_per_region(parsed):
    rows = [r for r in parsed["recruitments_page2.html"] if "Specialist Gr. II ( Sr./Jr. Scale)" in r.title]
    assert len(rows) == 21
    assert rows[0].title == "Result for Specialist Gr. II ( Sr./Jr. Scale) in ESIC Medical Institutes for ANDHRA PRADESH Region."
    assert rows[0].url == DOC + "Result_for_Specialist_Gr_II_Sr_Jr_Scale_in_ESIC_Medical_Institutes_for_ANDHRA_PRADESH_Region_1790320192.pdf"
    assert rows[-1].url == DOC + "Result_for_Specialist_Gr_II_Sr_Jr_Scale_in_ESIC_Medical_Institutes_for_UTTAR_PRADESH_Region_1790323627.pdf"
    assert {(r.published_on, r.kind_hint) for r in rows} == {(date(2026, 9, 25), "RESULT")}
    # "KARNATAKA Region" is written with a leading space inside the link; titles are trimmed
    assert "Result for Specialist Gr. II ( Sr./Jr. Scale) in ESIC Medical Institutes for KARNATAKA Region" in {r.title for r in rows}


def test_rows_labelled_others_get_no_kind_hint(by_url):
    row = by_url[DOC + "NOTICE_OF_RESULT_FOR_THE_POST_OF_FACULTY_AT_ESIC_MEDICAL_COLLEGE_amp_HOSPITAL_BELTOLA_GUWAHATI_22_1790243197.pdf"]
    assert row.title == "NOTICE OF RESULT FOR THE POST OF FACULTY AT ESIC MEDICAL COLLEGE & HOSPITAL, BELTOLA, GUWAHATI-22"
    assert row.published_on == date(2026, 9, 24)
    assert row.kind_hint is None and row.last_date is None


def test_page3_advertisements_and_corrigendum(parsed):
    rows = {r.title: r for r in parsed["recruitments_page3.html"]}
    hpv = rows["Advertisement for recruitment under HPV vaccine project"]
    assert hpv.url == DOC + "Advertisement_for_recruitment_under_HPV_vaccine_project_1790153738.pdf"
    assert (hpv.published_on, hpv.last_date) == (date(2026, 9, 23), date(2026, 10, 8))
    assert hpv.evidence["last_date"] == "Last Date (2026-10-08)"
    assert hpv.advertisement_number is None   # "Advertisement for ..." states no number

    alwar = rows["Advertisement for the post of Adjunct Faculty, SR and Teaching Faculty- ESIC Alwar"]
    assert alwar.last_date == date(2026, 10, 7)

    corr = rows["Corrigendum related to result of interview"]
    assert corr.url == DOC + "Corrigendum_related_to_result_of_interview_1789968501.pdf"
    assert corr.kind_hint == "CORRIGENDUM" and corr.evidence["kind_hint"] == "Corrigendum"
    assert corr.published_on == date(2026, 9, 21)

    # Same title on two rows, two different documents: both kept
    uo = [r for r in parsed["recruitments_page3.html"] if r.title == "UO TO ICT BRANCH"]
    assert [r.url for r in uo] == [DOC + "UO_TO_ICT_BRANCH_1789966924.pdf", DOC + "UO_TO_ICT_BRANCH_1789726580.pdf"]
    assert [r.published_on for r in uo] == [date(2026, 9, 21), date(2026, 9, 18)]


# ---------------------------------------------------------------- invariants over every row

def test_all_urls_are_documents_on_the_official_domain(parsed):
    for _, row in _all(parsed):
        assert POLICY.allows(row.url), row.url
        assert urlparse(row.url).hostname == "esic.gov.in"
        assert row.url.startswith(DOC) and row.url.endswith(".pdf"), row.url


def test_navigation_menu_and_pagination_links_are_skipped(parsed):
    urls = {row.url for _, row in _all(parsed)}
    assert not any("/recruitments" in u or u.rstrip("/").endswith("esic.gov.in") for u in urls)
    titles = {row.title for _, row in _all(parsed)}
    assert not titles & {"Home", "Next", "Previous", "2", "Recruitments", "Tenders"}


def test_titles_drop_the_file_type_and_size_suffix(parsed):
    for _, row in _all(parsed):
        assert row.title and row.title == row.title.strip()
        assert "size:(" not in row.title and not row.title.endswith("- PDF")


def test_every_filled_field_has_evidence_from_the_page(parsed):
    for name, row in _all(parsed):
        text = _page_text(_html(name))
        for field in FIELDS:
            if _filled(row, field):
                assert field in row.evidence, (row.title, field)
                assert row.evidence[field] in text or row.evidence[field] in _html(name), (row.title, field)
        assert set(row.evidence) <= set(FIELDS)


def test_no_row_has_an_invented_date(parsed):
    for name, row in _all(parsed):
        text = _page_text(_html(name))
        for field in ("published_on", "last_date"):
            value = getattr(row, field)
            if value is None:
                assert field not in row.evidence
                continue
            # The date is written in the evidence exactly as the page shows it (yyyy-mm-dd)
            assert value.isoformat() in row.evidence[field], (row.title, field)
            assert row.evidence[field] in text
        if row.last_date is None:
            assert row.kind_hint in (None, "RESULT", "CORRIGENDUM")
    # Rows whose Last Date column shows no date ("Result", "Others") never get one
    first = parsed["recruitments.html"]
    assert [r.last_date for r in first] == [
        None, date(2026, 10, 7), None, date(2026, 10, 13), date(2026, 10, 13), date(2026, 10, 7),
        date(2026, 10, 1), None, None, None, None]
    assert [r.published_on for r in first] == [
        date(2026, 10, 1), date(2026, 10, 1), date(2026, 9, 30), date(2026, 9, 30), date(2026, 9, 30),
        date(2026, 9, 30), date(2026, 9, 29), date(2026, 9, 28), date(2026, 9, 28), date(2026, 9, 28), date(2026, 9, 28)]


def test_post_names_are_not_invented(parsed):
    # The table has no post-name column; the adapter leaves post_names empty rather than guessing from titles
    assert all(row.post_names == [] for _, row in _all(parsed))


def test_parsing_is_deterministic(parsed):
    for name, (_, base) in PAGES.items():
        assert esic.parse_listing(_html(name), base) == parsed[name]


def test_page_without_the_recruitment_table_yields_nothing():
    # The captured page's markup outside its recruitment table (menus, search form, pagination, footer)
    html = _html("recruitments.html")
    start, end = html.index('<table border="0" cellpadding="0" cellspacing="2" class="_tbl-grid"'), html.index("</table>")
    assert esic.parse_listing(html[:start] + html[end + len("</table>"):], FINAL_URL) == []
