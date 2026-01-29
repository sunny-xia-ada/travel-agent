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
    """Generates a transparent, floating trend chart for the artistic edition."""
    plt.figure(figsize=(10, 4))
    plt.style.use('seaborn-whitegrid')
    
    # Artistic Palette
    colors = ['#F48FB1', '#FF4081']
    for i, (task_id, data) in enumerate(history.items()):
        records = data.get("history", [])
        df = pd.DataFrame(records)
        if df.empty: continue
        
        df = df[df['price'] > 0]
        if df.empty: continue
        
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').tail(14)
        
        plt.plot(df['date'], df['price'], color=colors[i % len(colors)], linewidth=4, alpha=0.9, label=task_id)
        
        # Highlight lowest points
        if len(df) > 2:
            min_p = df['price'].min()
            pts = df[df['price'] == min_p]
            plt.scatter(pts['date'], pts['price'], color=colors[i % len(colors)], s=120, edgecolors='white', linewidth=2, zorder=5)

    # Remove background and make transparent
    ax = plt.gca()
    ax.set_facecolor('none')
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
    """Generates an Artistic Signature Briefing with bold script and localized glass."""
    today_str = datetime.date.today().strftime("%B %d, %Y")
    
    recommendation_html = "".join([f'<div class="recommendation-memo">{rec}</div>' for rec in recommendations])
    hotel_html = "".join([f"""
        <div class="hotel-poster">
            <div class="assistant-note-tag">Assistant's Pick</div>
            <img src="{'hotel_sfo.png' if h['city'] == 'SFO' else 'hotel_psp.png'}" alt="{h['name']}">
            <div class="hotel-poster-info">
                <div class="hotel-poster-name">{h['name']}</div>
                <div class="hotel-poster-vibe">{h['vibe']}</div>
                <div class="hotel-poster-rate">${h['rate']}/night</div>
            </div>
            <div class="handwritten-memo">Yidan, {h['tip']}</div>
        </div>
    """ for h in hotels])

    html_template = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>YIDAN TRAVEL | PRIVATE BRIEFING</title>
    <link href="https://fonts.googleapis.com/css2?family=Great+Vibes&family=Outfit:wght@300;500;800&display=swap" rel="stylesheet">
    <style>
        :root {{
            --signature-pink: #F48FB1;
            --glass-white: rgba(255, 255, 255, 0.4);
            --text-dark: #2c3e50;
        }}

        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        
        body {{ 
            font-family: 'Outfit', sans-serif; 
            background: url('hello_kitty_vacation_bg.png') no-repeat center center fixed;
            background-size: cover;
            color: var(--text-dark);
            padding: 80px;
        }}

        header {{ margin-bottom: 120px; }}
        .badge {{ 
            font-weight: 800; 
            text-transform: uppercase; 
            letter-spacing: 8px; 
            color: var(--signature-pink); 
            font-size: 0.8rem; 
            margin-bottom: 20px;
        }}
        h1 {{ 
            font-family: 'Great Vibes', cursive;
            font-size: 5rem;
            font-weight: 900;
            color: var(--signature-pink);
            text-shadow: 2px 2px 4px rgba(255,255,255,0.8);
            line-height: 1.2;
        }}

        /* ticket Stack */
        .ticket-stack {{ 
            display: flex; 
            flex-direction: column; 
            gap: 60px; 
            margin-bottom: 150px; 
        }}

        .flight-ticket {{
            position: relative;
            width: 80%;
            background: var(--glass-white);
            backdrop-filter: blur(10px);
            padding: 60px;
            border-radius: 30px;
            border: 1px solid rgba(255, 255, 255, 0.6);
            box-shadow: 0 20px 40px rgba(0,0,0,0.05);
        }}
        
        .ticket-sfo {{ align-self: flex-start; }}
        .ticket-psp {{ align-self: flex-end; transform: translateX(-50px); }}

        .route-title {{ font-family: 'Outfit', sans-serif; font-weight: 300; font-size: 2.5rem; letter-spacing: 2px; }}
        .price-tag {{ font-size: 6rem; font-weight: 800; color: var(--signature-pink); margin: 10px 0; }}

        .handwritten-memo {{
            position: absolute;
            font-family: 'Great Vibes', cursive;
            font-size: 2rem;
            color: var(--signature-pink);
            width: 250px;
            top: 40px;
            right: -150px;
            transform: rotate(5deg);
            background: rgba(255, 255, 255, 0.9);
            padding: 20px;
            border-radius: 10px;
            box-shadow: 5px 5px 15px rgba(0,0,0,0.05);
        }}

        .market-sweep {{
            margin-top: 30px;
            padding-top: 20px;
            border-top: 1px dashed rgba(255,255,255,0.8);
            font-size: 0.8rem;
            font-weight: 500;
            color: #64748b;
        }}

        /* Hotel Posters */
        .stay-labels {{ text-align: center; margin-bottom: 60px; }}
        .hotel-grid {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 100px;
            margin-bottom: 200px;
        }}

        .hotel-poster {{
            position: relative;
            background: white;
            padding: 30px;
            border-radius: 40px;
            box-shadow: 0 30px 60px rgba(0,0,0,0.08);
        }}
        .hotel-poster img {{
            width: 100%;
            height: 550px;
            object-fit: cover;
            border-radius: 30px;
            margin-bottom: 30px;
        }}
        .hotel-poster-info {{ padding: 0 10px; }}
        .hotel-poster-name {{ font-size: 2.2rem; font-weight: 800; margin-bottom: 15px; }}
        .hotel-poster-vibe {{ font-size: 1rem; color: #94a3b8; font-style: italic; margin-bottom: 20px; }}
        .hotel-poster-rate {{ font-weight: 800; color: var(--signature-pink); font-size: 1.2rem; }}
        .assistant-note-tag {{ 
            position: absolute; top: 10px; left: 10px; 
            background: var(--signature-pink); color: white; 
            padding: 8px 15px; border-radius: 20px; 
            font-size: 0.7rem; font-weight: 800; 
            text-transform: uppercase; z-index: 10;
        }}

        /* Floating Trend */
        .market-pulse {{
            background: transparent;
            z-index: 50;
            position: relative;
            text-align: center;
        }}
        .trend-img {{ width: 100%; max-height: 400px; object-fit: contain; }}

        .recommendation-memo {{
            font-family: 'Great Vibes', cursive;
            font-size: 2.5rem;
            color: var(--signature-pink);
            margin: 40px 0;
            line-height: 1.4;
        }}

        footer {{ 
            margin-top: 200px; text-align: center; font-weight: 800; letter-spacing: 5px; color: var(--signature-pink); text-transform: uppercase; font-size: 0.7rem;
        }}
    </style>
</head>
<body>
    <div class="briefing-canvas">
        <header>
            <div class="badge">Travel Agent from Yidan • {today_str}</div>
            <h1>YIDAN TRAVEL: THE PRIVATE BRIEFING</h1>
        </header>

        <section class="ticket-stack">
            {generate_status_cards(reports_data)}
        </section>

        <div class="stay-labels">
            <div class="badge">Curated Stays</div>
            <h2 style="font-family: 'Great Vibes', cursive; font-size: 4rem; color: var(--signature-pink);">The Sanctuary</h2>
        </div>
        <section class="hotel-grid">
            {hotel_html}
        </section>

        <section class="market-pulse">
            <div class="badge">Price Intelligence</div>
            <img src="price_trend.png" class="trend-img" alt="Market Pulse">
            <div class="recommendations">
                {recommendation_html}
            </div>
        </section>

        <footer>
            Hand-curated with care for Yidan Yan • Spring 2026
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
        ticket_class = "ticket-sfo" if "SFO" in data['route_name'] else "ticket-psp"
        
        # Build market sweep list
        alts = [f for f in data['all_flights'] if f['carrier'] != data['carrier']]
        sweep_text = ", ".join([f"{f['carrier']} (${f['price']})" for f in sorted(alts, key=lambda x: x['price'])[:3]])
        
        cards += f"""
        <div class="flight-ticket {ticket_class}">
            <div class="handwritten-memo">Best pick for balance & comfort.</div>
            <div class="route-title">{data['route_name']}</div>
            <div style="font-weight: 800; font-size: 0.8rem; color: #94a3b8; letter-spacing: 3px;">{data['dates']}</div>
            <div class="price-tag">${data['price']}</div>
            <div style="font-weight: 800; text-transform: uppercase; color: var(--signature-pink); font-size: 0.7rem;">Primary: {data['carrier']} Nonstop</div>
            
            <div class="market-sweep">
                Market Sweep: {sweep_text}
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
    
    print(f"--- YIDAN TRAVEL: ARTISTIC BRIEFING ({today_str}) ---")
    
    reports_data = []
    recommendations = []

    for task in TASKS:
        print(f"Artistic Sweep: {task['route_name']}...")
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
    print("Artistic Update Complete: flight_report.html")

if __name__ == "__main__":
    asyncio.run(run_tracker())
