import pytest
from govcareeros.backend.network.auth_detector import AuthDetector
from unittest.mock import AsyncMock, MagicMock

@pytest.mark.asyncio
async def test_detect_authenticated():
    detector = AuthDetector("upsc")
    # Mock a page that contains "Logout"
    page = AsyncMock()
    page.url = "https://upsc.gov.in/dashboard"
    page.content.return_value = "<html><body>Welcome! <a href=\"/logout\">Logout</a></body></html>"
    page.query_selector.return_value = None
    
    state = await detector.check_auth_state(page)
    assert state == "AUTHENTICATED"

@pytest.mark.asyncio
async def test_detect_anonymous():
    detector = AuthDetector("upsc")
    page = AsyncMock()
    page.url = "https://upsc.gov.in/home"
    page.content.return_value = "<html><body>Welcome to the portal. Please login.</body></html>"
    page.query_selector.return_value = None
    
    state = await detector.check_auth_state(page)
    assert state == "ANONYMOUS"
