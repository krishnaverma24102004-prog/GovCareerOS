from typing import Dict, Any, Optional, List
from playwright.async_api import Page

class AuthDetector:
    """
    Detects authentication state based on source-specific observable signals.
    """
    def __init__(self, source_id: str):
        self.source_id = source_id
        # In a real system, these markers would be loaded from the source registry
        self.auth_markers = {
            "upsc": {
                "authenticated_text": ["My Account", "Logout", "Dashboard"],
                "login_url": "https://upsc.gov.in/login",
                "auth_selectors": [".user-profile", "#logout-btn"]
            },
            "generic": {
                "authenticated_text": ["Logout", "Sign Out", "My Profile"],
                "login_url": None,
                "auth_selectors": [".account", "#logout"]
            }
        }

    async def check_auth_state(self, page: Page) -> str:
        # 1. Check current URL for login indicators
        url = page.url
        markers = self.auth_markers.get(self.source_id, self.auth_markers["generic"])
        
        if markers["login_url"] and markers["login_url"] in url:
            return "MANUAL_LOGIN_REQUIRED"

        # 2. Check page content for authenticated markers
        content = await page.content()
        if any(marker in content for marker in markers["authenticated_text"]):
            return "AUTHENTICATED"

        # 3. Check for specific authenticated selectors
        for selector in markers["auth_selectors"]:
            if await page.query_selector(selector):
                return "AUTHENTICATED"

        # 4. Default to anonymous or login required
        return "ANONYMOUS"
