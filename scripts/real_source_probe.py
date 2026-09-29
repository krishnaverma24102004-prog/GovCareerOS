import asyncio
from playwright.async_api import async_playwright

async def probe():
    source_url = "https://upsc.gov.in/jobs-notifications"
    print("Probing Official Source: " + source_url)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        notifications = []
        try:
            print("Loading page...")
            await page.goto(source_url, wait_until="domcontentloaded", timeout=60000)
            
            # Instead of just <a> tags, search for any text containing "Notification"
            # and then find the nearest parent link.
            elements = await page.query_selector_all("text=Notification")
            for el in elements:
                parent = await el.evaluate_handle("node => node.closest(\"a\")")
                if parent:
                    href = await parent.get_attribute("href")
                    text = await el.inner_text()
                    if href:
                        url = href if href.startswith("http") else "https://upsc.gov.in" + href
                        notifications.append({"text": text.strip(), "url": url})
                    await parent.dispose()
                await el.dispose()
            
            # Fallback: All links
            if not notifications:
                links = await page.query_selector_all("a")
                for link in links:
                    href = await link.get_attribute("href")
                    text = await link.inner_text()
                    if href and (".pdf" in href.lower() or "notification" in text.lower()):
                        url = href if href.startswith("http") else "https://upsc.gov.in" + href
                        notifications.append({"text": text.strip(), "url": url})
            
            print("Discovered " + str(len(notifications)) + " potential notification documents.")
            if notifications:
                sample = notifications[0]
                print("\n--- Sample Notification ---")
                print("Title: " + sample["text"])
                print("URL: " + sample["url"])
                print("\nDiscovery Mechanism: DOCUMENT (PDF/HTML)")
                print("Adapter Mode: BROWSER/PDF_PARSER")
            else:
                print("No notifications found. Site might be using JS-rendered content or different tags.")
        except Exception as e:
            print("Error: " + str(e))
        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(probe())