import asyncio
from playwright.async_api import async_playwright

async def probe():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.goto("https://upsc.gov.in/jobs-notifications", wait_until="networkidle")
            print("Page Title: " + (await page.title()))
            links = await page.query_selector_all("a")
            print("Found " + str(len(links)) + " links")
            for i in range(min(10, len(links))):
                link = links[i]
                text = await link.inner_text()
                href = await link.get_attribute("href")
                print(str(i) + ": " + text + " -> " + str(href))
        except Exception as e:
            print("Error: " + str(e))
        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(probe())