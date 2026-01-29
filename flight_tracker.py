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
    """Generates a 14-day line chart with the Kitty Pink palette."""
    plt.figure(figsize=(10, 6))
    plt.style.use('seaborn-whitegrid')
    
    # Kitty Pink Palette
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
        plt.plot(df['date'], df['price'], marker='o', markersize=8, label=f"{label}", color=colors[i % len(colors)], linewidth=3)
        
        if 'market_avg' in df.columns:
            plt.plot(df['date'], df['market_avg'], linestyle='--', alpha=0.3, color='#f472b6', label=f"{label} Avg")

    plt.title("Price Momentum Analysis", fontsize=16, fontweight='bold', color='#ff1493', pad=25)
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
    """Generates a Kitty-anchored Private Travel Briefing."""
    today_str = datetime.date.today().strftime("%B %d, %Y")
    
    recommendation_html = "".join([f'<div class="briefing-pill">⭐ {rec}</div>' for rec in recommendations])
    hotel_html = "".join([f"""
        <div class="hotel-polaroid">
            <div class="ribbon">🎀</div>
            <div class="hotel-img">
                <img src="{'hotel_sfo.png' if h['city'] == 'SFO' else 'hotel_psp.png'}" alt="{h['name']}">
            </div>
            <div class="hotel-info">
                <div class="hotel-name">{h['name']}</div>
                <div class="hotel-vibe">{h['vibe']}</div>
                <div class="hotel-rate">From ${h['rate']}/night</div>
            </div>
        </div>
    """ for h in hotels])

    html_template = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Personal Briefing for Yidan</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&family=Playfair+Display:ital,wght@1,700&display=swap" rel="stylesheet">
    <style>
        :root {{
            --primary: #ff69b4;
            --secondary: #ff1493;
            --accent: #d81b60;
            --glass: rgba(255, 255, 255, 0.85);
            --text: #333333;
        }}

        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ 
            font-family: 'Outfit', sans-serif; 
            background: url('hello_kitty_vacation_bg.png') no-repeat center center fixed;
            background-size: cover;
            color: var(--text);
            padding: 60px 20px;
        }}

        .briefing-container {{ 
            max-width: 900px; 
            margin: 0 auto; 
            background: var(--glass);
            backdrop-filter: blur(20px);
            padding: 80px;
            border-radius: 60px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.1);
            border: 5px solid white;
        }}

        header {{ margin-bottom: 60px; text-align: center; }}
        .signature {{ 
            font-family: 'Playfair Display', serif; 
            font-style: italic; 
            font-size: 3.5rem; 
            color: var(--secondary); 
            margin-bottom: 10px;
        }}
        .briefing-date {{ font-weight: 800; text-transform: uppercase; letter-spacing: 5px; color: var(--primary); font-size: 0.8rem; }}

        /* Choices for You */
        .choices-section {{ margin-bottom: 80px; }}
        .section-header {{ font-size: 1.2rem; font-weight: 800; text-transform: uppercase; letter-spacing: 4px; color: var(--accent); margin-bottom: 40px; text-align: center; }}
        
        .flight-choice {{ background: white; padding: 40px; border-radius: 30px; margin-bottom: 30px; box-shadow: 0 10px 20px rgba(0,0,0,0.02); }}
        .choice-top {{ display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 20px; }}
        .choice-route {{ font-size: 1.5rem; font-weight: 800; color: var(--text); }}
        .choice-price {{ font-size: 2.5rem; font-weight: 800; color: var(--secondary); }}
        
        .market-alt {{ font-size: 0.85rem; color: #94a3b8; font-weight: 600; border-top: 1px dashed #eee; padding-top: 15px; margin-top: 15px; }}

        /* Everywhere You'll Stay */
        .stay-section {{ display: grid; grid-template-columns: 1fr 1fr; gap: 40px; margin-bottom: 80px; }}
        .hotel-polaroid {{ 
            background: white; 
            padding: 20px 20px 40px; 
            box-shadow: 0 10px 30px rgba(0,0,0,0.05); 
            position: relative;
            transform: rotate(-1deg);
        }}
        .hotel-polaroid:nth-child(even) {{ transform: rotate(1deg); }}
        .ribbon {{ position: absolute; top: -15px; right: -15px; font-size: 2.5rem; z-index: 10; }}
        .hotel-img img {{ width: 100%; height: 250px; object-fit: cover; border-radius: 4px; }}
        .hotel-info {{ margin-top: 20px; }}
        .hotel-name {{ font-family: 'Playfair Display', serif; font-size: 1.8rem; color: var(--text); }}
        .hotel-vibe {{ font-size: 0.8rem; color: #64748b; margin: 10px 0; font-style: italic; }}
        .hotel-rate {{ font-weight: 800; color: var(--accent); }}

        /* Market Pulse */
        .pulse-memo {{ 
            background: white; 
            padding: 50px; 
            border-radius: 40px; 
            box-shadow: 0 10px 40px rgba(255, 105, 180, 0.1); 
            border: 2px solid #fff5f8;
        }}
        .pulse-chart {{ width: 100%; border-radius: 20px; }}

        .briefing-pill {{ 
            background: #fff5f8; 
            padding: 20px 30px; 
            border-radius: 20px; 
            font-weight: 600; 
            margin-bottom: 15px;
            color: var(--accent);
            border-left: 5px solid var(--primary);
        }}

        footer {{ text-align: center; margin-top: 80px; font-weight: 800; color: var(--primary); letter-spacing: 3px; font-size: 0.7rem; text-transform: uppercase; }}
    </style>
</head>
<body>
    <div class="briefing-container">
        <header>
            <div class="signature">A Personal Travel Briefing for Yidan</div>
            <div class="briefing-date">{today_str}</div>
        </header>

        <section class="choices-section">
            <div class="section-header">Choices curated for you</div>
            {generate_status_cards(reports_data)}
        </section>

        <div class="section-header">Where You'll Stay</div>
        <section class="stay-section">
            {hotel_html}
        </section>

        <section class="pulse-memo">
            <div class="section-header">Market Pulse</div>
            <img src="price_trend.png" class="pulse-chart" alt="Market Intelligence">
            <div style="margin-top: 40px;">
                {recommendation_html}
            </div>
        </section>

        <footer>
            Generated for Yidan | Antigravity AI Travel Assistant
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
        # Build market alternatives sub-text
        alt_texts = []
        best_carrier = data['carrier']
        carriers_seen = set([best_carrier])
        
        for f in sorted(data['all_flights'], key=lambda x: x['price']):
            if f['carrier'] not in carriers_seen:
                alt_texts.append(f"{f['carrier']} (${f['price']})")
                carriers_seen.add(f['carrier'])
                
        alt_html = f'<div class="market-alt">Market Alternatives: {", ".join(alt_texts[:3])}</div>' if alt_texts else ""
        
        cards += f"""
        <div class="flight-choice">
            <div class="choice-top">
                <div class="choice-route">{data['route_name']}</div>
                <div class="choice-price">${data['price']}</div>
            </div>
            <div style="font-weight: 800; color: var(--primary); font-size: 0.9rem; margin-bottom: 10px;">Primary Pick: {best_carrier} (Nonstop)</div>
            <div style="color: #64748b; font-size: 0.85rem; margin-bottom: 20px;">{data['dates']}</div>
            {alt_html}
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
            "rate": 325
        },
        {
            "name": "Korakia Pensione", 
            "city": "PSP", 
            "vibe": "Moroccan-inspired desert soul and architectural beauty.", 
            "rate": 425
        }
    ]
    
    print(f"--- Yidan's Private Briefing Refinement ({today_str}) ---")
    
    reports_data = []
    recommendations = []

    for task in TASKS:
        print(f"Briefing Update: Sweeping {task['route_name']}...")
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
    print("Briefing Optimized: flight_report.html")

if __name__ == "__main__":
    asyncio.run(run_tracker())
