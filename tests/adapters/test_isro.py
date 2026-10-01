"""ISRO listing adapter against the live careers pages captured on 2026-10-01 (see fixtures/adapters/isro/meta.json)."""
import json
import re
from datetime import date
from html import unescape
from pathlib import Path
from urllib.parse import urlparse

import pytest

from govcareeros.backend.sources.adapters import adapter_for, isro
from govcareeros.backend.sources.registry import SOURCES
from govcareeros.backend.sources.url_guard import UrlPolicy

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "adapters" / "isro"
CAREERS_URL = "https://www.isro.gov.in/Careers.html"
VIEW_ALL_URL = "https://www.isro.gov.in/ViewAllOpportunities.html"
SITE = "https://www.isro.gov.in/"
PAGES = {"careers.html": CAREERS_URL, "view_all_opportunities.html": VIEW_ALL_URL}

def _read(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")

@pytest.fixture(scope="module")
def careers_html():
    return _read("careers.html")

@pytest.fixture(scope="module")
def view_all_html():
    return _read("view_all_opportunities.html")

@pytest.fixture(scope="module")
def board(careers_html):
    return isro.parse_listing(careers_html, CAREERS_URL)

@pytest.fixture(scope="module")
def table(view_all_html):
    return isro.parse_listing(view_all_html, VIEW_ALL_URL)

@pytest.fixture(scope="module")
def all_rows(board, table, careers_html, view_all_html):
    """(row, raw page html) for both pages."""
    return [(r, careers_html) for r in board] + [(r, view_all_html) for r in table]

def _one(rows, **match):
    found = [r for r in rows if all(getattr(r, k) == v for k, v in match.items())]
    assert len(found) == 1, (match, [r.title for r in found])
    return found[0]

def _squash(text: str) -> str:
    """Page text with tags and all whitespace removed, to look evidence up independently of the adapter."""
    return re.sub(r"\s+", "", unescape(re.sub(r"<[^>]+>", "", text)))

# ---------- capture / registration ----------

def test_fixture_meta_records_the_live_capture():
    meta = json.loads((FIXTURE_DIR / "meta.json").read_text(encoding="utf-8"))
    assert meta["source"] == "ISRO"
    assert meta["captured_on"] == "2026-10-01"
    for name, url in PAGES.items():
        entry = meta["files"][name]
        assert entry["url"] == url and entry["final_url"] == url
        assert entry["http_status"] == 200
        assert entry["fetch_mode"] == "HTTP"
        assert entry["captured_at"].startswith("2026-10-01T")
        assert (FIXTURE_DIR / name).exists()

def test_adapter_is_auto_discovered_under_the_registry_name():
    assert adapter_for("ISRO") is isro
    spec = next(s for s in SOURCES if s.name == "ISRO")
    assert spec.recruitment_url == CAREERS_URL
    # The extra listing page is on the same official site and is the one Careers.html links to
    assert isro.LISTING_URLS == [VIEW_ALL_URL]
    policy = UrlPolicy.from_url(CAREERS_URL)
    assert all(policy.allows(u) for u in isro.LISTING_URLS)

def test_careers_page_links_the_extra_listing_page(careers_html):
    assert 'href="ViewAllOpportunities.html"' in careers_html

# ---------- notice board (Careers.html) ----------

def test_one_row_per_linked_notice_on_the_board(board, careers_html):
    table_html = careers_html[careers_html.index('<tbody class="list">'):careers_html.index("</tbody>")]
    cells = re.findall(r'<td class="news">(.*?)</td>', table_html, re.S)
    assert len(cells) == 31
    linked = [c for c in cells if re.search(r"<a\b", c) and "pageContent" not in c]
    assert len(linked) == 28
    assert len(board) == 28
    # two plain-text notices have no link; one cell is the "Click here" navigation
    assert sum(1 for c in cells if not re.search(r"<a\b", c)) == 2
    assert sum(1 for c in cells if "pageContent" in c) == 1

def test_board_skips_navigation_and_linkless_notices(board):
    urls = {r.url for r in board}
    for nav in ("CurrentOpportunities.html", "ViewAllOpportunities.html", "Fellowships.html", "index.html"):
        assert SITE + nav not in urls
    assert not any(r.title.lower() in ("here", "read more") for r in board)
    assert not any("Due to huge response" in r.title for r in board)
    assert not any("written test for the post of Scientist/Engineer" in r.title for r in board)
    assert not any(r.title.endswith("Read More") for r in board)

def test_board_new_advertisement_row(board):
    row = _one(board, url=SITE + "LPSCRecruitment14.html")
    assert row.title == "Advt. No. LPSC/03/2026 dated 26.09.2026 - Recruitment to the post of Scientist / Engineer 'SC'"
    assert row.advertisement_number == "LPSC/03/2026"
    assert row.published_on == date(2026, 9, 26)
    assert row.last_date is None
    assert row.kind_hint == "VACANCY"
    assert row.post_names == ["Scientist / Engineer 'SC'"]
    assert row.evidence == {
        "title": row.title,
        "url": "LPSCRecruitment14.html",
        "kind_hint": "Recruitment to",
        "advertisement_number": "Advt. No. LPSC/03/2026",
        "published_on": "Advt. No. LPSC/03/2026 dated 26.09.2026",
        "post_names": "post of Scientist / Engineer 'SC'",
    }

def test_board_lists_posts_and_named_month_dates(board):
    iprc = _one(board, url=SITE + "IPRCRecruitment4.html")
    assert iprc.advertisement_number == "IPRC/RMT/2026/01"
    assert iprc.published_on == date(2026, 9, 12)
    assert iprc.post_names == ["Technical Assistant", "Technician 'B'", "Cook 'A'", "Fireman 'A'"]

    sdsc = _one(board, url=SITE + "SDSCRecruitment25.html")
    assert sdsc.advertisement_number == "SDSC SHAR/RMT/02/2026"
    assert sdsc.published_on == date(2026, 6, 23)
    assert sdsc.evidence["published_on"] == "Advt. No. SDSC SHAR/RMT/02/2026 dated June 23, 2026"

    hsfc = _one(board, url=SITE + "HSFCRecruitment2026_1.html")
    assert hsfc.advertisement_number == "HSFC:01:RMT:2026"
    assert hsfc.published_on == date(2026, 8, 10)
    assert hsfc.evidence["published_on"] == "Advt. No. HSFC:01:RMT:2026 Dated.10.08.2026"

    nesac = _one(board, url=SITE + "NESACRecruitment23.html")
    assert nesac.advertisement_number == "NESAC/RMT-TEMP/WI-02/2026"
    assert nesac.post_names == ["Research Scientists", "Junior Research Fellows", "Project Associate - I"]

def test_board_closing_date_only_where_the_text_states_it(board):
    ursc = _one(board, url=SITE + "URSCRecruitment12.html")
    assert ursc.advertisement_number == "URSC:03:2026"
    assert ursc.published_on == date(2026, 7, 29)
    assert ursc.last_date == date(2026, 8, 28)
    assert ursc.evidence["last_date"] == "ACTIVE FROM 29.07.2026 TO 28.08.2026"
    assert [r.url for r in board if r.last_date] == [ursc.url]

def test_board_updates_carry_kind_but_not_the_advertisement_date(board):
    admit = _one(board, url=SITE + "ICRB_Recruitment11.html", kind_hint="ADMIT_CARD")
    assert admit.title.startswith("Admit Card Download Link – CBT For Recruitment Of Assistants")
    assert admit.advertisement_number == "ISRO:ICRB:01(A-JPA):2026"
    assert admit.evidence["advertisement_number"] == "Advertisement No.: ISRO:ICRB:01(A-JPA):2026"
    # "dated 27-07-2026" is the advertisement's date, "Held on 21-09-2026" the exam's: neither is this notice's
    assert admit.published_on is None and admit.last_date is None

    corr = _one(board, url=SITE + "ICRB_Recruitment11.html", kind_hint="CORRIGENDUM")
    assert corr.title.startswith("CORRIGENDUM 2: ADVT. NO. ISRO:ICRB:01(A-JPA):2026 dated 27-07-2026")
    assert corr.evidence["kind_hint"] == "CORRIGENDUM 2:"
    assert corr.published_on is None

    revised = _one(board, url=SITE + "NESACRecruitment24.html", kind_hint="CORRIGENDUM")
    assert revised.title.startswith("Revised: Advt. No. DS_8C-12012/1/2026-Sec.VIII dated 30/09/2026")
    original = _one(board, url=SITE + "NESACRecruitment24.html", kind_hint="VACANCY")
    assert original.published_on == date(2026, 9, 30)  # "dated 30/09/2026", day first
    assert original.post_names == ["Director"]

    panel = _one(board, url=SITE + "URSCRecruitment2026_1.html", kind_hint="RESULT")
    assert panel.title == "Selection Panel for the post of Medical Officer- 'SC' against Advertisement No. URSC:01:2026 dated 21.03.2026."
    assert panel.advertisement_number == "URSC:01:2026"
    assert panel.evidence["kind_hint"] == "Selection Panel"
    assert panel.published_on is None

    cbt = _one(board, url=SITE + "ICRB_Recruitment13.html", kind_hint="SCHEDULE")
    assert cbt.title == "Advt. No. ISRO:ICRB:03(EMC):2026 Dated 27-08-2026 - Date of conducting CBT - 19th October, 2026"
    assert cbt.advertisement_number == "ISRO:ICRB:03(EMC):2026"
    assert cbt.published_on is None and cbt.last_date is None

    screened = [r for r in board if r.url == SITE + "ICRB_Recruitment12.html"]
    assert len(screened) == 2  # listed twice with different wording ("screened in" / "screened-in")
    assert all(r.kind_hint == "RESULT" and r.advertisement_number == "ISRO:ICRB:02(EMC-CEPO):2026" for r in screened)

def test_board_rows_without_a_label_get_no_kind(board):
    row = _one(board, title="ADVT NO. ISRO:ICRB:01(A-JPA):2026 DATED 27.07.2026 - Link for change of Post & Zone preference")
    assert row.kind_hint is None and row.published_on is None
    assert row.advertisement_number == "ISRO:ICRB:01(A-JPA):2026"

# ---------- opportunities table (ViewAllOpportunities.html) ----------

def test_one_row_per_opportunity_in_the_table(table, view_all_html):
    body = view_all_html[view_all_html.index('<tbody class="list">'):view_all_html.index("</tbody>")]
    targets = re.findall(r"onclick=\"\s*window\.location\.href\s*=\s*'([^']+)'", body)
    assert len(targets) == len(set(targets)) == 123
    assert len(table) == 123
    assert [r.evidence["url"] for r in table] == targets

def test_table_row_with_both_dates(table):
    row = _one(table, url=SITE + "LPSCRecruitment14.html")
    assert row.title == "Recruitment to the post of Scientist / Engineer 'SC'"
    assert row.advertisement_number == "LPSC/03/2026"
    assert row.published_on == date(2026, 9, 29)
    assert row.last_date == date(2026, 10, 13)
    assert row.kind_hint == "VACANCY"
    assert row.post_names == ["Scientist / Engineer 'SC'"]
    assert row.evidence == {
        "title": row.title,
        "url": "LPSCRecruitment14.html",
        "advertisement_number": "LPSC/03/2026",
        "published_on": "September 29, 2026",
        "last_date": "October 13, 2026",
        "post_names": "post of Scientist / Engineer 'SC'",
        "kind_hint": "Recruitment to",
    }

def test_table_reads_the_pages_own_date_spellings(table):
    iprc = _one(table, url=SITE + "IPRCRecruitment4.html")
    assert (iprc.evidence["published_on"], iprc.published_on) == ("Sept 15,2026", date(2026, 9, 15))
    assert (iprc.evidence["last_date"], iprc.last_date) == ("Oct 05,2026", date(2026, 10, 5))
    assert iprc.advertisement_number == "IPRC/RMT/2026/01"

    nesac = _one(table, url=SITE + "NESACRecruitment24.html")
    assert (nesac.evidence["published_on"], nesac.published_on) == ("September 30,2026", date(2026, 9, 30))
    assert nesac.advertisement_number == "DS_8C-12012/1/2026-Sec.VIII"

    dos = _one(table, url=SITE + "DOSRecruitment9.html")
    assert (dos.published_on, dos.last_date) == (date(2026, 8, 12), date(2026, 9, 12))
    assert dos.post_names == ["Director (Technical & Strategy)"]

    # numeric dd/mm/yyyy: 05/08/2022 is 5 August (the same advertisement's other row says "Aug 05, 2022")
    iist = _one(table, url=SITE + "DOSRecruitmentdirector_iist.html")
    assert iist.published_on is None
    assert (iist.evidence["last_date"], iist.last_date) == ("05/08/2022", date(2022, 8, 5))
    assert _one(table, url=SITE + "iistdirec13july.html").published_on == date(2022, 8, 5)

def test_table_never_invents_a_missing_or_vague_date(table):
    # "Last Date of Submission" cell is empty
    nesac = _one(table, url=SITE + "NESACRecruitment23.html")
    assert nesac.last_date is None and "last_date" not in nesac.evidence
    assert nesac.published_on == date(2026, 7, 17)
    # "Opening Date" reads "September / October 2022." - not a date
    ursc = _one(table, url=SITE + "iisacadvtnoURSC012020.html")
    assert ursc.published_on is None and "published_on" not in ursc.evidence
    assert ursc.last_date == date(2020, 3, 6)
    assert sum(1 for r in table if r.last_date is None) == 6
    assert sum(1 for r in table if r.published_on is None) == 2

def test_table_advertisement_numbers(table):
    vssc = _one(table, url=SITE + "VSSCRecruitment37.html")
    assert vssc.advertisement_number == "VSSC-335"
    assert vssc.evidence["advertisement_number"] == "VSSC-335 DATED 31.05.2025"
    dos = _one(table, url=SITE + "DOSRecruitment7.html")
    assert dos.advertisement_number == "E.28011/1/2015-V(VIII) (Vol.II)"
    assert dos.evidence["advertisement_number"] == "No.E.28011/1/2015-V(VIII) (Vol.II)"
    assert _one(table, url=SITE + "ICRB_Recruitment11.html").advertisement_number == "ISRO:ICRB:01(A-JPA):2026"
    assert all(r.advertisement_number for r in table)

def test_table_post_names(table):
    assert _one(table, url=SITE + "ICRB_Recruitment11.html").post_names == [
        "Assistants", "Junior Personal Assistants", "Upper Division Clerk", "Stenographers"]
    # a Post cell that is only the post's name
    hsfc = _one(table, url=SITE + "HSFCRecruitment2026_1.html")
    assert hsfc.post_names == ["Scientist/Engineer - SD"] and hsfc.kind_hint is None
    # the list ends where the cell turns to the disciplines / organisation
    assert _one(table, url=SITE + "ICRB_Recruitment10.html").post_names == ["Scientist/Engineer 'SC'"]
    assert _one(table, url=SITE + "NSILRecruitment2.html").post_names == ["Chairman-cum-Managing Director"]
    assert _one(table, url=SITE + "DOSRecruitment4.html").post_names == ["Director (Technical & Stategy)"]
    # a shared name is not split
    assert _one(table, url=SITE + "NRSCRecruitment2023_2.html").post_names == ["Graduate and Technician Apprentices"]
    # no post named: "Recruitment to various Administrative posts at LPSC Valiamala/Bengaluru"
    assert _one(table, url=SITE + "LPSCRecruitment1.html").post_names == []

# ---------- every row ----------

def test_all_urls_are_absolute_on_the_official_domain(all_rows):
    policy = UrlPolicy.from_url(CAREERS_URL)
    for row, _ in all_rows:
        parsed = urlparse(row.url)
        assert parsed.scheme == "https" and parsed.hostname == "www.isro.gov.in", row.url
        assert policy.allows(row.url)

def test_every_filled_field_has_evidence_from_the_page(all_rows):
    for row, html in all_rows:
        page = _squash(html)
        assert row.title and row.evidence["title"] == row.title
        assert f'"{row.evidence["url"]}' in html or f"'{row.evidence['url']}'" in html
        for name in ("advertisement_number", "published_on", "last_date", "kind_hint"):
            filled = getattr(row, name) is not None
            assert filled == (name in row.evidence), (row.url, name)
        assert bool(row.post_names) == ("post_names" in row.evidence), row.url
        for name, text in row.evidence.items():
            if name != "url":
                assert re.sub(r"\s+", "", text) in page, (row.url, name, text)
        if row.advertisement_number:
            assert row.advertisement_number in row.evidence["advertisement_number"]
        for post in row.post_names:
            assert post in row.evidence["post_names"]

_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]

def _dates_in(text: str):
    """Every date written in `text`, read independently of the adapter (day-first numeric, Month d, yyyy)."""
    found = set()
    for d, m, y in re.findall(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})", text):
        found.add(date(int(y), int(m), int(d)))
    for mon, d, y in re.findall(r"([A-Za-z]{3,9})\.?\s*(\d{1,2})\s*,\s*(\d{4})", text):
        if mon[:3].lower() in _MONTHS:
            found.add(date(int(y), _MONTHS.index(mon[:3].lower()) + 1, int(d)))
    return found

def test_no_row_has_an_invented_date(all_rows):
    dated = 0
    for row, _ in all_rows:
        for name in ("published_on", "last_date"):
            value = getattr(row, name)
            if value is None:
                continue
            dated += 1
            assert value in _dates_in(row.evidence[name]), (row.url, name, value, row.evidence[name])
            assert date(2000, 1, 1) < value < date(2027, 1, 1)
    assert dated > 200

def test_parsing_is_deterministic(careers_html, view_all_html, board, table):
    assert isro.parse_listing(careers_html, CAREERS_URL) == board
    assert isro.parse_listing(view_all_html, VIEW_ALL_URL) == table
