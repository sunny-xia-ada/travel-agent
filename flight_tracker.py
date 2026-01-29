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
    """Generates a Loopy Soft-Focus Briefing with 'Travel Agent from 一丹' branding."""
    today_str = datetime.date.today().strftime("%Y年%m月%d日")
    
    recommendation_html = "".join([f'<div class="loopy-memo-pill">{rec}</div>' for rec in recommendations])
    hotel_html = "".join([f"""
        <div class="sanctuary-collage-card">
            <div class="memo-tag">Recommended Stay</div>
            <img src="{'hotel_sfo.png' if h['city'] == 'SFO' else 'hotel_psp.png'}" alt="{h['name']}">
            <div class="sanctuary-info">
                <div class="sanctuary-name">{h['name']}</div>
                <div class="sanctuary-vibe">{h['vibe']}</div>
                <div class="sanctuary-price">Special rate from ${h['rate']}</div>
            </div>
            <div class="assistant-handwritten">一丹，{h['tip']}</div>
        </div>
    """ for h in hotels])

    html_template = f"""
<!DOCTYPE html>
<html lang="zh">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Travel Agent from 一丹 | PRIVATE BRIEFING</title>
    <link href="https://fonts.googleapis.com/css2?family=Mrs+Saint+Delafield&family=Outfit:wght@300;500;800&display=swap" rel="stylesheet">
    <style>
        :root {{
            --cream-pink: #FADADD;
            --dusty-rose: #B06C7E;
            --loopy-glow: rgba(255, 248, 245, 0.4);
            --text-dark: #4a4a4a;
        }}

        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        
        body {{ 
            font-family: 'Outfit', sans-serif; 
            background: linear-gradient(rgba(255, 255, 255, 0.5), rgba(255, 250, 250, 0.5)), 
                        url('loopy_vacation_bg.png') no-repeat center center fixed;
            background-size: cover;
            color: var(--text-dark);
            overflow-x: hidden;
            padding: 100px 60px;
        }}

        header {{ margin-bottom: 220px; text-align: left; padding-left: 4%; }}
        .header-title {{
            font-family: 'Mrs Saint Delafield', cursive;
            font-size: 7rem;
            color: var(--dusty-rose);
            text-shadow: 0 0 10px rgba(255,255,255,0.9);
            margin-bottom: 10px;
        }}
        .header-subtitle {{ font-weight: 800; letter-spacing: 10px; font-size: 0.9rem; color: var(--dusty-rose); opacity: 0.8; }}

        /* Collage Grid */
        .collage-wrapper {{
            position: relative;
            max-width: 1300px;
            margin: 0 auto;
            height: 1000px;
            margin-bottom: 150px;
        }}

        .collage-card {{
            position: absolute;
            background: var(--loopy-glow);
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            padding: 50px;
            border-radius: 60px;
            border: 2px solid rgba(255, 255, 255, 0.7);
            box-shadow: 20px 20px 40px rgba(176, 108, 126, 0.05);
            width: 520px;
            transition: transform 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275);
        }}
        
        .collage-card:hover {{ transform: scale(1.02) rotate(1deg); z-index: 50; }}
        
        .pos-left {{ top: 0; left: 0; z-index: 10; }}
        .pos-right {{ top: 380px; right: 0; z-index: 5; }}

        .tag-label {{ font-size: 0.75rem; font-weight: 800; letter-spacing: 4px; color: var(--dusty-rose); text-transform: uppercase; margin-bottom: 25px; }}
        .route-accent {{ font-family: 'Mrs Saint Delafield', cursive; font-size: 4rem; color: var(--dusty-rose); margin-bottom: 15px; }}
        
        .hero-price {{ display: flex; align-items: baseline; gap: 15px; margin-bottom: 35px; }}
        .price-big {{ font-size: 7.5rem; font-weight: 800; letter-spacing: -5px; line-height: 1; color: var(--dusty-rose); }}
        .price-sub {{ font-size: 1.4rem; font-weight: 500; color: #a1a1a1; }}

        .assistant-memo {{
            font-family: 'Mrs Saint Delafield', cursive;
            font-size: 2.2rem;
            color: var(--dusty-rose);
            line-height: 1.1;
            margin-top: 25px;
            padding: 20px;
            background: rgba(255, 255, 255, 0.3);
            border-radius: 30px;
        }}

        .market-comparison {{ margin-top: 35px; border-top: 1px dashed rgba(176, 108, 126, 0.2); padding-top: 25px; }}
        .comparison-row {{ display: flex; justify-content: space-between; font-size: 0.9rem; padding: 4px 0; color: #888; }}
        .best-pick {{ color: var(--dusty-rose); font-weight: 800; }}

        /* Sanctuary */
        .sanctuary-title-box {{ text-align: center; margin-bottom: 100px; }}
        .sanctuary-grid {{ display: flex; justify-content: center; gap: 70px; margin-bottom: 200px; }}
        
        .sanctuary-collage-card {{ 
            width: 460px; 
            background: white; 
            padding: 25px; 
            border-radius: 70px;
            box-shadow: 0 40px 80px rgba(176, 108, 126, 0.08); 
            position: relative; 
            overflow: hidden;
        }}
        .sanctuary-collage-card img {{ width: 100%; height: 520px; object-fit: cover; border-radius: 50px; }}
        .sanctuary-info {{ margin-top: 25px; text-align: center; }}
        .sanctuary-name {{ font-size: 2rem; font-weight: 800; margin-bottom: 8px; color: var(--dusty-rose); }}
        .sanctuary-vibe {{ font-size: 0.85rem; color: #a1a1a1; font-style: italic; margin-bottom: 12px; }}
        .sanctuary-price {{ font-weight: 800; color: var(--dusty-rose); letter-spacing: 2px; text-transform: uppercase; font-size: 0.75rem; }}
        .memo-tag {{ position: absolute; top: 15px; left: 30px; background: var(--cream-pink); color: var(--dusty-rose); padding: 5px 15px; border-radius: 20px; font-weight: 800; font-size: 0.7rem; }}

        /* Polaroid Polaroid */
        .polaroid-container {{
            max-width: 900px;
            margin: 0 auto;
            background: #fffcfb;
            padding: 35px 35px 110px 35px;
            box-shadow: 0 50px 100px rgba(176, 108, 126, 0.1);
            transform: rotate(-2deg);
            border-radius: 3px;
            text-align: center;
            margin-bottom: 120px;
        }}
        .polaroid-container img {{ width: 100%; opacity: 0.95; }}
        .polaroid-text {{ font-family: 'Mrs Saint Delafield', cursive; font-size: 3.5rem; color: var(--dusty-rose); margin-top: 45px; }}

        .memos-hub {{ margin-top: 120px; text-align: center; }}
        .loopy-memo-pill {{ font-family: 'Mrs Saint Delafield', cursive; font-size: 3rem; color: var(--dusty-rose); margin-bottom: 15px; }}

        footer {{ text-align: center; margin-top: 200px; font-weight: 800; letter-spacing: 12px; font-size: 0.75rem; color: var(--dusty-rose); text-transform: uppercase; padding-bottom: 120px; opacity: 0.7; }}
    </style>
</head>
<body>
    <header>
        <h1 class="header-title">Travel Agent from 一丹</h1>
        <div class="header-subtitle">SPRING COLLECTION • {today_str} • PRIVATE BRIEFING</div>
    </header>

    <section class="collage-wrapper">
        {generate_status_cards(reports_data)}
    </section>

    <div class="sanctuary-title-box">
        <h2 style="font-family: 'Mrs Saint Delafield', cursive; font-size: 5rem; color: var(--dusty-rose);">The Sanctuary Brief</h2>
    </div>
    <section class="sanctuary-grid">
        {hotel_html}
    </section>

    <div class="polaroid-container">
        <img src="price_trend.png" alt="Loopy Pulse">
        <div class="polaroid-text">Market Intelligence Pulse</div>
    </div>

    <section class="memos-hub">
        {recommendation_html}
    </section>

    <footer>
        Antigravity Personalized Concierge • For Yidan Yan
    </footer>
</body>
</html>
    """
    with open("flight_report.html", "w") as f:
        f.write(html_template)

def generate_status_cards(reports_data: List[Dict]) -> str:
    cards = ""
    for data in reports_data:
        pos_class = "pos-left" if "SFO" in data['route_name'] else "pos-right"
        
        # Build comparison sweep
        picks = [f for f in data['all_flights'] if f.get('is_priority')]
        others = [f for f in data['all_flights'] if not f.get('is_priority')]
        
        comparison_html = ""
        for p in picks[:2]: comparison_html += f'<div class="comparison-row best-pick"><span>{p["carrier"]} Primary</span><span>${p["price"]}</span></div>'
        for o in others[:2]: comparison_html += f'<div class="comparison-row"><span>{o["carrier"]} Value</span><span>${o["price"]}</span></div>'

        # Personal memo text
        memo_text = "Highly recommended for seasonal timing." if "SFO" in data['route_name'] else "A perfect escape for your upcoming weekend."

        cards += f"""
        <div class="collage-card {pos_class}">
            <div class="tag-label">Private Flight • {data['dates']}</div>
            <div class="route-accent">{data['route_name']}</div>
            <div class="hero-price">
                <span class="price-big">${data['price']}</span>
                <span class="price-sub">USD / RT</span>
            </div>
            
            <div class="market-comparison">
                <div class="tag-label" style="font-size:0.6rem; color: #B06C7E;">Concierge Market Brief</div>
                {comparison_html}
            </div>
            
            <div class="assistant-memo">助理追踪提示：{memo_text}</div>
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
        
        recommendations.append(get_recommendation(task_id, value_leader["price"], stats, task["price_trigger"]))

    save_history(history)
    generate_trend_chart(history)
    generate_html_report(reports_data, recommendations, HOTELS)
    print("Loopy Update Complete: flight_report.html")

if __name__ == "__main__":
    asyncio.run(run_tracker())
