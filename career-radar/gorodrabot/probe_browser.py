"""One read-only browser check of a public job search, without login."""
import asyncio
import json
import urllib.parse
from pathlib import Path
from playwright.async_api import async_playwright

URL = "https://russia.gorodrabot.ru/" + urllib.parse.quote("автор_текстов")

async def main():
    report = {"search_url": URL, "success": False}
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            page = await browser.new_page(locale="ru-RU", viewport={"width": 1365, "height": 950})
            response = await page.goto(URL, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(1500)
            report["http_status"] = response.status if response else None
            report["final_url"] = page.url
            report["page_title"] = await page.title()
            report["body_excerpt"] = (await page.locator("body").inner_text(timeout=10000))[:500]
            all_links = await page.locator("a[href]").evaluate_all("""els => els.slice(0,400).map(a => ({text:(a.innerText||'').trim().slice(0,160),href:a.href}))""")
            candidates = [a for a in all_links if any(part in a["href"].lower() for part in ["ваканси", "vacanc", "/job/", "/jobs/"])]
            report["link_count"] = len(all_links)
            report["candidate_links_count"] = len(candidates)
            report["candidate_links_sample"] = candidates[:20]
            report["success"] = report["http_status"] == 200 and bool(candidates)
            await browser.close()
    except Exception as exc:
        report["error"] = type(exc).__name__ + ": " + str(exc)[:350]
    Path("gorodrabot-browser-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))

asyncio.run(main())
