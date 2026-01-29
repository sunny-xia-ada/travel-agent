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
    """Generates a minimal, axe-less trend chart for the editorial edition."""
    plt.figure(figsize=(10, 4))
    plt.style.use('seaborn-whitegrid')
    
    # Soft Pastel Palette
    colors = ['#FFD1DC', '#FADADD']
    for i, (task_id, data) in enumerate(history.items()):
        records = data.get("history", [])
        df = pd.DataFrame(records)
        if df.empty: continue
        
        df = df[df['price'] > 0]
        if df.empty: continue
        
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').tail(14)
        
        plt.plot(df['date'], df['price'], color=colors[i % len(colors)], linewidth=4, alpha=0.8)
        
        # Mark local minima with a soft heart
        if len(df) > 2:
            min_price = df['price'].min()
            min_points = df[df['price'] == min_price]
            plt.scatter(min_points['date'], min_points['price'], color='#FFD1DC', s=100, alpha=0.5, edgecolors='none')

    # Remove all axes for minimalist look
    plt.axis('off')
    plt.tight_layout(pad=0)
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
    """Generates a Soft-Focus Editorial Briefing with asymmetric layout."""
    today_str = datetime.date.today().strftime("%B %d, %Y")
    
    recommendation_html = "".join([f'<div class="briefing-pill">{rec}</div>' for rec in recommendations])
    hotel_html = "".join([f"""
        <div class="hotel-magazine-card">
            <div class="hotel-magazine-img">
                <img src="{'hotel_sfo.png' if h['city'] == 'SFO' else 'hotel_psp.png'}" alt="{h['name']}">
            </div>
            <div class="hotel-magazine-content">
                <div class="magazine-label">THE STAY</div>
                <div class="magazine-hotel-name">{h['name']}</div>
                <div class="magazine-vibe">{h['vibe']}</div>
                <div class="magazine-tip">“{h['tip']}”</div>
            </div>
        </div>
    """ for h in hotels])

    html_template = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>YIDAN TRAVEL | PRIVATE BRIEFING</title>
    <link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@300;400;600&family=Outfit:wght@300;500;800&display=swap" rel="stylesheet">
    <style>
        :root {{
            --pastel-pink: #FFD1DC;
            --soft-white: rgba(255, 255, 255, 0.4);
            --text-main: #3d3d3d;
            --text-muted: #8e8e8e;
            --accent: #FADADD;
        }}

        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        
        body {{ 
            font-family: 'Outfit', sans-serif; 
            background: url('hello_kitty_vacation_bg.png') no-repeat center center fixed;
            background-size: cover;
            color: var(--text-main);
            overflow-x: hidden;
            min-height: 200vh;
        }}

        /* Grain Texture Overlay */
        .grain {{
            position: fixed;
            top: 0; left: 0; width: 100%; height: 100%;
            background: url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noiseFilter'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.65' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noiseFilter)'/%3E%3C/svg%3E");
            opacity: 0.04;
            pointer-events: none;
            z-index: 1000;
        }}

        .editorial-wrapper {{
            position: relative;
            width: 100%;
            backdrop-filter: blur(15px) brightness(1.05);
            -webkit-backdrop-filter: blur(15px) brightness(1.05);
            background: rgba(255, 255, 255, 0.1);
            padding: 120px 80px;
        }}

        header {{ 
            margin-bottom: 200px;
            padding-left: 10%;
        }}

        .briefing-title {{
            font-family: 'Cormorant Garamond', serif;
            font-size: 5.5rem;
            font-weight: 300;
            letter-spacing: 4px;
            color: var(--pastel-pink);
            line-height: 1;
            margin-bottom: 15px;
            text-transform: uppercase;
        }}

        .signature-line {{
            font-family: 'Cormorant Garamond', serif;
            font-size: 1.4rem;
            font-style: italic;
            color: var(--text-muted);
            letter-spacing: 1px;
        }}

        /* Asymmetric Layout */
        .travel-grid {{
            position: relative;
            max-width: 1400px;
            margin: 0 auto;
            height: 1200px;
        }}

        .route-article {{
            position: absolute;
            width: 38%;
            background: var(--soft-white);
            padding: 60px;
            border-radius: 2px;
            box-shadow: 0 40px 100px rgba(0,0,0,0.03);
            border: 1px solid rgba(255, 255, 255, 0.2);
        }}

        .route-sfo {{ top: 0; left: 5%; }}
        .route-psp {{ top: 500px; right: 5%; }}

        .label {{
            font-size: 0.7rem;
            font-weight: 800;
            letter-spacing: 4px;
            color: var(--text-muted);
            text-transform: uppercase;
            margin-bottom: 20px;
        }}

        .route-name {{
            font-family: Cormorant Garamond;
            font-size: 3rem;
            font-weight: 300;
            margin-bottom: 40px;
            border-bottom: 1px solid #eee;
            padding-bottom: 20px;
        }}

        .price-display {{
            display: flex;
            align-items: baseline;
            gap: 20px;
            margin-bottom: 40px;
        }}

        .price-val {{
            font-size: 6rem;
            font-weight: 300;
            color: var(--pastel-pink);
            line-height: 1;
        }}

        .price-unit {{
            font-size: 1.2rem;
            font-weight: 500;
            color: var(--text-muted);
        }}

        /* Market Comparison */
        .comparison-list {{ margin-top: 40px; }}
        .compare-item {{
            display: flex;
            justify-content: space-between;
            padding: 12px 0;
            font-size: 0.85rem;
            border-bottom: 1px solid rgba(0,0,0,0.03);
        }}
        .highlighted {{
            color: var(--text-main);
            font-weight: 800;
            text-shadow: 0 0 15px rgba(255, 209, 220, 0.6);
        }}
        .muted-pink {{ color: #d6b8be; }}

        /* Hotel Vertical Column */
        .stay-section {{
            margin-top: 300px;
            display: flex;
            gap: 100px;
            justify-content: center;
        }}

        .hotel-magazine-card {{
            width: 400px;
            display: flex;
            flex-direction: column;
            gap: 30px;
        }}

        .hotel-magazine-img img {{
            width: 100%;
            height: 600px; /* 2:3 approx */
            object-fit: cover;
            filter: brightness(0.95) contrast(0.9);
        }}

        .hotel-magazine-content {{ padding: 0 20px; }}
        .magazine-label {{ font-size: 0.7rem; font-weight: 800; letter-spacing: 5px; color: var(--pastel-pink); margin-bottom: 10px; }}
        .magazine-hotel-name {{ font-family: 'Cormorant Garamond'; font-size: 2.5rem; margin-bottom: 15px; }}
        .magazine-vibe {{ font-size: 0.9rem; color: var(--text-muted); line-height: 1.6; margin-bottom: 20px; }}
        .magazine-tip {{ font-style: italic; color: var(--text-main); font-weight: 500; }}

        /* Pulse Section */
        .pulse-section {{
            margin-top: 400px;
            text-align: center;
            padding: 0 100px;
        }}

        .trend-chart-img {{
            width: 100%;
            opacity: 0.6;
            margin-bottom: 80px;
        }}

        .briefing-pill {{
            font-family: Cormorant Garamond;
            font-size: 1.8rem;
            color: var(--text-main);
            margin-bottom: 20px;
            font-style: italic;
        }}

        footer {{
            margin-top: 200px;
            text-align: center;
            font-size: 0.7rem;
            font-weight: 800;
            letter-spacing: 6px;
            color: var(--text-muted);
            text-transform: uppercase;
        }}
    </style>
</head>
<body>
    <div class="grain"></div>
    <div class="editorial-wrapper">
        <header>
            <div class="signature-line">London Edition • Spring 2026</div>
            <h1 class="briefing-title">YIDAN TRAVEL:<br>THE PRIVATE BRIEFING</h1>
            <div class="signature-line">Curated by Antigravity for your Spring escape.</div>
        </header>

        <section class="travel-grid">
            {generate_status_cards(reports_data)}
        </section>

        <div class="label" style="text-align:center; margin-bottom: 60px;">THE SANCTUARY</div>
        <section class="stay-section">
            {hotel_html}
        </section>

        <section class="pulse-section">
            <div class="label">MARKET PULSE</div>
            <img src="price_trend.png" class="trend-chart-img" alt="Pulse">
            <div class="recommendations">
                {recommendation_html}
            </div>
        </section>

        <footer>
            A Private Publication for Yidan Yan • All Rights Reserved
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
        route_class = "route-sfo" if "SFO" in data['route_name'] else "route-psp"
        
        # picks vs alts
        picks = [f for f in data['all_flights'] if f.get('is_priority')]
        alts = [f for f in data['all_flights'] if not f.get('is_priority')]
        
        picks_html = "".join([f'<div class="compare-item highlighted"><span>{p["carrier"]}</span><span>${p["price"]}</span></div>' for p in picks[:2]])
        alts_html = "".join([f'<div class="compare-item muted-pink"><span>{a["carrier"]}</span><span>${a["price"]}</span></div>' for a in alts[:2]])

        cards += f"""
        <article class="route-article {route_class}">
            <div class="label">TRANSIT • {data['dates']}</div>
            <div class="route-name">{data['route_name']}</div>
            <div class="price-display">
                <span class="price-val">${data['price']}</span>
                <span class="price-unit">Curated Best Value</span>
            </div>
            
            <div class="comparison-list">
                <div class="label" style="font-size:0.6rem; color:var(--pastel-pink)">MARKET COMPARISON</div>
                {picks_html}
                {alts_html}
            </div>
        </article>
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
