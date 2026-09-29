import asyncio
from playwright.async_api import async_playwright
from govcareeros.backend.network.browser_session import BrowserSession

async def debug_session():
    source_id = "upsc"
    session = BrowserSession(source_id=source_id)
    
    async with session:
        page = await session.new_page()
        print(f"Using Profile: {session.user_data_dir}")
        await page.goto("https://upsc.gov.in/jobs-notifications")
        
        content = await page.content()
        print("\n--- PAGE CONTENT SNAPSHOT ---")
        print(content[:2000]) # Print first 2k chars to find auth markers
        print("\n--- END SNAPSHOT ---")
        
        # Check for common auth keywords
        keywords = ["Logout", "Sign Out", "My Account", "Dashboard", "Profile"]
        found = [kw for kw in keywords if kw.lower() in content.lower()]
        print(f"Found Auth Keywords: {found}")

if __name__ == "__main__":
    asyncio.run(debug_session())