import asyncio
import re
import random
import json
import os
import datetime
from playwright.async_api import async_playwright

HISTORY_FILE = "price_history.json"

RETRY_TASKS = [
    {
        "id": "desert_escape",
        "route_name": "SEA-PSP",
        "origin": "SEA",
        "dest": "PSP",
        "name_cn": "棕榈泉 Palm Springs",
        "depart_date": "2026-04-09",
        "return_date": "2026-04-13",
        "priority_airlines": ["Alaska", "Delta", "United"],
    },
    {
        "id": "bali_retreat",
        "route_name": "SEA-DPS",
        "origin": "SEA",
        "dest": "DPS",
        "name_cn": "巴厘岛 Bali",
        "depart_date": "2026-07-01",
        "return_date": "2026-07-08",
        "priority_airlines": ["Singapore Airlines", "Qatar Airways", "Emirates", "EVA Air"],
    },
]

SELECTORS = [
    'li[role="listitem"]',
    '.pIav2d',
    '[data-result-index]',
    '[jsname="IWWDBc"]',
    '.YMlIz',
]

def build_url(task: dict) -> str:
    """Build structured Google Flights URL using the tfs parameter format."""
    # Structured URL: works better for less-common airport codes
    dep = task["depart_date"].replace("-", "")   # 20260409
    ret = task["return_date"].replace("-", "")   # 20260413
    origin = task["origin"]
    dest = task["dest"]
    
    # Try the structured tfs URL format
    url = (
        f"https://www.google.com/travel/flights/search?"
        f"tfs=CBwQAhoeEgoyMDI2LTA0LTA5agcIARIDU0VBcgcIARIDUFNQGh4SCjIwMjYtMDQtMTNqBwgBEgNQU1ByBwgBEgNTRUEqAggBcgIIAQ"
    )
    # Fall back to simpler but still structured URL
    url = (
        f"https://www.google.com/travel/flights?"
        f"q=nonstop+flights+from+{origin}+to+{dest}"
        f"&hl=en&curr=USD"
        f"&tfs=CBwQAhoeEgoyMDI2LTA*"  # placeholder, use simpler approach below
    )
    # Simplest reliable structured form
    url = (
        f"https://www.google.com/travel/flights#flt={origin}.{dest}.{task['depart_date']}*{dest}.{origin}.{task['return_date']};c:USD;e:1;s:0*0;sd:1;t:f"
    )
    return url

async def scrape_with_retry(task: dict, max_retries: int = 3) -> dict:
    # Try two different URL formats
    urls = [
        # Hash-based structured URL (most reliable for specific airports)
        f"https://www.google.com/travel/flights#flt={task['origin']}.{task['dest']}.{task['depart_date']}*{task['dest']}.{task['origin']}.{task['return_date']};c:USD;e:1;s:0*0;sd:1;t:f",
        # Query-based with simpler phrasing
        f"https://www.google.com/travel/flights?q=nonstop+flights+{task['origin']}+to+{task['dest']}+{task['depart_date']}+returning+{task['return_date']}&hl=en",
        # Natural language (original)
        f"https://www.google.com/travel/flights?q=Flights%20from%20{task['origin']}%20to%20{task['dest']}%20on%20{task['depart_date']}%20returning%20{task['return_date']}%20nonstop",
    ]

    for attempt, url in enumerate(urls, 1):
        print(f"\n[{task['route_name']}] Attempt {attempt}/{len(urls)} — URL format {attempt}")
        print(f"  {url}")
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-infobars",
                        "--window-size=1280,900",
                    ]
                )
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                    viewport={'width': 1280, 'height': 900},
                    locale="en-US",
                    timezone_id="America/Los_Angeles",
                    extra_http_headers={
                        "Accept-Language": "en-US,en;q=0.9",
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                        "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
                        "sec-ch-ua-mobile": "?0",
                        "sec-ch-ua-platform": '"macOS"',
                    }
                )
                await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
                page = await context.new_page()

                await page.goto(url, wait_until="domcontentloaded", timeout=60000)

                wait_secs = random.uniform(8, 12)
                print(f"  Waiting {wait_secs:.1f}s...")
                await asyncio.sleep(wait_secs)

                # Scroll to trigger lazy-load
                await page.evaluate("window.scrollBy(0, 600)")
                await asyncio.sleep(3)

                # Check what page we actually landed on
                body_text = await page.inner_text('body')
                if "Explore deals" in body_text and "Find cheap flights" in body_text:
                    print(f"  ❌ Landed on Google Flights homepage — URL format not working")
                    await browser.close()
                    continue

                found_selector = None
                for sel in SELECTORS:
                    try:
                        await page.wait_for_selector(sel, timeout=15000)
                        found_selector = sel
                        break
                    except:
                        continue

                if not found_selector:
                    print(f"  ❌ No selector found. Page: {body_text[:300]}")
                    await browser.close()
                    continue

                print(f"  ✅ Selector: {found_selector}")
                rows = await page.query_selector_all(found_selector)
                print(f"  Found {len(rows)} rows")

                results = []
                for row in rows:
                    try:
                        aria_label = await row.get_attribute('aria-label') or ""
                        airline_text = (await row.inner_text()) + " " + aria_label

                        price = 0
                        pm = re.search(r'(\d{1,4}(?:,\d{3})?)\s+US\s+dollars', aria_label)
                        if pm:
                            price = int(pm.group(1).replace(',', ''))
                        if price == 0:
                            pm = re.search(r'\$(\d{1,4}(?:,\d{3})?)', aria_label + airline_text)
                            if pm:
                                price = int(pm.group(1).replace(',', ''))

                        matched_carrier = "Other"
                        for airline in task['priority_airlines']:
                            if airline.lower() in airline_text.lower():
                                matched_carrier = airline
                                break

                        if price > 0:
                            results.append({"price": price, "carrier": matched_carrier})
                    except:
                        continue

                await browser.close()

                if results:
                    best = min(results, key=lambda x: x['price'])
                    print(f"  💰 Best: ${best['price']} on {best['carrier']} ({len(results)} results total)")
                    return {"task_id": task["id"], "route": task["route_name"], "results": results, "best": best, "live": True}
                else:
                    print("  No prices parsed from rows.")

        except Exception as e:
            print(f"  Error: {e}")

    # All formats failed
    mock = {"PSP": {"price": 413, "carrier": "Alaska"}, "DPS": {"price": 1250, "carrier": "Singapore Airlines"}}
    dest = task["dest"]
    print(f"\n  ⚠️ All URL formats failed for {dest}. Using mock.")
    return {"task_id": task["id"], "route": task["route_name"], "best": mock.get(dest, {"price": 0, "carrier": "N/A"}), "live": False}


async def main():
    print("=== Retrying PSP + DPS (multi-URL format) ===")
    results = []
    for task in RETRY_TASKS:
        r = await scrape_with_retry(task)
        results.append(r)

    print("\n=== FINAL RESULTS ===")
    for r in results:
        live_label = "🟢 LIVE" if r["live"] else "🟡 MOCK"
        print(f"  {r['route']}: ${r['best']['price']} ({r['best']['carrier']}) [{live_label}]")

    # Patch price_history.json with any live results
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE) as f:
            history = json.load(f)
        today = datetime.date.today().isoformat()
        for r in results:
            if r.get("live"):
                if r["task_id"] not in history:
                    history[r["task_id"]] = {}
                history[r["task_id"]][today] = r["best"]
        with open(HISTORY_FILE, "w") as f:
            json.dump(history, f, indent=4)
        print("Price history updated.")

if __name__ == "__main__":
    asyncio.run(main())
