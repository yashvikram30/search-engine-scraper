import asyncio, random
from playwright.async_api import async_playwright

async def main(q="rust concurrency", pages=4, headed=True):
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=not headed)
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir="./g_profile",
            channel="chrome",
            headless=False,
            locale="en-US",
            viewport={"width": 1366, "height": 850},
            ignore_default_args=["--enable-automation"],
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        page = await ctx.new_page()
        await page.goto(f"https://www.google.com/search?q={q}&hl=en&gl=us",
                        wait_until="domcontentloaded")
        for n in range(1, pages + 1):
            await asyncio.sleep(2)
            if "/sorry/" in page.url or "consent." in page.url:
                print(f"page {n}: blocked or consent -> {page.url}")
                break
            count = await page.locator("#search a:has(h3)").count()
            print(f"page {n}: {count} results | {page.url}")
            nxt = page.locator("#pnnext")
            if not await nxt.count():
                print("no next button")
                break
            await asyncio.sleep(random.uniform(3, 7))
            await nxt.click()
            await page.wait_for_load_state("domcontentloaded")
        await browser.close()

asyncio.run(main())