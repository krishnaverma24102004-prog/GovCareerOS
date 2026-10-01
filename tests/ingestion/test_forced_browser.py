"""
Sources whose listing is filled by JavaScript although the served HTML looks complete (registry force_browser).
Pages are the AGRIWELFARE captures of 2026-10-01 (tests/fixtures/sources/agriwelfare/meta.json).
"""
import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from govcareeros.backend.services.source_discovery import probe_sources, sync_registry
from govcareeros.backend.sources.generic_notices import FetchResult, PageFetcher, ProbeResult
from govcareeros.backend.sources.registry import SOURCES
from govcareeros.backend.sources.url_guard import UrlPolicy

AGRI = Path(__file__).parents[1] / "fixtures" / "sources" / "agriwelfare"
SPECS = {s.name: s for s in SOURCES}
URL = SPECS["AGRIWELFARE"].recruitment_url

def test_registry_forces_browser_only_where_verified():
    assert {s.name for s in SOURCES if s.force_browser} == {"AGRIWELFARE", "TRIBAL"}

class _RenderedFetcher(PageFetcher):
    """fetch_browser returns the captured rendered page instead of launching Chromium."""
    browser_calls = 0

    async def fetch_browser(self, url, policy=None):
        self.browser_calls += 1
        return FetchResult(url, 200, url, (AGRI / "recruitmentDetail_rendered.html").read_text(encoding="utf-8"))

def _served(request: httpx.Request) -> httpx.Response:
    if str(request.url) == URL:
        return httpx.Response(200, content=(AGRI / "recruitmentDetail_http.html").read_bytes(),
                              headers={"content-type": "text/html; charset=utf-8"})
    return httpx.Response(404, text="not found")

def _client():
    return httpx.AsyncClient(transport=httpx.MockTransport(_served))

@pytest.mark.asyncio
async def test_http_probe_misses_the_ajax_table_and_forced_browser_finds_it():
    policy = UrlPolicy.for_source("agriwelfare.gov.in", URL, "MINISTRY")
    async with _RenderedFetcher(allow_browser=True, client=_client()) as f:
        plain = await f.probe(URL, policy)
        assert (plain.status, plain.fetch_mode, f.browser_calls) == ("NO_NOTICES", "HTTP", 0)
        forced = await f.probe(URL, policy, browser=True)
    assert (forced.status, forced.fetch_mode, f.browser_calls) == ("OK", "BROWSER", 1)
    assert len(forced.notices) == 9
    assert all(n.url.startswith("https://agriwelfare.gov.in/Documents/Recruitment/") for n in forced.notices)

@pytest.mark.asyncio
async def test_forced_browser_without_a_browser_reports_needs_browser():
    async with _RenderedFetcher(allow_browser=False, client=_client()) as f:
        result = await f.discover(URL, UrlPolicy.from_url(URL), browser=True)
    assert result.status == "NEEDS_BROWSER" and f.browser_calls == 0

class _Recorder:
    allow_browser = True

    def __init__(self):
        self.calls = []

    async def discover(self, url, policy=None, browser=False):
        self.calls.append((url, browser))
        return ProbeResult("NO_NOTICES")

@pytest.mark.asyncio
async def test_sync_stores_the_flag_and_the_sweep_passes_it(db_session):
    specs = [SPECS["AGRIWELFARE"], SPECS["CONSUMER-AFFAIRS"]]
    sources = await sync_registry(db_session, specs)
    assert json.loads(sources["AGRIWELFARE"].configuration) == {"force_browser": True}
    assert sources["CONSUMER-AFFAIRS"].configuration is None

    recorder = _Recorder()
    await probe_sources(recorder, list(sources.values()))
    assert sorted(recorder.calls) == sorted([(URL, True), (SPECS["CONSUMER-AFFAIRS"].recruitment_url, False)])

    # Dropping the flag from the registry clears it, other stored configuration stays
    sources["AGRIWELFARE"].configuration = json.dumps({"force_browser": True, "resolved_url": URL})
    again = await sync_registry(db_session, [replace(SPECS["AGRIWELFARE"], force_browser=False)])
    assert json.loads(again["AGRIWELFARE"].configuration) == {"resolved_url": URL}
