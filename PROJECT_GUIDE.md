# x-stock-sentiment 项目完整指南

> 2026-06-12 生成，供其他 agent/开发者快速接入。

---

## 快速开始

```bash
cd x-stock-sentiment

# 1. 清除代理（Windows 环境必须！）
export NO_PROXY='*'
unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY

# 2. 安装依赖
pip install -r requirements.txt

# 3. 抓取推文 + 情绪分析 + 入库
python run_minimal.py

# 4. 生成持仓快照
python run_snapshot.py aleaborteddit

# 5. 生成 YTD 收益曲线（含 QQQ 基准）
python run_equity_curve.py aleaborteddit
```

**模型文件**: `data/models/fintwitbert/` 已包含在项目包中，无需重新下载。

---

## 项目定位

基于 X（Twitter）博主 **@aleaborteddit** 的推文，构建一个 **美股情绪投资组合**。

```
X 推文抓取 → 股票 NER 提取 → FinTwitBERT 情绪分析 → 时间衰减 + sigmoid 映射 → 持仓快照 + YTD 收益曲线
```

## 当前状态（截至 2026-06-12）

| 指标 | 值 |
|------|-----|
| 最新推文 | 21 条 (2025-09-19 ~ 2026-06-04) |
| 持仓股票 | 72 只 |
| 价格缓存 | 77 只股票，~44,640 数据点，62/77 覆盖 1Y+ |
| YTD 收益 | +28.00%（vs QQQ +16.79%） |
| Alpha | +11.21% |
| scale | 0.5 |

---

## 目录结构

```
x-stock-sentiment/
├── main.py                      # 旧版主入口（VADER 引擎，已弃用）
├── run_minimal.py               # ★ 当前主力流程（抓取→情绪→入库）
├── run_pipeline.py              # 完整流水线
├── run_snapshot.py              # ★ 生成静态持仓快照（Markdown 报告）
├── run_equity_curve.py          # ★ 生成 YTD 收益曲线（HTML，含 QQQ 对比）
├── run_warmup_prices.py         # Yahoo chart API 预热（按需）
├── run_warmup_av.py             # Alpha Vantage 预热（25 次/天）
├── run_warmup_yahoo.py          # Yahoo 全量预热
├── config.py                    # 全局配置
├── .env                         # 密钥/凭证（不上传 Git）
├── requirements.txt             # Python 依赖
│
├── ai/
│   ├── sentiment_analyzer.py    # 情绪分析器（调用 FinTwitBERT）
│   └── finbert_analyzer.py      # FinTwitBERT 模型封装（GPU, RTX 5060）
│                                #   模型路径: data/models/fintwitbert/
│                                #   标签: bullish→positive, bearish→negative
│
├── scraper/
│   ├── playwright_scraper.py    # 读取 latest_tweets.json（不发起请求）
│   ├── browserbase_scraper.py   # ★ 云端 X 抓取（Browserbase US IP）
│   ├── x_api.py                 # X API v2 客户端（备用，基本不可用）
│   ├── scrape.py                # ★ 统一入口（本地 → 云端自动 fallback）
│   ├── scrape_x.sh              # agent-browser 本地抓取 shell
│   ├── scrape_tweets.sh         # 旧版抓取 shell
│   ├── extract_tweets.js        # DOM 选择器 ([data-testid="tweet"])
│   └── __init__.py              # 导出 PlaywrightScraper, BrowserbaseScraper, XAPIClient
│
├── parser/
│   └── stock_ner.py             # $TICKER 正则 + 中文名词典 + 黑名单过滤
│
├── storage/
│   └── models.py                # SQLite: Tweet, StockMention 表
│
├── portfolio/
│   ├── snapshot.py              # ★ Snapshot 引擎核心
│   └── price_fetcher.py         # 多源 fallback: 缓存→Yahoo→YFinance→AV
│
├── data/
│   ├── price_cache/             # 77 只股票 JSON 缓存（未含在包内，需重建）
│   │   └── QQQ.json             # 纳斯达克基准（已含）
│   ├── models/fintwitbert/      # FinTwitBERT 模型（已含，420MB）
│   ├── latest_tweets.json       # 最新抓取的 21 条推文
│   ├── tweets.db                # SQLite 数据库（未含在包内）
│   ├── snapshot_*.md            # 快照报告
│   └── equity_curve_*.html      # 收益曲线
│
└── .workbuddy/memory/           # 项目记忆
    ├── MEMORY.md
    └── 2026-06-*.md
```

---

## 核心参数

### Snapshot 引擎 (portfolio/snapshot.py)

| 参数 | 值 | 说明 | 调参历史 |
|------|-----|------|---------|
| initial_cash | $1,000,000 | 初始资金 | 固定 |
| C_base | $100,000 | 单股基准 (10%×cash) | 固定 |
| **ALLOC_SCALE** | **0.5** | sigmoid 缩放 | 0.3→5.0→8.0→**0.5** |
| DECAY_ALPHA | 1.0 | 推文序数衰减 | 固定 |
| DECAY_BETA | 0.02 | 日历天数衰减 | 固定 |

> **scale 调参记录**: 0.3(全部饱和)→5.0(略平均)→8.0(更平均)→**0.5(当前，权重极差 0.85pp)**

### 关键公式

**时间衰减**:
```
decay(k) = 1 / ln(1 + 1.0 × T_tweet + 0.02 × T_days)
```
- `T_tweet`: 此推文之后 Serenity 又发了多少条推文
- `T_days`: 推文发出到现在的天数

**Sigmoid 映射**:
```
alloc(k) = $100,000 × σ(score / 0.5)
         = $100,000 × 1/(1 + e^(-score/0.5))
```
- 只计入 positive sentiment（score > 0）
- 分配上限受 total_cash 约束，超出时等比缩放

**YTD 收益计算（buy-and-hold）**:
- 在 YTD 起点按情绪权重一次性建仓
- 此后固定股数不变，每日按收盘价计算组合价值
- **不每日 rebalance**（Footer 已标注）

---

## 数据库 Schema (SQLite: data/tweets.db)

### Tweet 表
| 字段 | 类型 | 说明 |
|------|------|------|
| id | TEXT (PK) | 推文 ID |
| author_id | TEXT | 博主 ID (aleaborteddit) |
| text | TEXT | 推文正文 |
| created_at | DATETIME | 发布时间 |
| fetched_at | DATETIME | 抓取时间 |

### StockMention 表
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER (PK) | 自增 |
| tweet_id | TEXT (FK) | 关联推文 |
| symbol | TEXT | 股票代码 (e.g. AAPL) |
| name | TEXT | 公司名称 |
| sentiment_score | FLOAT | 情绪分数 (0~1) |
| sentiment_label | TEXT | 情绪标签 |
| analyzed_at | DATETIME | 分析时间 |

---

## 数据流详解

### 1. 推文抓取
| 方式 | 原理 | 适用场景 |
|------|------|---------|
| `scrape_x.sh` | agent-browser CLI → 本地 Chromium → x.com | 本地网络可访问 |
| `browserbase_scraper.py` | Browserbase API → 云端 Chromium (US IP) | 本地网络受限 |
| `scrape.py` | 先本地，失败自动切 Browserbase | 日常使用 |

- 输出: `data/latest_tweets.json` (格式: `[{id, text, created_at}]`)
- 使用 Playwright CDP 协议远端操控
- DOM 选择器: `[data-testid="tweet"]` / `[data-testid="tweetText"]` / `time` / `a[href*="/status/"]`

### 2. 股票识别 (parser/stock_ner.py)
- 正则 `\$([A-Z]{1,5})` 提取 Cashtag
- 中文名词典: 英伟达→NVDA, 特斯拉→TSLA, 苹果→AAPL 等
- 黑名单: BTC, ETH, SPY, QQQ, AI, CEO, IPO 等 70+ 非股票 Cashtag

### 3. 情绪分析 (ai/finbert_analyzer.py)
- 模型: **StephanAkkerman/FinTwitBERT-sentiment**
  - RoBERTa-base, 1000 万条金融推文预训练
  - 3 分类: bullish / bearish / neutral
  - 本地路径: `data/models/fintwitbert/`
- 硬件: RTX 5060, CUDA PyTorch
- 输出: score (0~1 的 positive 概率)
- batch_size=8, 21 条推文 ~3 秒

### 4. 价格缓存 (portfolio/price_fetcher.py)
优先级: **本地 JSON 缓存 → Yahoo chart API → YFinance → Alpha Vantage**

关键发现: Yahoo chart API `query2.finance.yahoo.com/v8/finance/chart/{symbol}` **完全无需 cookie/crumb/认证**，裸 GET 即可。

当前覆盖:
- 62/77 只 1Y+ (≥2024-01-02)
- 69/77 只 6M+
- 3 只缺数据: FLY, NKLR, WYFI (Yahoo 无更早记录)
- 2 只缺价格: WLAC, CCCX

### 5. 输出产物
| 产物 | 生成方式 | 格式 |
|------|---------|------|
| 持仓快照 | `python run_snapshot.py aleaborteddit` | Markdown |
| YTD 曲线 | `python run_equity_curve.py aleaborteddit` | HTML (ECharts) |

---

## 多源 Fallback 总览

```
                          ┌─────────────────────────┐
                          │  scraper/scrape.py      │
                          │  统一抓取入口            │
                          └───────────┬─────────────┘
                                      │
                    ┌─────────────────┼─────────────────┐
                    ▼                 ▼                  │
            ┌──────────────┐  ┌──────────────────┐      │
            │ scrape_x.sh  │  │ Browserbase      │      │
            │ (本地Chrome) │  │ (云端US IP)      │      │
            └──────────────┘  └──────────────────┘      │
                                      │                  │
                                      ▼                  │
                          ┌─────────────────────────┐    │
                          │  latest_tweets.json     │    │
                          └─────────────────────────┘    │
                                      │                  │
                                      ▼                  │
                          ┌─────────────────────────┐    │
                          │  PriceFetcher (多源)    │◄───┘
                          │  缓存→Yahoo→YF→AV      │
                          └─────────────────────────┘
```

---

## 已知问题

1. **持仓过于平均** — scale=0.5 下极差仅 0.85pp (1.00%~1.85%)。72 只全部买入，等权色彩重。建议后续加 `min_score` 截断或只买 Top 20。

2. **价格缺失** — WLAC, CCCX 两只无价格 → 收益计算自动跳过。

3. **Yahoo 数据范围** — FLY/NKLR/WYFI 只有 ~5 个月。这些是近期上市股票，Yahoo 无更早记录。

4. **AV 免费层 25 次/天** — `run_warmup_av.py` 自动追踪配额。

5. **Windows .env** — 代理变量有时被系统读取，运行前必须 `unset`。

6. **模型只能在 GPU 环境用** — 路径硬编码 `data/models/fintwitbert/`。

---

## 依赖清单

```
# Python
httpx>=0.27.0
python-dotenv>=1.0.0
loguru>=0.7.0
openai>=1.30.0
transformers>=4.40.0
torch>=2.3.0          # CUDA
yfinance>=0.2.40
requests>=2.31.0
curl_cffi
sqlalchemy>=2.0
browserbase>=1.13.0
playwright>=1.60.0

# 外部 CLI
agent-browser         # npm install -g agent-browser (用于本地抓取)
```

---

## 环境变量 (.env)

| 变量 | 用途 |
|------|------|
| X_BEARER_TOKEN | X API v2 认证（备用） |
| ALPHA_VANTAGE_API_KEY | 价格数据备用源 |
| BROWSERBASE_API_KEY | 云端浏览器 API |
| BROWSERBASE_PROJECT_ID | 云端浏览器项目 |
| HTTPS_PROXY / HTTP_PROXY | 本地代理（运行时先清除！） |
