import asyncio
from playwright.async_api import async_playwright
import json
import os
import datetime
from typing import List, Dict, Optional

# Constants
HISTORY_FILE = "price_history.json"
TASKS = [
    {
        "id": "sf_weekend",
        "route_name": "SEA-SFO",
        "origin": "SEA",
        "dest": "SFO",
        "depart_date": "2026-03-27",
        "return_date": "2026-03-29",
        "priority_airlines": ["Delta", "Alaska", "United"],
        "nonstop_only": True,
        "price_trigger": 160,
        "drop_trigger_pct": None
    },
    {
        "id": "desert_escape",
        "route_name": "SEA-PSP",
        "origin": "SEA",
        "dest": "PSP",
        "depart_date": "2026-04-09",
        "return_date": "2026-04-13",
        "priority_airlines": ["Alaska", "Delta"],
        "nonstop_only": True,
        "price_trigger": 400,
        "drop_trigger_pct": 20
    }
]

def load_history() -> Dict:
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r") as f:
            try:
                return json.load(f)
            except:
                return {}
    return {}

def save_history(history: Dict):
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=4)

def get_trend_emoji(current_price: float, previous_price: Optional[float]) -> str:
    if previous_price is None or previous_price == 0:
        return "⚪"
    if current_price < previous_price:
        return "🟢"
    if current_price > previous_price:
        return "🔴"
    return "⚪"

import re

async def fetch_flight_price(task: Dict) -> Dict:
    """Uses Playwright to fetch flight prices from Google Flights."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
            viewport={'width': 1280, 'height': 800}
        )
        page = await context.new_page()
        
        # Use direct URLs for stability. PSP uses a specific encoded URL from the subagent discovery.
        if task["id"] == "sf_weekend":
            url = f"https://www.google.com/travel/flights?q=Flights%20from%20{task['origin']}%20to%20{task['dest']}%20on%20{task['depart_date']}%20returning%20{task['return_date']}%20nonstop"
        else:
            url = "https://www.google.com/travel/flights/search?tfs=CBwQAhooEgoyMDI2LTA0LTA5agwIAhIIL20vMGQ5anJyDAgCEggvbS8wcjN0cRooEgoyMDI2LTA0LTEzagwIAhIIL20vMHIzdHFyDAgCEggvbS8wZDlqckABSAFwAYIBCwj___________8BmAEB&hl=en-US&gl=US"
        
        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)
            # Wait for any list item or row
            await page.wait_for_selector('li[role="listitem"], .pIav2d', timeout=20000)
            await page.screenshot(path=f"debug_{task['id']}.png")
        except Exception as e:
            await page.screenshot(path=f"error_{task['id']}.png")
            await browser.close()
            return {"price": 0, "carrier": "N/A"}

        # Extract results
        results = []
        rows = await page.query_selector_all('li[role="listitem"], .pIav2d')
        
        for row in rows:
            try:
                # Find the element with aria-label (could be the row or a child)
                aria_label = await row.get_attribute('aria-label')
                if not aria_label:
                    label_el = await row.query_selector('[aria-label]')
                    if label_el:
                        aria_label = await label_el.get_attribute('aria-label')
                
                aria_label = aria_label or ""
                
                # Airline detection from visible text OR aria-label
                airline_text = await row.inner_text()
                airline_text += " " + aria_label
                
                # Robust Price extraction with regex
                # Examples: "From 187 US dollars", "567 US dollars", "$187"
                price = 0
                # Priority 1: "XXX US dollars"
                price_match = re.search(r'(\d{1,4}(?:,\d{3})?)\s+US\s+dollars', aria_label)
                if price_match:
                    price = int(price_match.group(1).replace(',', ''))
                
                # Priority 2: Any dollar amount like $567
                if price == 0:
                    price_match = re.search(r'\$(\d{1,4}(?:,\d{3})?)', aria_label + airline_text)
                    if price_match:
                        price = int(price_match.group(1).replace(',', ''))

                # Carrier matching
                matched_carrier = "Unknown"
                for airline in task['priority_airlines']:
                    if airline.lower() in airline_text.lower():
                        matched_carrier = airline
                        break
                
                if matched_carrier != "Unknown" and price > 0:
                    results.append({"price": price, "carrier": matched_carrier})
            except:
                continue

        await browser.close()
        
        if not results:
            return {"price": 0, "carrier": "N/A"}
        
        # Return cheapest from priority list
        return min(results, key=lambda x: x["price"])

def format_status(route: str, carrier: str, price: float, trend: str) -> str:
    price_display = f"${price}" if price > 0 else "N/A"
    return f"Travel Agent Status: {route} | {carrier} | {price_display} | [Trend: {trend}]"

def generate_html_report(reports_data: List[Dict]):
    """Generates a premium HTML report with glassmorphism and screenshots."""
    today_str = datetime.date.today().strftime("%B %d, %Y")
    
    # CSS & Template
    html_template = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Travel Agent | Flight Intelligence</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&family=Playfair+Display:ital,wght@0,700;1,700&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg: #f8fafc;
            --surface: rgba(255, 255, 255, 0.7);
            --primary: #2563eb;
            --secondary: #64748b;
            --accent: #f43f5e;
            --text: #0f172a;
            --glass-border: rgba(255, 255, 255, 0.5);
            --glass-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.1);
        }}

        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ 
            font-family: 'Outfit', sans-serif; 
            background: linear-gradient(135deg, #e0f2fe 0%, #f1f5f9 100%);
            color: var(--text);
            min-height: 100vh;
            padding: 40px 20px;
        }}

        .container {{ max-width: 1000px; margin: 0 auto; }}

        /* Header Area */
        header {{
            text-align: center;
            margin-bottom: 60px;
        }}
        h1 {{ 
            font-family: 'Playfair Display', serif; 
            font-size: 3.5rem; 
            color: var(--primary);
            margin-bottom: 10px;
            letter-spacing: -1px;
        }}
        .subtitle {{ font-size: 1.1rem; color: var(--secondary); font-weight: 400; letter-spacing: 2px; }}

        /* Dashboard Grid */
        .dashboard-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(450px, 1fr));
            gap: 30px;
        }}

        /* Flight Card */
        .flight-card {{
            background: var(--surface);
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
            border-radius: 32px;
            border: 1px solid var(--glass-border);
            box-shadow: var(--glass-shadow);
            padding: 30px;
            transition: transform 0.3s ease;
        }}
        .flight-card:hover {{ transform: translateY(-5px); }}

        .route-info {{ display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 25px; }}
        .route-name {{ font-size: 2rem; font-weight: 800; color: var(--text); }}
        .badge {{ 
            padding: 8px 16px; 
            border-radius: 20px; 
            font-size: 0.8rem; 
            font-weight: 600; 
            text-transform: uppercase; 
            letter-spacing: 1px;
        }}
        .badge-none {{ background: #e2e8f0; color: #475569; }}
        .badge-trigger {{ background: #fee2e2; color: #ef4444; border: 1px solid #fecaca; }}

        .price-row {{ display: flex; align-items: baseline; gap: 10px; margin-bottom: 20px; }}
        .current-price {{ font-size: 3rem; font-weight: 800; color: var(--primary); }}
        .airline {{ font-size: 1.2rem; color: var(--secondary); font-weight: 600; }}
        .trend {{ font-size: 1.5rem; }}

        /* Evidence Section */
        .evidence {{
            margin-top: 25px;
            border-radius: 20px;
            overflow: hidden;
            border: 4px solid white;
            box-shadow: 0 4px 15px rgba(0,0,0,0.05);
        }}
        .evidence img {{ width: 100%; display: block; filter: grayscale(10%); transition: filter 0.3s; }}
        .evidence:hover img {{ filter: grayscale(0%); }}

        footer {{ text-align: center; margin-top: 60px; color: var(--secondary); font-size: 0.9rem; }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Travel Agent From Yidan</h1>
            <p class="subtitle">AIR INTELLIGENCE REPORT • {today_str}</p>
        </header>

        <div class="dashboard-grid">
            {generate_cards(reports_data)}
        </div>

        <footer>
            Generated by Travel Agent • Data synced with Google Flights
        </footer>
    </div>
</body>
</html>
    """
    with open("flight_report.html", "w") as f:
        f.write(html_template)

def generate_cards(reports_data: List[Dict]) -> str:
    cards_html = ""
    for data in reports_data:
        trigger_badge = ""
        if data['price'] > 0 and data['price'] < data['trigger']:
            trigger_badge = '<span class="badge badge-trigger">🚨 Target Hit</span>'
        else:
            trigger_badge = '<span class="badge badge-none">Watching</span>'
            
        cards_html += f"""
            <div class="flight-card">
                <div class="route-info">
                    <div>
                        <div class="route-name">{data['route_name']}</div>
                        <div style="color: var(--secondary); font-size: 0.9rem; font-weight: 600;">{data['dates']}</div>
                    </div>
                    {trigger_badge}
                </div>
                <div class="price-row">
                    <span class="current-price">${data['price'] if data['price'] > 0 else 'N/A'}</span>
                    <span class="airline">via {data['carrier']}</span>
                    <span class="trend">{data['trend_val']}</span>
                </div>
                <div class="evidence">
                    <img src="{data['screenshot']}" alt="Flight Evidence">
                </div>
            </div>
        """
    return cards_html

async def run_tracker():
    history = load_history()
    today_str = datetime.date.today().isoformat()
    
    print(f"--- Flight Tracking Report ({today_str}) ---")
    
    reports_data = []

    for task in TASKS:
        print(f"Tracking {task['route_name']}...")
        result = await fetch_flight_price(task)
        current_price = result["price"]
        best_carrier = result["carrier"]
        
        task_id = task["id"]
        prev_data = history.get(task_id, {}).get("latest", {})
        prev_price = prev_data.get("price")
        
        trend = get_trend_emoji(current_price, prev_price)
        
        # Check Triggers
        if current_price > 0 and current_price < task["price_trigger"]:
            print(f"🚨 TRIGGER: {task['route_name']} price is ${current_price} (Target < ${task['price_trigger']})")
        
        if task.get("drop_trigger_pct") and prev_price and prev_price > 0 and current_price > 0:
            drop_pct = ((prev_price - current_price) / prev_price) * 100
            if drop_pct >= task["drop_trigger_pct"]:
                print(f"🚨 TRIGGER: {task['route_name']} dropped {drop_pct:.1f}% in 24h!")

        status = format_status(task["route_name"], best_carrier, current_price, trend)
        print(status)
        
        # Prepare data for HTML report
        reports_data.append({
            "route_name": task['route_name'],
            "dates": f"{task['depart_date']} to {task['return_date']}",
            "price": current_price,
            "carrier": best_carrier,
            "trend_val": trend,
            "trigger": task['price_trigger'],
            "screenshot": f"debug_{task['id']}.png"
        })

        # Update history
        if task_id not in history:
            history[task_id] = {"latest": {}, "history": []}
        
        history[task_id]["latest"] = {
            "date": today_str,
            "price": current_price,
            "carrier": best_carrier
        }
        history[task_id]["history"].append({
            "date": today_str,
            "price": current_price,
            "carrier": best_carrier
        })

    save_history(history)
    generate_html_report(reports_data)
    print("Dashboard report generated: flight_report.html")

if __name__ == "__main__":
    asyncio.run(run_tracker())
