# SSS Backtesting — Stock Sentiment Backtesting System

基于 Twitter/X 博主推文情绪分析的**虚拟持仓回测系统**。

## 架构

```
推文 → Step 1 导入 → Step 2 提取股票代码 → Step 3 情绪分析 → Step 4 回测 → 📊 Dashboard
                          ↓                           ↓
                    ticker_validator              FinBERT + LLM
                    （噪声过滤）                    （两阶段分析）
```

## 功能

- **情绪分析管线**：4 步流水线，从推文到回测全自动
- **智能 NER**：识别 $TICKER、中文公司名、英文公司名
- **噪声过滤**：自动排除非股票代码，保证数据质量
- **两阶段情感分析**：FinBERT 初筛 → LLM 二审（反讽检测）
- **指数衰减权重**：14 天半衰期，时间越近权重越高
- **回测引擎**：模拟虚拟持仓，记录完整调仓历史
- **实时看板**：FastAPI + Chart.js，深浅色模式切换

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 配置（复制 .env 填入 API Key）
cp .env.example .env

# 运行完整管线
python3 pipeline.py

# 启动 Web 看板
python3 -m uvicorn webui.app:app --host 0.0.0.0 --port 8080
```

## 数据流

| 步骤 | 输入 | 输出 | 说明 |
|------|------|------|------|
| Step 1 | Twitter API / 本地 JSON | `tweets` 表 | 导入推文数据 |
| Step 2 | `tweets` 表 | `tweet_tickers` 表 | NER 提取股票代码 |
| Step 3 | `tweet_tickers` + `tweets` | `stock_mentions` 表 | FinBERT + LLM 情绪打分 |
| Step 4 | `stock_mentions` | `rebalance_events` 表 | 虚拟持仓回测 |

## 价格数据

- 数据源：Yahoo Finance Direct API
- 代理：SOCKS5（访问外网）
- 缓存：`data/price_cache/`，按 ticker 分文件
- 支持欧美主要交易所（NYSE、NASDAQ、LSE、Euronext、Stockholm 等）

## 技术栈

- **存储**：SQLite + SQLAlchemy
- **NLP**：FinBERT + 自定义 NER
- **回测**：纯 Python，指数衰减模型
- **看板**：FastAPI + Chart.js + CSS Variables 主题