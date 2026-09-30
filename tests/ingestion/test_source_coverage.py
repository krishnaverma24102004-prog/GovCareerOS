"""
Registry hygiene and the sitemap locator, on real captured sitemaps (tests/fixtures/sitemaps,
sources.json has each file's URL and capture date).
"""
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pytest

from govcareeros.backend.sources.generic_notices import PageFetcher, english_home_links, sitemap_locs, sitemap_recruitment_urls
from govcareeros.backend.sources.registry import SOURCES, STATE_PSC
from govcareeros.backend.sources.url_guard import UrlPolicy

SITEMAPS = Path(__file__).parents[1] / "fixtures" / "sitemaps"
NABARD_LISTING = "https://www.nabard.org/careers-notices1.aspx?cid=693&id=26"

def test_registry_entries_are_https_on_their_own_official_domain():
    names = [s.name for s in SOURCES]
    assert len(names) == len(set(names))
    for s in SOURCES:
        host = urlparse(s.recruitment_url).hostname
        assert urlparse(s.recruitment_url).scheme == "https", s.name
        assert s.official_domain == (host[4:] if host.startswith("www.") else host), s.name
        assert UrlPolicy.for_source(s.official_domain, s.recruitment_url, s.category).allows(s.recruitment_url), s.name

def test_state_psc_sources_are_registered():
    pscs = {s.name for s in SOURCES if s.category == STATE_PSC}
    assert {"UPPSC", "BPSC", "MPSC", "RPSC", "TNPSC", "GPSC", "HPSC", "PPSC", "APPSC", "CGPSC"} <= pscs

def test_sitemap_recruitment_urls_on_real_sitemaps():
    nabard = (SITEMAPS / "nabard.org.sitemap.xml").read_text(encoding="utf-8")
    locs, is_index = sitemap_locs(nabard)
    assert not is_index and len(locs) > 400 and "https://www.nabard.org/careers-notices1.aspx?cid=693&id=26" in locs  # &amp; unescaped
    found = sitemap_recruitment_urls(nabard, UrlPolicy.from_url("https://www.nabard.org/"))
    assert NABARD_LISTING in found and all("career" in u for u in found)
    # Listing pages rank before "life at NABARD"-style pages under the same keyword
    assert found[0].startswith("https://www.nabard.org/careers-notices1.aspx")

    meity = (SITEMAPS / "meity.gov.in.sitemap.xml").read_text(encoding="utf-8")
    assert sitemap_recruitment_urls(meity, UrlPolicy.from_url("https://www.meity.gov.in/")) == ["https://www.meity.gov.in/offerings/vacancies"]
    # Another domain's policy keeps none of them
    assert sitemap_recruitment_urls(meity, UrlPolicy.from_url("https://www.nabard.org/")) == []

def test_english_home_links():
    html = ('<a href="http://www.mod.gov.in/en">x</a><a href="/en" class="language-link" hreflang="en">English</a>'
            '<a href="https://mnre.gov.in/en/" aria-label="English" title="English"></a><a href="/hi">हिन्दी</a>')
    assert english_home_links(html, "https://www.mod.gov.in/") == ["https://www.mod.gov.in/en"]

@pytest.mark.asyncio
async def test_discover_finds_the_listing_page_through_the_sitemap():
    """NABARD's configured page now redirects to pagenotfound and the homepage menu has no careers link;
    the sitemap names the careers listing, which is served as captured."""
    pages = {
        "https://www.nabard.org/sitemap.xml": (SITEMAPS / "nabard.org.sitemap.xml").read_bytes(),
        NABARD_LISTING: (SITEMAPS / "nabard.org.careers-notices1.cid693.html").read_bytes(),
    }
    fetched = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        fetched.append(url)
        if url in pages:
            ctype = "text/xml" if url.endswith(".xml") else "text/html; charset=utf-8"
            return httpx.Response(200, content=pages[url], headers={"content-type": ctype})
        return httpx.Response(404, text="not found")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
    async with PageFetcher(allow_browser=False, client=client) as f:
        result = await f.discover("https://www.nabard.org/careers-notices1.aspx")
    assert result.status == "OK" and result.final_url == NABARD_LISTING
    assert result.detail.startswith("located via sitemap")
    assert len(result.notices) >= 40 and "Advertisement_BMO" in [n.title for n in result.notices]
    assert "https://www.nabard.org/robots.txt" in fetched
