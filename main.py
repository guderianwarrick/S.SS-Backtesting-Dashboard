"""X 博主股票情绪分析 + 虚拟持仓回测 — 主入口"""
import sys
import io
from datetime import datetime, timezone, timedelta, date

# 修复 Windows 控制台 GBK 编码问题（保留原始引用避免冲突）
if sys.platform == "win32":
    try:
        _old_stdout = sys.stdout
        _old_stderr = sys.stderr
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
        # stderr 不二次包装，避免被外部调用方覆盖后 I/O closed 错误
    except (ValueError, OSError):
        pass
from collections import defaultdict
from loguru import logger

import config
from scraper.playwright_scraper import PlaywrightScraper
from parser.stock_ner import StockExtractor
from ai.sentiment_analyzer import SentimentAnalyzer
from storage.models import (
    init_db, get_session,
    Tweet as TweetModel,
    StockMention as StockMentionModel,
    RebalanceEvent as RebalanceModel,
)
from portfolio.engine import PortfolioEngine


logger.remove()
logger.add(sys.stderr, level="INFO", format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}")


def save_tweets_to_db(tweets: list[dict], username: str) -> int:
    """将原始推文存入 SQLite，返回新增数量"""
    session = get_session()
    saved = 0
    for t in tweets:
        tid = t.get("id") or str(hash(t["text"]))
        if session.query(TweetModel).filter_by(id=tid).first():
            continue

        # 解析时间
        created_str = t.get("created_at", "")
        if created_str:
            try:
                created = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                created = datetime.now(timezone.utc)
        else:
            created = datetime.now(timezone.utc)

        session.add(TweetModel(
            id=tid,
            text=t["text"],
            created_at=created,
            author_id=username,  # 用用户名代替 ID
            like_count=0,
            retweet_count=0,
            reply_count=0,
            quote_count=0,
            lang="",
        ))
        saved += 1
    session.commit()
    session.close()
    logger.info("Saved {} new tweets to DB", saved)
    return saved


def run_sentiment_analysis(username: str, tweets: list[dict], days: int) -> dict:
    """逐条分析推文情绪并存入 DB，返回按股票聚合的统计"""
    extractor = StockExtractor()
    analyzer = SentimentAnalyzer()

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    recent_tweets = []
    for t in tweets:
        created_str = t.get("created_at", "")
        if created_str:
            try:
                dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                if dt >= cutoff:
                    recent_tweets.append(t)
            except (ValueError, TypeError):
                recent_tweets.append(t)  # 无法解析时间的也纳入
        else:
            recent_tweets.append(t)  # 无时间的也纳入
    logger.info("Analyzing {} tweets from last {} days", len(recent_tweets), days)

    stock_stats = defaultdict(lambda: {
        "mentions": 0,
        "scores": [],
        "labels": defaultdict(int),
        "reasons": [],
        "latest_text": "",
        "latest_date": None,
    })

    session = get_session()
    new_count = 0
    skip_count = 0
    for tweet in recent_tweets:
        text = tweet["text"]
        tid = tweet.get("id") or str(hash(text))

        # 解析推文原始时间，确保 analyzed_at 反映真实发布日期
        tweet_dt: datetime | None = None
        created_str = tweet.get("created_at", "")
        if created_str:
            try:
                tweet_dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                tweet_dt = None

        mentions = extractor.extract(text)

        # 检查是否已有分析记录（去重）
        existing_mentions = (
            session.query(StockMentionModel)
            .filter(StockMentionModel.tweet_id == tid)
            .all()
        )
        existing_symbols = {m.symbol for m in existing_mentions}

        for m in mentions:
            if m.symbol in existing_symbols:
                skip_count += 1
                continue

            result = analyzer.analyze(text, m.symbol, m.name)
            session.add(StockMentionModel(
                tweet_id=tid,
                symbol=m.symbol,
                name=m.name,
                sentiment_score=result.score,
                sentiment_label=result.label,
                llm_reason=result.reason,
                analyzed_at=(tweet_dt or datetime.now(timezone.utc)),
            ))
            new_count += 1

            stats = stock_stats[m.symbol]
            stats["mentions"] += 1
            stats["scores"].append(result.score)
            stats["labels"][result.label] += 1
            stats["reasons"].append(result.reason)
            stats["latest_text"] = text
            stats["latest_date"] = tweet["created_at"]

        # 已有分析记录的股票，从 DB 读取并汇入 stats
        for em in existing_mentions:
            s = stock_stats[em.symbol]
            s["mentions"] += 1
            s["scores"].append(em.sentiment_score)
            s["labels"][em.sentiment_label] += 1
            s["reasons"].append(em.llm_reason or "")
            s["latest_text"] = text
            s["latest_date"] = tweet["created_at"]

    logger.info("Sentiment: {} new, {} skipped (already analyzed)", new_count, skip_count)

    session.commit()
    session.close()
    return dict(stock_stats)


def print_sentiment_report(username: str, stock_stats: dict, days: int):
    """打印情绪分析报告"""
    print("\n" + "=" * 60)
    print(f"  @{username} 最近 {days} 天股票情绪分析报告")
    print(f"  生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    if not stock_stats:
        print("\n  未检测到任何股票提及。\n")
        return

    sorted_stocks = sorted(stock_stats.items(), key=lambda x: x[1]["mentions"], reverse=True)

    for symbol, stats in sorted_stocks:
        avg_score = sum(stats["scores"]) / len(stats["scores"])
        label_counts = dict(stats["labels"])

        if avg_score > 0.3:
            emoji, trend = "🟢", "看多"
        elif avg_score < -0.3:
            emoji, trend = "🔴", "看空"
        else:
            emoji, trend = "⚪", "中性"

        print(f"\n  {emoji} {symbol}")
        print(f"     提及次数: {stats['mentions']} 次")
        print(f"     平均情绪分: {avg_score:+.2f} ({trend})")
        print(f"     情绪分布: 正面 {label_counts.get('positive',0)} / "
              f"中性 {label_counts.get('neutral',0)} / "
              f"负面 {label_counts.get('negative',0)}")
        print(f"     最近提及: {stats['latest_date'][:10]}")
        print(f"     原文片段: {stats['latest_text'][:60]}...")

    print("=" * 60)


def print_portfolio_report(username: str, result: dict):
    """打印虚拟持仓回测报告"""
    print("\n" + "=" * 60)
    print(f"  @{username} 虚拟持仓回测报告")
    print(f"  起止时间: {config.PORTFOLIO_START_DATE} ~ {date.today()}")
    print("=" * 60)

    if note := result.get("note"):
        print(f"\n  {note}")

    # 最终持仓
    positions = result.get("final_positions", {})
    if positions:
        print(f"\n  📊 最新虚拟持仓 (初始资金 ¥{config.PORTFOLIO_INITIAL_CASH:,.0f}):")
        sorted_pos = sorted(positions.items(), key=lambda x: x[1], reverse=True)
        for sym, weight in sorted_pos:
            bar = "█" * max(1, int(weight * 40))
            print(f"     {sym:<8} {weight:>6.1%}  {bar}")
    else:
        print("\n  💤 当前空仓（无看多信号）")

    # 累计收益
    cum_ret = result.get("cumulative_return", 0)
    final_val = result.get("final_value", config.PORTFOLIO_INITIAL_CASH)
    pnl = final_val - config.PORTFOLIO_INITIAL_CASH

    print(f"\n  📈 收益统计:")
    print(f"     初始资金: ¥{config.PORTFOLIO_INITIAL_CASH:,.0f}")
    print(f"     最终价值: ¥{final_val:,.0f}")
    print(f"     累计盈亏: ¥{pnl:+,.0f}")
    print(f"     累计收益率: {cum_ret:+.2%}")

    # 调仓事件
    rebalances = result.get("rebalances", [])
    if rebalances:
        print(f"\n  🔄 调仓历史 (共 {result.get('total_events', 0)} 笔):")
        # 取最近 20 条
        for r in rebalances[-20:]:
            action = "买入" if r["new_weight"] > r["old_weight"] else (
                "卖出" if r["new_weight"] < r["old_weight"] else "调整"
            )
            price_str = f"¥{r['price']}" if r.get("price") else "N/A"
            print(f"     {r['date']} | {r['symbol']:<6} | {action} | "
                  f"权重 {r['old_weight']:.1%}→{r['new_weight']:.1%} | "
                  f"情绪分 {r['sentiment_score']:+.2f} | 股价 {price_str}")

        if len(rebalances) > 20:
            print(f"     ... 共 {len(rebalances)} 条，仅显示最近 20 条")

    print("=" * 60)


def save_portfolio_summary(username: str, result: dict):
    """将持仓报告保存为 Markdown 文件"""
    from pathlib import Path

    out_path = config.DATA_DIR / f"portfolio_{username}_{date.today()}.md"
    lines = [
        f"# @{username} 虚拟持仓回测报告",
        "",
        f"- 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"- 回测区间: {config.PORTFOLIO_START_DATE} ~ {date.today()}",
        f"- 初始资金: ¥{config.PORTFOLIO_INITIAL_CASH:,.0f}",
        "",
    ]

    positions = result.get("final_positions", {})
    if positions:
        lines.append("## 最新持仓")
        lines.append("| 股票 | 权重 |")
        lines.append("|------|------|")
        for sym, weight in sorted(positions.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"| {sym} | {weight:.1%} |")

    cum_ret = result.get("cumulative_return", 0)
    final_val = result.get("final_value", config.PORTFOLIO_INITIAL_CASH)
    pnl = final_val - config.PORTFOLIO_INITIAL_CASH

    lines += [
        "",
        "## 收益统计",
        f"| 指标 | 数值 |",
        f"|------|------|",
        f"| 初始资金 | ¥{config.PORTFOLIO_INITIAL_CASH:,.0f} |",
        f"| 最终价值 | ¥{final_val:,.0f} |",
        f"| 累计盈亏 | ¥{pnl:+,.0f} |",
        f"| 累计收益率 | {cum_ret:+.2%} |",
        "",
    ]

    rebalances = result.get("rebalances", [])
    if rebalances:
        lines.append("## 调仓历史")
        lines.append(f"共 {len(rebalances)} 笔调仓记录")
        lines.append("| 日期 | 股票 | 操作 | 旧权重 | 新权重 | 情绪分 | 股价 |")
        lines.append("|------|------|------|--------|--------|--------|------|")
        for r in rebalances:
            action = "买入" if r["new_weight"] > r["old_weight"] else "卖出" if r["new_weight"] < r["old_weight"] else "调整"
            lines.append(
                f"| {r['date']} | {r['symbol']} | {action} | "
                f"{r['old_weight']:.1%} | {r['new_weight']:.1%} | "
                f"{r['sentiment_score']:+.2f} | "
                f"{'¥' + str(r['price']) if r.get('price') else 'N/A'} |"
            )

    out_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Portfolio summary saved: {}", out_path)


# ── 主入口 ────────────────────────────────────────
def analyze_recent_tweets(username: str, max_tweets: int = 200, days: int = 3):
    """完整工作流：拉取推文 → 情绪分析 → 投资组合回测"""
    init_db()

    # Phase 1: 采集
    with PlaywrightScraper() as scraper:
        tweets = scraper.load_tweets()
        if not tweets:
            logger.info("No tweets fetched for @{}.", username)
            return

        save_tweets_to_db(tweets, username)

    stock_stats = run_sentiment_analysis(username, tweets, days)
    print_sentiment_report(username, stock_stats, days)

    # Phase 2: 投资组合回测（从年初开始）
    logger.info("Running portfolio backtest from {}", config.PORTFOLIO_START_DATE)
    engine = PortfolioEngine(
        username=username,
        author_id=username,
        initial_cash=config.PORTFOLIO_INITIAL_CASH,
    )
    result = engine.backtest(
        from_date=date.fromisoformat(config.PORTFOLIO_START_DATE),
        to_date=date.today(),
    )
    print_portfolio_report(username, result)
    save_portfolio_summary(username, result)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python main.py <username> [max_tweets] [days]")
        print("Example: python main.py elonmusk 100 3")
        sys.exit(1)

    username = sys.argv[1]
    max_tweets = int(sys.argv[2]) if len(sys.argv) > 2 else 100
    days = int(sys.argv[3]) if len(sys.argv) > 3 else 3

    analyze_recent_tweets(username, max_tweets=max_tweets, days=days)
