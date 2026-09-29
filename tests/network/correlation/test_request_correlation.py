import pytest
import asyncio
from govcareeros.backend.network.network_discovery_engine import NetworkDiscoveryEngine
from govcareeros.backend.network.browser_session import BrowserSession
from playwright.async_api import async_playwright

@pytest.mark.asyncio
async def test_duplicate_url_correlation():
    engine = NetworkDiscoveryEngine()
    # We simulate the scenario manually because we need to control the exact sequence of requests
    # without a real server for this specific unit test of the recorder
    
    from govcareeros.backend.network.request_recorder import RequestRecorder
    from govcareeros.backend.network.response_recorder import ResponseRecorder
    from govcareeros.backend.network.models import ResourceType
    
    req_recorder = RequestRecorder()
    res_recorder = ResponseRecorder()
    
    # Simulate: GET /api/jobs?page=1
    # Request 1
    id1 = "req-1"
    req_recorder.record(id1, "http://example.com/api/jobs?page=1", "GET", "xhr", {}, None)
    # Request 2 (duplicate URL)
    id2 = "req-2"
    req_recorder.record(id2, "http://example.com/api/jobs?page=1", "GET", "xhr", {}, None)
    # Request 3
    id3 = "req-3"
    req_recorder.record(id3, "http://example.com/api/jobs?page=2", "GET", "xhr", {}, None)
    
    # Responses
    res_recorder.record(id1, 200, {}, "body1", "application/json")
    res_recorder.record(id2, 200, {}, "body2", "application/json")
    res_recorder.record(id3, 200, {}, "body3", "application/json")
    
    assert len(req_recorder.requests) == 3
    assert res_recorder.responses[id1].body == "body1"
    assert res_recorder.responses[id2].body == "body2"
    assert res_recorder.responses[id3].body == "body3"
    assert id1 != id2
