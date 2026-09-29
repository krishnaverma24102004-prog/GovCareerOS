import pytest
from govcareeros.backend.network.request_recorder import RequestRecorder

def test_header_redaction():
    recorder = RequestRecorder()
    headers = {
        "Authorization": "Bearer SECRET123",
        "Cookie": "session=SECRET456",
        "Content-Type": "application/json",
        "X-Custom-Header": "NormalValue"
    }
    
    sanitized = recorder.sanitize_headers(headers)
    
    assert sanitized["Authorization"] == "[REDACTED]"
    assert sanitized["Cookie"] == "[REDACTED]"
    assert sanitized["Content-Type"] == "application/json"
    assert sanitized["X-Custom-Header"] == "NormalValue"
    assert "SECRET123" not in sanitized.values()
    assert "SECRET456" not in sanitized.values()
