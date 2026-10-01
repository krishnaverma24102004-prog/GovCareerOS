"""
Listing adapters for sources re-verified live on 2026-10-01, on the pages captured then
(tests/fixtures/sources/<source>/, meta.json has each file's URL, capture date and fetch mode).
"""
from datetime import date
from pathlib import Path

from govcareeros.backend.sources.adapters import adapter_for
from govcareeros.backend.sources.registry import SOURCES

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources"
URLS = {s.name: s.recruitment_url for s in SOURCES}

def _rows(source: str, path: str):
    html = (FIXTURES / path).read_text(encoding="utf-8")
    return adapter_for(source).parse_listing(html, URLS[source])

def test_agriwelfare_reads_the_rendered_recruitment_table():
    rows = _rows("AGRIWELFARE", "agriwelfare/recruitmentDetail_rendered.html")
    # 10 table rows, one PDF listed twice (draft-RR comments, 01-09 and 19-08)
    assert len(rows) == 9 and len({r.url for r in rows}) == 9
    assert all(r.url.startswith("https://agriwelfare.gov.in/Documents/Recruitment/") for r in rows)
    scheme_officer = next(r for r in rows if r.url.endswith("/VC_SO_18092026.pdf"))
    assert scheme_officer.title.startswith("Filling up of one post of Scheme Officer (General Central Service")
    assert (scheme_officer.published_on, scheme_officer.last_date) == (date(2026, 9, 13), date(2026, 11, 11))
    assert scheme_officer.evidence["last_date"] == "11-11-2026"
    steno = rows[0]
    assert steno.title.startswith("Recruitment of Two Posts of Stenographer Gr-I (Sub-Office Cadre)")
    assert steno.published_on == date(2026, 9, 16) and steno.last_date is None  # its date cells are empty
    assert steno.evidence["url"] == "/Documents/Recruitment/Stenographer_16092026.pdf"
    draft_rr = next(r for r in rows if r.url.endswith("/RR_JD_extension_19082026.pdf"))
    assert draft_rr.title.startswith("Invitation of comments on draft RRs") and draft_rr.published_on == date(2026, 9, 1)

def test_agriwelfare_http_page_has_no_rows():
    """The served HTML has the table shell only: why the registry forces the browser for this source."""
    assert _rows("AGRIWELFARE", "agriwelfare/recruitmentDetail_http.html") == []

def test_employment_news_reads_the_all_jobs_table():
    rows = _rows("EMPLOYMENT-NEWS", "employment-news/alljobs.html")
    assert len(rows) == 12
    first = rows[0]
    assert first.title == "MINERAL EXPLORATION AND CONSULTANCY LIMITED - ACCOUNTANT & OTHERS"
    assert first.post_names == ["ACCOUNTANT & OTHERS"]
    assert (first.published_on, first.last_date) == (date(2026, 9, 12), date(2026, 10, 11))
    assert first.evidence == {"title": "MINERAL EXPLORATION AND CONSULTANCY LIMITED", "post_names": "ACCOUNTANT & OTHERS",
                              "published_on": "12/09/2026", "last_date": "11/10/2026"}
    ndma = next(r for r in rows if r.evidence["title"] == "NATIONAL DISASTER MANAGEMENT AUTHORITY")
    assert ndma.post_names == ["YOUNG CONSULTANT (FOREST FIRE RISK MANAGEMENT)"]
    # Issued date read day first despite the "(MM/DD/YYYY)" header: "31/08/2026" is the 31st of August
    assert (ndma.published_on, ndma.last_date) == (date(2026, 8, 31), date(2026, 10, 18))
    # No per-row documents on the page: every row points at the listing itself
    assert {r.url for r in rows} == {"https://employmentnews.gov.in/NewEmp/AllJobs.aspx?k=All"}

def test_dopt_reads_every_circular_with_its_subject():
    rows = _rows("DOPT", "dopt/vacancy_circulars.html")
    assert len(rows) == 90  # "No. of Records found : 90" on the page
    assert all(r.url.startswith("https://documents.doptcirculars.nic.in/") for r in rows)
    despatch = rows[1]
    assert despatch.title == "Filling up of 01 post of Despatch Rider (DR) on deputation basis in DoPT, New Delhi-reg."
    assert despatch.advertisement_number == "A-12026/1/2023-Ad-III"
    assert despatch.published_on == date(2026, 8, 27) and despatch.last_date is None
    assert despatch.url == "https://documents.doptcirculars.nic.in/D2/D02adm/Vacancy%20CircularwwTEQ.pdf"
    oldest = rows[-1]
    assert oldest.title == "Eight Vacancies of Administrative Members in the Central Administrative Tribunal."
    assert oldest.published_on == date(2008, 1, 8) and oldest.evidence["advertisement_number"] == "DO No. A.11013/15/2007-AT"

def test_consumer_affairs_takes_titles_from_the_title_column():
    rows = _rows("CONSUMER-AFFAIRS", "consumer-affairs/vacancy.html")
    assert [r.kind_hint for r in rows] == [None, None, "CORRIGENDUM"]
    ccpa, nth, corrigendum = rows
    assert ccpa.title == ("Engagement of Experts and Professionals [Level-II] by the Central Consumer Protection Authority "
                          "for assisting the Central Authority for works related to Regulatory Compliances")
    assert ccpa.url == "https://consumeraffairs.gov.in/public/upload/admin/cmsfiles/circulars/Vacancy_CCPA_2026-09-28_12-09-28.pdf"
    assert ccpa.published_on == date(2026, 9, 26) and ccpa.last_date is None  # "Archive Date" is not a closing date
    assert nth.title.startswith("Filling up of 02 Two posts of Scientist-B Chemical in National Test House")
    assert nth.published_on == date(2026, 9, 2)
    assert corrigendum.url.endswith("/cmsfiles/corrigendum/Corrigendum_corrigendum.pdf")
    assert corrigendum.title == nth.title + " - 1. Corrigendum" and corrigendum.published_on is None
