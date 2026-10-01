"""GPSC listing adapter, against pages captured live from gpsc.gujarat.gov.in (see fixtures/adapters/gpsc/meta.json)."""
import json
import re
from datetime import date
from html import unescape
from pathlib import Path
from urllib.parse import urlparse

import pytest

from govcareeros.backend.sources.adapters import adapter_for, all_adapters
from govcareeros.backend.sources.adapters import gpsc

FIXTURES = Path(__file__).parent.parent / "fixtures" / "adapters" / "gpsc"
META = json.loads((FIXTURES / "meta.json").read_text(encoding="utf-8"))
PAGES = list(META["files"])

def _page(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")

def _rows(name: str):
    return gpsc.parse_listing(_page(name), META["files"][name]["url"])

def _by_url(rows):
    return {r.url: r for r in rows}

def _page_text(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html)))

_MONTHS = {m: i for i, m in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}

def _dates_in(text: str):
    """Every date written in `text` in the page's own formats: 24-09-2026, 29 Sep 2026, Sep 29 2026."""
    found = {date(int(y), int(m), int(d)) for d, m, y in re.findall(r"\b(\d{2})-(\d{2})-(\d{4})\b", text)}
    for d, mon, y in re.findall(r"\b(\d{1,2}) ([A-Z][a-z]{2}) (\d{4})\b", text):
        found.add(date(int(y), _MONTHS[mon], int(d)))
    for mon, d, y in re.findall(r"\b([A-Z][a-z]{2}) (\d{1,2}) (\d{4})\b", text):
        found.add(date(int(y), _MONTHS[mon], int(d)))
    return found

def test_registered_and_listing_urls_are_official():
    assert adapter_for("GPSC") is gpsc
    assert all_adapters()["GPSC"] is gpsc
    for url in gpsc.LISTING_URLS:
        assert urlparse(url).scheme == "https" and urlparse(url).hostname == "gpsc.gujarat.gov.in"
    for meta in META["files"].values():
        assert meta["captured_at"] and meta["fetch_mode"] == "HTTP" and meta["http_status"] == 200

# ------------------------------------------------------------------ advertisement dashboard

def test_dashboard_advertisement_tab_rows():
    rows = _rows("dashboard_advertisement.html")
    assert len(rows) == 110                       # 56 + 40 + 11 + 3 rows in the four stage tabs
    assert sum(r.kind_hint == "VACANCY" for r in rows) == 56
    by_url = _by_url(rows)

    r = by_url["https://gpsc.gujarat.gov.in/AdvertisementDetail?no=2380&tab="]
    assert r.title == "Assitant Port Officer, Class-2, Gujarat Maritime Board"
    assert r.advertisement_number == "52/2026-27"
    assert r.post_names == ["Assitant Port Officer, Class-2, Gujarat Maritime Board"]
    assert r.last_date == date(2026, 10, 8)
    assert r.evidence["last_date"] == "24-09-2026 02:00 PM - 08-10-2026 11:59 PM"
    assert r.kind_hint == "VACANCY" and r.evidence["kind_hint"] == "Advertisement"
    assert r.published_on is None                 # the page shows only "Updated on", not a publication date

    r = by_url["https://gpsc.gujarat.gov.in/AdvertisementDetail?no=2329&tab="]
    assert r.advertisement_number == "1/2026-27"
    assert r.title.startswith("Gujarat Engineering Service (Civil), Class-1 and Class-2")
    assert r.last_date == date(2026, 4, 15)
    assert r.evidence["last_date"] == "01-04-2026 01:00 PM - 15-04-2026 11:59 PM"

def test_dashboard_later_stage_rows():
    by_url = _by_url(_rows("dashboard_advertisement.html"))

    exam = by_url["https://gpsc.gujarat.gov.in/AdvertisementDetail?no=2329&tab=Exam"]
    assert exam.advertisement_number == "1/2026-27"
    assert exam.title.startswith("Exam - Gujarat Engineering Service (Civil)")
    # the row shows exam/call-letter dates, none of which is a publication or application closing date
    assert exam.published_on is None and exam.last_date is None and exam.kind_hint is None

    result = by_url["https://gpsc.gujarat.gov.in/AdvertisementDetail?no=2329&tab=Result"]
    assert result.kind_hint == "RESULT" and result.evidence["kind_hint"] == "PT / Mains Results"
    assert result.published_on is None

    final = by_url["https://gpsc.gujarat.gov.in/AdvertisementDetail?no=2349&tab=Interview"]
    assert final.advertisement_number == "12/2026-27"
    assert final.title == ("Interviews / Final Results - Manager, Grade-1, Class-2, Road and Building Department "
                           "(Special Recruitment-PwD-Third Attempt)")
    assert final.kind_hint == "RESULT" and final.evidence["kind_hint"] == "Interviews / Final Results"
    assert final.published_on == date(2026, 6, 17) and final.evidence["published_on"] == "17-06-2026"

    # "Final Result Declared on" is empty for 22/2026-27: no date is filled in
    law = by_url["https://gpsc.gujarat.gov.in/AdvertisementDetail?no=2350&tab=Interview"]
    assert law.advertisement_number == "22/2026-27"
    assert law.published_on is None and law.last_date is None

# ------------------------------------------------------------------ news list

def test_news_list_rows():
    rows = _rows("newseventlist.html")
    assert len(rows) == 2059                      # 2124 list items, some NewsIDs listed twice
    by_url = _by_url(rows)
    url = "https://gpsc.gujarat.gov.in/newseventdetail?NewsID={}".format

    r = by_url[url(28916)]
    assert r.title == ("Advertisement - 42/2026-27 Detailed Advertisement (Marine Engineer, Class-1, "
                       "Gujarat Maritime Board(Special Recruitment Drive))")
    assert r.advertisement_number == "42/2026-27"
    assert r.published_on == date(2026, 9, 24) and r.evidence["published_on"] == "24 Sep 2026"
    assert r.post_names == ["Marine Engineer, Class-1, Gujarat Maritime Board(Special Recruitment Drive)"]
    assert r.kind_hint == "VACANCY" and r.evidence["kind_hint"] == "Detailed Advertisement"

    r = by_url[url(28933)]
    assert r.title == "Recommendation - 28/2024-25 From Waiting List (Forth List)"
    assert r.advertisement_number == "28/2024-25"
    assert r.published_on == date(2026, 9, 29)
    assert r.post_names == []                     # "(Forth List)" is not a post

    r = by_url[url(28917)]
    assert r.kind_hint == "CORRIGENDUM" and r.evidence["kind_hint"] == "Corrigendum"
    assert r.advertisement_number == "113/2024-25"
    assert r.post_names == ["Assistant Engineer (Civil), Class-2, Road and Building Department"]

    r = by_url[url(27375)]
    assert r.title == "Final Result - 33/2025-26 (Psychiatrist (Specialist Service), Class 1, Health and Medical Services)"
    assert r.kind_hint == "RESULT" and r.published_on == date(2026, 2, 25)

    r = by_url[url(28921)]
    assert r.kind_hint == "ADMIT_CARD" and r.evidence["kind_hint"] == "Call Letter"
    assert r.advertisement_number == "82/2024-25"

    # Titles naming several advertisements get none; dates inside a title are not taken as listing dates
    r = by_url[url(28876)]
    assert r.title.startswith("Newspaper Format Advertisement for Online Advertisements from Advt. No. 42/2026-27 to 56/2026-27")
    assert r.advertisement_number is None and r.last_date is None
    assert r.published_on == date(2026, 9, 23)
    assert by_url[url(28721)].advertisement_number is None
    assert by_url[url(28611)].advertisement_number is None   # "Advertisement No. 51/2024-25 ... and Advertisement No. 77/2024- 25"

    assert all(r.last_date is None for r in rows)

# ------------------------------------------------------------------ home page

def test_home_page_news_box_only():
    rows = _rows("home.html")
    assert len(rows) == 46
    by_url = _by_url(rows)

    r = by_url["https://gpsc.gujarat.gov.in/newseventdetail?NewsID=28683"]
    assert r.title == ("Provisional AnswerKey of Common General Studies Exam of Advt. No. 9993/2026-27 "
                       "(34 Advertisements), Exam held on 09 August,2026")
    assert r.advertisement_number == "9993/2026-27" and r.evidence["advertisement_number"] == "Advt. No. 9993/2026-27"
    assert r.kind_hint == "ANSWER_KEY"
    assert r.published_on == date(2026, 8, 17) and r.evidence["published_on"] == "Aug 17 2026"

    r = by_url["https://gpsc.gujarat.gov.in/newseventdetail?NewsID=15550"]
    assert r.published_on == date(2021, 10, 25) and r.evidence["published_on"] == "Oct 25 2021"

    # menus, footer and the gpsc-ojas "Current Advertisement" box (outside the official domain) are skipped
    assert all("/newseventdetail?NewsID=" in r.url for r in rows)
    assert "https://gpsc.gujarat.gov.in/aboutgpsc" not in by_url

# ------------------------------------------------------------------ invariants over every captured page

@pytest.mark.parametrize("name", PAGES)
def test_urls_official_and_unique(name):
    rows = _rows(name)
    assert rows
    for r in rows:
        parts = urlparse(r.url)
        assert parts.scheme == "https" and parts.hostname == "gpsc.gujarat.gov.in", r.url
    assert len({r.url for r in rows}) == len(rows)

@pytest.mark.parametrize("name", PAGES)
def test_every_filled_field_has_page_evidence(name):
    text = _page_text(_page(name))
    for r in _rows(name):
        # dashboard rows of later stages are titled "<stage tab> - <post>"; both parts are page text
        assert r.title and all(part in text for part in (
            [r.title] if r.title in text else r.title.split(" - ", 1))), r.title
        filled = {f for f in ("advertisement_number", "published_on", "last_date", "kind_hint") if getattr(r, f)}
        if r.post_names:
            filled.add("post_names")
        assert filled == set(r.evidence), (r.url, r.evidence)
        for value in r.evidence.values():
            assert value and value in text, value
        if r.advertisement_number:
            assert r.advertisement_number in r.evidence["advertisement_number"]
        for post in r.post_names:
            assert post in r.evidence["post_names"]

@pytest.mark.parametrize("name", PAGES)
def test_no_invented_dates(name):
    for r in _rows(name):
        for field in ("published_on", "last_date"):
            value = getattr(r, field)
            if value is not None:
                assert value in _dates_in(r.evidence[field]), (r.url, field, value, r.evidence[field])
