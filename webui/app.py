"""Stock Sentiment 回测 Web UI — FastAPI + Chart.js"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import date, timedelta
from collections import defaultdict
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from storage.models import init_db, session_scope, RebalanceEvent, StockMention, Tweet
from sqlalchemy import func

app = FastAPI(title="Stock Sentiment Backtest Dashboard")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

init_db()

# ── HTML Template ─────────────────────────────────────

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Stock Sentiment 回测看板</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<style>
:root {
  --bg-primary: #0f1923;
  --bg-card: #1a2d3a;
  --bg-card-hover: #1e3342;
  --text-primary: #e0e6ed;
  --text-secondary: #8899aa;
  --text-heading: #fff;
  --border-color: #253a48;
  --accent-blue: #2563eb;
  --accent-green: #4ade80;
  --accent-red: #f87171;
  --chart-grid: #253a48;
  --chart-text: #8899aa;
  --select-bg: #1a2d3a;
  --select-text: #e0e6ed;
  --select-border: #253a48;
  --btn-bg: #253a48;
  --btn-hover: #3a5568;
  --btn-text: #e0e6ed;
  --tag-bullish-bg: #14522b;
  --tag-bullish-text: #4ade80;
  --tag-bearish-bg: #521414;
  --tag-bearish-text: #f87171;
  --tag-neutral-bg: #2a3a4a;
  --tag-neutral-text: #94a3b8;
  --placeholder-text: #556677;
}
[data-theme="light"] {
  --bg-primary: #f0f4f8;
  --bg-card: #ffffff;
  --bg-card-hover: #f8fafc;
  --text-primary: #1e293b;
  --text-secondary: #64748b;
  --text-heading: #0f172a;
  --border-color: #e2e8f0;
  --accent-blue: #2563eb;
  --accent-green: #16a34a;
  --accent-red: #dc2626;
  --chart-grid: #e2e8f0;
  --chart-text: #64748b;
  --select-bg: #ffffff;
  --select-text: #1e293b;
  --select-border: #e2e8f0;
  --btn-bg: #e2e8f0;
  --btn-hover: #cbd5e1;
  --btn-text: #1e293b;
  --tag-bullish-bg: #dcfce7;
  --tag-bullish-text: #16a34a;
  --tag-bearish-bg: #fef2f2;
  --tag-bearish-text: #dc2626;
  --tag-neutral-bg: #f1f5f9;
  --tag-neutral-text: #64748b;
  --placeholder-text: #94a3b8;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: var(--bg-primary); color: var(--text-primary); padding: 20px; }
.container { max-width: 1400px; margin: 0 auto; }
h1 { font-size: 24px; margin-bottom: 6px; color: var(--text-heading); }
.subtitle { color: var(--text-secondary); font-size: 14px; margin-bottom: 24px; }
.header-bar { display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: 12px; margin-bottom: 24px; }
.header-bar h1 { margin-bottom: 0; }
.stats-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 24px; }
.stat-card { background: var(--bg-card); border-radius: 10px; padding: 16px 20px; border: 1px solid var(--border-color); }
.stat-card .label { font-size: 12px; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.5px; }
.stat-card .value { font-size: 26px; font-weight: 700; color: var(--text-heading); margin-top: 4px; }
.stat-card .value.positive { color: var(--accent-green); }
.stat-card .value.negative { color: var(--accent-red); }
.chart-container { background: var(--bg-card); border-radius: 10px; padding: 20px; border: 1px solid var(--border-color); margin-bottom: 24px; }
.chart-container h3 { font-size: 15px; color: var(--text-secondary); margin-bottom: 12px; }
.chart-wrapper { position: relative; width: 100%; height: 400px; }
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin-bottom: 24px; }
@media (max-width: 900px) { .grid-2 { grid-template-columns: 1fr; } }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
thead th { text-align: left; padding: 8px 10px; color: var(--text-secondary); border-bottom: 1px solid var(--border-color); font-weight: 600; font-size: 11px; text-transform: uppercase; }
tbody td { padding: 8px 10px; border-bottom: 1px solid var(--border-color); }
tbody tr:hover { background: var(--bg-card-hover); }
.tag { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }
.tag.bullish { background: var(--tag-bullish-bg); color: var(--tag-bullish-text); }
.tag.bearish { background: var(--tag-bearish-bg); color: var(--tag-bearish-text); }
.tag.neutral { background: var(--tag-neutral-bg); color: var(--tag-neutral-text); }
.pagination { display: flex; justify-content: space-between; align-items: center; margin-top: 12px; gap: 12px; flex-wrap: wrap; }
.pagination button { background: var(--btn-bg); color: var(--btn-text); border: 1px solid var(--border-color); padding: 6px 16px; border-radius: 6px; cursor: pointer; font-size: 13px; }
.pagination button:hover { background: var(--btn-hover); }
.pagination button:disabled { opacity: 0.4; cursor: default; }
.pagination span { color: var(--text-secondary); font-size: 13px; }
.loading { text-align: center; padding: 40px; color: var(--text-secondary); font-size: 14px; }
.error { color: var(--accent-red); text-align: center; padding: 20px; }
.chart-placeholder { height: 120px; display: flex; align-items: center; justify-content: center; color: var(--placeholder-text); font-size: 13px; }
select, .theme-btn { background: var(--select-bg); color: var(--select-text); border: 1px solid var(--select-border); border-radius: 6px; padding: 6px 12px; font-size: 13px; cursor: pointer; }
.theme-btn { padding: 6px 10px; line-height: 1; }
</style>
</head>
<body>
<div class="container">
  <div class="header-bar">
    <div>
      <h1>📊 情绪回测看板</h1>
      <div class="subtitle">@aleabitoreddit · 虚拟组合回测 · <span id="dateRange">加载中...</span></div>
    </div>
    <div style="display:flex;gap:8px;align-items:center">
      <button class="theme-btn" onclick="toggleTheme()" id="themeToggle" title="切换主题">🌙</button>
      <select id="datePreset" class="theme-btn">
        <option value="all">全部时间</option>
        <option value="1m">最近1个月</option>
        <option value="3m">最近3个月</option>
        <option value="6m">最近6个月</option>
        <option value="1y">最近1年</option>
      </select>
      <button onclick="refreshData()" style="background:#2563eb;color:#fff;border:none;border-radius:6px;padding:6px 16px;cursor:pointer;font-size:13px">↻ 刷新</button>
    </div>
  </div>

  <!-- Stats Cards -->
  <div class="stats-row" id="statsRow">
    <div class="stat-card"><div class="label">初始资金</div><div class="value" id="initialCash">-</div></div>
    <div class="stat-card"><div class="label">当前总值</div><div class="value" id="finalValue">-</div></div>
    <div class="stat-card"><div class="label">累计收益</div><div class="value" id="cumulativeReturn">-</div></div>
    <div class="stat-card"><div class="label">调仓次数</div><div class="value" id="totalEvents">-</div></div>
    <div class="stat-card"><div class="label">涉及股票</div><div class="value" id="totalSymbols">-</div></div>
    <div class="stat-card"><div class="label">最长持仓</div><div class="value" id="longestHolding">-</div></div>
  </div>

  <!-- Equity Curve -->
  <div class="chart-container">
    <h3>📈 组合净值曲线</h3>
    <div class="chart-wrapper"><canvas id="equityChart"></canvas></div>
  </div>

  <!-- Holdings + Top Symbols -->
  <div class="grid-2">
    <div class="chart-container">
      <h3>🧩 当前持仓分布</h3>
      <div class="chart-wrapper" style="height:300px"><canvas id="holdingsChart"></canvas></div>
    </div>
    <div class="chart-container">
      <h3>🏆 最常交易股票</h3>
      <div class="chart-wrapper" style="height:300px"><canvas id="topSymbolsChart"></canvas></div>
    </div>
  </div>

  <!-- Rebalance Table -->
  <div class="chart-container">
    <h3>📋 调仓记录</h3>
    <div id="rebalanceTable"><div class="loading">加载中...</div></div>
    <div class="pagination" id="pagination">
      <button id="prevPage" onclick="changePage(-1)" disabled>← 上一页</button>
      <span id="pageInfo">第 1 / 1 页</span>
      <button id="nextPage" onclick="changePage(1)">下一页 →</button>
    </div>
  </div>
</div>

<script>
let currentTheme = localStorage.getItem('theme') || 'dark';
document.documentElement.setAttribute('data-theme', currentTheme);
document.getElementById('themeToggle').textContent = currentTheme === 'dark' ? '🌙' : '☀️';

function toggleTheme() {
  currentTheme = currentTheme === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', currentTheme);
  localStorage.setItem('theme', currentTheme);
  document.getElementById('themeToggle').textContent = currentTheme === 'dark' ? '🌙' : '☀️';
  // Update chart colors on theme switch
  if (equityChartInst) updateChartTheme(equityChartInst);
  if (holdingsChartInst) updateChartTheme(holdingsChartInst);
  if (topSymbolsChartInst) updateChartTheme(topSymbolsChartInst);
}

function getChartTheme() {
  const isDark = document.documentElement.getAttribute('data-theme') !== 'light';
  return {
    grid: isDark ? '#253a48' : '#e2e8f0',
    tick: isDark ? '#8899aa' : '#64748b',
    bg: isDark ? 'rgba(37,99,235,0.08)' : 'rgba(37,99,235,0.06)',
    line: '#2563eb',
    legend: isDark ? '#b0c4d8' : '#64748b',
  };
}

function updateChartTheme(chart) {
  const t = getChartTheme();
  if (chart.options.scales) {
    Object.values(chart.options.scales).forEach(s => {
      if (s.ticks) s.ticks.color = t.tick;
      if (s.grid) s.grid.color = t.grid;
    });
  }
  if (chart.options.plugins?.legend?.labels) {
    chart.options.plugins.legend.labels.color = t.legend;
  }
  chart.update();
}

let equityChartInst = null;
let holdingsChartInst = null;
let topSymbolsChartInst = null;
let currentPage = 1;
let pageSize = 20;
let allRebalances = [];

function fmt(n) {
  if (n === null || n === undefined) return '-';
  return n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtPct(n) {
  if (n === null || n === undefined) return '-';
  const v = (n * 100).toFixed(2);
  return v + '%';
}

async function fetchJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

function getDateFilter() {
  const preset = document.getElementById('datePreset').value;
  if (preset === 'all') return '';
  const now = new Date();
  let from = new Date(now);
  if (preset === '1m') from.setMonth(from.getMonth() - 1);
  else if (preset === '3m') from.setMonth(from.getMonth() - 3);
  else if (preset === '6m') from.setMonth(from.getMonth() - 6);
  else if (preset === '1y') from.setFullYear(from.getFullYear() - 1);
  return from.toISOString().split('T')[0];
}

async function refreshData() {
  const df = getDateFilter();
  const query = df ? `?from=${df}` : '';
  try {
    const summary = await fetchJSON(`/api/summary${query}`);
    document.getElementById('initialCash').textContent = '$' + fmt(summary.initial_cash);
    document.getElementById('finalValue').textContent = '$' + fmt(summary.final_value);
    const cr = document.getElementById('cumulativeReturn');
    cr.textContent = fmtPct(summary.cumulative_return);
    cr.className = 'value ' + (summary.cumulative_return >= 0 ? 'positive' : 'negative');
    document.getElementById('totalEvents').textContent = summary.total_events.toLocaleString();
    document.getElementById('totalSymbols').textContent = summary.total_symbols;
    document.getElementById('longestHolding').textContent = summary.longest_holding_days ? summary.longest_holding_days + ' 天' : '-';
    document.getElementById('dateRange').textContent = summary.date_range || '-';
  } catch(e) {
    console.error('Summary error:', e);
  }

  try {
    const eq = await fetchJSON(`/api/equity_curve${query}`);
    if (equityChartInst) equityChartInst.destroy();
    const ctx = document.getElementById('equityChart').getContext('2d');
    equityChartInst = new Chart(ctx, {
      type: 'line',
      data: {
        labels: eq.dates,
        datasets: [{
          label: '组合净值',
          data: eq.values,
          borderColor: '#2563eb',
          backgroundColor: getChartTheme().bg,
          fill: true,
          tension: 0.2,
          pointRadius: 0,
          borderWidth: 2,
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: ctx => '$' + ctx.parsed.y.toLocaleString('en-US', { minimumFractionDigits: 2 })
            }
          }
        },
        scales: {
          x: { ticks: { color: getChartTheme().tick, maxTicksLimit: 12, font: { size: 11 } }, grid: { color: getChartTheme().grid } },
          y: { ticks: { color: getChartTheme().tick, font: { size: 11 }, callback: v => '$' + v.toLocaleString() }, grid: { color: getChartTheme().grid } }
        }
      }
    });
  } catch(e) { console.error('Equity error:', e); }

  try {
    const h = await fetchJSON(`/api/holdings${query}`);
    if (holdingsChartInst) holdingsChartInst.destroy();
    const entries = Object.entries(h.holdings || {});
    entries.sort((a, b) => b[1] - a[1]);
    const labels = entries.slice(0, 20).map(e => e[0]);
    const values = entries.slice(0, 20).map(e => +(e[1] * 100).toFixed(2));
    const colors = ['#2563eb','#4ade80','#f59e0b','#f87171','#a78bfa','#34d399','#fb923c','#60a5fa','#f472b6','#2dd4bf'];
    const ctx2 = document.getElementById('holdingsChart').getContext('2d');
    holdingsChartInst = new Chart(ctx2, {
      type: 'doughnut',
      data: {
        labels, datasets: [{ data: values, backgroundColor: colors.concat(Array(20).fill('#3a5568')), borderWidth: 0 }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: { position: 'right', labels: { color: getChartTheme().legend, font: { size: 11 }, padding: 8 } },
          tooltip: { callbacks: { label: ctx => ctx.label + ': ' + ctx.parsed.toFixed(2) + '%' } }
        }
      }
    });
  } catch(e) { console.error('Holdings error:', e); }

  try {
    const ts = await fetchJSON(`/api/top_symbols${query}`);
    if (topSymbolsChartInst) topSymbolsChartInst.destroy();
    const labels3 = ts.map(t => t.symbol);
    const values3 = ts.map(t => t.count);
    const colors3 = ts.map((_, i) => {
      const clrs = ['#2563eb','#4ade80','#f59e0b','#f87171','#a78bfa','#34d399'];
      return clrs[i % clrs.length];
    });
    const ctx3 = document.getElementById('topSymbolsChart').getContext('2d');
    topSymbolsChartInst = new Chart(ctx3, {
      type: 'bar',
      data: {
        labels: labels3,
        datasets: [{ data: values3, backgroundColor: colors3, borderRadius: 6, borderSkipped: false }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: ctx => ctx.parsed.y + ' 次调仓' } }
        },
        scales: {
          x: { ticks: { color: getChartTheme().tick, font: { size: 10 } }, grid: { display: false } },
          y: { ticks: { color: getChartTheme().tick, font: { size: 11 } }, grid: { color: getChartTheme().grid } }
        }
      }
    });
  } catch(e) { console.error('Top symbols error:', e); }

  try {
    const rb = await fetchJSON(`/api/rebalances${query}`);
    allRebalances = rb.rebalances || [];
    currentPage = 1;
    renderTable();
  } catch(e) { console.error('Rebalances error:', e); }
}

function renderTable() {
  const total = allRebalances.length;
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const start = (currentPage - 1) * pageSize;
  const end = Math.min(start + pageSize, total);
  const pageData = allRebalances.slice(start, end);

  document.getElementById('prevPage').disabled = currentPage <= 1;
  document.getElementById('nextPage').disabled = currentPage >= pages;
  document.getElementById('pageInfo').textContent = `第 ${currentPage} / ${pages} 页 (共 ${total} 条)`;

  if (pageData.length === 0) {
    document.getElementById('rebalanceTable').innerHTML = '<div class="loading">暂无调仓记录</div>';
    return;
  }

  let html = `<table>
    <thead><tr>
      <th>日期</th><th>股票</th><th>仓位变化</th><th>情绪</th><th>股价</th><th>组合价值</th><th>理由</th>
    </tr></thead><tbody>`;
  for (const r of pageData) {
    const change = (r.new_weight - r.old_weight);
    const changeStr = change > 0 ? '+' + (change*100).toFixed(1) + '%' : (change*100).toFixed(1) + '%';
    const changeCls = change > 0 ? 'positive' : (change < 0 ? 'negative' : '');
    const sentimentCls = r.sentiment_score > 0.15 ? 'bullish' : (r.sentiment_score < -0.15 ? 'bearish' : 'neutral');
    const sentimentStr = (r.sentiment_score >= 0 ? '+' : '') + r.sentiment_score.toFixed(3);
    html += `<tr>
      <td>${r.date}</td>
      <td><strong>${r.symbol}</strong></td>
      <td style="color:${change > 0 ? '#4ade80' : (change < 0 ? '#f87171' : '#94a3b8')}">
        ${(r.old_weight*100).toFixed(1)}% → ${(r.new_weight*100).toFixed(1)}% (${changeStr})
      </td>
      <td><span class="tag ${sentimentCls}">${sentimentStr}</span></td>
      <td>${r.price ? '$' + r.price.toFixed(2) : '-'}</td>
      <td>$${fmt(r.portfolio_value)}</td>
      <td style="color:#8899aa;font-size:12px">${r.reason || ''}</td>
    </tr>`;
  }
  html += '</tbody></table>';
  document.getElementById('rebalanceTable').innerHTML = html;
}

function changePage(delta) {
  const total = allRebalances.length;
  const pages = Math.max(1, Math.ceil(total / pageSize));
  currentPage = Math.max(1, Math.min(pages, currentPage + delta));
  renderTable();
}

document.getElementById('datePreset').addEventListener('change', refreshData);
refreshData();
</script>
</body>
</html>"""

# ── API Routes ──────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index():
    return HTML_TEMPLATE

@app.get("/api/summary")
def api_summary(from_date: str = Query("", description="开始日期 YYYY-MM-DD")):
    with session_scope() as s:
        q = s.query(RebalanceEvent)
        if from_date:
            q = q.filter(RebalanceEvent.date >= from_date)
        events = q.order_by(RebalanceEvent.date.asc()).all()

        if not events:
            return {
                "initial_cash": 100000.0,
                "final_value": 100000.0,
                "cumulative_return": 0.0,
                "total_events": 0,
                "total_symbols": 0,
                "longest_holding_days": None,
                "date_range": "无数据",
            }

        symbols = set(e.symbol for e in events)
        initial = events[0].portfolio_value if len(events) > 0 else 100000.0
        final = events[-1].portfolio_value if len(events) > 0 else 100000.0

        # longest holding: track consecutive symbol occurrences
        sym_last_seen = {}
        longest = 0
        for e in events:
            if e.symbol in sym_last_seen:
                days = (date.fromisoformat(e.date) - sym_last_seen[e.symbol]["date"]).days
                if days > longest:
                    longest = days
            sym_last_seen[e.symbol] = {"date": date.fromisoformat(e.date)}

        return {
            "initial_cash": round(initial, 2),
            "final_value": round(final, 2),
            "cumulative_return": round((final - initial) / initial, 4),
            "total_events": len(events),
            "total_symbols": len(symbols),
            "longest_holding_days": longest,
            "date_range": f"{events[0].date} ~ {events[-1].date}",
        }

@app.get("/api/equity_curve")
def api_equity_curve(from_date: str = Query("", description="开始日期 YYYY-MM-DD")):
    with session_scope() as s:
        q = s.query(RebalanceEvent.date, func.avg(RebalanceEvent.portfolio_value))
        if from_date:
            q = q.filter(RebalanceEvent.date >= from_date)
        rows = q.group_by(RebalanceEvent.date).order_by(RebalanceEvent.date.asc()).all()

    return {
        "dates": [r[0] for r in rows],
        "values": [round(r[1], 2) for r in rows],
    }

@app.get("/api/holdings")
def api_holdings(from_date: str = Query("", description="开始日期 YYYY-MM-DD")):
    """获取最新日期的持仓分布"""
    with session_scope() as s:
        q = s.query(RebalanceEvent)
        if from_date:
            q = q.filter(RebalanceEvent.date >= from_date)
        last_date = q.order_by(RebalanceEvent.date.desc()).first()
        if not last_date:
            return {"holdings": {}, "date": None}

        holdings = (
            s.query(RebalanceEvent)
            .filter(RebalanceEvent.date == last_date.date)
            .all()
        )
        weights = {h.symbol: h.new_weight for h in holdings if h.new_weight > 0}
        # Sort by weight descending
        weights = dict(sorted(weights.items(), key=lambda x: -x[1]))

        return {"holdings": weights, "date": last_date.date}

@app.get("/api/top_symbols")
def api_top_symbols(from_date: str = Query("", description="开始日期 YYYY-MM-DD")):
    """最常交易股票 TOP 20"""
    with session_scope() as s:
        q = s.query(RebalanceEvent.symbol, func.count(RebalanceEvent.id).label("cnt"))
        if from_date:
            q = q.filter(RebalanceEvent.date >= from_date)
        rows = q.group_by(RebalanceEvent.symbol).order_by(func.count(RebalanceEvent.id).desc()).limit(20).all()

    return [{"symbol": r[0], "count": r[1]} for r in rows]

@app.get("/api/rebalances")
def api_rebalances(from_date: str = Query("", description="开始日期 YYYY-MM-DD")):
    """调仓记录列表"""
    with session_scope() as s:
        q = s.query(RebalanceEvent).order_by(RebalanceEvent.date.desc(), RebalanceEvent.id.desc())
        if from_date:
            q = q.filter(RebalanceEvent.date >= from_date)
        rows = q.limit(500).all()
        rebalances = [
            {
                "date": r.date,
                "symbol": r.symbol,
                "old_weight": r.old_weight,
                "new_weight": r.new_weight,
                "sentiment_score": r.sentiment_score,
                "price": r.price,
                "portfolio_value": r.portfolio_value,
                "reason": r.reason,
            }
            for r in rows
        ]

    return {"rebalances": rebalances}

@app.get("/api/metrics")
def api_metrics(from_date: str = Query("", description="开始日期 YYYY-MM-DD")):
    """额外统计指标"""
    with session_scope() as s:
        q = s.query(RebalanceEvent)
        if from_date:
            q = q.filter(RebalanceEvent.date >= from_date)
        events = q.all()

        if not events:
            return {"win_rate": 0, "avg_hold_days": 0, "max_drawdown": 0, "sharpe_approx": 0}

        # Win rate: count positive weight changes vs negative
        wins = sum(1 for e in events if e.new_weight > e.old_weight)
        win_rate = wins / len(events) if events else 0

        # Daily returns for approximation
        daily_values = defaultdict(list)
        for e in events:
            daily_values[e.date].append(e.portfolio_value)
        daily_avg = {d: sum(vs)/len(vs) for d, vs in daily_values.items()}
        sorted_dates = sorted(daily_avg.keys())
        returns = []
        for i in range(1, len(sorted_dates)):
            prev = daily_avg[sorted_dates[i-1]]
            curr = daily_avg[sorted_dates[i]]
            if prev > 0:
                returns.append((curr - prev) / prev)

        max_dd = 0
        peak = 0
        for d in sorted_dates:
            v = daily_avg[d]
            if v > peak:
                peak = v
            dd = (peak - v) / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd

        sharpe = 0
        if len(returns) > 1:
            avg_r = sum(returns) / len(returns)
            std_r = (sum((r - avg_r) ** 2 for r in returns) / len(returns)) ** 0.5
            sharpe = (avg_r / std_r) * (252 ** 0.5) if std_r > 0 else 0

        return {
            "win_rate": round(win_rate, 4),
            "max_drawdown": round(max_dd, 4),
            "sharpe_approx": round(sharpe, 4),
            "total_trading_days": len(sorted_dates),
        }


# ── Entry Point ─────────────────────────────────────

if __name__ == "__main__":
    import os
    port = int(os.environ.get("WEBUI_PORT", "8824"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")