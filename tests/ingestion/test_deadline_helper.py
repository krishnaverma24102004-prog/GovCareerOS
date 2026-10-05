"""
Employment-News-relative closing dates and the in-service forwarding estimate, on real captured documents only:

  SSC CGL 2026 notice (tests/fixtures/ssc), p.5: "... before the date of publication of the Notice/Advertisement on the
      website of the Commission or Employments News, whichever is earlier." (mentions publication; states no closing rule)
  Ministry of Steel vacancy page (tests/fixtures/adapters/steel/vacancy.html, captured 2026-10-01):
      "[The tentative date for publishing the Advertisement in Employment News/Rozgaar Samachar is 4.7.26]"
  CRPF archive page (tests/fixtures/adapters/crpf/archived.html, captured 2026-10-01):
      "... recruitment of constable (Technical/ Tradesmen) in CRPF, published in employment news dated 25-31 March,2023."
  MHA (CEPI) vacancy circular, OCR text p.2 (tests/fixtures/mha): "Willing and eligible officers must forward their
      applications through their respective Ministries/Departments/Cadre Controlling Authorities onlor before
      28.08.2026 by 5.00 PM."

None of the documents we hold states both a "N days from publication in Employment News" rule and a publication day,
so the KNOWN branch has no real fixture yet; it is not tested with made-up text.
"""
import json
import re
from datetime import datetime, timedelta
from html import unescape
from pathlib import Path

import pytest

from govcareeros.backend.config.settings import settings
from govcareeros.backend.documents.pdf_text import extract_pages
from govcareeros.backend.parsing.upsc_advertisement import IST
from govcareeros.backend.services.deadline_helper import (
    HeldText, find_publication, find_rule, forwarding_estimate, relative_deadline,
)

FIXTURES = Path(__file__).parents[1] / "fixtures"
SSC = FIXTURES / "ssc"
CGL_URL = json.loads((SSC / "sources.json").read_text(encoding="utf-8"))["Notice_of_adv_cgl_2026.pdf"]["url"]

def _listing(path, url):
    return HeldText(url, None, re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", path.read_text(encoding="utf-8")))))

@pytest.fixture(scope="module")
def cgl():
    return [HeldText(CGL_URL, p.page_number, p.text) for p in extract_pages((SSC / "Notice_of_adv_cgl_2026.pdf").read_bytes(), ocr=False)]

STEEL = _listing(FIXTURES / "adapters" / "steel" / "vacancy.html", "https://steel.gov.in/vacancy")
CRPF = _listing(FIXTURES / "adapters" / "crpf" / "archived.html", "https://rect.crpf.gov.in/Archived")

def test_a_mention_of_employment_news_is_not_a_closing_rule(cgl):
    assert "before the date of publication of the Notice/Advertisement on the website of the Commission or Employments News" \
        in re.sub(r"\s+", " ", cgl[4].text)
    assert find_rule(cgl) == (None, [])

def test_a_tentative_date_and_an_issue_week_are_not_a_publication_day():
    found, reasons = find_publication([STEEL, CRPF])
    assert found is None
    assert reasons == [
        "'tentative date for publishing the Advertisement in Employment News/Rozgaar Samachar is 4.7.26' (https://steel.gov.in/vacancy) "
        "is a tentative date for publishing, not a publication",
        "'published in employment news dated 25-31 March,2023' (https://rect.crpf.gov.in/Archived) names an issue week, "
        "not the day of publication"]

def test_relative_deadline_is_unknown_unless_rule_and_publication_are_both_stated(cgl):
    r = relative_deadline(cgl + [STEEL, CRPF])
    assert (r.status, r.closing_date, r.rule, r.publication) == ("UNKNOWN", None, None, None)
    assert r.reasons[-2:] == ["no document we hold states the closing date as N days from publication in Employment News",
                              "no document we hold states the day the advertisement was published in Employment News"]
    assert any("tentative date for publishing" in x for x in r.reasons) and any("issue week" in x for x in r.reasons)

def test_forwarding_estimate_is_separate_labelled_and_off_by_default(monkeypatch):
    # MHA circular p.2: "... through their respective Ministries/Departments/Cadre Controlling Authorities onlor before 28.08.2026 by 5.00 PM."
    pages = json.loads((FIXTURES / "mha" / "CircularVacancy_03082026.ocr.json").read_text(encoding="utf-8"))["pages"]
    assert "Cadre Controlling Authorities onlor before 28.08.2026 by 5.00" in re.sub(r"\s+", " ", pages[1]["text"])
    official = datetime(2026, 8, 28, 17, 0, tzinfo=IST)

    assert settings.IN_SERVICE_FORWARDING_BUFFER_DAYS == 0 and forwarding_estimate(official, in_service=True) is None
    assert forwarding_estimate(official, in_service=False, buffer_days=10) is None
    assert forwarding_estimate(None, in_service=True, buffer_days=10) is None
    est = forwarding_estimate(official, in_service=True, buffer_days=10)
    assert est.is_official is False and est.official_deadline == official and est.estimate == official - timedelta(days=10)
    assert est.label == ("Estimate, not an official date: hand your application to your office for forwarding about 10 day(s) "
                         "before the official last date (28 Aug 2026 17:00 IST), i.e. by 18 Aug 2026. Your office's own instructions decide.")
    monkeypatch.setattr(settings, "IN_SERVICE_FORWARDING_BUFFER_DAYS", 5)
    assert forwarding_estimate(official, in_service=True).estimate == datetime(2026, 8, 23, 17, 0, tzinfo=IST)
