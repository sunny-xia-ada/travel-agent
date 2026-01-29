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
    """Generates a 14-day line chart with market averages, assistant edition."""
    plt.figure(figsize=(10, 6))
    plt.style.use('seaborn-whitegrid')
    
    colors = ['#ff69b4', '#d147a3']
    for i, (task_id, data) in enumerate(history.items()):
        records = data.get("history", [])
        df = pd.DataFrame(records)
        if df.empty: continue
        
        df = df[df['price'] > 0]
        if df.empty: continue
        
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').tail(14)
        
        label = task_id.replace('_', ' ').title()
        plt.plot(df['date'], df['price'], marker='s', markersize=8, label=f"{label} (Assigned)", color=colors[i % len(colors)], linewidth=2.5)
        
        if 'market_avg' in df.columns:
            plt.plot(df['date'], df['market_avg'], linestyle=':', alpha=0.4, color='#94a3b8', label=f"{label} (Momentum)")

    plt.title("Market Momentum Analysis", fontsize=16, fontweight='bold', color='#2c3e50', pad=25)
    plt.xlabel("Timeline", fontsize=11, color='#64748b')
    plt.ylabel("Value (USD)", fontsize=11, color='#64748b')
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
    """Generates Yidan's Private Travel Concierge Briefing."""
    today_str = datetime.date.today().strftime("%B %d, %Y")
    
    recommendation_html = "".join([f'<div class="briefing-note">✧ {rec}</div>' for rec in recommendations])
    hotel_html = "".join([f"""
        <div class="property-highlight">
            <div class="property-image">
                <img src="{'hotel_sfo.png' if h['city'] == 'SFO' else 'hotel_psp.png'}" alt="{h['name']}">
            </div>
            <div class="property-details">
                <div class="assistant-stamp">Assistant's Pick</div>
                <div class="property-name">{h['name']}</div>
                <div class="property-vibe">{h['vibe']}</div>
                <div class="assistant-tip">
                    <strong>Concierge Note:</strong> {h['tip']}
                </div>
                <div class="property-rate">Nightly rate from ${h['rate']}</div>
            </div>
        </div>
    """ for h in hotels])

    html_template = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Yidan's Personal Travel Briefing</title>
    <link href="https://fonts.googleapis.com/css2?family=Great+Vibes&family=Outfit:wght@300;400;600;800&family=Playfair+Display:ital,wght@0,700;0,900;1,700&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg-paper: linear-gradient(to bottom right, #ffffff, #fff0f5);
            --primary: #2c3e50;
            --accent-pink: #ff6eb4;
            --muted: #94a3b8;
            --glass: rgba(255, 255, 255, 0.6);
            --note: #fff9c4;
        }}

        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ 
            font-family: 'Outfit', sans-serif; 
            background: var(--bg-paper);
            min-height: 100vh;
            color: var(--primary);
            padding: 80px;
            overflow-x: hidden;
            background-image: radial-gradient(#ff6eb4 0.5px, transparent 0.5px);
            background-size: 40px 40px;
            background-opacity: 0.1;
        }}

        .staggered-canvas {{
            max-width: 1200px;
            margin: 0 auto;
            display: flex;
            flex-direction: column;
            gap: 120px;
        }}

        /* Header Signature Style */
        header {{ margin-bottom: 100px; }}
        .signature {{ 
            font-family: 'Great Vibes', cursive; 
            font-size: 4.5rem; 
            color: var(--accent-pink); 
            line-height: 1;
            margin-bottom: 10px;
        }}
        .briefing-title {{ 
            font-weight: 800; 
            text-transform: uppercase; 
            letter-spacing: 8px; 
            font-size: 0.9rem; 
            color: var(--muted);
        }}

        /* Staggered Flight Cards */
        .flight-memo {{
            position: relative;
            width: 70%;
            background: white;
            padding: 60px;
            border-radius: 2px;
            box-shadow: 20px 20px 60px rgba(0,0,0,0.03);
            border-left: 1px solid #f1f5f9;
        }}
        .memo-right {{ align-self: flex-end; transform: translateX(20px); }}
        .memo-left {{ align-self: flex-start; transform: translateX(-20px); }}

        .route-header {{ font-family: 'Playfair Display', serif; font-size: 3rem; margin-bottom: 30px; border-bottom: 1px solid #f1f5f9; padding-bottom: 20px; }}
        .price-focus {{ font-size: 4rem; font-weight: 800; color: var(--accent-pink); margin: 20px 0; }}
        
        /* The Sticky Note */
        .sticky-note {{
            position: absolute;
            width: 220px;
            padding: 25px;
            background: var(--note);
            box-shadow: 5px 5px 15px rgba(0,0,0,0.05);
            font-size: 0.85rem;
            font-weight: 600;
            line-height: 1.4;
            transform: rotate(2deg);
            right: -110px;
            top: 40px;
            border-radius: 4px;
            z-index: 10;
        }}
        .sticky-note::before {{
            content: "";
            position: absolute;
            top: 0; right: 0;
            border-width: 0 20px 20px 0;
            border-style: solid;
            border-color: #fff transparent transparent transparent;
        }}

        .alternatives {{ margin-top: 40px; border-top: 1px dashed #eee; padding-top: 20px; }}
        .alt-title {{ font-size: 0.7rem; font-weight: 800; text-transform: uppercase; color: var(--muted); letter-spacing: 2px; margin-bottom: 10px; }}
        .alt-row {{ display: flex; justify-content: space-between; font-size: 0.85rem; color: var(--muted); margin-bottom: 5px; }}

        /* Property Highlight */
        .section-label {{ text-align: center; font-weight: 800; letter-spacing: 10px; text-transform: uppercase; font-size: 0.9rem; color: var(--muted); margin-bottom: 60px; }}
        
        .property-highlight {{
            display: grid;
            grid-template-columns: 1.2fr 1fr;
            gap: 60px;
            align-items: center;
            background: var(--glass);
            backdrop-filter: blur(20px);
            padding: 60px;
            border-radius: 80px 0 80px 0;
            box-shadow: 0 40px 100px rgba(0,0,0,0.04);
        }}
        .property-image img {{ width: 100%; border-radius: 60px 0 60px 0; filter: grayscale(20%); transition: filter 0.5s; }}
        .property-image img:hover {{ filter: grayscale(0%); }}
        .assistant-stamp {{ color: var(--accent-pink); font-weight: 800; text-transform: uppercase; font-size: 0.7rem; letter-spacing: 3px; margin-bottom: 15px; }}
        .property-name {{ font-family: 'Playfair Display', serif; font-size: 3.5rem; line-height: 1.1; margin-bottom: 20px; }}
        .property-vibe {{ font-style: italic; color: #64748b; font-size: 1.2rem; margin-bottom: 30px; }}
        .assistant-tip {{ background: #f8fafc; padding: 25px; border-radius: 20px; border-left: 4px solid var(--accent-pink); margin-bottom: 30px; font-size: 0.95rem; }}
        .property-rate {{ font-weight: 800; color: var(--primary); letter-spacing: 1px; }}

        /* Momentum Analysis */
        .glass-frame {{
            background: var(--glass);
            backdrop-filter: blur(30px);
            padding: 80px;
            border-radius: 60px;
            border: 1px solid rgba(255,255,255,0.4);
            box-shadow: 0 50px 120px rgba(0,0,0,0.04);
        }}
        
        .briefing-footer {{ 
            margin-top: 150px; 
            text-align: center; 
            font-size: 0.8rem; 
            color: var(--muted); 
            letter-spacing: 2px;
            font-family: 'Great Vibes', cursive;
            font-size: 2rem;
        }}
    </style>
</head>
<body>
    <div class="staggered-canvas">
        <header>
            <div class="signature">Yidan's Personal Travel Briefing</div>
            <div class="briefing-title">Private Concierge Logic • {today_str}</div>
        </header>

        <section class="memo-stack">
            {generate_status_cards(reports_data)}
        </section>

        <section class="property-grid">
            <div class="section-label">Curated Property Highlights</div>
            {hotel_html}
        </section>

        <section class="glass-frame">
            <div class="section-label" style="margin-bottom:40px;">Market Momentum Analysis</div>
            <img src="price_trend.png" style="width:100%;" alt="Market Intelligence">
        </section>

        <footer class="briefing-footer">
            Always at your service, Antigravity
        </footer>
    </div>
</body>
</html>
    """
    with open("flight_report.html", "w") as f:
        f.write(html_template)

def generate_status_cards(reports_data: List[Dict]) -> str:
    cards = ""
    for i, data in enumerate(reports_data):
        side_class = "memo-left" if i % 2 == 0 else "memo-right"
        
        # Mute alternatives
        alt_html = ""
        best_carrier = data['carrier']
        best_price = data['price']
        
        carriers_seen = set([best_carrier])
        alts = []
        for f in data['all_flights']:
            if f['carrier'] not in carriers_seen:
                alts.append(f)
                carriers_seen.add(f['carrier'])
        
        for alt in sorted(alts, key=lambda x: x['price'])[:3]:
            alt_html += f'<div class="alt-row"><span>{alt["carrier"]}</span><span>${alt["price"]}</span></div>'

        cards += f"""
        <div class="flight-memo {side_class}" style="margin-bottom: 80vh;">
            <div class="sticky-note">
                {data['personal_note']}
            </div>
            <div class="route-header">{data['route_name']}</div>
            <div style="font-size: 0.9rem; font-weight: 800; color: var(--muted); letter-spacing: 3px;">{data['dates']}</div>
            <div class="price-focus">${best_price}</div>
            <div style="font-weight: 800; text-transform: uppercase; font-size: 0.8rem; color: var(--primary);">Assigned: {best_carrier} (Nonstop)</div>
            
            <div class="alternatives">
                <div class="alt-title">Alternatives Considered</div>
                {alt_html}
            </div>
        </div>
        """
    return cards

async def run_tracker():
    history = load_history()
    today_str = datetime.date.today().isoformat()
    
    HOTELS = [
        {
            "name": "Proper Hotel", 
            "city": "SFO", 
            "vibe": "Kelly Wearstler maximalism in a historic landmark.", 
            "rate": 295,
            "tip": "I've flagged the Corner Suite for its sweeping views of the Bay Bridge."
        },
        {
            "name": "Korakia Pensione", 
            "city": "PSP", 
            "vibe": "Moroccan-inspired desert soul and architectural beauty.", 
            "rate": 425,
            "tip": "The Mediterranean Villa is particularly quiet this time of year."
        }
    ]
    
    print(f"--- Yidan's Private Concierge Sweep ({today_str}) ---")
    
    reports_data = []
    recommendations = []

    for task in TASKS:
        print(f"Personal Briefing: Sweeping {task['route_name']}...")
        all_flights = await fetch_flight_price(task)
        
        if not all_flights:
            continue
            
        value_leader = min(all_flights, key=lambda x: x['price'])
        market_avg = round(sum(f['price'] for f in all_flights) / len(all_flights), 2)
        
        priority_flights = [f for f in all_flights if f.get('is_priority')]
        aesthetic_leader = min(priority_flights, key=lambda x: x['price']) if priority_flights else None
        
        task_id = task["id"]
        
        # Personalized Notes
        personal_note = f"Yidan, I've noticed {value_leader['carrier']} is currently the most efficient choice for this route."
        if aesthetic_leader and aesthetic_leader['carrier'] == "Alaska":
            personal_note = "Yidan, I noticed Alaska has significantly better legroom for this specific leg, worth the focus."
        elif aesthetic_leader and aesthetic_leader['carrier'] == "Delta":
            personal_note = "Yidan, Delta's morning service is exceptionally reliable for this window."

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
            "personal_note": personal_note,
            "trigger": task['price_trigger']
        })
        
        recommendations.append(get_recommendation(task_id, value_leader["price"], stats, task["price_trigger"]))

    save_history(history)
    generate_trend_chart(history)
    generate_html_report(reports_data, recommendations, HOTELS)
    print("Concierge Briefing Complete: flight_report.html")

if __name__ == "__main__":
    asyncio.run(run_tracker())
