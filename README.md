# S.SS Backtesting Dashboard S100指数看板

基于 Twitter/X 财经博主推文情绪的**虚拟持仓回测看板**。

## 数据源

当前追踪博主：**[@aleabitoreddit](https://x.com/aleabitoreddit)**

> 本框架可复用于任何财经博主——只需修改 `config.py` 中的 `TARGET_USERNAME`，即可将另一位博主的推文接入完整的情绪分析管线。

## 管线架构

```
推文导入 → 股票代码提取 → 情绪分析 → 回测 → 📊 看板
  Step 1        Step 2         Step 3    Step 4
                               ├─ FinBERT 初筛
                               └─ LLM 二审（反讽检测）
```

## 核心特性

- **智能提取**：识别 `$TICKER`、中英文公司名，内置噪声过滤
- **两阶段情绪分析**：FinBERT 快速打分 + LLM 复核（带反讽检测）
- **指数衰减模型**：14 天半衰期，新推文权重更高
- **回测引擎**：虚拟持仓模拟，完整调仓记录
- **响应式看板**：FastAPI + Chart.js，深色/浅色模式切换

## 快速开始

```bash
pip install -r requirements.txt
# 配置环境变量（API Key 等）
python3 pipeline.py            # 运行完整管线
python3 -m uvicorn webui.app:app --host 0.0.0.0 --port 8080  # 启动看板
```

## 技术栈

| 组件 | 方案 |
|------|------|
| 存储 | SQLite + SQLAlchemy |
| NLP | FinBERT + 自定义 NER + LLM |
| 价格 | Yahoo Finance API |
| 前端 | FastAPI + Chart.js |

## 管线步骤

| 步骤 | 说明 |
|------|------|
| Step 1 | 导入推文数据 |
| Step 2 | NER 提取股票代码 + 噪声过滤 |
| Step 3 | FinBERT 情绪打分 + LLM 二审 |
| Step 4 | 虚拟持仓回测，生成调仓记录 |
