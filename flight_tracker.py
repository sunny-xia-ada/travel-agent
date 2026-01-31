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
    """用中文生成个性化的旅行建议，包含具体的价格目标。"""
    avg = stats['avg_7d']
    
    if current_price == 0:
        return "情报获取中，请保持关注。"
    
    if current_price <= target:
        return f"🚨 目标达成！{route_name} 目前价格为 ${current_price}。一丹，这是最佳入手时机，建议立即预订。"
    
    if current_price < avg * 0.95:
        return f"{route_name} 当前价格 ${current_price}，已低于 7 日平均价，具备不错的出行价值。"
    
    return f"{route_name} 目前价格 ${current_price}。建议再等等，关注 ${target} 的突破点。"

def generate_trend_chart(history: Dict):
    """生成带有奶油蓝(Creamy Blue)渐变的平滑趋势图。"""
    plt.figure(figsize=(10, 4))
    plt.style.use('seaborn-whitegrid')
    
    # Creamy Blue Palette
    colors = ['#B0E0E6', '#ADD8E6', '#B0C4DE']
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
    """生成带有'Travel Agent from 一丹 | Yidan's Travel Assistant'双语标题的奶油蓝波浪版简报。"""
    today_str = datetime.date.today().strftime("%Y年%m月%d日")
    
    recommendation_html = "".join([f'<div class="wave-memo-pill">{rec}</div>' for rec in recommendations])
    hotel_html = "".join([f"""
        <div class="pastel-vertical-card">
            <div class="highlight-bar"></div>
            <div class="vibe-check-badge">Sanctuary Vibe Check</div>
            <div class="view-frame">
                <img src="{'hotel_sfo.png' if h['city'] == 'SFO' else 'hotel_psp.png'}" alt="{h['name']}">
            </div>
            <div class="pastel-info">
                <div class="pastel-name">{h['name']}</div>
                <div class="pastel-vibe-text">{h['vibe']}</div>
                <div class="pastel-meta">Exclusive Rate from ${h['rate']} 起</div>
            </div>
            <div class="concierge-handwritten">助理提示：{h['tip']}</div>
        </div>
    """ for h in hotels])

    html_template = f"""
<!DOCTYPE html>
<html lang="zh">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Travel Agent from 一丹 | Private Dashboard</title>
    <link href="https://fonts.googleapis.com/css2?family=Mrs+Saint+Delafield&family=Outfit:wght@300;500;800&display=swap" rel="stylesheet">
    <style>
        :root {{
            --milky-blue: #B0E0E6;
            --soft-blue: #ADD8E6;
            --creamy-white: rgba(255, 255, 255, 0.4);
            --creamy-pink: #FADADD;
            --foam-glow: 0 0 20px rgba(255, 255, 255, 1);
            --text-dark: #4a5a6a;
        }}

        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        
        body {{ 
            font-family: 'Outfit', 'PingFang SC', sans-serif; 
            background: linear-gradient(rgba(255, 255, 255, 0.2), rgba(255, 255, 255, 0.2)), 
                        url('loopy_vacation_bg.png') no-repeat center center fixed;
            background-size: cover;
            color: var(--text-dark);
            overflow-x: hidden;
            padding: 80px 40px;
        }}

        header {{ margin-bottom: 220px; text-align: left; padding-left: 5%; }}
        .header-title {{
            font-family: 'Mrs Saint Delafield', cursive;
            font-size: 7.5rem;
            color: var(--milky-blue);
            text-shadow: var(--foam-glow), 0 5px 15px rgba(176, 224, 230, 0.3);
            margin-bottom: -10px;
            letter-spacing: -2px;
            line-height: 1.1;
        }}
        .header-edition {{ font-weight: 800; letter-spacing: 12px; font-size: 0.85rem; color: var(--soft-blue); opacity: 0.9; margin-bottom: 20px; text-transform: uppercase; }}

        /* Pastel Dreams Grid */
        .dashboard-wrapper {{
            position: relative;
            max-width: 1400px;
            margin: 0 auto;
            height: 1100px;
            margin-bottom: 200px;
        }}

        .pastel-floating-card {{
            position: absolute;
            background: var(--creamy-white);
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            padding: 60px;
            border-radius: 95px;
            border: 3px solid rgba(255, 255, 255, 0.8);
            box-shadow: 0 40px 80px rgba(176, 224, 230, 0.08);
            width: 620px;
            transition: all 0.5s ease;
        }}
        
        .pastel-floating-card:hover {{ transform: translateY(-15px) scale(1.01); z-index: 100; }}
        
        .pos-alpha {{ top: 0; left: 5%; z-index: 20; border-left: 10px solid var(--creamy-pink); }}
        .pos-beta {{ top: 480px; right: 5%; z-index: 10; border-right: 10px solid var(--creamy-pink); }}

        .label-upper {{ font-size: 0.75rem; font-weight: 800; letter-spacing: 6px; color: var(--soft-blue); text-transform: uppercase; margin-bottom: 25px; }}
        .route-fluid {{ font-family: 'Mrs Saint Delafield', cursive; font-size: 5.5rem; color: var(--soft-blue); margin-bottom: 15px; }}
        
        .hero-price-box {{ display: flex; align-items: baseline; gap: 15px; margin-bottom: 40px; }}
        .hero-price-val {{ font-size: 9rem; font-weight: 800; letter-spacing: -6px; line-height: 1; color: var(--soft-blue); }}
        .hero-price-curr {{ font-size: 1.5rem; font-weight: 500; color: #a1b1c1; }}

        .concierge-memo-wave {{
            font-family: 'Mrs Saint Delafield', cursive;
            font-size: 2.7rem;
            color: var(--soft-blue);
            line-height: 1.1;
            margin-top: 30px;
            padding: 25px;
            background: rgba(255, 255, 255, 0.6);
            border-radius: 45px;
        }}

        .market-briefing {{ margin-top: 45px; border-top: 2px dashed rgba(176, 224, 230, 0.2); padding-top: 30px; }}
        .carrier-brief-row {{ display: flex; justify-content: space-between; font-size: 1rem; padding: 7px 0; color: #6a7a8a; font-weight: 600; }}
        .carrier-priority {{ color: var(--soft-blue); font-weight: 800; }}

        /* Sanctuary */
        .sanctuary-title-box {{ text-align: center; margin-bottom: 120px; }}
        .sanctuary-pastel-grid {{ display: flex; justify-content: center; gap: 90px; margin-bottom: 220px; padding: 0 40px; }}
        
        .pastel-vertical-card {{ 
            width: 520px; 
            background: white; 
            padding: 35px; 
            border-radius: 110px;
            box-shadow: 0 50px 100px rgba(176, 224, 230, 0.1); 
            position: relative;
            overflow: hidden;
        }}
        .highlight-bar {{ position: absolute; top: 0; left: 0; width: 100%; height: 15px; background: var(--creamy-pink); }}
        .view-frame {{ width: 100%; height: 650px; border-radius: 90px; overflow: hidden; margin-bottom: 30px; }}
        .view-frame img {{ width: 100%; height: 100%; object-fit: cover; filter: brightness(1.02); }}
        
        .pastel-info {{ text-align: center; }}
        .pastel-name {{ font-size: 2.4rem; font-weight: 800; color: var(--soft-blue); margin-bottom: 10px; }}
        .pastel-vibe-text {{ font-size: 1rem; color: #a1b1c1; font-style: italic; margin-bottom: 15px; }}
        .pastel-meta {{ font-weight: 800; color: var(--soft-blue); letter-spacing: 3px; text-transform: uppercase; font-size: 0.85rem; }}
        
        .vibe-check-badge {{ 
            position: absolute; top: 60px; left: -10px; 
            background: var(--creamy-pink); color: white; 
            padding: 10px 30px; border-radius: 40px; 
            font-weight: 800; font-size: 0.9rem; transform: rotate(-5deg);
            box-shadow: 0 10px 20px rgba(250, 218, 221, 0.4);
            z-index: 10;
        }}

        /* Memory Polaroid */
        .tilted-memory {{
            max-width: 950px;
            margin: 0 auto;
            background: #fff;
            padding: 50px 50px 150px 50px;
            box-shadow: 0 60px 120px rgba(176, 224, 230, 0.12);
            transform: rotate(-2deg);
            border-radius: 5px;
            text-align: center;
            margin-bottom: 150px;
        }}
        .tilted-memory img {{ width: 100%; opacity: 0.95; }}
        .polaroid-hand-title {{ font-family: 'Mrs Saint Delafield', cursive; font-size: 4.5rem; color: var(--soft-blue); margin-top: 55px; }}

        .concierge-hub {{ text-align: center; margin-bottom: 180px; padding: 0 15%; }}
        .wave-memo-pill {{ font-family: 'Mrs Saint Delafield', cursive; font-size: 3.5rem; color: var(--soft-blue); margin-bottom: 25px; }}

        footer {{ text-align: center; margin-top: 250px; font-weight: 800; letter-spacing: 18px; font-size: 0.9rem; color: var(--soft-blue); text-transform: uppercase; padding-bottom: 150px; opacity: 0.6; }}
    </style>
</head>
<body>
    <header>
        <div class="header-edition">Pastel Dreams Suite • {today_str} • Private Briefing</div>
        <h1 class="header-title">Travel Agent from 一丹 | Yidan's Travel Assistant</h1>
    </header>

    <section class="dashboard-wrapper">
        {generate_status_cards(reports_data)}
    </section>

    <div class="sanctuary-title-box">
        <h2 style="font-family: 'Mrs Saint Delafield', cursive; font-size: 6rem; color: var(--soft-blue);">Sanctuary Highlights</h2>
    </div>
    <section class="sanctuary-pastel-grid">
        {hotel_html}
    </section>

    <div class="tilted-memory">
        <img src="price_trend.png" alt="Intelligence History">
        <div class="polaroid-hand-title">Market Pulse Memory</div>
    </div>

    <section class="concierge-hub">
        {recommendation_html}
    </section>

    <footer>
        Antigravity Personalized Concierge • For Yidan Yan
    </footer>
</body>
</html>
    """
    with open("index.html", "w") as f:
        f.write(html_template)

def generate_status_cards(reports_data: List[Dict]) -> str:
    cards = ""
    for data in reports_data:
        pos_class = "pos-alpha" if "SFO" in data['route_name'] else "pos-beta"
        
        # Concierge Carrier Sweep
        priorities = [f for f in data['all_flights'] if f.get('is_priority')]
        alternatives = [f for f in data['all_flights'] if not f.get('is_priority')]
        
        comparison_html = ""
        for p in priorities[:2]: 
            comparison_html += f'<div class="carrier-brief-row carrier-priority"><span>{p["carrier"]} Focus</span><span>${p["price"]}</span></div>'
        for a in alternatives[:2]: 
            comparison_html += f'<div class="carrier-brief-row"><span>{a["carrier"]} Context</span><span>${a["price"]}</span></div>'

        # Personalized Memos (Chinese)
        if "SFO" in data['route_name']:
            memo_text = f"一丹，这次旧金山的‘Vibe’很特别。目前价格为 ${data['price']}，建议关注 ${160} 的回调契机。" if data['price'] > 160 else "绝佳时机！旧金山的价格已回落至目标区间，建议立即启程。"
        else:
            memo_text = f"棕榈泉的沙漠阳光正当时 (${data['price']})。若是价格跌破 ${400}，那将是完美的逃离理由。" if data['price'] > 400 else "沙漠之约已就绪。PSP 价格突破预期，可以锁定您的周末了。"

        cards += f"""
        <div class="pastel-floating-card {pos_class}">
            <div class="label-upper">Private Flight Path • {data['dates']}</div>
            <div class="route-fluid">{data['route_name']}</div>
            <div class="hero-price-box">
                <span class="hero-price-val">${data['price']}</span>
                <span class="hero-price-curr">USD</span>
            </div>
            
            <div class="market-briefing">
                <div class="label-upper" style="font-size:0.6rem; color: #ADD8E6; margin-bottom:15px;">Market Intelligence Brief</div>
                {comparison_html}
            </div>
            
            <div class="concierge-memo-wave">助理提示：{memo_text}</div>
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
    
    print(f"--- 一丹的旅行助理: LOOPY 中文私享简报 ({today_str}) ---")
    
    reports_data = []
    recommendations = []

    for task in TASKS:
        print(f"Loopy 智控扫描: {task['route_name']}...")
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
    print("中文版 Loopy 更新完成: index.html")

if __name__ == "__main__":
    asyncio.run(run_tracker())
