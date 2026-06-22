# S100 指数回测看板

> 基于 Twitter/X 博主推文情绪分析的虚拟持仓回测系统。
>
> **S100 指数**：选取衰减后情绪分排名前 100 的股票构建虚拟组合，7 天半衰期加速新旧更替。

## 数据源

当前追踪 **[@aleabitoreddit](https://x.com/aleabitoreddit)** 的推文，提取股票代码 → 情绪分析 → 回测 → 看板。

> 可复用于任何财经博主——修改 `config.py` 中的 `TARGET_USERNAME` 即可切换数据源。

## 管线架构

```
推文导入  →  代码提取  →  情绪分析  →  回测  →  📊 看板
 Step 1      Step 2       Step 3      Step 4
                           ├─ FinBERT 初筛
                           └─ LLM 二审（反讽检测）
```

| 步骤 | 说明 |
|------|------|
| Step 1 | 导入推文数据（Twitter API / 本地 JSON） |
| Step 2 | NER 提取股票代码 + 噪声过滤（`ticker_validator.py`） |
| Step 3 | 两阶段情绪分析：FinBERT 快速打分 + LLM 复核 |
| Step 4 | 虚拟持仓回测，14天半衰期衰减，只保留前 100 名 |

## 核心特性

- **智能提取**：识别 `$TICKER`、中英文公司名，80+ 黑名单噪声过滤
- **两阶段情绪分析**：FinBERT 初筛 + LLM 二审（带反讽检测）
- **指数衰减模型**：7 天半衰期，新推文权重更高，加速移仓
- **S100 指数规则**：只保留衰减后情绪分排名前 100 的股票
- **对比基准**：净值曲线叠加 QQQ（纳斯达克 ETF），悬停显示收益率
- **响应式看板**：FastAPI + Chart.js，日间/夜间模式切换

## 快速开始

```bash
git clone https://github.com/guderianwarrick/S.SS-Backtesting-Dashboard.git
cd S.SS-Backtesting-Dashboard

# 安装依赖
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env
# 编辑 .env 填入 API Key（LLM 二审用）

# 运行完整管线
python3 pipeline.py

# 启动 Web 看板
python3 -m uvicorn webui.app:app --host 0.0.0.0 --port 8080

# 或生成静态仪表盘
python3 generate_site.py
```

## 技术栈

| 组件 | 方案 |
|------|------|
| 存储 | SQLite + SQLAlchemy |
| NLP | FinBERT + 自定义 NER + LLM |
| 价格 | Yahoo Finance API（多源 Fallback） |
| 前端 | FastAPI + Chart.js，CSS Variables 主题 |
| 部署 | GitHub + EdgeOne Pages（自动构建） |
| 定时 | crontab（每 4 小时更新数据） |

## 看板指标

| 指标 | 说明 |
|------|------|
| 初始资金 | $1,000,000 虚拟本金 |
| 最终价值 | 当前组合总市值 |
| 累计收益 | 总收益率（含已实现+未实现） |
| 夏普比率 | 年化夏普（日收益 × √252） |
| 最大回撤 | 历史最高点到最低点的跌幅 |
| 调仓次数 | 累计调仓事件数 |
| 涉及股票 | 曾进入组合的不同股票数 |
| vs QQQ 超额 | 组合收益减去同期 QQQ ETF 收益 |

## 项目结构

```
├── pipeline.py          # 主控制器
├── config.py            # 全局配置
├── steps/               # 4 步管线
├── portfolio/           # 回测引擎 + 价格获取器
├── parser/              # 股票 NER + 代码验证器
├── ai/                  # FinBERT + LLM + 反讽检测
├── storage/             # SQLite 模型定义
├── webui/               # FastAPI 看板
├── scraper/             # 推文抓取模块
├── edge-functions/      # EdgeOne 边缘函数
└── cron_monitor.sh      # 定时监控脚本
```