"""BSF listing adapter against the live page captured on 2026-10-01 (see fixtures/adapters/bsf/meta.json).

On that capture the "Current Recruitment Openings" list is empty and every live notice button links off
the official domain, so parse_listing returns no rows; the extraction itself is checked on the same
buttons through bsf._entries (all candidates before the official-domain filter). The page also keeps its
retired buttons inside HTML comments, all linking documents on rectt.bsf.gov.in; one test reads exactly
those comment-wrapped buttons of the captured page with only the comment markers removed, to check the
rows parse_listing emits for on-domain links.
"""
import hashlib
import json
import re
from html import unescape
from pathlib import Path
from urllib.parse import urlparse

import pytest

from govcareeros.backend.sources.adapters import adapter_for, bsf
from govcareeros.backend.sources.registry import SOURCES

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "adapters" / "bsf"
HOME = "https://rectt.bsf.gov.in/"
OFFICIAL_HOST = "rectt.bsf.gov.in"
CDN = "https://d3t79nicn48uzj.cloudfront.net/bsf/custom/"
DATE_TEXT_RE = re.compile(r"\d{1,2}[-./]\d{1,2}[-./]\d{4}|\d{1,2}[- ][A-Za-z]{3,9}[- ,]+\d{4}")

def _html():
    return (FIXTURE_DIR / "home.html").read_text(encoding="utf-8")

def _text(fragment):
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()

def _live_buttons(html):
    """(href, text) of the page's c-button links outside HTML comments, read independently of the adapter."""
    visible = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    return [(unescape(h).strip(), _text(t)) for h, t in re.findall(
        r"<a\s+href='([^']*)'[^>]*class='c-button[^']*'[^>]*>(.*?)</a>", visible, re.S)]

def _strip_block():
    """The notice-strip block of the captured page (between the content wrapper and the openings list)."""
    html = _html()
    start = html.index("c-layout__wrapper design5")
    end = html.index('<div class="c-layout__wrapper-content">', start)
    return html, start, end

def _retired_buttons_page():
    """The captured page with the comment markers of its notice strip removed (nothing else changed)."""
    html, start, end = _strip_block()
    return html[:start] + html[start:end].replace("<!--", "").replace("-->", "") + html[end:]

@pytest.fixture(scope="module")
def entries():
    return bsf._entries(_html(), HOME)

@pytest.fixture(scope="module")
def retired_rows():
    return bsf.parse_listing(_retired_buttons_page(), HOME)

def _by_url(rows):
    return {r.url: r for r in rows}

def _assert_evidence(row, page_text):
    assert row.evidence["title"] == row.title
    assert row.evidence["url"]
    if row.advertisement_number:
        assert row.evidence["advertisement_number"] in row.title
    if row.published_on:
        assert row.evidence["published_on"] in row.title
    if row.last_date:
        assert row.evidence["last_date"] in row.title
    if row.post_names:
        assert row.evidence["post_names"] in row.title
        for post in row.post_names:
            assert post in row.evidence["post_names"]
    if row.kind_hint:
        assert row.evidence["kind_hint"] in page_text
    for name in ("published_on", "last_date"):
        if getattr(row, name) is None:
            assert name not in row.evidence

# --- capture and registration ---------------------------------------------------------------------------------

def test_fixture_meta_records_the_live_capture():
    meta = json.loads((FIXTURE_DIR / "meta.json").read_text(encoding="utf-8"))
    assert meta["source"] == "BSF"
    assert meta["url"] == HOME
    assert meta["captured_on"] == "2026-10-01"
    assert meta["fetch_mode"] == "HTTP"
    assert meta["user_agent"] == "GovCareerOS/0.1 (personal government-job tracker)"
    (page,) = meta["pages"]
    assert page["file"] == "home.html" and page["url"] == HOME and page["final_url"] == HOME
    assert page["http_status"] == 200
    # stored with LF line endings (the page has no CR); a CRLF checkout must not break the check
    data = (FIXTURE_DIR / "home.html").read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(data).hexdigest() == page["sha256"]

def test_adapter_is_discovered_for_the_registry_source():
    assert adapter_for("BSF") is bsf
    (spec,) = [s for s in SOURCES if s.name == "BSF"]
    assert spec.recruitment_url == HOME
    assert spec.official_domain == OFFICIAL_HOST
    assert not getattr(bsf, "LISTING_URLS", [])

# --- the captured page as served --------------------------------------------------------------------------------

def test_captured_page_yields_no_rows_because_every_live_notice_is_off_domain(entries):
    rows = bsf.parse_listing(_html(), HOME)
    assert rows == []
    assert all(urlparse(r.url).hostname == OFFICIAL_HOST for r in rows)
    assert len(entries) == 18
    assert {urlparse(e.url).hostname for e in entries} == {
        "d3t79nicn48uzj.cloudfront.net", "cdn.digialm.com", "cdn4.digialm.com"}

def test_openings_list_shows_only_its_empty_state():
    html = _html()
    assert "<li class='c-common__list-item emptyState'>No Job(s) Available.</li>" in html
    page = bsf._Page()
    page.feed(html)
    assert page.openings_heading == "Current Recruitment Openings"
    assert [kind for kind, _, _ in page.items if kind == "opening"] == []
    assert all("No Job(s) Available" not in e.title for e in bsf._entries(html, HOME))

def test_entries_are_exactly_the_live_buttons_that_have_a_link(entries):
    buttons = _live_buttons(_html())
    assert len(buttons) == 19
    linked = [(h, t) for h, t in buttons if not h.startswith("javascript:")]
    assert [(e.evidence["url"], e.title) for e in entries] == linked
    # the "NOTE FOR CANDIDATES" button has no link
    assert ("javascript:;", "NOTE FOR CANDIDATES:- IN CASE OF NON-RECEIPT OF OTP IN MOBILE, CANDIDATES ARE "
            "REQUESTED TO APPROACH THEIR NETWORK PROVIDER") in buttons
    assert not any(e.title.startswith("NOTE FOR CANDIDATES") for e in entries)

def test_specific_live_notices(entries):
    by_url = _by_url(entries)

    notice = by_url[CDN + "1789982259.pdf"]
    assert notice.title == ("NOTICE FOR OBJECTION MANAGEMENT PORTAL OF CBT EXAM FOR THE POST OF ASI (STENO)/HC (MIN) "
                            "EXAM - 2024 THROUGH DIRECT ENTRY AND LDCE")
    assert notice.post_names == ["ASI (STENO)/HC (MIN)"]
    assert notice.evidence["post_names"] == "THE POST OF ASI (STENO)/HC (MIN)"
    assert notice.kind_hint is None and notice.advertisement_number is None

    result = by_url[CDN + "1788961968.pdf"]
    assert result.title == ("PUBLISHING OF FINAL RESULT FOR THE RECRUITMENT OF MERITORIOUS SPORTS PERSON TO THE OF "
                            "CT(GD) (MALE/FEMALE) IN BSF UNDER SPORTS QUOTA-2025 (3RD PHASE)")
    assert result.kind_hint == "RESULT" and result.evidence["kind_hint"] == "RESULT"
    assert result.post_names == []  # the page's "TO THE OF CT(GD)" names no post

    fake = by_url[CDN + "1789467350.pdf"]
    assert fake.title == ("NOTICE REGARDING FAKE CIRCULATION OF WRITTEN EXAMINATION (CBT) DATE FOR THE POST OF "
                          "HC(RO)/(RM) IN BSF COMN SET-UP -2025")
    assert fake.post_names == ["HC(RO)/(RM)"]

    rescheduling = by_url[CDN + "1788273108.pdf"]
    assert rescheduling.post_names == ["ASI(STENO/COMBATANT STENO)", "HC(MIN/COMBATANT MIN )"]
    assert rescheduling.evidence["post_names"] == "THE POST OF ASI(STENO/COMBATANT STENO) AND HC(MIN/COMBATANT MIN )"

    admit = [e for e in entries if e.title.startswith("Link to download admit card")]
    assert len(admit) == 1
    assert admit[0].title == ("Link to download admit card of candidates for the post of HC/Min whose exam was "
                              "rescheduled on 18.09.2026")
    assert admit[0].url == "https://cdn.digialm.com//EForms/configuredHtml/1258/102021/login.html"
    assert admit[0].kind_hint == "ADMIT_CARD" and admit[0].evidence["kind_hint"] == "admit card"
    assert admit[0].post_names == ["HC/Min"]
    # an exam date in the title is not a published or closing date
    assert admit[0].published_on is None and admit[0].last_date is None

    mock = by_url["https://cdn4.digialm.com//OnlineAssessment/index.html?1258@@M705"]
    assert mock.title == "Static Mock Link for BSF Exam 2026: AIR WING"
    assert mock.kind_hint is None and mock.post_names == []

def test_no_live_entry_has_a_date_number_or_menu_link(entries):
    # the notice strip shows no published/closing date column and no advertisement number
    for e in entries:
        assert e.published_on is None and e.last_date is None and e.advertisement_number is None
    urls = {e.url for e in entries}
    for menu in ("/registration/basic-details", "/home/help-desk", "/auth/login", "/payment/csc/connect", "/"):
        assert HOME.rstrip("/") + menu not in urls
    assert not any("facebook" in u or "youtube" in u or "BSF-Instructions" in u for u in urls)

def test_every_filled_field_has_its_page_text_as_evidence(entries, retired_rows):
    page_text = _text(_html())
    for row in [*entries, *retired_rows]:
        _assert_evidence(row, page_text)
        if row.kind_hint:  # buttons are labelled by their own wording (no openings-list item on this page)
            assert row.evidence["kind_hint"] in row.title

def test_no_row_has_a_date_the_page_does_not_show(entries, retired_rows):
    for row in [*entries, *retired_rows]:
        for name in ("published_on", "last_date"):
            value = getattr(row, name)
            if value is None:
                continue
            shown = DATE_TEXT_RE.search(row.evidence[name])
            assert shown and bsf._parse_date(shown.group(0)) == value

# --- the page's own retired (comment-wrapped) buttons: on-domain links -------------------------------------------

def test_comment_wrapped_buttons_are_ignored_on_the_page_as_served(entries):
    html, start, end = _strip_block()
    retired = re.findall(r'href="(/static/bsf/pdf/[^"]+)"', html[start:end])
    assert len(retired) == 15
    assert not any("/static/bsf/pdf/" in e.url for e in entries)

def test_retired_buttons_give_official_rows(retired_rows):
    assert len(retired_rows) == 15
    assert all(r.url.startswith("https://rectt.bsf.gov.in/static/bsf/pdf/") for r in retired_rows)
    assert all(urlparse(r.url).hostname == OFFICIAL_HOST for r in retired_rows)
    # the Google Drive demo-video button and the link-less note are not rows
    assert not any("drive.google.com" in r.url or r.title.startswith("NOTE FOR CANDIDATES") for r in retired_rows)
    by_url = _by_url(retired_rows)

    corrigendum = by_url["https://rectt.bsf.gov.in/static/bsf/pdf/corrigendum-1-ro-rm-2025.pdf"]
    assert corrigendum.title == ("CORRIGENDUM -1 : DIRECT/DEPARTMENTAL RECRUITMENT FOR THE POST OF HEAD CONSTABLE "
                                 "(RADIO OPERATOR & RADIO MECHANIC) IN BSF COMMUNICATION SETUP - 2025.")
    assert corrigendum.kind_hint == "CORRIGENDUM"
    assert corrigendum.post_names == ["HEAD CONSTABLE (RADIO OPERATOR & RADIO MECHANIC)"]
    assert corrigendum.evidence["url"] == "/static/bsf/pdf/corrigendum-1-ro-rm-2025.pdf"

    advert = by_url["https://rectt.bsf.gov.in/static/bsf/pdf/55c6c141-5288-11f0-8331-0a1dcac2b80f.pdf"]
    assert advert.title == ("ADVERTISMENT- DIRECT & DEPARTMENTAL RECRUITMENT FOR THE POST OF HEAD CONSTABLE "
                            "(RADIO OPERATOR & RADIO MECHANIC) IN BSF COMMUNICATION SET-UP : 2025.")
    assert advert.kind_hint == "VACANCY" and advert.evidence["kind_hint"] == "ADVERTISMENT"
    assert advert.post_names == ["HEAD CONSTABLE (RADIO OPERATOR & RADIO MECHANIC)"]

    rally = by_url["https://rectt.bsf.gov.in/static/bsf/pdf/180825-MG.pdf"]
    assert rally.title == "ADVERTISMENT- OPEN RALLY RECRUITMENT FOR THE POST OF CT(GD)-2025 FOR MEGHALAYA OF ST CANDIDATES ONLY."
    assert rally.kind_hint == "VACANCY" and rally.post_names == ["CT(GD)"]

    mizoram = by_url["https://rectt.bsf.gov.in/static/bsf/pdf/180825_MC.pdf"]
    assert mizoram.post_names == ["CONSTABLE (GD)"]

    water = by_url["https://rectt.bsf.gov.in/static/bsf/pdf/Result_pst-pet_Water%20Wing.pdf"]
    assert water.evidence["url"] == "/static/bsf/pdf/Result_pst-pet_Water Wing.pdf"
    assert water.kind_hint == "RESULT" and water.evidence["kind_hint"] == "SHORTLISTED"
    assert water.post_names == []  # "VARIOUS POSTS OF WATER WING" names a wing, not posts

    group = by_url["https://rectt.bsf.gov.in/static/bsf/pdf/1741850525.pdf"]
    assert group.post_names == [] and group.kind_hint is None

    for r in retired_rows:
        assert r.published_on is None and r.last_date is None and r.advertisement_number is None
