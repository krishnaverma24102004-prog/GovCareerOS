"""DST listing adapter against the live Recruitment Cell page captured on 2026-10-01 (see fixtures/adapters/dst/meta.json)."""
import hashlib
import json
import re
from html import unescape
from pathlib import Path
from urllib.parse import urljoin, urlparse

import pytest

from govcareeros.backend.sources.adapters import adapter_for, dst
from govcareeros.backend.sources.registry import SOURCES

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "adapters" / "dst"
PAGE_URL = "https://dst.gov.in/administrationfinance/recruitment-cell"
NEWS = "https://dst.gov.in/news/"
FILES = "https://dst.gov.in/sites/default/files/"

def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", unescape(text)).strip()

@pytest.fixture(scope="module")
def html():
    return (FIXTURE_DIR / "recruitment-cell.html").read_bytes().decode("utf-8")

@pytest.fixture(scope="module")
def rows(html):
    return dst.parse_listing(html, PAGE_URL)

@pytest.fixture(scope="module")
def by_url(rows):
    return {r.url: r for r in rows}

@pytest.fixture(scope="module")
def region(html):
    """The page's content region, cut out independently of the adapter's parser."""
    return html[html.index('<div id="content"'):html.index('class="bg-wrapper slider_container_bottom"')]

@pytest.fixture(scope="module")
def region_text(region):
    return _norm(re.sub(r"<[^>]+>", " ", region))

@pytest.fixture(scope="module")
def page_links(region):
    """(href, text) of every non-empty link in the content region, read with a plain regex."""
    out = []
    for href, inner in re.findall(r"<a\b[^>]*?href=\"([^\"]+)\"[^>]*>(.*?)</a>", region, re.S):
        text = _norm(re.sub(r"<[^>]+>", " ", inner))
        if text:
            out.append((href, text))
    return out

def test_fixture_meta_records_the_live_capture(html):
    meta = json.loads((FIXTURE_DIR / "meta.json").read_text(encoding="utf-8"))
    assert meta["url"] == PAGE_URL
    assert meta["final_url"] == PAGE_URL
    assert meta["captured_on"] == "2026-10-01"
    assert meta["fetch_mode"] == "HTTP"
    assert meta["http_status"] == 200
    # git may rewrite line endings on checkout; the content (ignoring CR) is what was captured
    raw = html.replace("\r\n", "\n").encode("utf-8")
    assert hashlib.sha256(raw).hexdigest() == meta["sha256"]

def test_adapter_is_auto_discovered_under_the_registry_name():
    assert adapter_for("DST") is dst
    spec = next(s for s in SOURCES if s.name == "DST")
    assert spec.recruitment_url == PAGE_URL

def test_one_row_per_distinct_link_in_the_content_region(rows, page_links):
    distinct = {urljoin(PAGE_URL, href) for href, _ in page_links}
    assert len(page_links) == 54          # 49 notice paragraphs + 5 PDFs under "Links:"
    assert len(distinct) == 53            # "Senior Adviser.pdf" is listed in both
    assert len(rows) == 53
    assert {r.url for r in rows} == distinct
    assert len({r.url for r in rows}) == len(rows)

def test_first_and_last_rows_in_page_order(rows):
    assert rows[0].title == ("Filling up the 02 posts of Scientist C and 01 post of Scientist D in the "
                             "Department of Science and Technology on Direct recruitment basis")
    assert rows[0].url == NEWS + "filling-02-posts-scientist-c-and-01-post-scientist-d-department-science-and-technology-direct"
    assert rows[-1].title == "Advertisement for filling up post of Library & Information Assistant in DST on deputation"
    assert rows[-1].url == FILES + "Advertisement%20Library%20%26%20Information%20Assistant%20_0.pdf"

def test_scientist_c_and_d_row(by_url):
    row = by_url[NEWS + "filling-02-posts-scientist-c-and-01-post-scientist-d-department-science-and-technology-direct"]
    assert row.kind_hint == "VACANCY"
    assert row.evidence["kind_hint"] == "Filling up"
    assert row.post_names == ["Scientist C", "Scientist D"]
    assert row.evidence["post_names"] == "posts of Scientist C and 01 post of Scientist D"
    assert row.advertisement_number is None
    assert row.published_on is None and row.last_date is None

def test_estate_officer_row_carries_the_advertisement_number(by_url):
    row = by_url[NEWS + "filling-01-post-estate-officer-department-science-technology-deputation-including-short-term"]
    assert row.title == ("Filling up the 01 post of Estate Officer in the Department of Science & Technology on "
                         "Deputation (including short-term contract (ISTC)) basis against the advt. no. DST/01/2026-Estt.III")
    assert row.advertisement_number == "DST/01/2026-Estt.III"
    assert row.evidence["advertisement_number"] == "advt. no. DST/01/2026-Estt.III"
    assert row.post_names == ["Estate Officer"]
    assert row.kind_hint == "VACANCY"

def test_only_the_estate_officer_row_states_an_advertisement_number(rows):
    assert [r.advertisement_number for r in rows if r.advertisement_number] == ["DST/01/2026-Estt.III"]

def test_scientific_attache_tokyo_berlin_row(by_url):
    row = by_url[NEWS + "advertisement-two-temporary-posts-scientific-attache-group-gazetted-one-each-indian-mission"]
    assert row.title == ("Advertisement for the Two temporary posts of Scientific Attache (Group 'A', Gazetted), one each "
                         "in the Indian Mission at Tokyo (Japan) and Indian Mission at Berlin (Germany), on deputation "
                         "(including short-term contract) basis")
    assert row.kind_hint == "VACANCY"
    assert row.evidence["kind_hint"] == "Advertisement"
    assert row.post_names == ["Scientific Attache"]
    assert row.evidence["post_names"] == "posts of Scientific Attache (Group 'A', Gazetted)"

def test_engagement_and_hiring_rows(by_url):
    intern = by_url[NEWS + "data-strategy-unit-dsu-department-science-and-technology-looking-engagement-02-suitable"]
    assert intern.title == ("Engagement of intern (02 posts) in Data & Strategy Unit, Department of Science & "
                            "Technology on contract basis")
    assert intern.post_names == ["intern"]
    assert intern.evidence["post_names"] == "Engagement of intern (02 posts)"
    # relative href on the page, made absolute
    yp = by_url[NEWS + "engagement-young-professional-06-posts-dst-contract-basis"]
    assert yp.evidence["url"] == "/news/engagement-young-professional-06-posts-dst-contract-basis"
    assert yp.post_names == ["Young Professional"]
    consultants = by_url[NEWS + "advertisement-hiring-2-consultants-dst"]
    assert consultants.title == "Advertisement for hiring of 2 Consultants in DST"
    assert consultants.post_names == ["Consultants"]

def test_staff_car_driver_row_keeps_the_grade_and_drops_level_and_pay(by_url):
    url = ("https://dst.gov.in/filling-up-of-one-post-of-staff-car-driver-%28ordinary-grade%29%2C-level-2-%28rs.19900-63200/"
           "-%29-group-%27c%27-post%2C-on-deputation/-absorption-basis-in-dst")
    row = by_url[url]
    assert row.title == ("Filling up of one post of Staff Car Driver (Ordinary Grade), Level-2 (Rs.19900-63200/-) "
                         "Group 'C' post, on deputation/ absorption basis in DST")
    assert row.post_names == ["Staff Car Driver (Ordinary Grade)"]

def test_re_advertisement_row(by_url):
    row = by_url[NEWS + "re-advertisement-filling-01-post-scientist-g-dst-deputation-istc-basis"]
    assert row.title == "Re-advertisement for filling up of 01 post of Scientist 'G' in DST on deputation (ISTC) basis"
    assert row.kind_hint == "VACANCY"
    assert row.evidence["kind_hint"] == "Re-advertisement"
    assert row.post_names == ["Scientist 'G'"]

def test_pdf_rows_and_links_list(by_url):
    row = by_url[FILES + "One%20post%20of%20scientist%20C%20under%20mechanical%20engineering%20through%20reserve%20panel.pdf"]
    assert row.title == ("Filling up of one (01) post of Scientist 'C' under Mechanical Engineering discipline on direct "
                         "recruitment basis in DST through Reserve panel in the order of merit")
    assert row.post_names == ["Scientist 'C'"]
    # "Links:" list: the "(5.17 MB)" file size is not part of the title
    rules = by_url[FILES + "gazetted-post-2013.pdf"]
    assert rules.title == ("Department of Science & Technology Group 'A' Gazetted Posts (Non Ministerial, Scientific and "
                           "Technical) Rules, 2013")
    assert rules.kind_hint == "RULES"
    assert rules.evidence["kind_hint"] == "Rules, 2013"
    assert rules.post_names == []

def test_recruitment_rules_rows_are_rules_without_post_names(by_url):
    row = by_url[NEWS + "seeking-comments-stakeholders-draft-recruitment-rules-post-junior-analyst-technical-department"]
    assert row.title == ("Seeking comments from the stakeholders on draft recruitment rules for the post of Junior "
                         "Analyst (Technical) in Department of Science and Technology")
    assert row.kind_hint == "RULES"
    assert row.post_names == []
    rr = by_url[FILES + "RRs%20of%20non%20gazetted%20posts%20of%20DST.pdf"]
    assert rr.title == "Recruitment Rules (RRs) of Non Gazetted posts of Department of Science and Technology (DST)"
    assert rr.kind_hint == "RULES"

def test_unlabelled_row_has_no_kind(by_url):
    row = by_url[NEWS + "appointment-compassionate-ground-dst-year-2022"]
    assert row.title == "Appointment on compassionate ground in DST for the year 2022"
    assert row.kind_hint is None and row.post_names == []

def test_kind_counts(rows):
    kinds = [r.kind_hint for r in rows]
    assert kinds.count("VACANCY") == 34
    assert kinds.count("RULES") == 18
    assert kinds.count(None) == 1

def test_all_urls_are_https_on_the_official_domain(rows):
    for r in rows:
        parts = urlparse(r.url)
        assert parts.scheme == "https", r.url
        assert parts.hostname == "dst.gov.in", r.url
        assert "#" not in r.url

def test_navigation_menus_and_footer_are_skipped(rows, html):
    urls = {r.url for r in rows}
    for nav in ("https://dst.gov.in/website-policies", "https://dst.gov.in/contactus", "https://dst.gov.in/feedback",
                "https://dst.gov.in/fellowship-opportunities-researchers", "https://www.facebook.com/"):
        assert not any(u.startswith(nav) for u in urls)
    assert 'href="/website-policies"' in html  # the menu link is on the page, the adapter leaves it out
    assert all(r.title != "Recruitment Cell" for r in rows)

def test_evidence_for_every_filled_field_is_page_text(rows, region, region_text):
    for r in rows:
        assert r.evidence["title"] == r.title and r.title in region_text
        assert f'href="{r.evidence["url"]}"' in region
        assert urljoin(PAGE_URL, r.evidence["url"]) == r.url
        for name in ("advertisement_number", "kind_hint", "published_on", "last_date"):
            if getattr(r, name) is not None:
                assert r.evidence.get(name), (r.url, name)
                assert r.evidence[name] in r.title
        if r.post_names:
            assert r.evidence["post_names"] in r.title
            for post in r.post_names:
                assert post in r.evidence["post_names"]
        if r.advertisement_number:
            assert r.advertisement_number in r.evidence["advertisement_number"]

def test_no_invented_dates(rows, region_text):
    # The listing shows no date at all (only years inside titles such as "Rules, 2013"), so no row may carry one
    assert not re.search(r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b", region_text)
    assert not re.search(r"\b\d{1,2}(?:st|nd|rd|th)?\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?,?\s+\d{4}",
                         region_text, re.I)
    for r in rows:
        assert r.published_on is None and r.last_date is None
        assert "published_on" not in r.evidence and "last_date" not in r.evidence

def test_page_without_content_region_gives_no_rows(html):
    without = html.replace('<div id="content"', '<div id="not-content"')
    assert dst.parse_listing(without, PAGE_URL) == []
