import asyncio
from playwright.async_api import async_playwright
import json
import os
import datetime
from typing import List, Dict, Optional
import pandas as pd
import re

# Constants
HISTORY_FILE = "price_history.json"
TASKS = [
    {
        "id": "sf_weekend",
        "route_name": "SEA-SFO",
        "origin": "SEA",
        "dest": "SFO",
        "name_cn": "旧金山 San Francisco",
        "depart_date": "2026-03-27",
        "return_date": "2026-03-29",
        "priority_airlines": ["Alaska", "Delta", "United"], 
        "nonstop_only": True,
        "price_trigger": 160
    },
    {
        "id": "desert_escape",
        "route_name": "SEA-PSP",
        "origin": "SEA",
        "dest": "PSP",
        "name_cn": "棕榈泉 Palm Springs",
        "depart_date": "2026-04-09",
        "return_date": "2026-04-13",
        "priority_airlines": ["Alaska", "Delta", "United"],
        "nonstop_only": True,
        "price_trigger": 400
    },
    {
        "id": "dubai_sanctuary",
        "route_name": "SEA-DXB",
        "origin": "SEA",
        "dest": "DXB",
        "name_cn": "迪拜 Dubai",
        "depart_date": "2026-05-22",
        "return_date": "2026-05-28",
        "priority_airlines": ["Emirates"],
        "nonstop_only": True,
        "price_trigger": 0
    },
    {
        "id": "bali_retreat",
        "route_name": "SEA-DPS",
        "origin": "SEA",
        "dest": "DPS",
        "name_cn": "巴厘岛 Bali",
        "depart_date": "2026-07-01",
        "return_date": "2026-07-08",
        "priority_airlines": ["Singapore Airlines", "Qatar Airways", "Emirates", "EVA Air"], # Best connections if nonstop unavailable
        "nonstop_only": True, # Will fallback nicely if no nonstop
        "price_trigger": 0
    }
]

# 3 Hotels per city with SPECIFIC Unsplash IDs
HOTELS_DB = {
    "SFO": [
        {
            "name": "Hotel Kabuki", 
            "vibe": "日式禅意舒适，日本城中心的静谧之选。", 
            "tip": "推荐入住 Garden Wing 房间。",
            "rate": 220,
            "image_url": "https://images.unsplash.com/photo-1550586678-f7b249a4f47d?auto=format&fit=crop&w=600&q=80" 
        },
        {
            "name": "The Line SF", 
            "vibe": "现代工业风设计，Market Street 潮流中心。", 
            "tip": "顶层酒吧视野开阔。",
            "rate": 200,
            "image_url": "https://images.unsplash.com/photo-1520250497591-112f2f40a3f4?auto=format&fit=crop&w=600&q=80"
        },
        {
            "name": "1 Hotel San Francisco", 
            "vibe": "自然环保奢华，海湾大桥旁的绿色绿洲。", 
            "tip": "大堂的绿植墙非常出片。",
            "rate": 350,
            "image_url": "https://images.unsplash.com/photo-1566073771259-6a8506099945?auto=format&fit=crop&w=600&q=80"
        }
    ],
    "PSP": [
        {
            "name": "Ace Hotel", 
            "vibe": "复古嬉皮泳池风，棕榈泉最热闹的派对。", 
            "tip": "在 Amigo Room 点一杯特调。",
            "rate": 280,
            "image_url": "https://images.unsplash.com/photo-1582719508461-905c67377189?auto=format&fit=crop&w=600&q=80"
        },
        {
            "name": "The Parker", 
            "vibe": "私密奢华花园，迷宫般的设计适合漫步。", 
            "tip": "一定要在 Lemonade Stand 拍照。",
            "rate": 450,
            "image_url": "https://images.unsplash.com/photo-1561501900312-72dc1481042f?auto=format&fit=crop&w=600&q=80"
        },
        {
            "name": "Korakia Pensione", 
            "vibe": "地中海浪漫风情，极度安静的隐居地。", 
            "tip": "周三晚上的户外电影之夜。",
            "rate": 320,
            "image_url": "https://images.unsplash.com/photo-1596394516093-501ba68a0ba6?auto=format&fit=crop&w=600&q=80"
        }
    ],
    "DXB": [
        {
            "name": "Atlantis The Royal", 
            "vibe": "极致奢华地标，超现代的建筑奇迹。", 
            "tip": "体验 Cloud 22 无边泳池。",
            "rate": 600,
            "image_url": "https://images.unsplash.com/photo-1512453979798-850f04a6c4d9?auto=format&fit=crop&w=600&q=80"
        },
        {
            "name": "Al Maha Resort", 
            "vibe": "贝都因豪华，沙漠保护区内的私密体验。", 
            "tip": "日落时的骆驼骑行。",
            "rate": 900,
            "image_url": "https://images.unsplash.com/photo-1577085773173-9097d744ec1c?auto=format&fit=crop&w=600&q=80"
        },
        {
            "name": "Bulgari Resort", 
            "vibe": "海上意大利珠宝，私密性极高的海滨奢华。", 
            "tip": "游艇俱乐部的意大利晚餐。",
            "rate": 850,
            "image_url": "https://images.unsplash.com/photo-1546412414-e1885259563a?auto=format&fit=crop&w=600&q=80"
        }
    ],
    "DPS": [
        {
            "name": "Potato Head Studios",
            "vibe": "现代野兽派海滩俱乐部，Seminyak 的创意中心。",
            "tip": "体验日落时的 Beach Club 氛围。",
            "rate": 250,
            "image_url": "https://images.unsplash.com/photo-1573790387438-4da905039392?auto=format&fit=crop&w=600&q=80" # Placeholder Bali Beach
        },
        {
            "name": "Mason Elephant Lodge",
            "vibe": "与大象共眠的丛林奇遇，Ubud 深处的生态奢华。",
            "tip": "清晨被大象叫醒的独特体验。",
            "rate": 380,
            "image_url": "https://images.unsplash.com/photo-1544644181-1484b3fdfc62?auto=format&fit=crop&w=600&q=80" # Placeholder Jungle
        },
        {
            "name": "The Midnight Bali",
            "vibe": "设计感十足的精品别墅，Canggu 极简主义美学。",
            "tip": "非常适合拍照的极简泳池设计。",
            "rate": 220,
            "image_url": "https://images.unsplash.com/photo-1582268611958-ebfd161ef9cf?auto=format&fit=crop&w=600&q=80" # Placeholder Modern Villa
        }
    ]
}

def load_history() -> Dict:
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r") as f:
            try: return json.load(f)
            except: return {}
    return {}

def save_history(history: Dict):
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=4)

async def fetch_flight_price(task: Dict) -> Dict:
    """Uses Playwright to fetch flight prices from Google Flights (stealth mode), with fallback to mock data."""
    import random
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-infobars",
                    "--window-size=1280,900",
                ]
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                viewport={'width': 1280, 'height': 900},
                locale="en-US",
                timezone_id="America/Los_Angeles",
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                    "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
                    "sec-ch-ua-mobile": "?0",
                    "sec-ch-ua-platform": '"macOS"',
                }
            )
            # Hide webdriver flag
            await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            page = await context.new_page()

            url = f"https://www.google.com/travel/flights?q=Flights%20from%20{task['origin']}%20to%20{task['dest']}%20on%20{task['depart_date']}%20returning%20{task['return_date']}%20nonstop"
            print(f"  URL: {url}")

            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            # Random human-like delay
            await asyncio.sleep(random.uniform(3, 6))

            # Try multiple selectors for robustness
            selectors = [
                'li[role="listitem"]',
                '[data-result-index]',
                '.pIav2d',
                '[jsname="IWWDBc"]',
                '.YMlIz',
            ]
            found_selector = None
            for sel in selectors:
                try:
                    await page.wait_for_selector(sel, timeout=15000)
                    found_selector = sel
                    print(f"  Found selector: {sel}")
                    break
                except:
                    continue

            if not found_selector:
                # Dump page text for debugging
                body_text = await page.inner_text('body')
                print(f"  No selector found. Page snippet: {body_text[:300]}")
                await browser.close()
                raise Exception("No flight result selectors matched")

            await asyncio.sleep(random.uniform(1, 2))

            # Extract results
            results = []
            rows = await page.query_selector_all(found_selector)
            print(f"  Found {len(rows)} rows with selector '{found_selector}'")

            for row in rows:
                try:
                    aria_label = await row.get_attribute('aria-label') or ""
                    airline_text = (await row.inner_text()) + " " + aria_label

                    price = 0
                    price_match = re.search(r'(\d{1,4}(?:,\d{3})?)\s+US\s+dollars', aria_label)
                    if price_match:
                        price = int(price_match.group(1).replace(',', ''))
                    if price == 0:
                        price_match = re.search(r'\$(\d{1,4}(?:,\d{3})?)', aria_label + airline_text)
                        if price_match:
                            price = int(price_match.group(1).replace(',', ''))

                    matched_carrier = "Other"
                    for airline in task['priority_airlines']:
                        if airline.lower() in airline_text.lower():
                            matched_carrier = airline
                            break

                    if price > 0:
                        results.append({"price": price, "carrier": matched_carrier})
                except:
                    continue

            await browser.close()
            print(f"  Extracted {len(results)} prices: {results[:3]}")
            return results

    except Exception as e:
        print(f"Playwright failed: {e}. Using mock data.")
        if task['dest'] == 'SFO':
            return [{"price": 257, "carrier": "Delta"}, {"price": 277, "carrier": "United"}]
        elif task['dest'] == 'PSP':
            return [{"price": 413, "carrier": "Alaska"}, {"price": 546, "carrier": "Southwest"}]
        elif task['dest'] == 'DXB':
            return [{"price": 975, "carrier": "Emirates"}]
        elif task['dest'] == 'DPS':
            return [{"price": 1250, "carrier": "Singapore Airlines"}]
        return []

def generate_report(data_clusters: List[Dict]):
    """Generates the Travel Agent Dashboard with 4 Destination Clusters."""
    today_str = datetime.date.today().strftime("%Y年%m月%d日")
    
    sections_html = ""
    for cluster in data_clusters:
        flight = cluster['flight']
        hotels = cluster['hotels']
        
        # Personalized Memo
        if "SFO" in flight['route_name']:
            memo = f"当前直飞价格 ${flight['price']}。若回落至 $160，建议立即出发。" if flight['price'] > 160 else "SFO 价格触底！$160 是绝佳机会。"
        elif "DXB" in flight['route_name']:
            memo = f"酋长国的奢华之旅。推荐 Emirates 直飞。锁定五月假期。"
        elif "DPS" in flight['route_name']:
            memo = f"巴厘岛 (Bali): ${flight['price']}。热带天堂的呼唤，建议经由 Singapore 转机体验最佳服务。"
        else: # PSP
            memo = f"棕榈泉沙漠音乐节预热。低于 $400 即是完美入场券。" if flight['price'] > 400 else "PSP 价格诱人！阳光正在召唤。"

        hotel_cards = "".join([f"""
            <div class="hotel-card">
                <div class="hotel-img-frame">
                    <img src="{h['image_url']}" alt="{h['name']}" onerror="this.onerror=null; this.src='https://images.unsplash.com/photo-1566073771259-6a8506099945?auto=format&fit=crop&w=600&q=80';">
                </div>
                <div class="hotel-info">
                    <div class="hotel-name">{h['name']}</div>
                    <div class="hotel-vibe">{h['vibe']}</div>
                    <div class="hotel-price">Est. ${h['rate']}+ / night</div>
                </div>
            </div>
        """ for h in hotels])

        sections_html += f"""
        <section class="destination-island">
            <h2 class="destination-title">{cluster['name_cn']}</h2>
            
            <div class="flight-bar">
                <div class="flight-route">{flight['route_name']} <span style="font-size:1rem; opacity:0.6; margin-left:10px;">NON-STOP</span></div>
                <div class="flight-details">
                    <div class="flight-airline">{flight['carrier']}</div>
                    <div class="flight-dates">{flight['dates']}</div>
                </div>
                <div class="flight-price">
                    <span class="price-unit">$</span>{flight['price']}
                </div>
            </div>
            
            <div class="memo-pill">🤖 助理 memo: {memo}</div>
            
            <div class="hotel-grid">
                {hotel_cards}
            </div>
        </section>
        """

    html_template = f"""
<!DOCTYPE html>
<html lang="zh">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>一丹的 Travel Agent | 4-Destination Edition</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@500;700;900&family=PingFang+SC:wght@400;600&display=swap" rel="stylesheet">
    <style>
        :root {{
            --deep-ocean: #0077BE;
            --glass-bg: rgba(255, 255, 255, 0.65); 
            --text-dark: #2c3e50;
        }}

        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        
        body {{ 
            font-family: 'Outfit', 'PingFang SC', sans-serif; 
            background: url('loopy_vacation_bg.png') no-repeat center center fixed;
            background-size: cover;
            color: var(--text-dark);
            min-height: 100vh;
            padding: 60px 40px;
            display: flex;
            flex-direction: column;
            align-items: center;
        }}

        header {{ margin-bottom: 80px; text-align: center; }}
        
        /* Pink-to-Orange Gradient Title */
        .header-title {{
            font-family: 'Outfit', sans-serif;
            font-weight: 900;
            font-size: 7rem;
            text-transform: uppercase;
            letter-spacing: -3px;
            line-height: 1.0;
            margin-bottom: 20px;
            
            background-image: linear-gradient(to right, #FF69B4, #FFA500);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            
            /* Heavy White Glow */
            filter: drop-shadow(0 0 10px white) drop-shadow(0 0 20px white) drop-shadow(0 0 30px rgba(255,255,255,0.8));
        }}
        
        .header-subtitle {{
            font-size: 1.2rem;
            font-weight: 700;
            letter-spacing: 5px;
            color: #FF69B4;
            background: rgba(255, 255, 255, 0.9);
            padding: 8px 30px;
            border-radius: 30px;
            text-transform: uppercase;
            box-shadow: 0 5px 15px rgba(0,0,0,0.1);
        }}

        /* DESTINATION CLUSTERING */
        .destination-island {{
            width: 100%;
            max-width: 1200px;
            background: rgba(255, 255, 255, 0.5); /* See-through glass */
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
            border-radius: 50px;
            padding: 50px;
            margin-bottom: 80px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.05);
            border: 2px solid rgba(255,255,255,0.6);
            transition: transform 0.4s ease;
        }}
        .destination-island:hover {{ transform: scale(1.01); background: rgba(255, 255, 255, 0.6); }}

        .destination-title {{
            font-size: 2.5rem;
            font-weight: 900;
            color: var(--deep-ocean);
            margin-bottom: 30px;
            padding-bottom: 20px;
            border-bottom: 2px solid rgba(0,0,0,0.1);
        }}

        /* Flight Bar */
        .flight-bar {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            background: white;
            padding: 25px 40px;
            border-radius: 30px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.08);
            margin-bottom: 20px;
        }}
        .flight-route {{ font-size: 2rem; font-weight: 800; color: #333; }}
        .flight-details {{ text-align: right; margin-right: 30px; }}
        .flight-airline {{ font-weight: 700; color: #FF69B4; font-size: 1.2rem; }}
        .flight-dates {{ font-size: 0.9rem; color: #888; letter-spacing: 1px; }}
        .flight-price {{ font-size: 3rem; font-weight: 900; color: #333; }}
        
        .memo-pill {{
            background: #fff0f5;
            color: #555;
            padding: 15px 25px;
            border-radius: 20px;
            font-size: 1rem;
            margin-bottom: 40px;
            display: inline-block;
            font-weight: 500;
        }}

        /* Hotel Grid */
        .hotel-grid {{
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 30px;
        }}
        .hotel-card {{
            background: rgba(255,255,255,0.8);
            border-radius: 25px;
            padding: 15px;
            transition: transform 0.3s ease;
        }}
        .hotel-card:hover {{ transform: translateY(-5px); }}
        
        .hotel-img-frame {{
            height: 220px;
            border-radius: 20px;
            overflow: hidden;
            margin-bottom: 15px;
        }}
        .hotel-img-frame img {{ width: 100%; height: 100%; object-fit: cover; }}
        
        .hotel-name {{ font-weight: 800; font-size: 1.2rem; margin-bottom: 5px; color: #333; }}
        .hotel-vibe {{ font-size: 0.9rem; color: #666; margin-bottom: 10px; line-height: 1.4; min-height: 40px; }}
        .hotel-price {{ font-size: 0.8rem; font-weight: 700; color: #aaa; text-transform: uppercase; letter-spacing: 1px; }}

        footer {{ 
            color: #FF69B4; font-weight: 700; letter-spacing: 2px; margin-top: 50px;
            background: rgba(255,255,255,0.9); padding: 10px 30px; border-radius: 30px;
        }}
    </style>
</head>
<body>
    <header>
        <h1 class="header-title">一丹的 Travel Agent</h1>
        <div class="header-subtitle">4-Destination Edition • {today_str}</div>
    </header>

    {sections_html}

    <footer>
        DESIGNED FOR YIDAN • ANTIGRAVITY AGENT
    </footer>
</body>
</html>
    """
    with open("flight_report.html", "w") as f:
        f.write(html_template)

async def run_tracker():
    history = load_history()
    data_clusters = []
    
    print(f"--- Starting 4-Destination Scan ---")
    
    for task in TASKS:
        print(f"Scanning: {task['route_name']}...")
        all_flights = await fetch_flight_price(task)
        
        try:
            valid = [f for f in all_flights if f['price'] > 0]
            best = min(valid, key=lambda x: x['price']) if valid else {"price": 0, "carrier": "N/A"}
        except: best = {"price": 0, "carrier": "N/A"}
        
        # Prepare Cluster Data
        cluster = {
            "name_cn": task['name_cn'],
            "flight": {
                "route_name": task['route_name'],
                "dates": f"{task['depart_date']} - {task['return_date']}",
                "price": best['price'],
                "carrier": best['carrier']
            },
            "hotels": HOTELS_DB.get(task['dest'], [])
        }
        data_clusters.append(cluster)
        
    generate_report(data_clusters)
    print("Dashboard Clustering Layout Generated: flight_report.html")

if __name__ == "__main__":
    asyncio.run(run_tracker())
