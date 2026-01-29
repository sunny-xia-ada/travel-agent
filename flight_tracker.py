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
    """Generates a monochromatic pink trend chart for the concierge edition."""
    plt.figure(figsize=(10, 4))
    plt.style.use('seaborn-whitegrid')
    
    # Monochromatic Pink Palette
    colors = ['#D81B60', '#F06292', '#F48FB1']
    for i, (task_id, data) in enumerate(history.items()):
        records = data.get("history", [])
        df = pd.DataFrame(records)
        if df.empty: continue
        
        df = df[df['price'] > 0]
        if df.empty: continue
        
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').tail(14)
        
        plt.plot(df['date'], df['price'], color=colors[i % len(colors)], linewidth=5, alpha=0.9, label=task_id)
        
        # Highlight lowest points with soft pink circles
        if len(df) > 2:
            min_p = df['price'].min()
            pts = df[df['price'] == min_p]
            plt.scatter(pts['date'], pts['price'], color='white', s=150, edgecolors=colors[i % len(colors)], linewidth=3, zorder=5)

    # Clean axes
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
    """Generates a Concierge Briefing with '一丹的旅行助理' branding and collage layout."""
    today_str = datetime.date.today().strftime("%Y年%m月%d日")
    
    recommendation_html = "".join([f'<div class="concierge-memo-pill">{rec}</div>' for rec in recommendations])
    hotel_html = "".join([f"""
        <div class="sanctuary-card">
            <div class="vibe-check-label">VIBE CHECK</div>
            <img src="{'hotel_sfo.png' if h['city'] == 'SFO' else 'hotel_psp.png'}" alt="{h['name']}">
            <div class="sanctuary-info">
                <div class="sanctuary-name">{h['name']}</div>
                <div class="sanctuary-vibe">{h['vibe']}</div>
                <div class="sanctuary-rate">Curated rate: ${h['rate']}</div>
            </div>
            <div class="assistant-handwritten">Yidan, {h['tip']}</div>
        </div>
    """ for h in hotels])

    html_template = f"""
<!DOCTYPE html>
<html lang="zh">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>一丹的旅行助理 | PRIVATE BRIEFING</title>
    <link href="https://fonts.googleapis.com/css2?family=Ma+Shan+Zheng&family=Outfit:wght@300;500;800&display=swap" rel="stylesheet">
    <style>
        :root {{
            --deep-pink: #D81B60;
            --soft-pink: #F48FB1;
            --glass-bg: rgba(255, 255, 255, 0.4);
            --text-main: #1a1a1a;
        }}

        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        
        body {{ 
            font-family: 'Outfit', sans-serif; 
            background: url('hello_kitty_vacation_bg.png') no-repeat center center fixed;
            background-size: cover;
            color: var(--text-main);
            overflow-x: hidden;
            padding: 100px 50px;
        }}

        header {{ margin-bottom: 200px; text-align: left; padding-left: 5%; }}
        .assistant-title {{
            font-family: 'Ma Shan Zheng', cursive;
            font-size: 6rem;
            color: var(--deep-pink);
            text-shadow: 0 0 15px rgba(255, 255, 255, 0.9), 0 0 30px rgba(255, 255, 255, 0.5);
            margin-bottom: 15px;
        }}
        .header-meta {{ font-weight: 800; letter-spacing: 12px; font-size: 0.9rem; color: var(--deep-pink); opacity: 0.7; }}

        /* Collage Grid */
        .collage-container {{
            position: relative;
            max-width: 1400px;
            margin: 0 auto;
            height: 1100px;
        }}

        .floating-card {{
            position: absolute;
            background: var(--glass-bg);
            backdrop-filter: blur(10px);
            -webkit-backdrop-filter: blur(10px);
            padding: 50px;
            border-radius: 4px;
            border: 1px solid rgba(255, 255, 255, 0.6);
            box-shadow: 30px 30px 60px rgba(0,0,0,0.02);
            width: 500px;
            transition: transform 0.3s ease;
        }}
        
        .floating-card:hover {{ transform: translateY(-10px); z-index: 100 !important; }}
        
        .card-left {{ top: 0; left: 0; z-index: 2; }}
        .card-right {{ top: 400px; right: 0; z-index: 1; }}

        .meta-label {{ font-size: 0.7rem; font-weight: 800; letter-spacing: 5px; color: #8e8e8e; text-transform: uppercase; margin-bottom: 20px; }}
        .route-header {{ font-family: 'Ma Shan Zheng', cursive; font-size: 3.5rem; margin-bottom: 30px; border-bottom: 1px solid rgba(216, 27, 96, 0.1); padding-bottom: 15px; }}
        
        .price-hero {{ display: flex; align-items: baseline; gap: 15px; margin-bottom: 40px; }}
        .price-val {{ font-size: 6.5rem; font-weight: 800; letter-spacing: -4px; line-height: 1; color: var(--deep-pink); }}
        .price-curr {{ font-size: 1.5rem; font-weight: 500; color: #94a3b8; }}

        .assistant-handwritten {{
            font-family: 'Ma Shan Zheng', cursive;
            font-size: 1.8rem;
            color: var(--deep-pink);
            line-height: 1.2;
            margin-top: 30px;
            padding: 20px;
            border-left: 2px solid var(--soft-pink);
            background: rgba(255, 255, 255, 0.2);
        }}

        .comparison-sweep {{ margin-top: 40px; border-top: 1px dashed rgba(0,0,0,0.05); padding-top: 20px; }}
        .sweep-item {{ display: flex; justify-content: space-between; font-size: 0.85rem; padding: 5px 0; color: #64748b; }}
        .item-priority {{ color: var(--deep-pink); font-weight: 800; }}

        /* Sanctuary */
        .sanctuary-title {{ text-align: center; margin-bottom: 100px; }}
        .sanctuary-grid {{ display: flex; justify-content: center; gap: 80px; margin-bottom: 200px; }}
        
        .sanctuary-card {{ 
            width: 450px; 
            background: white; 
            padding: 25px; 
            box-shadow: 50px 50px 100px rgba(0,0,0,0.05); 
            position: relative; 
        }}
        .sanctuary-card img {{ width: 100%; height: 500px; object-fit: cover; filter: sepia(10%) contrast(1.1); }}
        .sanctuary-info {{ margin-top: 30px; padding: 0 10px; }}
        .sanctuary-name {{ font-size: 2rem; font-weight: 800; margin-bottom: 10px; }}
        .sanctuary-vibe {{ font-size: 0.9rem; color: #94a3b8; font-style: italic; margin-bottom: 15px; }}
        .sanctuary-rate {{ font-weight: 800; color: var(--deep-pink); letter-spacing: 2px; text-transform: uppercase; font-size: 0.8rem; }}
        .vibe-check-label {{ position: absolute; top: -15px; left: 20px; background: var(--deep-pink); color: white; padding: 5px 15px; font-weight: 800; font-size: 0.7rem; }}

        /* Polaroid Trend */
        .polaroid-frame {{
            max-width: 900px;
            margin: 0 auto;
            background: white;
            padding: 30px 30px 100px 30px;
            box-shadow: 0 40px 80px rgba(0,0,0,0.1);
            transform: rotate(-3deg);
            border-radius: 4px;
            text-align: center;
        }}
        .polaroid-frame img {{ width: 100%; opacity: 0.9; }}
        .polaroid-caption {{ font-family: 'Ma Shan Zheng', cursive; font-size: 3rem; color: var(--deep-pink); margin-top: 40px; }}

        .memos-section {{ margin-top: 150px; text-align: center; }}
        .concierge-memo-pill {{ font-family: 'Ma Shan Zheng', cursive; font-size: 2.5rem; color: var(--deep-pink); margin-bottom: 20px; }}

        footer {{ text-align: center; margin-top: 200px; font-weight: 800; letter-spacing: 15px; font-size: 0.7rem; color: var(--deep-pink); text-transform: uppercase; padding-bottom: 100px; }}
    </style>
</head>
<body>
    <header>
        <h1 class="assistant-title">一丹的旅行助理</h1>
        <div class="header-meta">SPRING BRIEFING • {today_str} • PRIVATE CONCIERGE</div>
    </header>

    <section class="collage-container">
        {generate_status_cards(reports_data)}
    </section>

    <div class="sanctuary-title">
        <h2 style="font-family: 'Ma Shan Zheng', cursive; font-size: 4.5rem; color: var(--deep-pink);">THE SANCTUARY</h2>
    </div>
    <section class="sanctuary-grid">
        {hotel_html}
    </section>

    <div class="polaroid-frame">
        <img src="price_trend.png" alt="Intelligence Pulse">
        <div class="polaroid-caption">Market Intelligence Pulse</div>
    </div>

    <section class="memos-section">
        {recommendation_html}
    </section>

    <footer>
        Antigravity Intelligence Systems • For Yidan Yan
    </footer>
</body>
</html>
    """
    with open("flight_report.html", "w") as f:
        f.write(html_template)

def generate_status_cards(reports_data: List[Dict]) -> str:
    cards = ""
    for data in reports_data:
        side_class = "card-left" if "SFO" in data['route_name'] else "card-right"
        
        # Comparison Sweep
        picks = [f for f in data['all_flights'] if f.get('is_priority')]
        others = [f for f in data['all_flights'] if not f.get('is_priority')]
        
        comparison_html = ""
        for p in picks[:2]: comparison_html += f'<div class="sweep-item item-priority"><span>{p["carrier"]} Nonstop</span><span>${p["price"]}</span></div>'
        for o in others[:2]: comparison_html += f'<div class="sweep-item"><span>{o["carrier"]} Comparison</span><span>${o["price"]}</span></div>'

        # Assistant memo logic
        memo_text = "Highly recommended for timing." if "SFO" in data['route_name'] else "Perfect for weekend unwind."

        cards += f"""
        <div class="floating-card {side_class}">
            <div class="meta-label">{data['dates']}</div>
            <div class="route-header">{data['route_name']}</div>
            <div class="price-hero">
                <span class="price-val">${data['price']}</span>
                <span class="price-curr">USD / RT</span>
            </div>
            
            <div class="comparison-sweep">
                <div class="meta-label" style="font-size:0.6rem; color: #F48FB1;">Market Sweep Intelligence</div>
                {comparison_html}
            </div>
            
            <div class="assistant-handwritten">助理提示追踪：{memo_text}</div>
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
    
    print(f"--- 一丹的旅行助理: CONCIERGE BRIEFING ({today_str}) ---")
    
    reports_data = []
    recommendations = []

    for task in TASKS:
        print(f"Concierge Sweep: {task['route_name']}...")
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
    print("Concierge Update Complete: flight_report.html")

if __name__ == "__main__":
    asyncio.run(run_tracker())
