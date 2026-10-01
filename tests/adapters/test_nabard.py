"""NABARD listing adapter against the live Career Notices page captured on 2026-10-01 (see fixtures/adapters/nabard/meta.json)."""
import json
import re
from html import unescape
from pathlib import Path
from urllib.parse import urljoin, urlparse

import pytest

from govcareeros.backend.sources.adapters import adapter_for, nabard
from govcareeros.backend.sources.registry import SOURCES
from govcareeros.backend.sources.url_guard import UrlPolicy

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "adapters" / "nabard"
PAGE_URL = "https://www.nabard.org/careers-notices1.aspx?cid=693&id=26"
DOC = "https://www.nabard.org/auth/writereaddata/CareerNotices/"

@pytest.fixture(scope="module")
def html():
    return (FIXTURE_DIR / "career_notices.html").read_text(encoding="utf-8")

@pytest.fixture(scope="module")
def rows(html):
    return nabard.parse_listing(html, PAGE_URL)

@pytest.fixture(scope="module")
def by_url(rows):
    return {r.url: r for r in rows}

def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", fragment))).strip()

@pytest.fixture(scope="module")
def page_blocks(html):
    """The page's notice blocks, read independently of the adapter: (title, [(link text, href) shown])."""
    out = []
    for block in html.split('<div class="career_row">')[1:]:
        title = _text(re.search(r'<div class="career_title">(.*?)</div>', block, re.S).group(1))
        shown = re.findall(
            r"<div class=\"(?:download_pdf|ext_link)\" style='display:inline;'>\s*<a\b[^>]*?href=\"([^\"]*)\"[^>]*>(.*?)</a>",
            block, re.S)
        out.append((title, [(_text(text), href.strip()) for href, text in shown]))
    return out

def test_fixture_meta_records_the_live_capture():
    meta = json.loads((FIXTURE_DIR / "meta.json").read_text(encoding="utf-8"))
    assert meta["url"] == PAGE_URL
    assert meta["captured_on"] == "2026-10-01"
    assert meta["fetch_mode"] == "HTTP"
    assert meta["file"] == "career_notices.html"

def test_adapter_is_auto_discovered_under_the_registry_name():
    assert adapter_for("NABARD") is nabard
    spec = next(s for s in SOURCES if s.name == "NABARD")
    assert spec.recruitment_url == PAGE_URL

def test_one_row_per_official_document_shown(rows, page_blocks):
    assert len(page_blocks) == 167
    policy = UrlPolicy.from_url(PAGE_URL)
    official = []
    for _, links in page_blocks:
        for _, href in links:
            url = urljoin(PAGE_URL, href)
            if href and policy.allows(url) and url not in official:
                official.append(url)
    assert len(official) == 234
    assert [r.url for r in rows] == official   # same order as the page (newest first)
    assert len({r.url for r in rows}) == len(rows)

def test_all_urls_are_absolute_https_on_the_official_domain(rows):
    for r in rows:
        parsed = urlparse(r.url)
        assert parsed.scheme == "https" and parsed.hostname == "www.nabard.org", r.url
        assert r.url.startswith(DOC) or parsed.path.startswith("/hrmd-new/"), r.url
        assert not r.url.endswith("/CareerNotices/")   # the hidden empty upload slots are not documents

def test_navigation_menu_and_external_links_are_skipped(rows, html):
    urls = {r.url for r in rows}
    # menu / footer / sidebar links on the same page
    assert "https://www.nabard.org/Tenders.aspx?cid=501&id=24" in html
    assert not any("Tenders.aspx" in u or "careers-notices1.aspx" in u or "ftrcontent.aspx" in u for u in urls)
    # "Apply Here" (IBPS) and mail safelinks are on other domains
    assert "https://ibpsreg.ibps.in/nabardjun26/" in html
    assert not any("ibps" in u or "safelinks" in u for u in urls)

def test_latest_shortlist_row(by_url):
    r = by_url[DOC + "1048289184DDMABI - Registration Number of Candiddates Shortlisted for Interview.pdf"]
    assert r.title == (
        "Recruitment of Specialists on contract - DDMABI – 2026 - 27 - Registration Nos. Of the candidates "
        "shortlisted for Interview for the posts of AI Engineer, Data Engineer, Data Scientist, Document Designer, "
        "ETL Designer and Senior Data Scientist")
    assert r.kind_hint == "RESULT"
    assert r.evidence["kind_hint"] == "Shortlisted"   # link text "... Candiddates Shortlisted for Interview"
    assert r.post_names == [
        "AI Engineer", "Data Engineer", "Data Scientist", "Document Designer", "ETL Designer", "Senior Data Scientist"]
    assert r.evidence["post_names"] == (
        "posts of AI Engineer, Data Engineer, Data Scientist, Document Designer, ETL Designer and Senior Data Scientist")
    assert r.evidence["url"] == "/auth/writereaddata/CareerNotices/1048289184DDMABI - Registration Number of Candiddates Shortlisted for Interview.pdf"

def test_open_advertisement_row(by_url, rows):
    r = by_url[DOC + "1354898682FINAL  Advtertisement - 06 Posts - SPPID, DCAS & FD June 2026.pdf"]
    assert r.title == "Recruitment of Specialists on contract - 2026-27 - SPPID, DCAS & FD"
    assert r.kind_hint == "VACANCY"
    assert r.evidence["kind_hint"] == "Advtertisement"   # the site's own spelling in the link text
    assert r.post_names == []                            # the title names departments, not posts
    assert r.advertisement_number is None and r.published_on is None and r.last_date is None
    # its "Apply Here" link goes to IBPS: the notice has exactly one row
    assert [x.url for x in rows if x.title == r.title] == [r.url]

def test_department_codes_are_not_part_of_post_names(by_url):
    r = by_url[DOC + "1135739427List of Candidates Shortlisted for Interview - SPPID and FD.pdf"]
    assert r.post_names == [
        "Data Architect", "Application Architect", "Technology Architect",
        "Specialist – Derivatives and Treasury Management"]
    assert r.evidence["post_names"] == (
        "posts of Data Architect, Application Architect, Technology Architect- SPPID and "
        "Specialist – Derivatives and Treasury Management – FD")

def test_notice_with_several_documents_gives_one_row_each(rows):
    title = ("Recruitment of Development Assistant/ Development Assistant (Hindi) – 2026 - "
             "Roll Nos. of candidates provisionally Selected & Waitlisted")
    got = [(r.url, r.kind_hint, r.evidence["kind_hint"]) for r in rows if r.title == title]
    assert got == [
        (DOC + "231218666LPT Notification.pdf", "RESULT", "Selected"),
        (DOC + "636885612Development Assistant - Provisional Select List.pdf", "RESULT", "Select List"),
        (DOC + "884140162Development Assistant - Provisional Wait List.pdf", "RESULT", "Wait List"),
        (DOC + "1407262794Development Assistant (Hindi) - Provisional  Select List.pdf", "RESULT", "Select List"),
        (DOC + "2010277040Development Assistant (Hindi) - Provisional Wait List.pdf", "RESULT", "Wait List"),
    ]

def test_enumerated_posts_and_unreadable_lists(by_url):
    enumerated = by_url[DOC + "1132084451Result_03 Vertical Heads.pdf"]
    assert enumerated.kind_hint == "RESULT" and enumerated.evidence["kind_hint"] == "Result"
    assert enumerated.post_names == [
        "Head – Data & Impact Evaluation", "Head - Rural Tech and Innovations",
        "Head – Finance, Compliance and Commercialization"]
    # The same three posts written as a comma list: "," and "and" are part of the names, so none are reported
    comma_list = by_url[DOC + "2912252737List of Candidates Shortlisted for Interview - RMSMED.pdf"]
    assert "Head – Finance, Compliance and Commercialization." in comma_list.title
    assert comma_list.post_names == [] and "post_names" not in comma_list.evidence

def test_official_link_outside_the_upload_folder(by_url):
    r = by_url["https://www.nabard.org/hrmd-new/prelimresult.aspx"]
    assert r.title == ("Recruitment to the post of Grade - A - Assistant Manager (RDBS/Rajbhasha) - 2024 - "
                       "Individual Score Card")
    assert r.kind_hint == "RESULT" and r.evidence["kind_hint"] == "Marks"
    assert r.post_names == ["Grade - A - Assistant Manager (RDBS/Rajbhasha)"]
    # The 2023 score card links the same page further down; it is listed once, under the newest notice
    assert "https://www.nabard.org/hrmd-new/mainresult.aspx" in by_url

def test_corrigendum_and_call_letter_labels(by_url):
    withdrawn = by_url[DOC + "0310161053Cancellation of Advt 14 11 13.pdf"]
    assert withdrawn.title == "Withdrawal of Recruitment Process to the post of Manager in Grade 'B' (RDBS)"
    assert withdrawn.kind_hint == "CORRIGENDUM" and withdrawn.evidence["kind_hint"] == "Cancellation"
    assert withdrawn.post_names == ["Manager in Grade 'B' (RDBS)"]

    cfo_extension = by_url[DOC + "0511245146notification for extension.pdf"]
    assert cfo_extension.title == "Recruitment to the post of Senior Climate Finance Specialist and Chief Financial Officer"
    assert cfo_extension.kind_hint == "CORRIGENDUM" and cfo_extension.evidence["kind_hint"] == "Extension in"
    assert cfo_extension.last_date is None   # "Extension in last date of submission ..." gives no date

    extension = by_url[DOC + "0404254741notification for extension20250404_10305096.pdf"]
    assert extension.title == "Recruitment of Specialists on contract' - Extension for submission of applications"
    assert extension.kind_hint == "CORRIGENDUM" and extension.evidence["kind_hint"] == "Extension for"

    call = by_url[DOC + "0406191449Information Handout Grade B 2019 Eng.pdf"]
    assert call.title == "Download call letter for Preliminary Examination for the post of Manager in Grade ‘B’ - 2019"
    assert call.kind_hint == "ADMIT_CARD" and call.evidence["kind_hint"] == "call letter"
    assert call.post_names == ["Manager in Grade ‘B’"]

def test_unlabelled_notice_has_no_kind(by_url):
    r = by_url[DOC + "2512252251Final Advertisement_Young Professional Program.pdf"]
    assert r.title == "Recruitment of Young Professionals (on Contract) – 2025-26"
    # The link text is "Young Professional Program"; "Advertisement" is only in the file name
    assert r.kind_hint is None and "kind_hint" not in r.evidence

def test_notices_with_only_external_links_give_no_row(rows, page_blocks):
    titles = {r.title for r in rows}
    missing = [(t, links) for t, links in page_blocks if t not in titles]
    assert len(missing) == 10
    assert ("Recruitment to the post of Grade A (RDBS/Legal/Protocol & Security) - 2025 – "
            "Link to download Psychometric Test call letter- 07.03.2026") in {t for t, _ in missing}
    for _, links in missing:
        assert all(not UrlPolicy.from_url(PAGE_URL).allows(urljoin(PAGE_URL, h)) for _, h in links if h)

def test_no_dates_or_advertisement_numbers_are_invented(rows, html):
    # The page has no publication/closing date column or label and shows no advertisement numbers
    body = html[html.index('<h3 class="page_heading">Career Notices</h3>'):html.index('<div class="col-sm-3">')]
    assert not re.search(r"closing\s+date|published\s+on|posted\s+on|date\s+of\s+publication", _text(body), re.I)
    # The only mention of a last date is a link text that states no date
    assert re.findall(r"[^>]*last\s+date[^<]*", body, re.I) == [
        "Extension in last date of submission of application form for the post of Chief Financial Officer-( on contract)"] * 2
    for r in rows:
        assert r.published_on is None and r.last_date is None and r.advertisement_number is None, r.url
        assert not {"published_on", "last_date", "advertisement_number"} & set(r.evidence)
    # Dates inside titles are exam/call-letter dates, never read into the date fields
    assert any("21.11.2024" in t for t in (_text(b) for b in re.findall(r'<div class="career_title">(.*?)</div>', body, re.S)))

def test_every_filled_field_has_source_evidence(rows, page_blocks, html):
    texts_by_title = {}
    for title, links in page_blocks:
        texts_by_title.setdefault(title, set()).update(text for text, _ in links)
    for r in rows:
        assert r.evidence["title"] == r.title
        assert r.title in texts_by_title
        assert f'href="{r.evidence["url"]}"' in html or f'href=" {r.evidence["url"]}"' in html
        assert urljoin(PAGE_URL, r.evidence["url"]) == r.url
        if r.kind_hint:
            ev = r.evidence["kind_hint"]
            assert ev in r.title or any(ev in t for t in texts_by_title[r.title]), (r.url, ev)
        else:
            assert "kind_hint" not in r.evidence
        if r.post_names:
            ev = r.evidence["post_names"]
            assert ev in r.title
            for name in r.post_names:
                assert name in ev, (name, ev)
        else:
            assert "post_names" not in r.evidence

def test_kinds_found_on_the_page(rows):
    kinds = {}
    for r in rows:
        kinds[r.kind_hint] = kinds.get(r.kind_hint, 0) + 1
    assert kinds == {"RESULT": 105, "VACANCY": 38, "ADMIT_CARD": 12, "CORRIGENDUM": 5, None: 74}
