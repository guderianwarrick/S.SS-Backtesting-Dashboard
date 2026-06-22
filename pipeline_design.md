# 解耦 Pipeline 设计

## 问题
当前 `import_and_analyze.py` 把数据导入和情绪分析混在一起，难以单独调试某个步骤。

## 解决方案：分步 Pipeline

每个步骤独立成单独的脚本，通过 SQLite 数据库交互：

```
data/tweets_clean.json
        ↓
[step1_import.py] → SQLite: tweets 表
        ↓
[step2_extract_tickers.py] → SQLite: tweet_tickers 表（提取的股票代码）
        ↓
[step3_analyze.py] → SQLite: stock_mentions 表（情绪分析结果）
        ↓
[step4_backtest.py] → SQLite: rebalance_events 表（回测结果）
```

## 优势

1. **独立运行**：每个步骤可以单独执行
2. **可重跑**：某一步出错只需重跑该步
3. **易调试**：可以检查中间结果
4. **可插拔**：替换某个组件不影响其他步骤

## 用法

```bash
# 完整 pipeline
python3 pipeline.py

# 或者单独运行某一步
python3 steps/step1_import.py data/tweets_clean.json
python3 steps/step2_extract_tickers.py
python3 steps/step3_analyze.py
python3 steps/step4_backtest.py

# 查看中间结果
python3 pipeline.py --status
```

## 步骤说明

### Step 1: 导入推文
- 输入：JSON 文件
- 输出：tweets 表
- 可重跑：清空后重新导入

### Step 2: 提取股票代码
- 输入：tweets 表
- 输出：tweet_tickers 表
- 可重跑：清空后重新提取

### Step 3: 情绪分析
- 输入：tweets + tweet_tickers 表
- 输出：stock_mentions 表
- 可重跑：清空后重新分析
- 支持断点续传（已分析的跳过）

### Step 4: 回测
- 输入：stock_mentions 表
- 输出：rebalance_events 表
- 可重跑：清空后重新回测
