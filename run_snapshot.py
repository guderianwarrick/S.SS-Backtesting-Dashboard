"""静态复盘快照 — 一键生成"""
import sys
from datetime import date, datetime

from portfolio.snapshot import SnapshotEngine
import config

username = sys.argv[1] if len(sys.argv) > 1 else "aleaborteddit"

print(f"[Snapshot] @{username} 静态复盘快照生成中...")
engine = SnapshotEngine(author_id=username, initial_cash=config.PORTFOLIO_INITIAL_CASH)
snap = engine.build()

if not snap.get("positions"):
    print("[Snapshot] 无有效持仓数据，退出")
    sys.exit(0)

# ═══ 终端预览 ═══
print(f"\n{'='*70}")
print(f"  静态复盘快照 — @{snap['author_id']}")
print(f"  截至: {snap['as_of']}  |  推文: {snap['total_tweets']} 条")
print(f"  情绪记录: {snap['total_mentions']}  |  持仓: {snap['stock_count']} 只")
print(f"  总分配: ${snap['total_allocation']:,.0f}  |  现金余额: ${snap['cash_reserve']:,.0f}")
print(f"  收益 — 1Y: {snap['returns']['1y']:+.2%}  |  3M: {snap['returns']['3m']:+.2%}  |  YTD: {snap['returns']['ytd']:+.2%}")
print(f"{'='*70}")

print(f"\n  {'股票':<7} {'名称':<12} {'原始分':>8} {'衰减分':>8} {'提及':>4} {'权重':>7} {'分配':>12} {'现价':>8}")
print(f"  {'-'*66}")
for p in snap["positions"]:
    price_s = f"${p['current_price']:.2f}" if p["current_price"] else "N/A"
    alloc_s = f"${p['allocation']:,.0f}" if p["allocation"] else "$0"
    print(f"  {p['symbol']:<7} {p['name'][:12]:<12} "
          f"{p['raw_score']:>8.3f} {p['decayed_score']:>8.3f} "
          f"{p['mentions']:>4} {p['weight']:>6.2%} {alloc_s:>12} {price_s:>8}")

# ═══ Markdown 报告 ═══
lines = [
    f"# @{snap['author_id']} 静态复盘快照",
    "",
    f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}  ",
    f"**快照截止**: {snap['as_of']}  ",
    f"**情绪引擎**: StephanAkkerman/FinTwitBERT-sentiment (GPU)  ",
    f"**初始资金**: ${snap['parameters']['initial_cash']:,.0f}  ",
    "",
    "---",
    "",
    "## 算法",
    "",
    "### 1. 时间衰减",
    "",
    "每条看多推文的分数随时间衰减（对数化）：",
    "",
    "$$",
    "decay_i = \\frac{1}{\\ln(1 + \\alpha \\cdot T_{tweet} + \\beta \\cdot T_{days})}",
    "$$",
    "",
    "| 符号 | 含义 | 当前值 |",
    "|---:|:---|:---|",
    f"| α | 推文序数权重 | {snap['parameters']['decay_alpha']} |",
    f"| β | 日历天数权重 | {snap['parameters']['decay_beta']} |",
    "| T_tweet | 此推文之后 Serenity 又发了多少条推文 | — |",
    "| T_days | 推文发出到现在的天数 | — |",
    "",
    "### 2. sigmoid 映射",
    "",
    "衰减后的股票情绪得分，通过 sigmoid 曲线映射为绝对金额：",
    "",
    "$$",
    "alloc_k = C_{base} \\cdot \\sigma\\left(\\frac{\\sum score_i \\cdot decay_i}{scale}\\right)",
    "$$",
    "",
    "| 符号 | 含义 | 当前值 |",
    "|---:|:---|:---|",
    f"| C_base | 单股基准分配额 | ${snap['parameters']['C_base']:,.0f} |",
    f"| scale | 分数缩放系数 | {snap['parameters']['alloc_scale']} |",
    f"| σ(x) | sigmoid 函数 | 1/(1+e⁻ˣ) |",
    "",
    "### 3. 固定权重收益",
    "",
    "快照权重确定后不变，回测各区间收益：",
    "",
    "$$",
    "R_{period} = \\frac{\\sum w_k \\cdot \\frac{P_{k,end}}{P_{k,start}} - 1}{1}",
    "$$",
    "",
    "---",
    "",
    "## 收益概览",
    "",
    "| 周期 | 收益率 |",
    "|---:|:---|",
    f"| 近一年 (1Y) | {snap['returns']['1y']:+.2%} |",
    f"| 近三月 (3M) | {snap['returns']['3m']:+.2%} |",
    f"| 年初至今 (YTD) | {snap['returns']['ytd']:+.2%} |",
    "",
    "---",
    "",
    "## 持仓明细",
    "",
    "| # | 股票 | 名称 | 原始分 | 衰减分 | 提及 | 权重 | 分配金额 | 现价 |",
    "|---:|:---|:---|---:|---:|---:|---:|---:|---:|",
]

for i, p in enumerate(snap["positions"], 1):
    price_s = f"${p['current_price']:.2f}" if p["current_price"] else "N/A"
    alloc_s = f"${p['allocation']:,.0f}" if p["allocation"] else "$0"
    lines.append(
        f"| {i} | {p['symbol']} | {p['name']} | "
        f"{p['raw_score']:.3f} | {p['decayed_score']:.3f} | "
        f"{p['mentions']} | {p['weight']:.2%} | {alloc_s} | {price_s} |"
    )

lines += [
    "",
    "---",
    "",
    "## 数据来源",
    "",
    f"- 推文来源: X @{snap['author_id']}",
    f"- 情绪引擎: StephanAkkerman/FinTwitBERT-sentiment",
    f"- 价格数据: yfinance 本地缓存 (cache_only)",
    f"- 总推文数: {snap['total_tweets']}",
    f"- 总情绪记录: {snap['total_mentions']}",
    f"- 持仓股票: {snap['stock_count']} 只",
    f"- 现金余额: ${snap['cash_reserve']:,.0f}",
    "",
    "> 静态复盘：一次性汇总所有推文，生成一份\"如果今天下单\"的持仓快照。",
    "> 不迭代、不调仓，独立于动态追踪引擎。",
]

out = config.DATA_DIR / f"snapshot_{username}_{date.today()}.md"
out.write_text("\n".join(lines), encoding="utf-8")
print(f"\n[Snapshot] 报告已保存: {out}")
