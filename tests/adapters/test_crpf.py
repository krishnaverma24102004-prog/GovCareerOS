"""CRPF listing adapter against the live rect.crpf.gov.in pages captured on 2026-10-01 (see fixtures/adapters/crpf/meta.json)."""
import json
import re
from datetime import date, datetime
from html import unescape
from pathlib import Path
from urllib.parse import urljoin, urlparse

import pytest

from govcareeros.backend.sources.adapters import adapter_for, crpf
from govcareeros.backend.sources.registry import SOURCES

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "adapters" / "crpf"
HOME_URL = "https://rect.crpf.gov.in/"
ARCHIVED_URL = "https://rect.crpf.gov.in/Archived"
DOC = "https://rect.crpf.gov.in/Upload/Recruitment/"
FILLED = ("title", "url", "advertisement_number", "published_on", "last_date", "post_names", "kind_hint")

def _read(name):
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")

def _text(fragment):
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()

def _squash(text):
    return re.sub(r"\s+", "", text)

def _table_rows(html):
    """The listing tables' own rows, read independently of the adapter: (date cell text, [href, ...]).

    A row runs from its "<tr> <td> N</td>" start to the next row start or the end of its table
    (a Document cell can hold a table of its own, so rows are not cut at the first </tr>)."""
    starts = [m.start() for m in re.finditer(r"<tr>\s*<td>\s*\d+\s*</td>", html)]
    out = []
    for i, start in enumerate(starts):
        table_end = re.compile(r"</tbody>\s*</table>\s*</div>").search(html, start).start()
        end = min(starts[i + 1] if i + 1 < len(starts) else len(html), table_end)
        row = html[start:end]
        date_text = _text(re.search(r'<td style="width:130px;">(.*?)</td>', row, re.S).group(1))
        out.append((date_text, [unescape(h) for h in re.findall(r'href="([^"]+)"', row)]))
    return out

@pytest.fixture(scope="module")
def home_html():
    return _read("home.html")

@pytest.fixture(scope="module")
def archived_html():
    return _read("archived.html")

@pytest.fixture(scope="module")
def rows(home_html):
    return crpf.parse_listing(home_html, HOME_URL)

@pytest.fixture(scope="module")
def archived_rows(archived_html):
    return crpf.parse_listing(archived_html, ARCHIVED_URL)

@pytest.fixture(scope="module")
def by_url(rows):
    return {r.url: r for r in rows}

def test_fixture_meta_records_the_live_capture():
    meta = json.loads((FIXTURE_DIR / "meta.json").read_text(encoding="utf-8"))
    assert meta["url"] == HOME_URL
    assert meta["captured_on"] == "2026-10-01"
    assert meta["fetch_mode"] == "HTTP"
    assert [(p["url"], p["file"], p["http_status"]) for p in meta["pages"]] == [
        (HOME_URL, "home.html", 200), (ARCHIVED_URL, "archived.html", 200)]

def test_adapter_is_auto_discovered_under_the_registry_name():
    assert adapter_for("CRPF") is crpf
    spec = next(s for s in SOURCES if s.name == "CRPF")
    assert spec.recruitment_url == HOME_URL

def test_one_row_per_table_row_on_the_current_page(rows, home_html):
    table_rows = _table_rows(home_html)
    assert len(table_rows) == 62
    assert len(rows) == 62
    assert len({r.url for r in rows}) == len(rows)
    # Page order is kept: first row of section 1, last row of section 7
    assert rows[0].url == DOC + "Meritorious_Sportspersons_320092026-528.pdf"
    assert rows[-1].url == DOC + "Annexure 1 & 2-976.pdf"

def test_sports_quota_advertisement(by_url):
    row = by_url[DOC + "Meritorious_Sportspersons_315092026-672.pdf"]
    assert row.title == ("Recruitment of Meritorious Sportspersons for the post of HC/GD and CT/GD under Sports Quota : "
                         "Publication of Advertisement/Notification")
    assert row.published_on == date(2026, 9, 17)
    assert row.evidence["published_on"] == "17-Sep-2026"
    assert row.kind_hint == "VACANCY"
    assert row.post_names == ["HC/GD", "CT/GD"]
    assert row.evidence["post_names"] == "for the post of HC/GD and CT/GD"
    assert row.advertisement_number is None
    assert row.last_date is None

def test_hindi_version_of_the_sports_quota_advertisement(by_url):
    row = by_url[DOC + "Meritorious_Sportspersons_320092026-528.pdf"]
    assert row.title == ("स्पोर्ट्स कोटा के तहत HC/GD और CT/GD के पदों पर मेधावी खिलाड़ियों की भर्ती :: "
                         "नोटिफिकेशन और विज्ञापन का प्रकाशन")
    assert row.published_on == date(2026, 9, 17)
    assert (row.kind_hint, row.evidence["kind_hint"]) == ("VACANCY", "नोटिफिकेशन")
    # The Hindi title names no English post: the names come from the section heading, and evidence says so
    assert row.post_names == ["HC/GD", "CT/GD"]
    assert row.evidence["post_names"] in row.evidence["section"]

def test_compassionate_selection_list(by_url):
    row = by_url[DOC + "Rect_notice_315092026-147.pdf"]
    assert row.title == "List of 66 selected candidate on compassionate appointment in CRPF (Vacancy released on 14/09/2026)"
    assert row.evidence["section"] == "Compassionate Appointment in CRPF 2026"
    assert row.published_on == date(2026, 9, 14)
    assert (row.kind_hint, row.evidence["kind_hint"]) == ("RESULT", "selected candidate")
    assert row.post_names == []
    assert row.last_date is None  # "Vacancy released on 14/09/2026" is not a closing date

def test_admit_card_row_keeps_the_crpf_pdf_not_the_exam_agency_link(by_url, home_html):
    row = by_url[DOC + "E-ADMIT_CARD_FOR_SUB_INSPECTOR_(HINDI_TRANSLATOR)_295082026-939.pdf"]
    # The description embeds a link to www.sscer.org (not an official CRPF domain); it is never used
    assert "https://www.sscer.org/ac/chtcrpf2025_pst_callletter/DAC/dac" in home_html
    assert row.title == ("E-ADMIT CARD FOR PST/DV/DME/RME FOR THE POST OF SUB INSPECTOR (HINDI TRANSLATOR) -2025 "
                         "link to download admit card")
    assert row.published_on == date(2026, 8, 7)
    assert (row.kind_hint, row.evidence["kind_hint"]) == ("ADMIT_CARD", "E-ADMIT CARD")
    assert row.post_names == ["SUB INSPECTOR (HINDI TRANSLATOR)"]

def test_ldce_final_result_and_answer_key(by_url):
    result = by_url[DOC + "Recruitment_result_291082026-187.pdf"]
    assert result.title == ("Selection of Assistant Commandant/GD (CAPFs) through Limited Departmental Competitive "
                            "Examination for the vacancy year-2025: Declaration of final result")
    assert result.published_on == date(2026, 8, 3)
    assert result.kind_hint == "RESULT"
    assert result.post_names == ["Assistant Commandant/GD (CAPFs)"]

    key = by_url[DOC + "483062025-981.pdf"]
    assert key.title == ("Answer key for the written examination for the selection to the post of Assistant Commandant "
                         "(GD) in CAPFs (KEY SET-1 PAPER-1 SERIES-D)")
    assert key.published_on == date(2025, 6, 11)
    assert (key.kind_hint, key.evidence["kind_hint"]) == ("ANSWER_KEY", "Answer key")
    assert key.post_names == ["Assistant Commandant (GD)"]

def test_generic_document_takes_posts_from_its_section_only(by_url):
    row = by_url[DOC + "793082025-582.pdf"]
    assert row.title == "Detailed Application Form"
    assert row.evidence["section"] == ("Selection of Assistant Commandant/GD (CAPFs) through Limited Departmental "
                                       "Competitive Examination for the vacancy year (s) 2024 and 2025")
    assert row.published_on == date(2025, 8, 12)
    assert row.kind_hint is None
    assert row.post_names == ["Assistant Commandant/GD (CAPFs)"]
    assert row.evidence["post_names"] == "Selection of Assistant Commandant/GD (CAPFs)"

def test_constable_technical_tradesmen_rows(by_url):
    amendment = by_url[DOC + "Recuitment Notice 112042026-256.pdf"]
    assert amendment.title == "Recruitment of Constable (Technical & Tradesmen and Pioneer)-2026 (Amendment dated 17/04/2026)"
    assert amendment.published_on == date(2026, 4, 17)
    assert (amendment.kind_hint, amendment.evidence["kind_hint"]) == ("CORRIGENDUM", "Amendment")

    notice = by_url[DOC + "Recruitment Notice 103042026-720.pdf"]
    assert notice.title == "Recruitment for the post of Constable (Technical & Tradesmen and Pioneer) (Male/Female)-2026 in CRPF"
    assert notice.published_on == date(2026, 4, 8)
    assert notice.kind_hint == "VACANCY"
    assert notice.post_names == ["Constable (Technical & Tradesmen and Pioneer) (Male/Female)"]

    # "<recruitment> :: Window for online application form correction ..." labels no kind of its own
    window = by_url[DOC + "Notice_12-05-2026-994.pdf"]
    assert window.title.startswith("Recruitment of Constable (Technical & Tradesmen and Pioneer)-2026 :: "
                                   "Window for online application form correction")
    assert window.published_on == date(2026, 5, 12)
    assert window.kind_hint is None

def test_walk_in_interview(by_url):
    row = by_url[DOC + "Annexure 1 & 2-976.pdf"]
    assert row.title == "WALK-IN-INTERVIEW IN CRPF FOR APPOINTMENT OF CLINICAL PHYSIOTHERAPIST ON CONTRACTUAL BASIS"
    assert row.evidence["url"] == "/Upload/Recruitment/Annexure 1 & 2-976.pdf"
    assert row.published_on == date(2025, 12, 12)
    assert (row.kind_hint, row.evidence["kind_hint"]) == ("VACANCY", "WALK-IN")
    assert row.post_names == ["CLINICAL PHYSIOTHERAPIST"]

def test_navigation_and_footer_links_are_skipped(rows):
    urls = {r.url for r in rows}
    for nav in ("https://rect.crpf.gov.in/Archived", "https://rect.crpf.gov.in", "https://crpf.gov.in/Recruitment-Rules",
                "https://crpf.gov.in/Vacancy-Position", "https://crpf.gov.in/Citizen-Charter", "https://crpf.gov.in/Help"):
        assert nav not in urls
    assert all(r.url.startswith(DOC) for r in rows)
    assert not any(r.title in ("Home", "Archived", "Recruitment Rules", "Help") for r in rows)

@pytest.mark.parametrize("which", ["home", "archived"])
def test_every_url_is_https_on_the_official_domain(which, rows, archived_rows):
    for row in rows if which == "home" else archived_rows:
        parsed = urlparse(row.url)
        assert parsed.scheme == "https"
        assert parsed.hostname == "rect.crpf.gov.in"
        assert row.url == urljoin(HOME_URL, row.evidence["url"])

@pytest.mark.parametrize("which", ["home", "archived"])
def test_every_filled_field_has_evidence_from_the_page(which, rows, archived_rows, home_html, archived_html):
    page_text = _squash(_text(home_html if which == "home" else archived_html))
    for row in rows if which == "home" else archived_rows:
        for name in FILLED:
            if getattr(row, name) not in (None, [], ""):
                assert row.evidence.get(name), (row.url, name)
        assert row.evidence["title"] == row.title
        # (the page splits some words across inline tags, e.g. "05<sup>th</sup>": compare without whitespace)
        assert _squash(row.title) in page_text
        if "section" in row.evidence:
            assert _squash(row.evidence["section"]) in page_text
        # Text-derived values are quoted from the row's own text or its section heading
        for name in ("kind_hint", "post_names", "advertisement_number"):
            if name in row.evidence:
                assert row.evidence[name] in row.title or row.evidence[name] in row.evidence.get("section", ""), (row.url, name)
        for post in row.post_names:
            assert post in row.evidence["post_names"]
        if row.advertisement_number:
            assert row.advertisement_number in row.evidence["advertisement_number"]

@pytest.mark.parametrize("which", ["home", "archived"])
def test_no_invented_dates(which, rows, archived_rows, home_html, archived_html):
    html = home_html if which == "home" else archived_html
    date_cells = set(re.findall(r'<td style="width:130px;">\s*(.*?)\s*</td>', html))
    for row in rows if which == "home" else archived_rows:
        assert row.last_date is None  # the listing has no closing-date column
        if row.published_on is None:
            assert "published_on" not in row.evidence
            continue
        shown = row.evidence["published_on"]
        assert shown in date_cells
        assert datetime.strptime(shown, "%d-%b-%Y").date() == row.published_on

def test_archived_page_same_layout(archived_rows, archived_html):
    table_rows = _table_rows(archived_html)
    assert len(table_rows) == 494
    # Rows whose only links are off the official domain (exam-agency portals) are dropped; a PDF listed twice is kept once
    official = []
    for _, hrefs in table_rows:
        urls = [urljoin(ARCHIVED_URL, h) for h in hrefs]
        official.append(next((u for u in urls if (urlparse(u).hostname or "").endswith("crpf.gov.in")), None))
    assert len(archived_rows) == len({u for u in official if u}) == 476
    assert {urlparse(u).hostname for _, hrefs in table_rows for u in hrefs if u.startswith("https://")} >= {
        "cdn.digialm.com", "www.crpfonline.com"}

def test_archived_rows_with_na_dates_stay_undated(archived_rows, archived_html):
    na_rows = [r for r in archived_rows if r.published_on is None]
    assert len(na_rows) == 38
    corrigendum = next(r for r in archived_rows if r.url == DOC + "AMENDMENTS/246_1/1_CORRIGENDUMADDENDUM.pdf")
    assert corrigendum.title == "CORRIGENDUM"
    assert corrigendum.published_on is None
    assert corrigendum.kind_hint == "CORRIGENDUM"
    assert corrigendum.evidence["section"] == "Recruitment for the Post of ASI (Steno) and HC (Ministerial)-2022 in CRPF"
    assert corrigendum.post_names == ["ASI (Steno)", "HC (Ministerial)"]

def test_archived_specific_rows(archived_rows):
    by_url = {r.url: r for r in archived_rows}
    architect = by_url[DOC + "COMDT_ARCH_RECT_NOTICE-452.pdf"]
    assert architect.title == "Release of vacancy of 01 Commandant (Architect) to conduct walk-in-interview for contractual appointment."
    assert architect.published_on == date(2026, 6, 4)
    assert architect.kind_hint == "VACANCY"
    assert architect.post_names == ["Commandant (Architect)"]

    engineers = by_url[DOC + "ADVERTISE/1_156_1_840082018.pdf"]
    assert engineers.advertisement_number == "01/2018"
    assert engineers.evidence["advertisement_number"] == "advertisement No. 01/2018"
    assert engineers.published_on == date(2018, 8, 31)
    assert engineers.post_names == ["DIG(Engr)", "Commandant(Architect)", "Commandant(Engr)",
                                    "Deputy Commandant(Engr)", "Assistant Commandant(Engr)"]
    # "dated 10/07/2018" is the advertisement's date, not a published or closing date of this row
    assert engineers.last_date is None

def test_page_without_listing_tables_gives_no_rows(home_html):
    # Everything outside the Document/Date tables (header, menus, footer) yields nothing
    start, end = home_html.index("<table"), home_html.rindex("</table>") + len("</table>")
    assert crpf.parse_listing(home_html[:start] + home_html[end:], HOME_URL) == []
