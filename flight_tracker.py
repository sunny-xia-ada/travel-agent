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

def get_recommendation(task_id: str, current_price: float, stats: Dict, target: float, route_name: str) -> str:
    """Generates a human-readable escape recommendation with specific price targets."""
    avg = stats['avg_7d']
    status = stats['status']
    
    if current_price == 0:
        return "Intelligence unavailable. Stay prepared."
    
    if current_price <= target:
        return f"🚨 Target hit! {route_name} is at ${current_price}. This is your moment—secure it now."
    
    if current_price < avg * 0.95:
        return f"{route_name} is currently ${current_price}, dip detected below the 7-day average. Solid value."
    
    return f"{route_name} is ${current_price}. I recommend holding for the ${target} target breakthrough."

def generate_trend_chart(history: Dict):
    """Generates a monochromatic Cream Pink trend chart for the Loopy edition."""
    plt.figure(figsize=(10, 4))
    plt.style.use('seaborn-whitegrid')
    
    # Cream Pink Gradient Palette
    colors = ['#FADADD', '#F8C8DC', '#FFF0F5']
    for i, (task_id, data) in enumerate(history.items()):
        records = data.get("history", [])
        df = pd.DataFrame(records)
        if df.empty: continue
        
        df = df[df['price'] > 0]
        if df.empty: continue
        
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').tail(14)
        
        plt.plot(df['date'], df['price'], color=colors[i % len(colors)], linewidth=6, alpha=0.9, label=task_id)
        
        # Highlight lowest points with soft circles
        if len(df) > 2:
            min_p = df['price'].min()
            pts = df[df['price'] == min_p]
            plt.scatter(pts['date'], pts['price'], color='white', s=180, edgecolors=colors[i % len(colors)], linewidth=3, zorder=5)

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
    """Generates a Creamy Loopy Briefing with 'Travel Agent from 一丹' branding."""
    today_str = datetime.date.today().strftime("%Y年%m月%d日")
    
    recommendation_html = "".join([f'<div class="loopy-memo-pill">{rec}</div>' for rec in recommendations])
    hotel_html = "".join([f"""
        <div class="sanctuary-vertical-card">
            <div class="vibe-check-badge">Vibe Check</div>
            <div class="media-frame">
                <img src="{'hotel_sfo.png' if h['city'] == 'SFO' else 'hotel_psp.png'}" alt="{h['name']}">
            </div>
            <div class="sanctuary-info">
                <div class="sanctuary-name">{h['name']}</div>
                <div class="sanctuary-vibe">{h['vibe']}</div>
                <div class="sanctuary-meta">Special rate from ${h['rate']}</div>
            </div>
            <div class="assistant-handwritten">助理提示：{h['tip']}</div>
        </div>
    """ for h in hotels])

    html_template = f"""
<!DOCTYPE html>
<html lang="zh">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Travel Agent from 一丹 | Bespoke Briefing</title>
    <link href="https://fonts.googleapis.com/css2?family=Mrs+Saint+Delafield&family=Outfit:wght@300;500;800&display=swap" rel="stylesheet">
    <style>
        :root {{
            --cream-pink: #FADADD;
            --dusty-rose: #B06C7E;
            --milky-white: rgba(255, 255, 250, 0.45);
            --glow: 0 0 20px rgba(255, 255, 255, 0.9);
            --text-main: #4a4a4a;
        }}

        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        
        body {{ 
            font-family: 'Outfit', sans-serif; 
            background: linear-gradient(rgba(255, 255, 255, 0.4), rgba(255, 255, 255, 0.4)), 
                        url('loopy_vacation_bg.png') no-repeat center center fixed;
            background-size: cover;
            color: var(--text-main);
            overflow-x: hidden;
            padding: 80px 40px;
        }}

        header {{ margin-bottom: 240px; text-align: left; padding-left: 6%; }}
        .header-title {{
            font-family: 'Mrs Saint Delafield', cursive;
            font-size: 8rem;
            color: var(--cream-pink);
            text-shadow: var(--glow), 0 0 40px rgba(255, 255, 255, 0.4);
            margin-bottom: -15px;
            letter-spacing: -2px;
        }}
        .header-edition {{ font-weight: 800; letter-spacing: 12px; font-size: 0.8rem; color: var(--dusty-rose); opacity: 0.8; margin-bottom: 20px; }}

        /* Asymmetric Collage */
        .dashboard-grid {{
            position: relative;
            max-width: 1400px;
            margin: 0 auto;
            height: 1100px;
            margin-bottom: 200px;
        }}

        .editorial-card {{
            position: absolute;
            background: var(--milky-white);
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            padding: 60px;
            border-radius: 80px;
            border: 3px solid rgba(255, 255, 255, 0.8);
            box-shadow: 0 30px 60px rgba(176, 108, 126, 0.05);
            width: 580px;
            transition: all 0.5s cubic-bezier(0.23, 1, 0.32, 1);
        }}
        
        .editorial-card:hover {{ transform: translateY(-15px) scale(1.01); z-index: 100; }}
        
        .card-alpha {{ top: 0; left: 5%; z-index: 20; }}
        .card-beta {{ top: 450px; right: 5%; z-index: 10; }}

        .meta-header {{ font-size: 0.75rem; font-weight: 800; letter-spacing: 5px; color: var(--dusty-rose); text-transform: uppercase; margin-bottom: 30px; }}
        .route-title {{ font-family: 'Mrs Saint Delafield', cursive; font-size: 5rem; color: var(--dusty-rose); margin-bottom: 20px; }}
        
        .price-display {{ display: flex; align-items: baseline; gap: 20px; margin-bottom: 40px; }}
        .price-val {{ font-size: 8.5rem; font-weight: 800; letter-spacing: -8px; line-height: 1; color: var(--dusty-rose); }}
        .price-curr {{ font-size: 1.5rem; font-weight: 500; color: #b1b1b1; }}

        .assistant-memo-box {{
            font-family: 'Mrs Saint Delafield', cursive;
            font-size: 2.5rem;
            color: var(--dusty-rose);
            line-height: 1.1;
            margin-top: 30px;
            padding: 25px;
            background: rgba(255, 255, 255, 0.4);
            border-radius: 40px;
            border-left: 5px solid var(--cream-pink);
        }}

        .carrier-brief {{ margin-top: 40px; border-top: 2px dashed rgba(176, 108, 126, 0.1); padding-top: 30px; }}
        .carrier-row {{ display: flex; justify-content: space-between; font-size: 0.95rem; padding: 6px 0; color: #8a8a8a; font-weight: 600; }}
        .primary-carrier {{ color: var(--dusty-rose); font-weight: 800; }}

        /* Sanctuary vertical frames */
        .sanctuary-header {{ text-align: center; margin-bottom: 120px; }}
        .sanctuary-grid {{ display: flex; justify-content: center; gap: 80px; margin-bottom: 200px; padding: 0 40px; }}
        
        .sanctuary-vertical-card {{ 
            width: 480px; 
            background: white; 
            padding: 30px; 
            border-radius: 90px;
            box-shadow: 0 50px 100px rgba(176, 108, 126, 0.08); 
            position: relative;
        }}
        .media-frame {{ width: 100%; height: 600px; border-radius: 70px; overflow: hidden; margin-bottom: 30px; }}
        .media-frame img {{ width: 100%; height: 100%; object-fit: cover; transition: transform 0.8s ease; }}
        .sanctuary-vertical-card:hover .media-frame img {{ transform: scale(1.05); }}
        
        .sanctuary-info {{ text-align: center; }}
        .sanctuary-name {{ font-size: 2.2rem; font-weight: 800; color: var(--dusty-rose); margin-bottom: 10px; }}
        .sanctuary-vibe {{ font-size: 0.9rem; color: #a1a1a1; font-style: italic; margin-bottom: 15px; }}
        .sanctuary-meta {{ font-weight: 800; color: var(--dusty-rose); letter-spacing: 3px; text-transform: uppercase; font-size: 0.8rem; }}
        
        .vibe-check-badge {{ 
            position: absolute; top: 50px; left: -20px; 
            background: var(--cream-pink); color: white; 
            padding: 10px 25px; border-radius: 40px; 
            font-weight: 800; font-size: 0.8rem; transform: rotate(-5deg);
            box-shadow: 0 10px 20px rgba(250, 218, 221, 0.4);
        }}

        /* Polaroid Trend */
        .editorial-polaroid {{
            max-width: 950px;
            margin: 0 auto;
            background: #fff;
            padding: 40px 40px 140px 40px;
            box-shadow: 0 60px 120px rgba(176, 108, 126, 0.12);
            transform: rotate(-2deg);
            border-radius: 4px;
            text-align: center;
            margin-bottom: 150px;
        }}
        .editorial-polaroid img {{ width: 100%; opacity: 0.9; }}
        .polaroid-caption {{ font-family: 'Mrs Saint Delafield', cursive; font-size: 4rem; color: var(--dusty-rose); margin-top: 50px; }}

        .intelligence-summary {{ text-align: center; margin-bottom: 180px; }}
        .loopy-memo-pill {{ font-family: 'Mrs Saint Delafield', cursive; font-size: 3.2rem; color: var(--dusty-rose); margin-bottom: 20px; }}

        footer {{ text-align: center; margin-top: 250px; font-weight: 800; letter-spacing: 15px; font-size: 0.8rem; color: var(--dusty-rose); text-transform: uppercase; padding-bottom: 150px; opacity: 0.6; }}
    </style>
</head>
<body>
    <header>
        <div class="header-edition">Spring 2026 Collection • {today_str}</div>
        <h1 class="header-title">Travel Agent from 一丹</h1>
    </header>

    <section class="dashboard-grid">
        {generate_status_cards(reports_data)}
    </section>

    <div class="sanctuary-header">
        <h2 style="font-family: 'Mrs Saint Delafield', cursive; font-size: 6rem; color: var(--dusty-rose);">Sanctuary Highlights</h2>
    </div>
    <section class="sanctuary-grid">
        {hotel_html}
    </section>

    <div class="editorial-polaroid">
        <img src="price_trend.png" alt="Intelligence Pulse">
        <div class="polaroid-caption">Market Fluctuations & Insight</div>
    </div>

    <section class="intelligence-summary">
        {recommendation_html}
    </section>

    <footer>
        Antigravity Personalized Intelligence • For Yidan Yan
    </footer>
</body>
</html>
    """
    with open("flight_report.html", "w") as f:
        f.write(html_template)

def generate_status_cards(reports_data: List[Dict]) -> str:
    cards = ""
    for data in reports_data:
        pos_class = "card-alpha" if "SFO" in data['route_name'] else "card-beta"
        
        # Comparison logic: Alaska/Delta alerts, but show all for awareness
        priorities = [f for f in data['all_flights'] if f.get('is_priority')]
        alternatives = [f for f in data['all_flights'] if not f.get('is_priority')]
        
        comparison_html = ""
        for p in priorities[:2]: 
            comparison_html += f'<div class="carrier-row primary-carrier"><span>{p["carrier"]} Focus</span><span>${p["price"]}</span></div>'
        for a in alternatives[:2]: 
            comparison_html += f'<div class="carrier-row"><span>{a["carrier"]} Awareness</span><span>${a["price"]}</span></div>'

        # Assistant memo with positional awareness
        if "SFO" in data['route_name']:
            memo_text = f"SFO is currently ${data['price']}; hold for the $160 dip." if data['price'] > 160 else "The SFO target is cleared. Remarkable timing."
        else:
            memo_text = f"PSP is currently ${data['price']}; it's high season—watch for the $400 breakthrough." if data['price'] > 400 else "PSP breakthrough achieved. Secure your desert sanctuary."

        cards += f"""
        <div class="editorial-card {pos_class}">
            <div class="meta-header">Private Flight Path • {data['dates']}</div>
            <div class="route-title">{data['route_name']}</div>
            <div class="price-display">
                <span class="price-val">${data['price']}</span>
                <span class="price-curr">USD</span>
            </div>
            
            <div class="carrier-brief">
                <div class="meta-header" style="font-size:0.6rem; color: #B06C7E; margin-bottom:15px;">Market Situation Awareness</div>
                {comparison_html}
            </div>
            
            <div class="assistant-memo-box">Insight: {memo_text}</div>
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
            "vibe": "Sustainable luxury on the waterfront.", 
            "tip": "the panoramic bay views are best enjoyed at sunset.",
            "rate": 325
        },
        {
            "name": "Korakia Pensione", 
            "city": "PSP", 
            "vibe": "Moroccan desert sanctuary.", 
            "tip": "candlelit courtyards make this the ultimate escape.",
            "rate": 425
        }
    ]
    
    print(f"--- Travel Agent from 一丹: LOOPY SOFT-FOCUS BRIEFING ({today_str}) ---")
    
    reports_data = []
    recommendations = []

    for task in TASKS:
        print(f"Loopy Sweep: {task['route_name']}...")
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
        
        recommendations.append(get_recommendation(task_id, value_leader["price"], stats, task["price_trigger"], task['route_name']))

    save_history(history)
    generate_trend_chart(history)
    generate_html_report(reports_data, recommendations, HOTELS)
    print("Loopy Update Complete: flight_report.html")

if __name__ == "__main__":
    asyncio.run(run_tracker())
