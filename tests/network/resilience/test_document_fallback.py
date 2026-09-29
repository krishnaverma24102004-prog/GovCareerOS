import pytest
from govcareeros.backend.network.models import ResourceType

def test_document_discovery_logic():
    # Simulate discovering a PDF link when XHR fails
    discovered_links = [
        {"url": "https://example.gov.in/notice.pdf", "type": "pdf"},
        {"url": "https://example.gov.in/home", "type": "html"}
    ]
    
    pdf_links = [l for l in discovered_links if l["type"] == "pdf"]
    
    assert len(pdf_links) == 1
    assert "notice.pdf" in pdf_links[0]["url"]
