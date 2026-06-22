"""把推文导入 DB 并跑三层管道分析。

用法: python3 import_and_analyze.py [tweets_file]
默认: data/tweets_cookie.json

- 清空旧的测试数据(tweets/stock_mentions)
- 正确解析 X 的 created_at 时间格式
- 跑 StockExtractor(NER) + 三层管道 SentimentAnalyzer
- 写入 DB
"""
import json
import sys
import time
from datetime import datetime, timezone
from collections import Counter

from loguru import logger
from storage.models import init_db, get_session, Tweet, StockMention
from parser.stock_ner import StockExtractor
from ai.sentiment_analyzer import SentimentAnalyzer

tweets_file = sys.argv[1] if len(sys.argv) > 1 else "data/tweets_cookie.json"
logger.info(f"=== 导入推文 + 三层管道分析 ({tweets_file}) ===")
init_db()

tweets = json.load(open(tweets_file, encoding="utf-8"))
logger.info("加载 {} 条推文", len(tweets))


def parse_x_date(s: str) -> datetime:
    """解析 X 格式 'Wed May 27 16:35:00 +0000 2026' → datetime。"""
    for fmt in ("%a %b %d %H:%M:%S %z %Y", "%a %b %d %H:%M:%S +0000 %Y"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            continue
    return datetime.now(timezone.utc)


# 清空旧测试数据
session = get_session()
old_t = session.query(Tweet).count()
old_m = session.query(StockMention).count()
logger.info("清空旧数据: {} tweets, {} mentions", old_t, old_m)
session.query(StockMention).delete()
session.query(Tweet).delete()
session.commit()

# Phase 1: 写入 tweets（带真实日期）
for t in tweets:
    tid = t["id"]
    created = parse_x_date(t.get("created_at", ""))
    session.add(Tweet(
        id=tid,
        text=t["text"],
        created_at=created,
        author_id="aleabitoreddit",
        retweet_count=t.get("retweets", 0),
    ))
session.commit()
logger.info("tweets 入库 {} 条", len(tweets))

# Phase 2: NER + 三层管道分析
extractor = StockExtractor()
analyzer = SentimentAnalyzer()

stats = Counter()
t0 = time.time()

for i, t in enumerate(tweets, 1):
    text = t["text"]
    tid = t["id"]
    mentions = extractor.extract(text)
    stats["tweets"] += 1
    if not mentions:
        stats["no_ticker"] += 1
        continue

    for m in mentions:
        result = analyzer.analyze(text, m.symbol, m.name)
        session.add(StockMention(
            tweet_id=tid,
            symbol=m.symbol,
            name=m.name,
            sentiment_score=result.score,
            sentiment_label=result.label,
            llm_reason=result.reason,
            analyzed_at=parse_x_date(t.get("created_at", "")),
        ))
        stats["mentions"] += 1
        if result.method == "llm_review":
            stats["llm_reviewed"] += 1

    if i % 50 == 0:
        elapsed = time.time() - t0
        logger.info("进度 {}/{} ({:.0f}s) | mentions={} 二审={}",
                    i, len(tweets), elapsed, stats["mentions"], stats["llm_reviewed"])

session.commit()
elapsed = time.time() - t0

logger.info("=" * 50)
logger.info("完成！耗时 {:.0f}s", elapsed)
logger.info("  推文: {} (无$ticker: {})", stats["tweets"], stats["no_ticker"])
logger.info("  股票提及: {}", stats["mentions"])
logger.info("  LLM 二审触发: {}", stats["llm_reviewed"])

# 情绪分布
from sqlalchemy import func
dist = session.query(StockMention.sentiment_label, func.count(StockMention.id)) \
    .group_by(StockMention.sentiment_label).all()
logger.info("情绪分布:")
for label, n in dist:
    logger.info("  {}: {}", label, n)
unique = session.query(func.count(func.distinct(StockMention.symbol))).scalar()
total = session.query(func.count(StockMention.id)).scalar()
logger.info("独立股票: {} | 总提及: {}", unique, total)
session.close()
