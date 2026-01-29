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
        "priority_airlines": ["Alaska", "Delta", "United", "American", "Southwest", "Hawaiian"], 
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
        "priority_airlines": ["Alaska", "Delta", "United", "American", "Southwest", "Hawaiian"],
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
                aria_label = await row.get_attribute('aria-label') or ""
                label_el = await row.query_selector('[aria-label]')
                if not aria_label and label_el:
                    aria_label = await label_el.get_attribute('aria-label') or ""
                
                airline_text = (await row.inner_text()) + " " + aria_label
                
                # Robust Price extraction
                price = 0
                price_match = re.search(r'(\d{1,4}(?:,\d{3})?)\s+US\s+dollars', aria_label)
                if price_match:
                    price = int(price_match.group(1).replace(',', ''))
                elif price == 0:
                    price_match = re.search(r'\$(\d{1,4}(?:,\d{3})?)', aria_label + airline_text)
                    if price_match:
                        price = int(price_match.group(1).replace(',', ''))

                # Duration extraction
                duration_match = re.search(r'(\d+)\s+hr\s+(\d+)\s+min', airline_text)
                duration = duration_match.group(0) if duration_match else "N/A"

                matched_carrier = "Unknown"
                for airline in task['priority_airlines']:
                    if airline.lower() in airline_text.lower():
                        matched_carrier = airline
                        break
                
                if price > 0:
                    results.append({
                        "price": price, 
                        "carrier": matched_carrier, 
                        "duration": duration,
                        "is_priority": matched_carrier in ["Alaska", "Delta"]
                    })
            except:
                continue

        await browser.close()
        return results # Return all flights found

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
    """Generates a 14-day line chart with market averages."""
    plt.figure(figsize=(10, 5))
    plt.style.use('seaborn-whitegrid')
    
    for task_id, data in history.items():
        records = data.get("history", [])
        df = pd.DataFrame(records)
        if df.empty: continue
        
        df = df[df['price'] > 0]
        if df.empty: continue
        
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').tail(14)
        
        label = task_id.replace('_', ' ').title()
        plt.plot(df['date'], df['price'], marker='o', label=f"{label} (Best)", linewidth=2)
        
        if 'market_avg' in df.columns:
            plt.plot(df['date'], df['market_avg'], linestyle='--', alpha=0.5, label=f"{label} (Market Avg)")

    plt.title("14-Day Price Analysis: Best vs Market Average", fontsize=14, fontweight='bold', pad=20)
    plt.xlabel("Date", fontsize=10)
    plt.ylabel("Price (USD)", fontsize=10)
    plt.legend()
    plt.tight_layout()
    plt.savefig("price_trend.png", dpi=300, transparent=False)
    plt.close()

def format_status(route: str, carrier: str, price: float, trend: str) -> str:
    price_display = f"${price}" if price > 0 else "N/A"
    return f"Travel Agent Status: {route} | {carrier} | {price_display} | [Trend: {trend}]"

def generate_comparison_table(all_flights: List[Dict]) -> str:
    rows = ""
    # Deduplicate by carrier and keep cheapest
    best_per_carrier = {}
    for f in all_flights:
        c = f['carrier']
        if c not in best_per_carrier or f['price'] < best_per_carrier[c]['price']:
            best_per_carrier[c] = f
            
    sorted_flights = sorted(best_per_carrier.values(), key=lambda x: x['price'])[:4] # Top 4
    for f in sorted_flights:
        rows += f"""
        <tr>
            <td>{f['carrier']}</td>
            <td>${f['price']}</td>
            <td>{f['duration']}</td>
        </tr>
        """
    return f"""<table class="flight-comparison">{rows}</table>"""

def generate_html_report(reports_data: List[Dict], recommendations: List[str], hotels: List[Dict]):
    """Generates a high-aesthetic dashboard with real-time stats, hotel curation, and trend visualization."""
    today_str = datetime.date.today().strftime("%B %d, %Y")
    
    recommendation_html = "".join([f'<div class="recommendation-pill">{rec}</div>' for rec in recommendations])
    hotel_html = "".join([f"""
        <div class="hotel-card">
            <div class="hotel-info">
                <div class="hotel-name">🏨 {h['name']}</div>
                <div class="hotel-vibe">{h['vibe']}</div>
            </div>
            <div class="hotel-rate">From ${h['rate']}/nt</div>
        </div>
    """ for h in hotels])

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
            --bg: #fff5f8;
            --surface: rgba(255, 255, 255, 0.95);
            --primary: #ff69b4;
            --accent: #d81b60;
            --target: #ff1493;
            --border: #ffc0cb;
            --text-main: #333333;
            --text-sub: #ff69b4;
        }}

        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ 
            font-family: 'Outfit', sans-serif; 
            background: url('hello_kitty_vacation_bg.png') no-repeat center center fixed;
            background-size: cover;
            color: var(--text-main);
            padding: 60px 20px;
            line-height: 1.6;
        }}

        .container {{ 
            max-width: 1000px; 
            margin: 0 auto; 
            background: rgba(255, 255, 255, 0.85);
            backdrop-filter: blur(15px);
            padding: 60px;
            border-radius: 50px;
            border: 5px solid white;
            box-shadow: 0 10px 40px rgba(255, 105, 180, 0.4);
        }}

        header {{ margin-bottom: 60px; text-align: center; border-bottom: 2px dashed var(--border); padding-bottom: 40px; }}
        h1 {{ font-family: 'Playfair Display', serif; font-size: 4.5rem; color: var(--primary); margin-bottom: 10px; }}
        .date-badge {{ font-size: 0.9rem; font-weight: 800; text-transform: uppercase; letter-spacing: 5px; color: var(--accent); }}

        /* Status Cards */
        .status-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 40px; margin-bottom: 60px; }}
        .status-card {{ background: var(--surface); border: 2px solid white; padding: 40px; border-radius: 40px; box-shadow: 0 10px 20px rgba(0,0,0,0.05); }}
        .route-label {{ font-size: 0.8rem; font-weight: 800; text-transform: uppercase; color: var(--text-sub); margin-bottom: 20px; }}
        .price-display {{ display: flex; align-items: baseline; gap: 15px; margin-bottom: 15px; }}
        .price-val {{ font-size: 4rem; font-weight: 800; color: var(--accent); }}
        .leader-badge {{ background: #fdf2f8; padding: 4px 10px; border-radius: 20px; font-size: 0.7rem; font-weight: 800; text-transform: uppercase; color: var(--primary); }}

        /* Comparison Table */
        .flight-comparison {{ width: 100%; border-collapse: collapse; margin-top: 20px; font-size: 0.8rem; }}
        .flight-comparison td {{ padding: 8px 0; border-bottom: 1px solid #fee2e2; }}
        .flight-comparison td:nth-child(2) {{ font-weight: 800; color: var(--accent); text-align: right; }}
        .flight-comparison td:nth-child(3) {{ text-align: right; color: #94a3b8; }}

        /* Stay Section */
        .section-header {{ font-size: 1rem; font-weight: 800; text-transform: uppercase; letter-spacing: 6px; color: var(--accent); text-align: center; margin: 60px 0 30px; }}
        .hotel-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 40px; margin-bottom: 60px; }}
        .hotel-card {{ background: white; border: 2px solid var(--border); padding: 30px; border-radius: 30px; display: flex; justify-content: space-between; align-items: center; }}
        .hotel-name {{ font-weight: 800; font-size: 1.2rem; color: var(--primary); }}
        .hotel-vibe {{ font-size: 0.85rem; color: #64748b; font-style: italic; }}
        .hotel-rate {{ font-weight: 800; color: var(--accent); font-size: 1.1rem; }}

        /* Visuals */
        .trend-container {{ text-align: center; margin-top: 80px; }}
        .trend-image {{ width: 100%; border-radius: 40px; border: 10px solid white; box-shadow: 0 15px 30px rgba(0,0,0,0.1); }}
        
        footer {{ margin-top: 100px; padding-top: 40px; border-top: 2px dashed var(--border); color: var(--text-sub); font-size: 0.8rem; text-align: center; font-weight: 800; }}
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
            <div class="section-header">✈️ Intelligence Report</div>
            {recommendation_html}
        </section>

        <section class="stay-zone">
            <div class="section-header">🏨 Curated Sanctuary</div>
            <div class="hotel-grid">
                {hotel_html}
            </div>
        </section>

        <section class="trend-container">
            <div class="section-header">📈 Market Sweep Analysis</div>
            <img src="price_trend.png" class="trend-image" alt="Trend Chart">
        </section>

        <footer>
            Generated by Antigravity Final Build | Major Carriers Sweep | Aesthetic Leaders Flagged
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
        aesthetic_leader_html = f'<div class="leader-badge">✨ Aesthetic: {data["aesthetic_leader"]}</div>' if data["aesthetic_leader"] else ""
        cards += f"""
        <div class="status-card">
            <div class="route-label">{data['route_name']} • {data['dates']}</div>
            <div class="price-display">
                <span class="price-val">${data['price']}</span>
                <div style="display:flex; flex-direction:column; gap:5px;">
                    <div class="leader-badge">💎 Value: {data['carrier']}</div>
                    {aesthetic_leader_html}
                </div>
            </div>
            <div class="meta-info">
                <span>Market Avg: ${data['market_avg']}</span>
            </div>
            {generate_comparison_table(data['all_flights'])}
        </div>
        """
    return cards

async def run_tracker():
    history = load_history()
    today_str = datetime.date.today().isoformat()
    
    HOTELS = [
        {"name": "Proper Hotel", "city": "SFO", "vibe": "Kelly Wearstler maximalism in a historic landmark.", "rate": 295},
        {"name": "Korakia Pensione", "city": "PSP", "vibe": "Moroccan-inspired desert soul and architectural beauty.", "rate": 425}
    ]
    
    print(f"--- Travel Agent Tracker Comprehensive Sweep ({today_str}) ---")
    
    reports_data = []
    recommendations = []

    for task in TASKS:
        print(f"Sweeping Market for {task['route_name']}...")
        all_flights = await fetch_flight_price(task)
        
        if not all_flights:
            print(f"!! No flights found for {task['route_name']}")
            continue
            
        value_leader = min(all_flights, key=lambda x: x['price'])
        market_avg = round(sum(f['price'] for f in all_flights) / len(all_flights), 2)
        
        # Aesthetic Leader: Cheapest among Alaska/Delta
        priority_flights = [f for f in all_flights if f.get('is_priority')]
        aesthetic_leader = min(priority_flights, key=lambda x: x['price']) if priority_flights else None
        
        task_id = task["id"]
        
        # Undercut check
        united_undercut = False
        if aesthetic_leader:
            united_flights = [f for f in all_flights if f['carrier'] == 'United']
            if united_flights:
                best_united = min(united_flights, key=lambda x: x['price'])
                if best_united['price'] < aesthetic_leader['price'] * 0.85:
                    united_undercut = True
                    rec = f"⚠️ United is significantly undercutting Alaska/Delta for {task['route_name']} (${best_united['price']} vs ${aesthetic_leader['price']})."
                    recommendations.append(rec)

        # Update history
        if task_id not in history: history[task_id] = {"latest": {}, "history": []}
        history[task_id]["latest"] = {"date": today_str, "price": value_leader["price"], "market_avg": market_avg}
        history[task_id]["history"].append({"date": today_str, "price": value_leader["price"], "carrier": value_leader["carrier"], "market_avg": market_avg})

        stats = calculate_stats(history, task_id)
        reports_data.append({
            "route_name": task['route_name'],
            "dates": f"{task['depart_date']} to {task['return_date']}",
            "price": value_leader["price"],
            "carrier": value_leader["carrier"],
            "aesthetic_leader": aesthetic_leader["carrier"] if aesthetic_leader else "N/A",
            "market_avg": market_avg,
            "all_flights": all_flights,
            "trigger": task['price_trigger']
        })
        
        recommendations.append(get_recommendation(task_id, value_leader["price"], stats, task["price_trigger"]))

    save_history(history)
    generate_trend_chart(history)
    generate_html_report(reports_data, recommendations, HOTELS)
    print("Market Sweep Complete: flight_report.html")

if __name__ == "__main__":
    asyncio.run(run_tracker())
