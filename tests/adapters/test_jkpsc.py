"""
JKPSC listing adapter against the live homepage captured on 2026-10-01
(tests/fixtures/adapters/jkpsc/home.html, see meta.json next to it).
"""
import json
import re
from datetime import date
from html import unescape
from pathlib import Path
from urllib.parse import urlparse

import pytest

from govcareeros.backend.sources.adapters import adapter_for
from govcareeros.backend.sources.adapters import jkpsc
from govcareeros.backend.sources.url_guard import UrlPolicy

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "adapters" / "jkpsc"
META = json.loads((FIXTURE_DIR / "meta.json").read_text(encoding="utf-8"))
HTML = (FIXTURE_DIR / META["file"]).read_text(encoding="utf-8")
BASE_URL = META["final_url"]


def _visible_text(html: str) -> str:
    """Page text with HTML comments removed (what the page shows), whitespace-normalised."""
    html = re.sub(r"<!--.*?-->", " ", html, flags=re.S)
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html))).strip()


PAGE_TEXT = _visible_text(HTML)


@pytest.fixture(scope="module")
def rows():
    return jkpsc.parse_listing(HTML, BASE_URL)


@pytest.fixture(scope="module")
def by_url(rows):
    return {r.url: r for r in rows}


def test_fixture_is_a_live_capture_of_the_registry_page():
    assert META["url"] == "https://jkpsc.nic.in/"
    assert META["capture_date"] == "2026-10-01"
    assert META["fetch_mode"] == "HTTP"
    assert "Whats New...." in HTML


def test_adapter_is_discovered():
    assert adapter_for("JKPSC") is jkpsc
    assert jkpsc.SOURCE == "JKPSC"


def test_reads_every_notice_of_both_lists(rows):
    # 100 in "Whats New...." + 6 in the scrolling ticker (none repeated between the two)
    assert len(rows) == 106
    assert len({r.url for r in rows}) == len(rows)


def test_downloader_notice_with_hidden_dated_copy(by_url):
    row = by_url["https://jkpsc.nic.in/Pdf/Downloader1.ashx?nid=18044&type=n"]
    assert row.title == ("Conduct of Excise and Commercial Taxes Part I, II and III Departmental "
                         "Examination,2026, Rejection Notice thereof")
    assert row.published_on == date(2026, 9, 30)
    assert row.evidence["published_on"] == "30/09/2026"
    assert row.evidence["url"] == "Pdf/Downloader1.ashx?nid=18044&type=n"
    assert row.last_date is None and row.advertisement_number is None


def test_post_name_from_title(by_url):
    row = by_url["https://jkpsc.nic.in/Pdf/Downloader1.ashx?nid=18043&type=n"]
    assert row.title == ("Filling up of the post of Medical Officer (Allopathic) in Health and Family "
                         "Welfare Department. Rejection of candidature thereof")
    assert row.published_on == date(2026, 9, 30)
    assert row.post_names == ["Medical Officer (Allopathic)"]
    assert row.evidence["post_names"] == "post of Medical Officer (Allopathic)"

    lecturer = by_url["https://jkpsc.nic.in/Pdf/Downloader1.ashx?nid=17935&type=n"]
    assert lecturer.post_names == ["Lecturer English"]
    assert lecturer.published_on == date(2026, 6, 19)


def test_notification_number_is_the_advertisement_number(by_url):
    row = by_url["https://jkpsc.nic.in/Pdf/Downloader1.ashx?nid=17953&type=n"]
    assert row.title == ("Deficiency of documents for the post of Assistant Professor Urology notified vide "
                         "Notification No 03-PSC DR-P of 2025 in SKIMS HME Department")
    assert row.advertisement_number == "03-PSC DR-P of 2025"
    assert row.evidence["advertisement_number"] == "Notification No 03-PSC DR-P of 2025"
    assert row.post_names == ["Assistant Professor Urology"]
    assert row.published_on == date(2026, 7, 6)

    # The page's own misspelling "Notficaion"
    misspelt = by_url["https://jkpsc.nic.in/Pdf/Downloader1.ashx?nid=17952&type=n"]
    assert misspelt.advertisement_number == "15-PSC DR-P of 2025"
    assert misspelt.evidence["advertisement_number"] == "Notficaion No 15-PSC DR-P of 2025"


def test_directly_linked_notice_has_https_url_and_kind(by_url):
    row = by_url["https://jkpsc.nic.in/result/POMAINS_ADMITCARD_2025.html"]
    assert row.title == "Download Admit Card for Prosecuting Officer (G)(Mains) Examination, 2025"
    assert row.evidence["url"] == "http://jkpsc.nic.in/result/POMAINS_ADMITCARD_2025.html"
    assert row.published_on == date(2026, 7, 8)
    assert row.evidence["published_on"] == "08/07/2026"
    assert row.kind_hint == "ADMIT_CARD"
    assert row.evidence["kind_hint"] == "Admit Card"

    marks = by_url["https://jkpsc.nic.in/RESULT/KCSJ_MARKS_CARD_2025.HTML"]
    assert marks.title == "Download Marks Card for Civil Services (Judicial) Competitive Examination, 2025"
    assert marks.published_on == date(2026, 6, 11)
    assert marks.kind_hint == "RESULT"

    advt_pdf = by_url["https://jkpsc.nic.in/PDF/AP_DIRECTOR_FRESH_2026.PDF"]
    assert advt_pdf.title == ("Filling up of posts of Assistant Professor, College Director of Physical Education "
                              "and Sports and Librarians, in different disciplines, in Government Degree Colleges "
                              "of JK, in the HED, JK")
    assert advt_pdf.published_on == date(2026, 7, 31)
    assert advt_pdf.post_names == ["Assistant Professor, College Director of Physical Education and Sports and Librarians"]


def test_kind_labels(by_url):
    corrigendum = by_url["https://jkpsc.nic.in/Pdf/Downloader1.ashx?nid=18028&type=n"]
    assert corrigendum.kind_hint == "CORRIGENDUM"
    assert corrigendum.evidence["kind_hint"] == "Corrigendum"
    extension = by_url["https://jkpsc.nic.in/Pdf/Downloader1.ashx?nid=18002&type=n"]
    assert extension.title == "Filling up of Gazetted vacancies of Skill Department. Extension of date thereof"
    assert extension.kind_hint == "CORRIGENDUM"
    selection = by_url["https://jkpsc.nic.in/Pdf/Downloader1.ashx?nid=18016&type=n"]
    assert selection.kind_hint == "RESULT"
    assert selection.evidence["kind_hint"] == "Provisional Selection"
    plain = by_url["https://jkpsc.nic.in/Pdf/Downloader1.ashx?nid=18042&type=n"]
    assert plain.title == "Filling up of the posts of Superintendent Jail in Home Department. Edit Window Reg."
    assert plain.kind_hint is None


def test_ticker_only_notices_are_included(by_url):
    row = by_url["https://jkpsc.nic.in/Pdf/Downloader1.ashx?nid=18039&type=n"]
    assert row.title == ("Filling up of the posts of AP Librarian (GCET) (backlog vacancies) and AP, College "
                         "Director of Physical Education and Sports and Librarians in Government Colleges of "
                         "HED, in UT of JK - Extension of date thereof")
    assert row.published_on == date(2026, 8, 31)
    assert by_url["https://jkpsc.nic.in/Pdf/Downloader1.ashx?nid=17916&type=n"].published_on == date(2026, 6, 12)


def test_all_urls_on_official_domain(rows):
    policy = UrlPolicy.from_url(BASE_URL)
    for r in rows:
        parts = urlparse(r.url)
        assert parts.scheme == "https" and parts.hostname == "jkpsc.nic.in", r.url
        assert policy.allows(r.url), r.url


def test_skips_navigation_placeholders_and_commented_out_list(rows):
    urls = [r.url for r in rows]
    titles = [r.title for r in rows]
    assert not any("nid=&" in u for u in urls)                  # empty placeholder anchors
    assert not any(u.rstrip("/").endswith(("/#", "jkpsc.nic.in")) for u in urls)
    assert not any("notification.aspx" in u.lower() or "login" in u.lower() for u in urls)
    assert not any(t == "View More" for t in titles)
    assert not any("KCS(Judicial)-2013" in t for t in titles)  # 2017 list inside an HTML comment
    assert all(u.startswith(("https://jkpsc.nic.in/Pdf/Downloader1.ashx?nid=",
                             "https://jkpsc.nic.in/pdf/", "https://jkpsc.nic.in/PDF/",
                             "https://jkpsc.nic.in/result/", "https://jkpsc.nic.in/RESULT/",
                             "https://jkpsc.nic.in/resulT/")) for u in urls)


FIELDS = ("advertisement_number", "published_on", "last_date", "post_names", "kind_hint")


def test_evidence_for_every_filled_field(rows):
    for r in rows:
        assert r.evidence.get("title") == r.title and r.title in PAGE_TEXT, r.title
        assert r.evidence.get("url") and r.evidence["url"] in HTML, r.url
        for name in FIELDS:
            if getattr(r, name):
                ev = r.evidence.get(name)
                assert ev, (r.url, name)
                assert ev in r.title or ev in PAGE_TEXT, (r.url, name, ev)
        for name, value in (("advertisement_number", r.advertisement_number), ("post_names", r.post_names)):
            if value:
                assert r.evidence[name] in r.title
        if r.advertisement_number:
            assert r.advertisement_number in r.evidence["advertisement_number"]
        for post in r.post_names:
            assert post in r.evidence["post_names"]


def test_no_invented_dates(rows):
    for r in rows:
        assert r.last_date is None  # the page shows no closing dates
        if r.published_on:
            shown = r.evidence["published_on"]
            assert shown == r.published_on.strftime("%d/%m/%Y")
            # the date is printed in front of this very notice title on the page
            assert f"{shown} {r.title}" in PAGE_TEXT, r.url
    assert all(r.published_on for r in rows)
