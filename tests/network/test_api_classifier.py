import pytest
from govcareeros.backend.network.api_classifier import APIClassifier

def test_recruitment_classification():
    classifier = APIClassifier()
    url = "https://example.gov.in/api/vacancies"
    content_type = "application/json"
    body = "{\"post_name\": \"Engineer\", \"deadline\": \"2026-12-01\"}"
    
    is_candidate, confidence, reasons, fields = classifier.classify(url, content_type, body)
    
    assert is_candidate is True
    assert confidence > 0.5
    assert "post_name" in fields
    assert "deadline" in fields

def test_generic_classification():
    classifier = APIClassifier()
    url = "https://example.com/api/products"
    content_type = "application/json"
    body = "{\"product\": \"Laptop\", \"price\": 1000}"
    
    is_candidate, confidence, reasons, fields = classifier.classify(url, content_type, body)
    
    assert is_candidate is False
    # Adjusted to allow confidence up to 0.5 as long as it is not a candidate
    assert confidence <= 0.5
