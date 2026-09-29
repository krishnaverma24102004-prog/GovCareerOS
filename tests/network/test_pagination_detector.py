import pytest
from govcareeros.backend.network.pagination_detector import PaginationDetector

def test_page_pagination():
    detector = PaginationDetector()
    url = "https://example.com/api/jobs?page=2"
    body = "{}"
    result = detector.detect(url, body)
    assert result["type"] == "page"
    assert result["parameter"] == "page"

def test_offset_pagination():
    detector = PaginationDetector()
    url = "https://example.com/api/jobs?offset=20&limit=10"
    body = "{}"
    result = detector.detect(url, body)
    assert result["type"] == "offset"
    assert result["parameter"] == "offset"

def test_json_pagination():
    detector = PaginationDetector()
    url = "https://example.com/api/jobs"
    body = "{\"pagination\": {\"page\": 2}}"
    result = detector.detect(url, body)
    assert result is not None
    assert "page" in body.lower()
