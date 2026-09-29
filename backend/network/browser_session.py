import os
import uuid
from typing import Optional
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from govcareeros.backend.config.settings import settings

class BrowserSession:
    def __init__(self, source_id: Optional[str] = None):
        self.playwright = None
        self.browser = None
        self.context = None
        
        # Persistent profile per source to ensure isolation
        if source_id:
            self.user_data_dir = os.path.join(settings.storage_dir, "browser_profiles", source_id)
        else:
            self.user_data_dir = os.path.join(settings.storage_dir, "browser_profiles", "default")
            
        self.headless = settings.PLAYWRIGHT_HEADLESS
        self.timeout = int(os.getenv("BROWSER_TIMEOUT_MS", "30000"))

    async def start(self):
        self.playwright = await async_playwright().start()
        # Launch persistent context to reuse authentication/cookies
        self.context = await self.playwright.chromium.launch_persistent_context(
            user_data_dir=self.user_data_dir,
            headless=self.headless,
            timeout=self.timeout,
        )
        self.browser = self.context # In persistent context, context is the main entry

    async def new_page(self) -> Page:
        if not self.context:
            await self.start()
        return await self.context.new_page()

    async def stop(self):
        if self.context:
            await self.context.close()
        if self.playwright:
            await self.playwright.stop()

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop()
