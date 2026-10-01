"""DRDO listing adapter against the live pages captured on 2026-10-01 (see fixtures/adapters/drdo/meta.json)."""
import hashlib
import json
import re
from datetime import date
from html import unescape
from pathlib import Path
from urllib.parse import urlparse

import pytest

from govcareeros.backend.sources.adapters import adapter_for, drdo
from govcareeros.backend.sources.registry import SOURCES
from govcareeros.backend.sources.url_guard import UrlPolicy

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "adapters" / "drdo"
HOME_URL = "https://drdo.gov.in/drdo/en/"
PAGE1_URL = "https://drdo.gov.in/drdo/en/offerings/vacancies"
PAGE2_URL = "https://drdo.gov.in/drdo/en/offerings/vacancies?page=1"
VAC = "https://drdo.gov.in/drdo/en/offerings/vacancies/"
PAGES = {"home.html": HOME_URL, "vacancies.html": PAGE1_URL, "vacancies_page2.html": PAGE2_URL}
DATE_FIELDS = ("published_on", "last_date")

def _read(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")

def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()

@pytest.fixture(scope="module")
def parsed():
    return {name: drdo.parse_listing(_read(name), url) for name, url in PAGES.items()}

@pytest.fixture(scope="module")
def cards():
    """The Vacancies pages' cards, read independently of the adapter: url slug -> {label: shown text}."""
    out = {}
    for name in ("vacancies.html", "vacancies_page2.html"):
        for box in _read(name).split('<div class="vacanciess-box">')[1:]:
            box = box.split("</li>")[0]
            def value(cls):
                m = re.search(r'<div class="' + cls + r'">(.*?)</div>', box, re.S)
                return _text(m.group(1)) if m else None
            slug = re.search(r'<a href="http://drdo\.gov\.in/drdo/en/offerings/vacancies/([^"]+)"', box).group(1)
            out[slug] = {"title": value("vacanciess-title"), "advt": value("vacanciess-advertisment-no-content"),
                         "published": value("vacanciess-date-publish-content"),
                         "start": value("vacanciess-start-date-content"), "end": value("vacanciess-due-date-content")}
    return out

def _by_url(rows):
    return {r.url: r for r in rows}

def test_fixtures_are_the_recorded_live_captures():
    meta = json.loads(_read("meta.json"))
    assert meta["source"] == "DRDO" and meta["captured_on"] == "2026-10-01" and meta["fetch_mode"] == "HTTP"
    recorded = {f["file"]: f for f in meta["files"]}
    assert {name: f["url"] for name, f in recorded.items()} == PAGES
    for name, f in recorded.items():
        assert f["captured_on"] == "2026-10-01" and f["fetch_mode"] == "HTTP" and f["http_status"] == 200
        # git may check text files out with CRLF; the capture itself had LF line endings only
        data = (FIXTURE_DIR / name).read_bytes().replace(b"\r\n", b"\n")
        assert hashlib.sha256(data).hexdigest() == f["sha256"]

def test_discovered_and_registered():
    assert adapter_for("DRDO") is drdo
    spec = next(s for s in SOURCES if s.name == "DRDO")
    assert urlparse(spec.recruitment_url).hostname == "drdo.gov.in"
    policy = UrlPolicy.from_url(spec.recruitment_url)
    assert drdo.LISTING_URLS == [PAGE1_URL, PAGE2_URL]
    assert all(policy.allows(u) for u in drdo.LISTING_URLS)

def test_vacancies_page_one_card_per_row(parsed, cards):
    page1, page2 = parsed["vacancies.html"], parsed["vacancies_page2.html"]
    assert len(page1) == 12 and len(page2) == 2
    assert sorted(r.url for r in page1 + page2) == sorted(VAC + slug for slug in cards)
    for r in page1 + page2:
        assert r.title == cards[r.url[len(VAC):]]["title"]

def test_advertisement_with_dates_and_no_advt_number(parsed):
    row = _by_url(parsed["vacancies.html"])[VAC + "advertisement-post-director-dia-coe-bhu"]
    assert row.title == "Advertisement for the Post of Director, DIA-CoE, BHU"
    assert row.advertisement_number is None            # the card's "Advertisement No" box is empty
    assert row.published_on == date(2026, 9, 30) and row.evidence["published_on"] == "30/09/2026"
    assert row.last_date == date(2026, 10, 30) and row.evidence["last_date"] == "30/10/2026"
    assert row.kind_hint == "VACANCY" and row.evidence["kind_hint"] == "Advertisement"
    # "Director, DIA-CoE, BHU": the commas belong to the post, so the names cannot be read off reliably
    assert row.post_names == [] and "post_names" not in row.evidence
    assert row.evidence["url"] == "http://drdo.gov.in/drdo/en/offerings/vacancies/advertisement-post-director-dia-coe-bhu"

def test_result_row_has_no_closing_date(parsed, cards):
    slug = "list-selected-candidates-jrf-interview-held-15-sep-16-sep-2026-dgre-chandigarh"
    row = _by_url(parsed["vacancies.html"])[VAC + slug]
    assert row.title == "List of Selected candidates of JRF Interview held on 15 Sep & 16 Sep 2026 at DGRE, Chandigarh"
    assert row.advertisement_number == "DGRE-01/JRF-RA/01/2026-27"
    assert row.published_on == date(2026, 9, 29)
    assert row.kind_hint == "RESULT" and row.evidence["kind_hint"] == "Selected candidates"
    # The card's End Date (15/10/2026) is how long the list stays up, not an application deadline
    assert cards[slug]["end"] == "15/10/2026" and row.last_date is None and "last_date" not in row.evidence

    result = _by_url(parsed["vacancies.html"])[VAC + "result-walk-interview-held-02nd-sept-2026-ra-and-jrf-dmsrde-kanpur"]
    assert result.title == "Result of Walk in interview held on 02nd Sept 2026 for RA and JRF at DMSRDE, Kanpur"
    assert result.kind_hint == "RESULT" and result.advertisement_number is None
    assert result.published_on == date(2026, 9, 22) and result.last_date is None

def test_position_list_and_walk_in_rows(parsed):
    rows = _by_url(parsed["vacancies.html"])
    chair = rows[VAC + "advertisement-position-drdo-chair-drdo-distinguished-fellowship-and-drdo"]
    assert chair.advertisement_number == "DRDO/DHR&V/C&F-2026-01"
    assert (chair.published_on, chair.last_date) == (date(2026, 9, 15), date(2026, 10, 16))
    assert chair.post_names == ["DRDO Chair", "DRDO Distinguished Fellowship", "DRDO Fellowship"]
    assert chair.evidence["post_names"] == "the position of DRDO Chair, DRDO Distinguished Fellowship and DRDO Fellowship"

    mtrdc = rows[VAC + "mtrdc-bengaluru-invites-eligible-candidates-walk-interview-post-jrf"]
    assert mtrdc.title == "MTRDC, Bengaluru invites eligible candidates for walk in interview for the post of JRF"
    assert mtrdc.advertisement_number == "MTRDC/RF/RECT/2026/02"
    assert (mtrdc.published_on, mtrdc.last_date) == (date(2026, 9, 16), date(2026, 10, 15))
    assert mtrdc.post_names == ["JRF"] and mtrdc.kind_hint == "VACANCY"

    lrde = rows[VAC + "lrde-bengaluru-invites-eligible-candidates-walk-interview-post-junior-research"]
    assert lrde.advertisement_number == "03/2026/JRF"
    assert lrde.post_names == ["Junior Research Fellowship"]          # "-2026" is the year, not the post
    assert lrde.evidence["post_names"] == "the post of Junior Research Fellowship-2026"

    pxe = rows[VAC + "pxe-balasore-invites-eligible-candidates-engagement-apprentices-under"]
    assert pxe.title == ("PXE, Balasore invites eligible candidates for the engagement of Apprentices "
                         "(Under Apprentices Act 1961 as amended in 1961, 1973, 2014, 2025)")
    assert pxe.advertisement_number == "PXE/HRD/AT/01/2026-27"
    assert (pxe.published_on, pxe.last_date) == (date(2026, 9, 23), date(2026, 10, 12))

def test_second_page(parsed):
    rows = _by_url(parsed["vacancies_page2.html"])
    ceptam = rows[VAC + "ceptam-11-advertisement-post-sta-b-and-technician"]
    assert ceptam.title == "CEPTAM-11 Advertisement for the post of STA-B and Technician-A"
    assert ceptam.advertisement_number == "CEPTAM-11"
    assert ceptam.published_on == date(2025, 12, 11) and ceptam.evidence["published_on"] == "11/12/2025"
    assert ceptam.last_date == date(2026, 12, 31) and ceptam.evidence["last_date"] == "31/12/2026"
    assert ceptam.post_names == ["STA-B", "Technician-A"] and ceptam.kind_hint == "VACANCY"

    vrde = rows[VAC + "vrde-ahilyanagar-invites-eligible-candidates-walk-interview-post-junior"]
    assert vrde.advertisement_number == "VRDE/H&HC/HRD/811/Fellowship"
    assert vrde.post_names == ["Junior Research Fellowship (JRF)"]
    assert (vrde.published_on, vrde.last_date) == (date(2026, 8, 31), date(2026, 10, 7))

def test_homepage_ticker_titles_and_links_only(parsed):
    home = parsed["home.html"]
    listed = parsed["vacancies.html"] + parsed["vacancies_page2.html"]
    assert len(home) == 14
    assert [r.url for r in home] == [r.url for r in listed]        # same view, same order
    assert [r.title for r in home] == [r.title for r in listed]
    first = home[0]
    assert first.evidence["url"] == "/drdo/en/offerings/vacancies/advertisement-post-director-dia-coe-bhu"
    for r in home:
        # the ticker shows no dates or advertisement numbers
        assert r.published_on is None and r.last_date is None and r.advertisement_number is None
    assert not any(r.title == "View All" for r in home)

def test_urls_on_official_domain(parsed):
    policy = UrlPolicy.from_url(HOME_URL)
    for rows in parsed.values():
        assert len({r.url for r in rows}) == len(rows)
        for r in rows:
            parts = urlparse(r.url)
            assert parts.scheme == "https" and parts.hostname == "drdo.gov.in" and policy.allows(r.url)
            assert r.url.startswith(VAC) and not parts.fragment and not parts.query

def test_navigation_skipped(parsed):
    for rows in parsed.values():
        for r in rows:
            assert r.title not in ("Vacancies", "View All", "View More", "View Archive", "Know More About Vacancies",
                                   "Archives", "Sitemap", "Contact Us", "ToT", "1", "2")
            assert not r.url.rstrip("/").endswith(("/vacancies", "/archive", "/know-more"))

def test_evidence_for_every_filled_field(parsed):
    for name, rows in parsed.items():
        raw = _read(name)
        page_text = _text(raw)
        for r in rows:
            filled = {"title": r.title, "url": r.url, "advertisement_number": r.advertisement_number,
                      "published_on": r.published_on, "last_date": r.last_date,
                      "post_names": r.post_names or None, "kind_hint": r.kind_hint}
            for field, value in filled.items():
                if value is None:
                    assert field not in r.evidence, (r.url, field)
                    continue
                ev = r.evidence.get(field)
                assert ev, (r.url, field)
                if field == "url":
                    assert f'href="{ev}"' in raw
                else:
                    assert ev in page_text, (r.url, field, ev)
            for field in ("post_names", "kind_hint"):
                if field in r.evidence:
                    assert r.evidence[field] in r.title

def test_no_invented_dates(parsed, cards):
    for rows in (parsed["vacancies.html"], parsed["vacancies_page2.html"]):
        for r in rows:
            card = cards[r.url[len(VAC):]]
            # dates come only from the card's own Published Date / End Date boxes, in dd/mm/yyyy
            assert r.evidence.get("published_on") == card["published"]
            assert r.published_on == date(*reversed([int(x) for x in card["published"].split("/")]))
            if r.last_date is not None:
                assert r.evidence["last_date"] == card["end"]
                assert r.last_date == date(*reversed([int(x) for x in card["end"].split("/")]))
            else:
                assert r.kind_hint in ("RESULT", "ADMIT_CARD")
    for r in parsed["home.html"]:
        assert all(getattr(r, f) is None for f in DATE_FIELDS)

def test_empty_and_unrelated_html():
    assert drdo.parse_listing("", PAGE1_URL) == []
    assert drdo.parse_listing("<html><body><ul><li><a href='/x'>Vacancies</a></li></ul></body></html>", PAGE1_URL) == []
