"""FinTwitBERT 全流程运行器（绕过 main.py 输出包装问题）"""
import sys
import io
import json
import os

# 提前修复编码
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

os.environ["PYTHONIOENCODING"] = "utf-8"

from datetime import datetime, timezone, timedelta, date
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

# ── 日志配置 ──────────────────────────────────────────
logger.remove()
logger.add(
    sys.stderr,
    level="INFO",
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
)


def save_tweets_to_db(tweets, username):
    session = get_session()
    saved = 0
    for t in tweets:
        tid = t.get("id") or str(hash(t["text"]))
        if session.query(TweetModel).filter_by(id=tid).first():
            continue
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
            author_id=username,
            like_count=0, retweet_count=0, reply_count=0, quote_count=0,
            lang="",
        ))
        saved += 1
    session.commit()
    session.close()
    logger.info("Saved {} new tweets to DB", saved)
    return saved


def run_sentiment_analysis(username, tweets, days):
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
                recent_tweets.append(t)
        else:
            recent_tweets.append(t)
    logger.info("{} tweet(s) in analysis window ({}-day)", len(recent_tweets), days)

    session = get_session()
    stock_stats = defaultdict(lambda: {
        "mentions": 0, "scores": [],
        "labels": defaultdict(int), "reasons": [],
        "latest_text": "", "latest_date": "",
    })
    new_count = 0
    skip_count = 0

    for tweet in recent_tweets:
        text = tweet["text"]
        tid = tweet.get("id") or str(hash(text))
        try:
            tweet_dt = datetime.fromisoformat(
                tweet.get("created_at", "").replace("Z", "+00:00")
            )
        except (ValueError, TypeError):
            tweet_dt = datetime.now(timezone.utc)

        mentions = extractor.extract(text)
        if not mentions:
            continue

        existing_mentions = session.query(StockMentionModel).filter(
            StockMentionModel.tweet_id == tid
        ).all()
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

        for em in existing_mentions:
            s = stock_stats[em.symbol]
            s["mentions"] += 1
            s["scores"].append(em.sentiment_score)
            s["labels"][em.sentiment_label] += 1
            s["reasons"].append(em.llm_reason or "")

    logger.info("Sentiment: {} new, {} skipped", new_count, skip_count)
    session.commit()
    session.close()
    return dict(stock_stats)


def print_report(username, stock_stats, days):
    print("\n" + "=" * 60)
    print(f"  @{username} FinTwitBERT 情绪分析报告")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)
    if not stock_stats:
        print("\n  未检测到任何股票提及。\n")
        return
    for symbol, stats in sorted(stock_stats.items(), key=lambda x: x[1]["mentions"], reverse=True):
        avg_score = sum(stats["scores"]) / len(stats["scores"])
        label_counts = dict(stats["labels"])
        emoji = "🟢" if avg_score > 0.3 else ("🔴" if avg_score < -0.3 else "⚪")
        trend = "看多" if avg_score > 0.3 else ("看空" if avg_score < -0.3 else "中性")
        print(f"\n  {emoji} {symbol}")
        print(f"     提及: {stats['mentions']}次 | 均分: {avg_score:+.2f} ({trend})")
        print(f"     分布: 看多{label_counts.get('positive',0)} / 中性{label_counts.get('neutral',0)} / 看空{label_counts.get('negative',0)}")
        print(f"     片段: {stats['latest_text'][:60]}...")
    print("=" * 60)


def save_report(username, result):
    out_path = config.DATA_DIR / f"portfolio_{username}_{date.today()}.md"
    lines = [
        f"# @{username} FinTwitBERT 虚拟持仓回测报告",
        "",
        f"- 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"- 回测区间: {config.PORTFOLIO_START_DATE} ~ {date.today()}",
        f"- 情绪引擎: FinTwitBERT-sentiment (GPU)",
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
        "", "## 收益统计",
        "| 指标 | 数值 |", "|------|------|",
        f"| 初始资金 | ¥{config.PORTFOLIO_INITIAL_CASH:,.0f} |",
        f"| 最终价值 | ¥{final_val:,.0f} |",
        f"| 累计盈亏 | ¥{pnl:+,.0f} |",
        f"| 累计收益率 | {cum_ret:+.2%} |",
        "",
    ]
    rebalances = result.get("rebalances", [])
    if rebalances:
        lines.append("## 调仓历史")
        lines.append(f"共 {len(rebalances)} 笔调仓")
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
    logger.info("报告已保存: {}", out_path)
    return out_path


# ── 主流程 ───────────────────────────────────────────
if __name__ == "__main__":
    username = sys.argv[1] if len(sys.argv) > 1 else "aleaborteddit"
    max_tweets = int(sys.argv[2]) if len(sys.argv) > 2 else 200
    days = int(sys.argv[3]) if len(sys.argv) > 3 else 365

    print(f"\n{'='*60}")
    print(f"  FinTwitBERT Pipeline — @{username}")
    print(f"  max_tweets={max_tweets}  days={days}")
    print(f"{'='*60}")

    init_db()

    logger.info("Phase 1: 加载推文...")
    with PlaywrightScraper() as scraper:
        tweets = scraper.load_tweets()
    if not tweets:
        logger.error(" 无推文数据，请先运行 scraper/scrape_x.sh")
        sys.exit(1)
    save_tweets_to_db(tweets, username)

    logger.info("Phase 2: FinTwitBERT 情绪分析...")
    stock_stats = run_sentiment_analysis(username, tweets, days)
    print_report(username, stock_stats, days)

    logger.info("Phase 3: 虚拟持仓回测...")
    engine = PortfolioEngine(
        username=username,
        author_id=username,
        initial_cash=config.PORTFOLIO_INITIAL_CASH,
    )
    result = engine.backtest(
        from_date=date.fromisoformat(config.PORTFOLIO_START_DATE),
        to_date=date.today(),
    )

    cum_ret = result.get("cumulative_return", 0)
    final_val = result.get("final_value", config.PORTFOLIO_INITIAL_CASH)
    pnl = final_val - config.PORTFOLIO_INITIAL_CASH

    print("\n" + "=" * 60)
    print(f"  回测结果")
    print(f"  初始 ¥{config.PORTFOLIO_INITIAL_CASH:,.0f} → 最终 ¥{final_val:,.0f} ({cum_ret:+.2%})")
    print(f"  盈亏: ¥{pnl:+,.0f} | 调仓: {result.get('total_events', 0)} 笔")
    print(f"  持仓: {len(result.get('final_positions', {}))} 只")
    if note := result.get("note"):
        print(f"  注: {note}")
    print("=" * 60)

    report_path = save_report(username, result)
    print(f"\n✅ 完成! 报告: {report_path}")
