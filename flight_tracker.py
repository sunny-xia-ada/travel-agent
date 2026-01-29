import asyncio
from playwright.async_api import async_playwright
import json
import os
import datetime
from typing import List, Dict, Optional
import matplotlib.pyplot as plt
import pandas as pd
import io
import base64

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
        "priority_airlines": ["Alaska", "Delta"], # Focus: Alaska, Delta. No United as per update.
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

# Analytics & Intelligence
def calculate_stats(history: Dict, task_id: str) -> Dict:
    """Calculates 7-day average and trend stats."""
    records = history.get(task_id, {}).get("history", [])
    df = pd.DataFrame(records)
    if df.empty:
        return {"avg_7d": 0, "status": "Stable"}
    
    # Filter out 0 prices (failed scrapes)
    df = df[df['price'] > 0]
    if df.empty:
        return {"avg_7d": 0, "status": "Stable"}
    
    # Get last 7 days
    df['date'] = pd.to_datetime(df['date'])
    today = pd.Timestamp.now().normalize()
    seven_days_ago = today - pd.Timedelta(days=7)
    recent = df[df['date'] >= seven_days_ago]
    
    avg_7d = recent['price'].mean() if not recent.empty else df['price'].mean()
    
    # Volatility check
    volatility = df['price'].std() if len(df) > 1 else 0
    status = "Volatile" if volatility > 50 else "Stable"
    
    return {"avg_7d": round(avg_7d, 2), "status": status}

def get_recommendation(task_id: str, current_price: float, stats: Dict, target: float) -> str:
    """Generates a human-readable escape recommendation."""
    avg = stats['avg_7d']
    status = stats['status']
    
    if current_price == 0:
        return "Data unavailable. Monitor closely."
    
    if current_price < target:
        return f"🚨 Target hit! {task_id.replace('_', ' ').title()} is at a record low. Buy now."
    
    if current_price < avg * 0.95:
        return f"{task_id.replace('_', ' ').title()} is below average. Good time to consider."
    
    if status == "Volatile":
        return f"{task_id.replace('_', ' ').title()} is currently volatile. Prices are fluctuating; wait for a dip."
    
    return f"{task_id.replace('_', ' ').title()} is stable. No immediate rush, monitor for target."

def generate_trend_chart(history: Dict):
    """Generates a 14-day line chart for price trends."""
    plt.figure(figsize=(10, 5))
    plt.style.use('seaborn-whitegrid')
    
    for task_id, data in history.items():
        records = data.get("history", [])
        df = pd.DataFrame(records)
        if df.empty: continue
        
        df = df[df['price'] > 0]
        if df.empty: continue
        
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').tail(14) # Last 14 days
        
        plt.plot(df['date'], df['price'], marker='o', label=task_id.replace('_', ' ').title(), linewidth=2)

    plt.title("14-Day Price Trend Analysis", fontsize=14, fontweight='bold', pad=20)
    plt.xlabel("Date", fontsize=10)
    plt.ylabel("Price (USD)", fontsize=10)
    plt.legend()
    plt.tight_layout()
    plt.savefig("price_trend.png", dpi=300, transparent=False)
    plt.close()

def format_status(route: str, carrier: str, price: float, trend: str) -> str:
    price_display = f"${price}" if price > 0 else "N/A"
    return f"Travel Agent Status: {route} | {carrier} | {price_display} | [Trend: {trend}]"

def generate_html_report(reports_data: List[Dict], recommendations: List[str]):
    """Generates a high-aesthetic dashboard with real-time stats and trend visualization."""
    today_str = datetime.date.today().strftime("%B %d, %Y")
    
    recommendation_html = "".join([f'<div class="recommendation-pill">{rec}</div>' for rec in recommendations])

    html_template = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Travel Agent | Intelligence Dashboard</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&family=Playfair+Display:ital,wght@0,700;1,700&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg: #ffffff;
            --surface: #f8fafc;
            --primary: #0f172a;
            --accent: #2563eb;
            --target: #f43f5e;
            --border: #e2e8f0;
            --text-main: #1e293b;
            --text-sub: #64748b;
        }}

        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ 
            font-family: 'Outfit', sans-serif; 
            background-color: var(--bg);
            color: var(--text-main);
            padding: 60px 20px;
            line-height: 1.6;
        }}

        .container {{ max-width: 900px; margin: 0 auto; }}

        header {{ margin-bottom: 80px; text-align: left; border-bottom: 1px solid var(--border); padding-bottom: 20px; }}
        h1 {{ font-family: 'Playfair Display', serif; font-size: 4rem; letter-spacing: -2px; line-height: 1; margin-bottom: 10px; }}
        .date-badge {{ font-size: 0.8rem; font-weight: 800; text-transform: uppercase; letter-spacing: 3px; color: var(--accent); }}

        /* Status Cards */
        .status-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 40px; margin-bottom: 80px; }}
        .status-card {{ border: 1px solid var(--border); padding: 40px; border-radius: 4px; }}
        .route-label {{ font-size: 0.75rem; font-weight: 800; text-transform: uppercase; letter-spacing: 2px; color: var(--text-sub); margin-bottom: 20px; }}
        .price-display {{ display: flex; align-items: baseline; gap: 15px; margin-bottom: 10px; }}
        .price-val {{ font-size: 4rem; font-weight: 800; letter-spacing: -3px; }}
        .target-val {{ font-size: 1rem; font-weight: 600; color: var(--target); }}
        .meta-info {{ font-size: 0.9rem; color: var(--text-sub); display: flex; justify-content: space-between; }}

        /* Recommendations */
        .section-header {{ font-size: 0.8rem; font-weight: 800; text-transform: uppercase; letter-spacing: 4px; color: var(--text-sub); margin-bottom: 30px; border-top: 1px solid var(--border); padding-top: 40px; }}
        .recommendation-zone {{ margin-bottom: 80px; }}
        .recommendation-pill {{ 
            padding: 20px; 
            border-left: 4px solid var(--accent); 
            background: var(--surface); 
            margin-bottom: 15px; 
            font-weight: 500;
            font-size: 1.1rem;
        }}

        /* Visuals */
        .visual-container {{ margin-top: 80px; text-align: center; }}
        .trend-image {{ width: 100%; border-radius: 4px; margin-top: 20px; }}
        
        footer {{ margin-top: 100px; padding-top: 40px; border-top: 1px solid var(--border); color: var(--text-sub); font-size: 0.8rem; text-align: center; }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="date-badge">Travel Agent From Yidan • {today_str}</div>
            <h1>Travel Agent From Yidan</h1>
        </header>

        <section class="status-grid">
            {generate_status_cards(reports_data)}
        </section>

        <section class="recommendation-zone">
            <div class="section-header">Escape Recommendations</div>
            {recommendation_html}
        </section>

        <section class="visual-container">
            <div class="section-header">14-Day Price Trend Analysis</div>
            <img src="price_trend.png" class="trend-image" alt="Price Trend Chart">
        </section>

        <footer>
            Generated by Travel Agent From Yidan • Nonstop Flights Only • Focus: Alaska & Delta
        </footer>
    </div>
</body>
</html>
    """
    with open("flight_report.html", "w") as f:
        f.write(html_template)

def generate_status_cards(reports_data: List[Dict]) -> str:
    cards = ""
    for data in reports_data:
        cards += f"""
        <div class="status-card">
            <div class="route-label">{data['route_name']} • {data['dates']}</div>
            <div class="price-display">
                <span class="price-val">${data['price']}</span>
                <span class="target-val">Target: ${data['trigger']}</span>
            </div>
            <div class="meta-info">
                <span>7D Avg: ${data['avg_7d']}</span>
                <span>{data['carrier']}</span>
            </div>
        </div>
        """
    return cards

async def run_tracker():
    history = load_history()
    today_str = datetime.date.today().isoformat()
    
    print(f"--- Travel Agent Tracker ({today_str}) ---")
    
    reports_data = []
    recommendations = []

    for task in TASKS:
        print(f"Tracking {task['route_name']}...")
        result = await fetch_flight_price(task)
        current_price = result["price"]
        best_carrier = result["carrier"]
        
        task_id = task["id"]
        
        # Update history FIRST to include current price in stats
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

        # Calculate Stats & Recommendations
        stats = calculate_stats(history, task_id)
        rec = get_recommendation(task_id, current_price, stats, task["price_trigger"])
        recommendations.append(rec)
        
        prev_data = history.get(task_id, {}).get("history", [])[:-1]
        prev_price = prev_data[-1].get("price") if prev_data else None
        trend = get_trend_emoji(current_price, prev_price)
        
        # Check Triggers for console
        if current_price > 0 and current_price < task["price_trigger"]:
            print(f"🚨 TARGET HIT: {task['route_name']} is ${current_price}")

        # Prepare data for HTML report
        reports_data.append({
            "route_name": task['route_name'],
            "dates": f"{task['depart_date']} to {task['return_date']}",
            "price": current_price if current_price > 0 else "N/A",
            "carrier": best_carrier,
            "trend_val": trend,
            "trigger": task['price_trigger'],
            "avg_7d": stats['avg_7d'],
            "screenshot": f"debug_{task['id']}.png"
        })

    save_history(history)
    generate_trend_chart(history)
    generate_html_report(reports_data, recommendations)
    print("Dashboard report generated: flight_report.html")

if __name__ == "__main__":
    asyncio.run(run_tracker())
