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
    """Generates a 14-day line chart with market averages and editorial styling."""
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
        # Using a star marker as a placeholder for the "Kitty style"
        plt.plot(df['date'], df['price'], marker='*', markersize=12, label=f"{label} (Best)", color=colors[i % len(colors)], linewidth=3)
        
        if 'market_avg' in df.columns:
            plt.plot(df['date'], df['market_avg'], linestyle='--', alpha=0.3, color='#94a3b8', label=f"{label} (Market Avg)")

    plt.title("Price Intelligence Trends", fontsize=16, fontweight='bold', color='#ff1493', pad=25)
    plt.xlabel("Timeline", fontsize=11, color='#64748b')
    plt.ylabel("USD Value", fontsize=11, color='#64748b')
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
    """Generates The Editorial Edition: A digital travel magazine."""
    today_str = datetime.date.today().strftime("%B %d, %Y")
    
    recommendation_html = "".join([f'<div class="recommendation-pill">⭐ {rec}</div>' for rec in recommendations])
    hotel_html = "".join([f"""
        <div class="hotel-editorial">
            <div class="hotel-image-wrapper">
                <img src="{'hotel_sfo.png' if h['city'] == 'SFO' else 'hotel_psp.png'}" alt="{h['name']}">
            </div>
            <div class="hotel-body">
                <div class="hotel-name">{h['name']}</div>
                <div class="hotel-vibe">"{h['vibe']}"</div>
                <div class="hotel-meta">Curated Rate: From ${h['rate']}/night</div>
            </div>
        </div>
    """ for h in hotels])

    html_template = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Antigravity | The Editorial Edition</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&family=Playfair+Display:ital,wght@0,700;0,900;1,700&display=swap" rel="stylesheet">
    <style>
        :root {{
            --primary: #ff1493;
            --secondary: #ff69b4;
            --accent: #d81b60;
            --glass-bg: rgba(255, 255, 255, 0.7);
            --gradient: linear-gradient(135deg, #ff69b4, #ffb6c1);
            --text: #2c3e50;
        }}

        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ 
            font-family: 'Outfit', sans-serif; 
            background: url('hello_kitty_vacation_bg.png') no-repeat center center fixed;
            background-size: cover;
            color: var(--text);
            padding: 40px;
            overflow-x: hidden;
        }}

        /* The Editorial Grid */
        .magazine-canvas {{
            display: grid;
            grid-template-columns: 300px 1fr;
            gap: 60px;
            max-width: 1400px;
            margin: 0 auto;
        }}

        /* Floating Sidebar */
        aside {{
            position: sticky;
            top: 40px;
            height: calc(100vh - 80px);
            background: var(--glass-bg);
            backdrop-filter: blur(25px);
            border-radius: 40px;
            padding: 60px 40px;
            border: 1px solid rgba(255, 255, 255, 0.4);
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            box-shadow: 20px 0 50px rgba(0,0,0,0.05);
        }}

        .branding h2 {{ font-family: 'Playfair Display', serif; font-size: 2.5rem; line-height: 1; color: var(--primary); }}
        .branding p {{ font-weight: 800; text-transform: uppercase; letter-spacing: 4px; font-size: 0.7rem; color: var(--accent); margin-top: 20px; }}

        /* The Main Content */
        main {{ display: flex; flex-direction: column; gap: 80px; }}

        /* Polaroid Flight Cards */
        .flight-stack {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 40px; }}
        .polaroid-card {{
            background: white;
            padding: 25px 25px 60px;
            border-radius: 4px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.1);
            transform: rotate(-1deg);
            transition: transform 0.3s ease;
            position: relative;
        }}
        .polaroid-card:nth-child(even) {{ transform: rotate(1deg); }}
        .polaroid-card:hover {{ transform: scale(1.02) rotate(0); z-index: 10; }}

        .card-header {{ 
            background: #f8fafc; 
            height: 200px; 
            display: flex; 
            flex-direction: column; 
            justify-content: center; 
            align-items: center; 
            margin-bottom: 20px;
            border-radius: 4px;
            position: relative;
        }}
        .route-title {{ font-family: 'Playfair Display', serif; font-size: 2.2rem; font-weight: 900; }}
        .price-badge {{ 
            position: absolute; 
            bottom: -20px; 
            right: 20px; 
            background: var(--gradient); 
            color: white; 
            padding: 15px 25px; 
            font-size: 2rem; 
            font-weight: 800; 
            border-radius: 10px;
            box-shadow: 0 10px 20px rgba(255, 105, 180, 0.4);
        }}

        /* Price Spectrum */
        .spectrum-container {{ position: relative; height: 80px; margin-top: 40px; padding: 0 20px; }}
        .spectrum-line {{ position: absolute; top: 40px; left: 0; width: 100%; height: 2px; background: #eee; }}
        .spectrum-point {{ 
            position: absolute; 
            width: 12px; 
            height: 12px; 
            background: var(--primary); 
            border-radius: 50%; 
            top: 35px;
            cursor: pointer;
        }}
        .spectrum-label {{ 
            position: absolute; 
            top: -45px; 
            left: 50%; 
            transform: translateX(-50%); 
            font-size: 0.65rem; 
            font-weight: 800; 
            text-align: center; 
            white-space: nowrap; 
            color: #64748b;
        }}

        /* Sanctuary Sidebar Inspiration */
        .sanctuary-section h3 {{ font-family: 'Playfair Display', serif; font-size: 3rem; margin-bottom: 40px; }}
        .hotel-editorial {{ 
            background: var(--glass-bg); 
            backdrop-filter: blur(25px); 
            border-radius: 50px; 
            padding: 40px; 
            display: grid; 
            grid-template-columns: 1fr 1fr; 
            gap: 40px; 
            align-items: center; 
            border: 1px solid rgba(255,255,255,0.4);
            margin-bottom: 40px;
        }}
        .hotel-image-wrapper img {{ width: 100%; border-radius: 30px; box-shadow: 0 20px 40px rgba(0,0,0,0.15); }}
        .hotel-name {{ font-family: 'Playfair Display', serif; font-size: 2.5rem; color: var(--primary); }}
        .hotel-vibe {{ font-style: italic; font-size: 1.2rem; color: #64748b; margin: 20px 0; }}
        .hotel-meta {{ font-weight: 800; color: var(--accent); text-transform: uppercase; letter-spacing: 2px; }}

        /* Trend Box */
        .trend-frame {{ 
            background: white; 
            padding: 40px; 
            border-radius: 50px; 
            transform: rotate(-0.5deg); 
            box-shadow: 0 30px 60px rgba(0,0,0,0.05); 
            margin-top: 40px;
        }}
        .trend-frame h4 {{ font-family: 'Playfair Display', serif; font-size: 2rem; margin-bottom: 20px; }}
        .trend-image {{ width: 100%; filter: saturate(1.2); }}

        .rec-stack {{ display: flex; flex-direction: column; gap: 15px; margin-top: 40px; }}
        .recommendation-pill {{ 
            background: white; 
            padding: 20px 30px; 
            border-radius: 20px; 
            font-weight: 600; 
            border-left: 5px solid var(--primary);
            box-shadow: 0 5px 15px rgba(0,0,0,0.02);
        }}

        footer {{ margin-top: 100px; font-size: 0.7rem; letter-spacing: 4px; color: var(--accent); font-weight: 800; }}
    </style>
</head>
<body>
    <div class="magazine-canvas">
        <aside>
            <div class="branding">
                <h2>The Editorial Edition</h2>
                <p>Curated by Yidan & Antigravity</p>
            </div>
            <div class="date-marker">
                <div style="font-size: 0.8rem; font-weight: 800; color: var(--text)">{today_str}</div>
                <div style="font-size: 12rem; font-family: 'Playfair Display', serif; color: rgba(255,20,147,0.05); position: absolute; bottom:-40px; left:-20px;">26</div>
            </div>
        </aside>

        <main>
            <section class="flight-stack">
                {generate_status_cards(reports_data)}
            </section>

            <section class="rec-stack">
                {recommendation_html}
            </section>

            <section class="sanctuary-section">
                <h3>Curated Sanctuary</h3>
                {hotel_html}
            </section>

            <section class="trend-frame">
                <h4>Market Intelligence Trends</h4>
                <img src="price_trend.png" class="trend-image" alt="Visual Intelligence">
            </section>

            <footer>
                © 2026 ANTIGRAVITY TRAVEL CURATION • ALL RIGHTS RESERVED
            </footer>
        </main>
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
        <div class="polaroid-card">
            <div class="card-header">
                <div class="route-title">{data['route_name']}</div>
                <div style="color: #64748b; font-weight: 800; letter-spacing: 2px;">{data['dates']}</div>
            </div>
            <div class="price-badge">${data['price']}</div>
            <div style="margin-top: 40px;">
                <div style="font-size: 0.7rem; font-weight: 800; text-transform: uppercase; color: var(--accent); letter-spacing: 1px;">Market Positioning</div>
                {generate_price_spectrum(data['all_flights'])}
            </div>
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
