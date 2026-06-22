# Stock Sentiment Pipeline — 完整任務說明

## 目錄結構
```
/root/stock-sentiment/
├── parser/
│   ├── stock_ner.py        # 股票NER提取器
│   └── ticker_validator.py  # [新建] 代碼過濾器
├── steps/
│   ├── step2_extract_tickers.py
│   ├── step3_analyze.py
│   └── step4_backtest.py
├── webui/
│   └── app.py              # FastAPI Web UI
├── portfolio/
│   ├── engine.py
│   └── price_fetcher.py
└── data/
    ├── backtest_dashboard.html  # 靜態 HTML 儀表板
    └── price_cache/             # 價格緩存 JSON
```

## Phase 1 — Ticker 噪聲過濾

### 問題
Step 2 提取了太多非股票代碼（如 "X", "FI", "AL", "AWS", "FTX", "BLSKY", "BOA" 等），
這些根本不是真實股票代碼，導致 DB 被污染、回測失真。

### 做法
新建 `parser/ticker_validator.py`，包含：

1. **黑名單**（已知非股票，直接拒絕）
```
NON_STOCK_BLACKLIST = {
    "X", "FI", "AL", "AWS", "FTX", "BLSKY", "BOA", "CITI", "CATL", "KOSPI",
    "RUT", "ZW", "ZEC", "XLS", "XUSS", "XBOT", "WLFI", "VVST", "VNP.T",
    "VLN.T", "VLH", "VGP", "VGO", "UHR", "SMHSF", "SMHN", "SMHMD", "SKC",
    "SIV", "SHA.D", "SGCG", "QBUT", "PAY.B", "PDY", "ONET", "NTI", "NORBT",
    "NIDGY", "MVZ.B", "MVL", "MTL", "MKA", "LYC.A", "LVMH", "LPKK", "LCRX",
    "KOSPI", "HXSCL", "HSP.A", "HALEU", "GRZ", "GOGL", "FIT", "ELOSE",
    "DRFT", "DNKG", "DLFI", "DGDX", "CXV", "CSPH", "CRCLQ", "CNEX", "CCCX",
    "BLSKY", "ASMC", "ALCJ", "ACUVI", "ABB", "TPU", "PLSR", "EXA", "ETORO",
    "CRBS", "AMSL", "ALPD", "SYSS", "PSTG", "FI", "ASE", "AXT", "CREDO",
    "DOWA", "ASHM", "VNP", "TOWA", "QLCM", "APPL", "HPS.A", "RPI", "LPK",
    "ALRIB", "BITF", "SOI",
}
```

2. **格式驗證規則**
   - 長度 1~6 字符
   - 只含大寫字母、點、橫線
   - 純數字 → 拒絕
   - 常見英文單詞（短於 4 字母）→ 拒絕

3. **函數**
   - `is_valid_ticker(symbol: str) -> bool` — 完整校驗
   - `cleanup_database()` — 清理 DB 中已有的無效 ticker

### 集成方式
修改 `steps/step3_analyze.py`，在讀取 `TweetTicker` 時調用 `is_valid_ticker()`，
跳過無效 ticker，不讓它們進入 `StockMention` 表。

## Phase 2 — Dashboard 深淺色模式

修改 `webui/app.py`：

1. **添加 CSS 變量**
   - `--bg-primary`, `--bg-card`, `--text-primary`, `--text-secondary`, `--border-color`
   - 深色模式為當前配色，淺色模式為白底深字

2. **主題切換按鈕**
   - 在 header-bar 加一個 🌙/☀️ 按鈕
   - 點擊切換 `data-theme` 屬性
   - 存 `localStorage` 持久化

3. **所有顏色引用改為 CSS 變量**
   - body 背景、卡片背景、邊框、文字顏色、圖表顏色
   - Chart.js 的 scales/ticks 顏色也要根據主題動態切換

## Phase 3 — 部署到 EdgeOne

### 方式
EdgeOne 是騰訊雲邊緣加速+靜態託管服務。
FastAPI 應用需要打包部署。

### 步驟
1. 準備 `requirements-web.txt`（FastAPI + uvicorn + sqlalchemy + loguru）
2. 寫一個 EdgeOne 兼容的部署配置
3. 或者將 webui 轉為靜態 HTML（如 `backtest_dashboard.html`）部署到 EdgeOne 靜態託管

### 流程
- 先本地測試 webui 能正常運行
- 然後討論部署方案

## 執行順序

請按照以下順序執行：

1. **創建 `ticker_validator.py`**
2. **修改 `step3_analyze.py`** 集成過濾
3. **運行清理** — `python3 -c "from parser.ticker_validator import cleanup_database; cleanup_database()"`
4. **重新運行回測** — `python3 pipeline.py --step 3,4`
5. **修改 `webui/app.py`** — 加深淺色模式
6. **本地測試 webui** — `cd /root/stock-sentiment && python3 -m uvicorn webui.app:app --host 0.0.0.0 --port 8080`
7. **更新 dashboard HTML** — 再生成一次靜態儀表板
8. **準備 EdgeOne 部署配置**

請直接開始，每一步完成後告訴我結果摘要。