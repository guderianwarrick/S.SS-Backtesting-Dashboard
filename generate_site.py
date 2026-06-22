"""从数据库生成静态 dashboard HTML（Embedded 模式，所有数据嵌入页面）"""
import json, sys
from pathlib import Path
from datetime import date, timedelta, datetime
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent))
from storage.models import init_db, session_scope, RebalanceEvent, StockMention, Tweet
from sqlalchemy import func, desc

init_db()

def gen():
    with session_scope() as s:
        # ── 统计数据 ──
        last_date = s.query(func.max(RebalanceEvent.date)).scalar()
        if not last_date:
            print("No data")
            return
        
        today_str = date.today().isoformat()
        
        events = s.query(RebalanceEvent).filter(RebalanceEvent.date == last_date).order_by(RebalanceEvent.new_weight.desc()).all()
        
        # 持有分布
        hData = {h.symbol: round(h.new_weight, 4) for h in events if h.new_weight > 0}
        
        # 净值曲线
        equity_rows = s.query(RebalanceEvent.date, func.avg(RebalanceEvent.portfolio_value)).group_by(RebalanceEvent.date).order_by(RebalanceEvent.date.asc()).all()
        eq_dates = [r[0] for r in equity_rows]
        eq_values = [round(r[1], 2) for r in equity_rows]
        
        # 基本统计
        initial = events[0].portfolio_value if events else 100000
        final = events[-1].portfolio_value if events else 100000
        cr = (final - initial) / initial if initial > 0 else 0
        
        # 最近 7 天提及（从 StockMention 取）
        seven_days_ago = date.today() - timedelta(days=7)
        recent_raw = s.query(
            StockMention.symbol,
            func.count(StockMention.id).label("cnt"),
            func.sum(StockMention.sentiment_score).label("total_score")
        ).join(Tweet, StockMention.tweet_id == Tweet.id
        ).filter(Tweet.created_at >= seven_days_ago.isoformat()
        ).group_by(StockMention.symbol
        ).order_by(func.count(StockMention.id).desc()).limit(50).all()
        
        # 获取每个 symbol 最近一次提及的时间 + 对应情绪分
        # 子查询：每个 symbol 最新的 tweet_id
        latest_mention = {}
        for sym, _, _ in recent_raw:
            row = s.query(
                StockMention.symbol,
                StockMention.sentiment_score,
                Tweet.created_at
            ).join(Tweet, StockMention.tweet_id == Tweet.id
            ).filter(
                StockMention.symbol == sym,
                Tweet.created_at >= seven_days_ago.isoformat()
            ).order_by(desc(Tweet.created_at)).first()
            if row:
                latest_mention[sym] = {
                    "time": row.created_at.isoformat() if hasattr(row.created_at, 'isoformat') else str(row.created_at),
                    "score": round(row.sentiment_score, 3)
                }
        
        # 从 rebalance 获取真实权重和价格
        latest_holdings = {h.symbol: h for h in s.query(RebalanceEvent).filter(RebalanceEvent.date == last_date).all()}
        
        mentions = []
        for sym, cnt, score in recent_raw:
            h = latest_holdings.get(sym)
            weight = h.new_weight if h else 0
            price = h.price if h else None
            lm = latest_mention.get(sym, {})
            mentions.append({
                "symbol": sym,
                "count_7d": cnt,
                "total_score": round(score, 3),
                "weight": round(weight * 100, 1) if weight else 0,
                "price": price,
                "last_time": lm.get("time", ""),
                "last_score": lm.get("score", 0)
            })
        
        # 常交易股票
        top_syms = s.query(RebalanceEvent.symbol, func.count(RebalanceEvent.id).label("cnt")).group_by(RebalanceEvent.symbol).order_by(func.count(RebalanceEvent.id).desc()).limit(20).all()
        
    # ── 生成 HTML ──
    h_items = json.dumps(hData)
    eq_dates_json = json.dumps(eq_dates)
    eq_values_json = json.dumps(eq_values)
    top_labels = json.dumps([r[0] for r in top_syms])
    top_values = json.dumps([r[1] for r in top_syms])
    mentions_json = json.dumps(mentions)
    updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>情绪回测看板 - S.SS</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<style>
:root {{ --bg:#f0f4f8; --card:#fff; --card-hover:#f8fafc; --text:#1e293b; --text2:#64748b; --h:#0f172a; --border:#e2e8f0; --green:#16a34a; --red:#dc2626; --grid:#e2e8f0; --blue:#2563eb; }}
[data-theme="dark"] {{ --bg:#0f1923; --card:#1a2d3a; --card-hover:#1e3342; --text:#e0e6ed; --text2:#8899aa; --h:#fff; --border:#253a48; --green:#4ade80; --red:#f87171; --grid:#253a48; }}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:var(--bg);color:var(--text);padding:20px}}
.container{{max-width:1400px;margin:0 auto}}
h1{{font-size:24px;color:var(--h);margin-bottom:6px}}
.subtitle{{color:var(--text2);font-size:14px;margin-bottom:4px}}
.update-time{{color:var(--text2);font-size:12px;margin-bottom:24px}}
.header-bar{{display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:12px;margin-bottom:24px}}
.stats-row{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-bottom:24px}}
.stat-card{{background:var(--card);border-radius:10px;padding:16px 20px;border:1px solid var(--border)}}
.stat-card .l{{font-size:12px;color:var(--text2);text-transform:uppercase;letter-spacing:.5px}}
.stat-card .v{{font-size:26px;font-weight:700;color:var(--h);margin-top:4px}}
.stat-card .v.g{{color:var(--green)}} .stat-card .v.r{{color:var(--red)}}
.chart-box{{background:var(--card);border-radius:10px;padding:20px;border:1px solid var(--border);margin-bottom:24px}}
.chart-box h3{{font-size:15px;color:var(--text2);margin-bottom:12px}}
.chart-wrap{{position:relative;width:100%;height:400px}}
.grid-2{{display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-bottom:24px}}
@media(max-width:900px){{.grid-2{{grid-template-columns:1fr}}}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{text-align:left;padding:8px 10px;color:var(--text2);border-bottom:1px solid var(--border);font-weight:600;font-size:11px;text-transform:uppercase;white-space:nowrap}}
td{{padding:8px 10px;border-bottom:1px solid var(--border);white-space:nowrap}}
tr:hover td{{background:var(--card-hover)}}
.theme-btn{{background:var(--card);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:6px 10px;cursor:pointer;font-size:15px;line-height:1}}
.tag{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600}}
.tag.bullish{{background:#dcfce7;color:var(--green)}} .tag.bearish{{background:#fef2f2;color:var(--red)}} .tag.neutral{{background:#f1f5f9;color:var(--text2)}}
[data-theme="dark"] .tag.bullish{{background:#14522b;color:var(--green)}}
[data-theme="dark"] .tag.bearish{{background:#521414;color:var(--red)}}
[data-theme="dark"] .tag.neutral{{background:#2a3a4a;color:#94a3b8}}
.note{{color:var(--text2);font-size:12px;margin-top:8px}}
</style></head>
<body><div class="container">
<div class="header-bar"><div><h1>📊 情绪回测看板</h1>
<div class="subtitle">根据推特博主 <a href="https://x.com/aleabitoreddit" target="_blank" style="color:var(--blue)">@aleabitoreddit</a> 的推文生成的虚拟投资组合</div>
<div class="update-time">更新于 {updated_at}</div></div>
<div style="display:flex;gap:8px;align-items:center">
<button class="theme-btn" onclick="toggleTheme()" id="themeToggle">☀️</button>
</div></div>

<div class="stats-row">
<div class="stat-card"><div class="l">初始资金</div><div class="v">$ {initial:,.0f}</div></div>
<div class="stat-card"><div class="l">最终价值</div><div class="v">$ {final:,.0f}</div></div>
<div class="stat-card"><div class="l">累计收益</div><div class="v {'g' if cr>=0 else 'r'}">{cr*100:.2f}%</div></div>
<div class="stat-card"><div class="l">调仓次数</div><div class="v">{len(equity_rows):,}</div></div>
<div class="stat-card"><div class="l">涉及股票</div><div class="v">{len(hData)}</div></div>
</div>

<div class="chart-box"><h3>📈 组合净值曲线</h3><div class="chart-wrap"><canvas id="eqChart"></canvas></div></div>
<div class="grid-2">
<div class="chart-box"><h3>🧩 当前持仓 Top 20</h3><div class="chart-wrap" style="height:300px"><canvas id="hChart"></canvas></div></div>
<div class="chart-box"><h3>🏆 最常交易 Top 20</h3><div class="chart-wrap" style="height:300px"><canvas id="topChart"></canvas></div></div>
</div>

<div class="chart-box"><h3>📌 近 7 天提及股票</h3>
<div class="note">提及次数为近 7 天累计，情绪分为同期所有提及的情绪总分（正=看多，负=看空）</div>
<div style="overflow-x:auto;margin-top:8px"><table><thead><tr>
<th>股票</th><th>7天提及</th><th>情绪总分</th><th>上次情绪</th><th>上次提及</th><th>组合权重</th><th>最新价</th>
</tr></thead><tbody id="mb"></tbody></table></div>
</div></div>

<script>
const TH = {{'light':{{g:'#e2e8f0',t:'#64748b',l:'#64748b'}},'dark':{{g:'#253a48',t:'#8899aa',l:'#b0c4d8'}}}}

function toggleTheme() {{
  const d = document.documentElement;
  const cur = d.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
  d.setAttribute('data-theme', cur);
  localStorage.setItem('theme', cur);
  document.getElementById('themeToggle').textContent = cur === 'dark' ? '🌙' : '☀️';
  [eqChart, hChart, topChart].filter(Boolean).forEach(c => {{
    if (!c) return;
    const t = TH[cur];
    Object.values(c.options.scales||{{}}).forEach(s => {{ s.ticks.color = t.t; s.grid.color = t.g; }});
    if (c.options.plugins?.legend?.labels) c.options.plugins.legend.labels.color = t.l;
    c.update();
  }});
}}
const curTheme = localStorage.getItem('theme') || 'light';
document.documentElement.setAttribute('data-theme', curTheme);
document.getElementById('themeToggle').textContent = curTheme === 'dark' ? '🌙' : '☀️';

const MENTIONS = {mentions_json};

let html = '';
for (const r of MENTIONS) {{
  const tag = r.total_score > 0.5 ? 'bullish' : (r.total_score < -0.3 ? 'bearish' : 'neutral');
  const lastTag = r.last_score > 0.5 ? 'bullish' : (r.last_score < -0.3 ? 'bearish' : 'neutral');
  const lastTime = r.last_time ? r.last_time.slice(0,19).replace('T',' ') : '-';
  html += '<tr><td><strong>'+r.symbol+'</strong></td>'+
    '<td>'+r.count_7d+'次</td>'+
    '<td><span class="tag '+tag+'">'+(r.total_score>=0?'+':'')+r.total_score.toFixed(3)+'</span></td>'+
    '<td><span class="tag '+lastTag+'">'+(r.last_score>=0?'+':'')+r.last_score.toFixed(3)+'</span></td>'+
    '<td style="font-size:12px">'+lastTime+'</td>'+
    '<td>'+r.weight+'%</td>'+
    '<td>'+(r.price?'$'+r.price.toFixed(2):'-')+'</td></tr>';
}}
document.getElementById('mb').innerHTML = html;

const tt = TH[curTheme];
function mkScale() {{ return {{x:{{ticks:{{color:tt.t}},grid:{{color:tt.g}}}},y:{{ticks:{{color:tt.t}},grid:{{color:tt.g}}}}}} }}

const eqChart = new Chart(document.getElementById('eqChart'), {{
  type:'line',data:{{labels:{eq_dates_json},datasets:[{{label:'净值',data:{eq_values_json},borderColor:'#2563eb',backgroundColor:'rgba(37,99,235,0.08)',fill:true,tension:0.2,pointRadius:0,borderWidth:2}}]}},
  options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{display:false}},tooltip:{{callbacks:{{label:ctx=>'$'+ctx.parsed.y.toLocaleString()}}}}}},scales:mkScale()}}
}});

const hData = {h_items};
const hChart = new Chart(document.getElementById('hChart'), {{
  type:'doughnut',
  data:{{labels:Object.keys(hData),datasets:[{{data:Object.values(hData).map(v=>+(v*100).toFixed(2)),backgroundColor:['#2563eb','#4ade80','#f59e0b','#f87171','#a78bfa','#34d399','#fb923c','#60a5fa','#f472b6','#2dd4bf'],borderWidth:0}}]}},
  options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{position:'right',labels:{{color:tt.l,font:{{size:11}}}}}},tooltip:{{callbacks:{{label:ctx=>ctx.label+': '+ctx.parsed.toFixed(2)+'%'}}}}}}}}
}});

const topChart = new Chart(document.getElementById('topChart'), {{
  type:'bar',
  data:{{labels:{top_labels},datasets:[{{data:{top_values},backgroundColor:'#2563eb',borderRadius:6,borderSkipped:false}}]}},
  options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{display:false}},tooltip:{{callbacks:{{label:ctx=>ctx.parsed.y+' 次调仓'}}}}}},scales:{{x:{{ticks:{{color:tt.t,font:{{size:10}}}},grid:{{display:false}}}},y:{{ticks:{{color:tt.t,font:{{size:11}}}},grid:{{color:tt.g}}}}}}}}
}});
</script></body></html>'''
    
    Path("index.html").write_text(html, encoding="utf-8")
    print(f"✅ index.html generated: {len(html)} bytes, {len(mentions)} mentions")

if __name__ == "__main__":
    gen()