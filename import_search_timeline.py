#!/usr/bin/env python3
"""增量导入 SearchTimeline 抓到的更早推文并跑三层管道分析.

- 不清空已有 tweets, 只插入新增 tweet_id
- 对新增 tweet 跑 NER + 三层 SentimentAnalyzer
- 写入 stock_mentions
"""
import sys
import json
import time
from datetime import datetime, timezone
from collections import Counter

from loguru import logger
from storage.models import init_db, get_session, Tweet, StockMention
from parser.stock_ner import StockExtractor
from ai.sentiment_analyzer import SentimentAnalyzer

SCREEN_NAME = sys.argv[1] if len(sys.argv) > 1 else "aleabitoreddit"
JSON_FILE = sys.argv[2] if len(sys.argv) > 2 else f"data/tweets_search_{SCREEN_NAME}.json"

logger.info("=== 增量导入 {} 的历史推文 ===", SCREEN_NAME)
init_db()

session = get_session()

tweets = json.load(open(JSON_FILE, encoding="utf-8"))
logger.info("加载 {} 条推文", len(tweets))


def parse_x_date(s: str) -> datetime:
    for fmt in ("%a %b %d %H:%M:%S %z %Y", "%a %b %d %H:%M:%S +0000 %Y"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            continue
    return datetime.now(timezone.utc)


# 获取 DB 中已存在的 tweet_id
existing_ids = {r[0] for r in session.query(Tweet.id).filter(Tweet.author_id == SCREEN_NAME).all()}
logger.info("DB 中已有 {} 条 @{} 的推文", len(existing_ids), SCREEN_NAME)

# 插入新 tweets
new_tweets = []
for t in tweets:
    tid = t["id"]
    if tid in existing_ids:
        continue
    created = parse_x_date(t.get("created_at", ""))
    session.add(Tweet(
        id=tid,
        text=t["text"],
        created_at=created,
        author_id=SCREEN_NAME,
        retweet_count=t.get("retweets", 0),
    ))
    new_tweets.append(t)

session.commit()
logger.info("新增 {} 条推文", len(new_tweets))

if not new_tweets:
    logger.info("没有新推文需要分析")
    session.close()
    sys.exit(0)

# 跑 NER + 三层管道
extractor = StockExtractor()
analyzer = SentimentAnalyzer()

stats = Counter()
t0 = time.time()

for t in new_tweets:
    text = t["text"]
    tid = t["id"]
    mentions = extractor.extract(text)
    if not mentions:
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

session.commit()
elapsed = time.time() - t0

logger.info("=" * 50)
logger.info("分析完成！耗时 {:.0f}s", elapsed)
logger.info("  新增推文: {}", len(new_tweets))
logger.info("  新增股票提及: {}", stats["mentions"])
logger.info("  LLM 二审触发: {}", stats["llm_reviewed"])

# 当前该博主总统计
from sqlalchemy import func
dist = session.query(StockMention.sentiment_label, func.count(StockMention.id)) \
    .join(Tweet, StockMention.tweet_id == Tweet.id) \
    .filter(Tweet.author_id == SCREEN_NAME) \
    .group_by(StockMention.sentiment_label).all()
logger.info("当前情绪分布 (@{}):", SCREEN_NAME)
for label, n in dist:
    logger.info("  {}: {}", label, n)

session.close()

# 同时输出简洁 summary 到 stdout, 方便 cronjob 捕获
print(f"\n=== @{SCREEN_NAME} 监控摘要 ===")
print(f"新增推文: {len(new_tweets)}")
print(f"新增股票提及: {stats['mentions']}")
print(f"LLM 二审: {stats['llm_reviewed']}")
print(f"总推文数: {len(existing_ids) + len(new_tweets)}")
print("=" * 40)
