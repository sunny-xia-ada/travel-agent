# ✈️ Yidan's Travel Agent | 一丹的旅游助手

A premium, automated flight tracking and travel planning assistant designed with a vibrant "Loopy" aesthetic. This tool monitors flight prices across multiple global destinations and generates a beautiful, interactive dashboard for travel decision-making.

一款专为一丹打造的高端自动化机票监控与旅行规划助手。该工具采用活力的“露比 (Loopy)”主题美学设计，实时监控全球多个目的地的机票价格，并生成精美的交互式仪表盘，助力旅行决策。

---

## ✨ Features | 功能特性

### 1. 🔍 Intelligent Flight Monitoring | 智能机票监控
- **Real-time Scraping**: Uses Playwright to fetch live prices from Google Flights with stealth mode. (使用 Playwright 潜行模式从 Google Flights 获取实时价格。)
- **Multi-Route Tracking**: Monitors SEA to SFO, PSP, DXB, and DPS. (监控西雅图至旧金山、棕榈泉、迪拜及巴厘岛的航线。)
- **Smart Fallback**: Includes a mock data engine to ensure the dashboard remains functional even during scraping limits. (内置模拟数据引擎，确保在爬虫受限时仪表盘依然可用。)

### 2. 🏨 Curated Hotel Database | 精选酒店智库
- **Destination Vibes**: Hand-picked hotel recommendations for each city including vibe descriptions and insider tips. (为每个城市精选酒店推荐，包含风格描述与入住贴士。)
- **Visual Richness**: Integrated with Unsplash for high-quality destination imagery. (集成 Unsplash 高质感目的地影像。)

### 3. 🎨 Premium Visual Dashboard | 高端视觉仪表盘
- **Zanmang Loopy Aesthetics**: A fun, pink-themed interface inspired by the popular character. (以人气角色 Loopy 为灵感的粉色系趣味界面。)
- **Glassmorphism Design**: Modern UI featuring backdrop blurs, vibrant gradients, and elegant typography. (采用背景模糊、鲜艳渐变和优雅字体的现代 UI 设计。)
- **Responsive Layout**: Perfectly formatted for both desktop and mobile viewing. (完美适配桌面端与移动端浏览。)

---

## 🛠️ Tech Stack | 技术栈

- **Core**: Python 3.10+
- **Automation**: [Playwright](https://playwright.dev/python/) (Chromium)
- **Data Handling**: Pandas & JSON
- **Frontend**: Vanilla HTML5 / CSS3 (Glassmorphism & Flexbox)
- **Visuals**: Google Fonts (Outfit & PingFang SC)

---

## 🚀 Getting Started | 快速上手

### Prerequisites | 环境准备
Ensure you have Python 3.10 or higher installed.

```bash
# Install dependencies | 安装依赖
pip install playwright pandas

# Install browser binaries | 安装浏览器内核
playwright install chromium
```

### Running the Tracker | 运行程序
```bash
python flight_tracker.py
```
This will:
1. Scan all active flight tasks.
2. Analyze price trends.
3. Generate the `flight_report.html` dashboard.

---

## 📊 Preview | 预览
The generated **`flight_report.html`** includes:
- **Flight Bar**: Real-time pricing, airline info, and route details.
- **AI Memos**: Smart suggestions based on price triggers (e.g., "Price hit bottom!").
- **Hotel Grid**: Visual cards with rates and vibes.

---

## 📂 Project Structure | 项目结构
- `flight_tracker.py`: Main logic, scraping engine, and report generator.
- `price_history.json`: Local database for historical price tracking.
- `loopy_vacation_bg.png`: The signature background asset.
- `flight_report.html`: The interactive output dashboard.

---

*Designed for Yidan • Powered by Antigravity Agent*  
*为一丹量身定制 • 由 Antigravity 智能体驱动*
