"""
Listing adapters for sources re-verified live on 2026-10-01, on the pages captured then
(tests/fixtures/sources/<source>/, meta.json has each file's URL, capture date and fetch mode).
NHAI and NVS are read from the JSON endpoints their single-page apps call (LISTING_REQUEST); those
captures are the endpoint responses.
"""
from datetime import date
from pathlib import Path

import httpx
import pytest

from govcareeros.backend.sources.adapters import adapter_for
from govcareeros.backend.sources.adapters.base import ListingRequest
from govcareeros.backend.sources.generic_notices import PageFetcher, extract_notices
from govcareeros.backend.sources.registry import SOURCES
from govcareeros.backend.sources.url_guard import UrlPolicy

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sources"
URLS = {s.name: s.recruitment_url for s in SOURCES}
SPECS = {s.name: s for s in SOURCES}

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

def _policy(source: str) -> UrlPolicy:
    spec = SPECS[source]
    return UrlPolicy.for_source(spec.official_domain, spec.recruitment_url, spec.category)

def _json_rows(source: str, path: str):
    adapter = adapter_for(source)
    return adapter.parse_listing((FIXTURES / path).read_text(encoding="utf-8"), adapter.LISTING_REQUEST.url)

def test_json_listing_endpoints_are_on_the_official_domain():
    for source in ("NHAI", "NVS"):
        assert _policy(source).allows(adapter_for(source).LISTING_REQUEST.url)

def test_nhai_reads_every_vacancy_and_its_documents_from_the_api():
    rows = _json_rows("NHAI", "nhai/vacancies-current-en.json")
    assert all(_policy("NHAI").allows(r.url) for r in rows)
    assert all(r.url.startswith("https://nhai.gov.in/nhai/sites/default/files/vacancy_files/") for r in rows)
    assert len({r.url for r in rows}) == len(rows) == 307
    adverts = [r for r in rows if r.published_on]
    assert len(adverts) == 116  # "total_count": 116, one advertisement row per vacancy
    gm = rows[0]
    assert gm.title == ("National Highways Authority of India (NHAI) invites applications for recruitment to the post of "
                        "General Manager (Land Acquisition & Estate Management) on Deputation basis.")
    assert gm.url == "https://nhai.gov.in/nhai/sites/default/files/vacancy_files/Detailed-Adv.pdf"
    assert gm.published_on == date(2026, 9, 22) and gm.last_date is None  # archive_date_val is not a closing date
    assert gm.post_names == ["General Manager (Land Acquisition & Estate Management)"] and gm.kind_hint is None
    # Its second file is the pay-equivalency policy circular: a supporting document, not a notice
    assert not any("Pay-Equivalency-Circular-Policy-17-06-2022-22-11-2019_8" in r.url for r in rows)
    # The apply link is on vacancyportal.nhai.org, outside the official domain
    assert not any("vacancyportal" in r.url for r in rows)

def test_nhai_picks_the_advertisement_over_a_leading_policy_file_and_labels_follow_ups():
    rows = _json_rows("NHAI", "nhai/vacancies-current-en.json")
    # The API's title has a double space after "post of"; titles are whitespace-collapsed
    title = "An advertisement inviting online applications for recruitment to the post of Manager (Administration) on deputation mode"
    manager = [r for r in rows if r.evidence["title"] == title]
    # Listed files: "Policy guideline", "Detailed Adv.", then three addenda
    assert [r.url.rsplit("/", 1)[1] for r in manager] == [
        "Detailed_Adv_of_GM_Legal_DGM_Legal_Mgr_A_AM_A_2024.pdf", "Addendum_3.pdf",
        "Addendum_dt_12_07_2024_for_age_of_retirement_for_deputation_1.pdf", "Addendum_7.pdf"]
    advert, extension = manager[0], manager[1]
    assert advert.published_on == date(2024, 6, 8) and advert.post_names == ["Manager (Administration)"]
    assert advert.evidence["published_on"] == "2024-06-08"
    assert extension.title == title + " - Addendum Last date extented for Manager (Administration)"
    assert extension.kind_hint == "CORRIGENDUM" and extension.published_on is None
    assert extension.evidence["kind_hint"] == "Addendum Last date extented for Manager (Administration)"
    kinds = {r.url.rsplit("/", 1)[1]: r.kind_hint for r in rows}
    assert kinds["List-of-Shortlisted-Candidates-for-Skill-Test-Stenographer.pdf"] == "RESULT"
    assert kinds["City-Intimation-Slip-for-the-post-of-Stenographer-CBT-on-15-06-2026.pdf"] == "ADMIT_CARD"
    assert kinds["Objection-Management-27-04-2026.pdf"] == "ANSWER_KEY"
    assert kinds["Public_Notice_dated_11-02_2025_reg_postponement_of_CBT.pdf"] == "CORRIGENDUM"
    assert kinds["Public_Notice_dated_27_03_2026.pdf"] is None  # its description is only "Public Notice dated 27.03.2026"

def test_nhai_rendered_page_has_no_links_to_read():
    """The SPA's own page (rendered, Hindi by default) lists the rows without links: why the adapter reads the API."""
    html = (FIXTURES / "nhai/vacancies-current-rendered-hi.html").read_text(encoding="utf-8")
    assert extract_notices(html, URLS["NHAI"]) == []

def test_nvs_reads_the_recruitment_media_feed():
    rows = _json_rows("NVS", "nvs/api_jobs.json")
    assert len(rows) == 21  # "totalItems": 21
    assert all(_policy("NVS").allows(r.url) for r in rows)
    first = rows[0]
    assert first.title == ("Notice dated 14.09.2026 – View the result of the Tier-II examination for the post of "
                           "Junior Secretariat Assistants under Recruitment Notification 01/2025.")
    assert first.url == "https://navodaya.gov.in/api/public/media/305/download?organization_id=100"
    assert first.evidence["url"] == "/api/public/media/305/download?organization_id=100"
    assert first.published_on == date(2026, 9, 14) and first.last_date is None
    counsellors = next(r for r in rows if r.url.endswith("/media/292/download?organization_id=100"))
    assert counsellors.title.startswith("Notice for Engagement of Counsellors (Male & Female) on contract basis")
    assert counsellors.published_on == date(2026, 8, 26)
    assert rows[-1].title == "Corrigendum II Regarding the Recruitment Notification No. 01/2025."

def test_nta_pairs_board_titles_with_their_read_more_pdfs():
    rows = _rows("NTA", "nta/nta_home.html")
    assert len(rows) == 60 and len({r.url for r in rows}) == 60
    assert all(r.url.startswith("https://nta.ac.in/Download/Notice/") and _policy("NTA").allows(r.url) for r in rows)
    deputation, typist = rows[0], rows[1]
    assert deputation.title == ("Extension of last date for inviting application for various posts in National Testing "
                                "agency (NTA) on deputation basis-Reg")
    assert deputation.url == "https://nta.ac.in/Download/Notice/Notice_20260909172041.pdf"
    assert typist.title == "Vacancy notification for Bilingual typist and operation assistant"
    assert typist.evidence == {"title": typist.title, "url": "/Download/Notice/Notice_20260902173831.pdf"}
    # Admission-exam notices on the same board are left out
    assert not any(r.url.endswith("/Notice_20260925215817.pdf") for r in rows)  # CSIR-UGC NET certificates
    assert rows[-1].title == "Inviting Online Applications for IGNOU Recruitment of Junior Assistant-cum-Typist (JAT) 2023"

def test_nta_board_gives_the_generic_extractor_nothing():
    """Every link reads "Read More" and every PDF is named Notice_<timestamp>.pdf."""
    html = (FIXTURES / "nta/nta_home.html").read_text(encoding="utf-8")
    assert extract_notices(html, URLS["NTA"]) == []

def _endpoint(source: str, path: str, seen):
    """Serves the captured endpoint response; robots.txt (and anything else) answers 404."""
    body = (FIXTURES / path).read_bytes()
    url = adapter_for(source).LISTING_REQUEST.url

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if str(request.url) == url:
            return httpx.Response(200, content=body, headers={"content-type": "application/json"})
        return httpx.Response(404, text="Not Found")

    return PageFetcher(allow_browser=False, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

@pytest.mark.asyncio
async def test_fetch_listing_posts_the_declared_form_under_the_source_policy():
    seen = []
    request = adapter_for("NHAI").LISTING_REQUEST
    async with _endpoint("NHAI", "nhai/vacancies-current-en.json", seen) as fetcher:
        fetched = await fetcher.fetch_listing(request, _policy("NHAI"))
    assert fetched.error is None and fetched.status == 200
    robots, post = seen
    assert str(robots.url) == "https://nhai.gov.in/robots.txt"
    assert post.method == "POST" and post.headers["content-type"].startswith("multipart/form-data")
    body = post.content.decode()
    for name, value in request.form.items():
        assert f'name="{name}"\r\n\r\n{value}\r\n' in body
    assert len(adapter_for("NHAI").parse_listing(fetched.html, request.url)) == 307

@pytest.mark.asyncio
async def test_fetch_listing_gets_the_nvs_feed():
    seen = []
    request = adapter_for("NVS").LISTING_REQUEST
    async with _endpoint("NVS", "nvs/api_jobs.json", seen) as fetcher:
        fetched = await fetcher.fetch_listing(request, _policy("NVS"))
    assert seen[-1].method == "GET" and str(seen[-1].url) == request.url
    assert len(adapter_for("NVS").parse_listing(fetched.html, request.url)) == 21

@pytest.mark.asyncio
async def test_fetch_listing_refuses_an_endpoint_outside_the_source_policy():
    """NHAI's own apply link (apply_online_link_val in the capture) is on vacancyportal.nhai.org, not nhai.gov.in."""
    seen = []
    async with _endpoint("NHAI", "nhai/vacancies-current-en.json", seen) as fetcher:
        refused = await fetcher.fetch_listing(
            ListingRequest("https://vacancyportal.nhai.org/vacancy24/usermanager/user/index.aspx", "POST", {"language": "en"}),
            _policy("NHAI"))
        wrong_method = await fetcher.fetch_listing(ListingRequest(adapter_for("NHAI").LISTING_REQUEST.url, "DELETE"),
                                                   _policy("NHAI"))
    assert refused.error.status == "URL_REFUSED" and wrong_method.error.status == "URL_REFUSED"
    assert seen == []
