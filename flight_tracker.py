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
    """Generates a 14-day line chart with ribbon markers for buy points."""
    plt.figure(figsize=(10, 6))
    plt.style.use('seaborn-whitegrid')
    
    colors = ['#ff69b4', '#ff1493']
    for i, (task_id, data) in enumerate(history.items()):
        records = data.get("history", [])
        df = pd.DataFrame(records)
        if df.empty: continue
        
        df = df[df['price'] > 0]
        if df.empty: continue
        
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').tail(14)
        
        label = task_id.replace('_', ' ').title()
        
        # Plot main line
        plt.plot(df['date'], df['price'], color=colors[i % len(colors)], linewidth=3, label=f"{label}")
        
        # Mark potential "buy" points (local minima or below target)
        if len(df) > 2:
            buy_points = df[df['price'] == df['price'].min()]
            # Using $🎀$ directly without the backslash or just 'o' with a label if emoji fails
            plt.plot(buy_points['date'], buy_points['price'], marker='$\heartsuit$', markersize=15, linestyle='None', color='#ff69b4')

        if 'market_avg' in df.columns:
            plt.plot(df['date'], df['market_avg'], linestyle='--', alpha=0.3, color='#f472b6', label=f"{label} Avg")

    plt.title("Market Pulse Analysis", fontsize=16, fontweight='bold', color='#ff1493', pad=25)
    plt.xlabel("Timeline", fontsize=11, color='#db2777')
    plt.ylabel("Value (USD)", fontsize=11, color='#db2777')
    plt.legend(frameon=True, facecolor='white', framealpha=0.9)
    plt.tight_layout()
    plt.savefig("price_trend.png", dpi=300, transparent=True)
    plt.close()

def format_status(route: str, carrier: str, price: float, trend: str) -> str:
    price_display = f"${price}" if price > 0 else "N/A"
    return f"Travel Agent Status: {route} | {carrier} | {price_display} | [Trend: {trend}]"

def generate_price_spectrum(all_flights: List[Dict]) -> str:
    """Generates a horizontal spectrum line showing airline positioning."""
    if not all_flights: return ""
    prices = [f['price'] for f in all_flights]
    min_p = min(prices)
    max_p = max(prices)
    range_p = (max_p - min_p) or 1
    
    # Deduplicate by carrier
    carriers = {}
    for f in all_flights:
        c = f['carrier']
        if c not in carriers or f['price'] < carriers[c]['price']:
            carriers[c] = f
            
    markers_html = ""
    for c, f in carriers.items():
        pos = ((f['price'] - min_p) / range_p) * 90 + 5 # 5% to 95%
        style = "background:var(--accent); z-index:10;" if f.get('is_priority') else "background:#94a3b8; opacity:0.6;"
        markers_html += f"""
        <div class="spectrum-point" style="left: {pos}%; {style}">
            <div class="spectrum-label">{c}<br>${f['price']}</div>
        </div>
        """
        
    return f"""
    <div class="spectrum-container">
        <div class="spectrum-line"></div>
        {markers_html}
    </div>
    """

def generate_html_report(reports_data: List[Dict], recommendations: List[str], hotels: List[Dict]):
    """Generates YIDAN TRAVEL: THE PRIVATE BRIEFING with asymmetric logic."""
    today_str = datetime.date.today().strftime("%B %d, %Y")
    
    recommendation_html = "".join([f'<div class="briefing-pill">⭐ {rec}</div>' for rec in recommendations])
    hotel_html = "".join([f"""
        <div class="hotel-block">
            <div class="hotel-canvas">
                <img src="{'hotel_sfo.png' if h['city'] == 'SFO' else 'hotel_psp.png'}" alt="{h['name']}">
            </div>
            <div class="hotel-editorial">
                <div class="hotel-title">{h['name']}</div>
                <div class="hotel-vibe">{h['vibe']}</div>
                <div class="assistant-note">"Yidan, {h['tip']}"</div>
                <div class="hotel-meta">From ${h['rate']}/nt</div>
            </div>
        </div>
    """ for h in hotels])

    html_template = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>YIDAN TRAVEL | THE PRIVATE BRIEFING</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&family=Playfair+Display:wght@700;900&display=swap" rel="stylesheet">
    <style>
        :root {{
            --primary: #ff69b4;
            --secondary: #ff1493;
            --glass: rgba(255, 255, 255, 0.7);
            --text: #2c3e50;
        }}

        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ 
            font-family: 'Outfit', sans-serif; 
            background: url('hello_kitty_vacation_bg.png') no-repeat center center fixed;
            background-size: cover;
            color: var(--text);
            overflow-x: hidden;
            padding-bottom: 200px;
        }}

        .editorial-canvas {{
            max-width: 1400px;
            margin: 0 auto;
            position: relative;
            padding: 100px 40px;
        }}

        header {{ margin-bottom: 150px; text-align: left; position: relative; z-index: 100; }}
        .badge {{ font-weight: 800; text-transform: uppercase; letter-spacing: 8px; color: var(--primary); font-size: 0.9rem; margin-bottom: 20px; }}
        h1 {{ 
            font-family: 'Playfair Display', serif; 
            font-size: 6.5rem; 
            font-weight: 900; 
            line-height: 0.9; 
            color: var(--secondary); 
            text-shadow: 10px 10px 0px rgba(255, 105, 180, 0.1);
        }}

        /* The Anti-Grid Staggered Layout */
        .flight-container {{ position: relative; height: 1000px; margin-bottom: 200px; }}
        .flight-card {{
            position: absolute;
            background: var(--glass);
            backdrop-filter: blur(25px);
            padding: 60px;
            border-radius: 4px;
            box-shadow: 30px 30px 60px rgba(0,0,0,0.05);
            border: 1px solid rgba(255,255,255,0.4);
            transition: all 0.5s ease;
        }}
        
        .card-sfo {{ width: 550px; top: 0; left: 0; z-index: 2; }}
        .card-psp {{ width: 550px; bottom: 0; right: 0; z-index: 1; }}
        .flight-card:hover {{ z-index: 10; transform: translateY(-10px); }}

        .route-label {{ font-family: 'Playfair Display', serif; font-size: 3rem; margin-bottom: 30px; }}
        .price-hero {{ font-size: 5rem; font-weight: 900; color: var(--secondary); margin: 20px 0; }}
        
        .sweep-section {{ margin-top: 40px; border-top: 1px dashed rgba(255, 105, 180, 0.3); padding-top: 30px; }}
        .sweep-title {{ font-weight: 800; text-transform: uppercase; letter-spacing: 4px; font-size: 0.7rem; color: var(--primary); margin-bottom: 20px; }}
        
        .assistant-pick {{ background: white; padding: 20px; border-radius: 15px; border-left: 6px solid var(--secondary); margin-bottom: 15px; }}
        .other-options {{ font-size: 0.8rem; color: #94a3b8; font-weight: 600; display: flex; gap: 15px; }}

        /* Sanctuary Spotlight */
        .stay-header {{ font-family: 'Playfair Display', serif; font-size: 4rem; text-align: center; margin-bottom: 100px; }}
        .hotel-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 80px; margin-bottom: 200px; }}
        
        .hotel-block {{ background: white; padding: 30px; box-shadow: 20px 20px 50px rgba(0,0,0,0.05); }}
        .hotel-canvas img {{ width: 100%; height: 400px; object-fit: cover; filter: sepia(20%); }}
        .hotel-editorial {{ margin-top: 40px; }}
        .hotel-title {{ font-family: 'Playfair Display', serif; font-size: 2.5rem; margin-bottom: 15px; }}
        .assistant-note {{ font-style: italic; color: var(--primary); font-size: 1.1rem; margin: 20px 0; border-left: 3px solid #eee; padding-left: 20px; }}
        .hotel-meta {{ font-weight: 800; letter-spacing: 2px; text-transform: uppercase; font-size: 0.8rem; color: #94a3b8; }}

        /* Visual Trend Integration */
        .trend-frame {{
            background: var(--glass);
            backdrop-filter: blur(25px);
            padding: 80px;
            transform: rotate(-2deg);
            border: 10px solid white;
            box-shadow: 0 40px 100px rgba(0,0,0,0.1);
        }}
        .pulse-header {{ font-family: 'Playfair Display', serif; font-size: 3rem; margin-bottom: 40px; text-align: center; }}
        
        footer {{ text-align: center; margin-top: 150px; font-weight: 800; letter-spacing: 12px; font-size: 0.7rem; color: var(--primary); text-transform: uppercase; }}
    </style>
</head>
<body>
    <div class="editorial-canvas">
        <header>
            <div class="badge">{today_str} • Private Briefing</div>
            <h1>YIDAN TRAVEL:<br>THE PRIVATE BRIEFING</h1>
        </header>

        <section class="flight-container">
            {generate_status_cards(reports_data)}
        </section>

        <div class="stay-header">Sanctuary Spotlight</div>
        <section class="hotel-grid">
            {hotel_html}
        </section>

        <section class="trend-frame">
            <div class="pulse-header">Market Pulse & Intelligence</div>
            <img src="price_trend.png" style="width:100%;" alt="Trend Data">
            <div style="margin-top: 60px;">
                {recommendation_html}
            </div>
        </section>

        <footer>
            Antigravity Private Assistant Edition
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
        card_class = "card-sfo" if "SFO" in data['route_name'] else "card-psp"
        
        # asistente picks (alaska/delta)
        picks = [f for f in data['all_flights'] if f.get('is_priority')]
        picks_html = "".join([f'<div class="assistant-pick"><strong>{p["carrier"]}</strong>: ${p["price"]} ({p["duration"]})</div>' for p in picks[:2]])
        
        # other options
        others = [f for f in data['all_flights'] if not f.get('is_priority')]
        others_html = ", ".join([f'{o["carrier"]} (${o["price"]})' for o in others[:2]])
        
        cards += f"""
        <div class="flight-card {card_class}">
            <div class="route-label">{data['route_name']}</div>
            <div style="font-weight: 800; font-size: 0.8rem; letter-spacing: 3px; color: var(--primary);">{data['dates']}</div>
            <div class="price-hero">${data['price']}</div>
            
            <div class="sweep-section">
                <div class="sweep-title">Assistant's Market Sweep</div>
                {picks_html}
                <div class="other-options">
                    <span>Others Considered:</span>
                    <span>{others_html}</span>
                </div>
            </div>
        </div>
        """
    return cards

async def run_tracker():
    history = load_history()
    today_str = datetime.date.today().isoformat()
    
    HOTELS = [
        {
            "name": "1 Hotel San Francisco", 
            "city": "SFO", 
            "vibe": "Nature-inspired luxury on the Embarcadero.", 
            "tip": "this room has the best natural light for your morning routine.",
            "rate": 325
        },
        {
            "name": "Korakia Pensione", 
            "city": "PSP", 
            "vibe": "Moroccan-inspired desert soul.", 
            "tip": "the courtyard here is perfect for evening unwinding.",
            "rate": 425
        }
    ]
    
    print(f"--- YIDAN TRAVEL: THE PRIVATE BRIEFING ({today_str}) ---")
    
    reports_data = []
    recommendations = []

    for task in TASKS:
        print(f"Editorial Sweep: {task['route_name']}...")
        all_flights = await fetch_flight_price(task)
        
        if not all_flights:
            continue
            
        value_leader = min(all_flights, key=lambda x: x['price'])
        market_avg = round(sum(f['price'] for f in all_flights) / len(all_flights), 2)
        
        task_id = task["id"]
        
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
            "market_avg": market_avg,
            "all_flights": all_flights,
        })
        
        recommendations.append(get_recommendation(task_id, value_leader["price"], stats, task["price_trigger"]))

    save_history(history)
    generate_trend_chart(history)
    generate_html_report(reports_data, recommendations, HOTELS)
    print("Editorial Update Complete: flight_report.html")

if __name__ == "__main__":
    asyncio.run(run_tracker())
