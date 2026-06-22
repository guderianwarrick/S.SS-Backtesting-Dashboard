#!/usr/bin/env python3
"""监控 aleabitoreddit 推特更新 → 写入数据库 → 情绪分析 → 输出飞书通知内容。

设计为 cron 脚本（no_agent=True）：stdout 直接作为飞书消息发送。
无新推文时 stdout 为空 = 静默，不打扰用户。

数据流（统一接口）:
  1. 抓取新推文 → tweets 表
  2. NER 提取 → tweet_tickers 表
  3. 情绪分析 → stock_mentions 表
  4. 输出通知内容

用法:
  python3 monitor.py            # 正常运行
  python3 monitor.py --dry-run  # 忽略状态文件，分析最新一页（首次测试用）
"""
import json
import os
import sys
import time
import contextlib
from pathlib import Path
from datetime import datetime

# ── 路径 ──
BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

STATE_FILE = BASE / "data" / ".monitor_state.json"

# ── 抓取（复用 scrape_cookie 的认证逻辑） ──
from scrape_cookie import (
    get_user_id, gql_get, extract_tweets,
    TARGET_SCREEN_NAME, QID_USER_TWEETS,
)

# ── 数据库 ──
from storage.models import init_db, session_scope, Tweet, TweetTicker, StockMention

# ── 情绪分析 + NER ──
from ai.finbert_analyzer import FinBERTAnalyzer
from ai.sarcasm_detector import detect as detect_sarcasm
from parser.stock_ner import StockExtractor


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"last_tweet_id": None}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def fetch_recent_tweets(user_id, count=1):
    """抓最新 count 页推文"""
    all_tweets = []
    cursor = None
    for _ in range(count):
        variables = {
            "userId": user_id,
            "count": 20,
            "includePromotedContent": False,
            "withQuickPromoteEligibilityTweetFields": False,
            "withVoice": False,
        }
        if cursor:
            variables["cursor"] = cursor
        status, data = gql_get(QID_USER_TWEETS, "UserTweets", variables)
        if status != 200:
            break
        tweets, cursor = extract_tweets(data)
        all_tweets.extend(tweets)
        if not cursor:
            break
        time.sleep(1)
    return all_tweets


def filter_new_tweets(tweets, last_id):
    """返回 id 严格大于 last_id 的新推文（X 的 id 是递增的雪花 ID）"""
    if last_id is None:
        return []
    last_int = int(last_id)
    return [t for t in tweets if int(t["id"]) > last_int]


def parse_time(created: str) -> datetime:
    """解析时间字符串"""
    if not created:
        return datetime.now()
    if "T" in created and "-" in created.split("T")[0]:
        try:
            return datetime.fromisoformat(created.replace("Z", "+00:00"))
        except:
            pass
    for fmt in ("%a %b %d %H:%M:%S %z %Y", "%a %b %d %H:%M:%S +0000 %Y"):
        try:
            return datetime.strptime(created.strip(), fmt)
        except ValueError:
            continue
    return datetime.now()


def save_tweets_to_db(tweets):
    """写入 tweets 表（去重）"""
    with session_scope() as session:
        for t in tweets:
            # 检查是否已存在
            existing = session.query(Tweet).filter(Tweet.id == str(t["id"])).first()
            if existing:
                continue
            session.add(Tweet(
                id=str(t["id"]),
                text=t["text"],
                created_at=parse_time(t.get("created_at", "")),
                author_id="aleabitoreddit",
                retweet_count=t.get("retweet_count", 0) or t.get("retweets", 0),
                like_count=t.get("like_count", 0) or t.get("favorites", 0),
            ))


def extract_and_save_tickers(tweets):
    """NER 提取股票代码，写入 tweet_tickers 表"""
    extractor = StockExtractor()
    with session_scope() as session:
        for t in tweets:
            tweet_id = str(t["id"])
            # 检查是否已提取
            existing = session.query(TweetTicker).filter(
                TweetTicker.tweet_id == tweet_id
            ).first()
            if existing:
                continue
            
            mentions = extractor.extract(t["text"])
            for m in mentions:
                session.add(TweetTicker(
                    tweet_id=tweet_id,
                    symbol=m.symbol,
                    name=m.name,
                    confidence=m.confidence,
                ))


def analyze_and_save_sentiment(tweets):
    """情绪分析，写入 stock_mentions 表，返回分析结果用于通知"""
    finbert = FinBERTAnalyzer()
    extractor = StockExtractor()
    
    results = []
    
    with session_scope() as session:
        for t in tweets:
            tweet_id = str(t["id"])
            text = t["text"]
            
            # NER 提取
            mentions = extractor.extract(text)
            if not mentions:
                continue
            
            # FinBERT 分析
            for m in mentions:
                # 检查是否已分析
                existing = session.query(StockMention).filter(
                    StockMention.tweet_id == tweet_id,
                    StockMention.symbol == m.symbol,
                ).first()
                if existing:
                    # 复用已有结果
                    results.append({
                        "tweet": t,
                        "symbol": m.symbol,
                        "score": existing.sentiment_score,
                        "label": existing.sentiment_label,
                    })
                    continue
                
                # 分析
                fb_result = finbert.analyze(text, m.symbol)
                
                # 检查是否需要 LLM 二审
                sig = detect_sarcasm(text)
                need_review = False
                if sig and sig.hit:
                    need_review = True
                elif abs(fb_result.score) >= 0.30 and fb_result.confidence < 0.55:
                    need_review = True
                
                # 构建 reason
                reason = (
                    f"FinTwitBERT({fb_result.device}) "
                    + " ".join(f"{k}={v:.3f}" for k, v in fb_result.prob_dist.items())
                )
                
                # 写入数据库
                mention = StockMention(
                    tweet_id=tweet_id,
                    symbol=m.symbol,
                    name=m.name,
                    sentiment_score=fb_result.score,
                    sentiment_label=fb_result.label,
                    llm_reason=reason,
                    method="finbert",
                    needs_llm_review=need_review,
                )
                session.add(mention)
                
                results.append({
                    "tweet": t,
                    "symbol": m.symbol,
                    "score": fb_result.score,
                    "label": fb_result.label,
                })
    
    return results


def aggregate_sentiment(results):
    """按 ticker 聚合：平均分、提及次数"""
    ticker_data = {}  # symbol → {scores: []}
    for r in results:
        sym = r["symbol"]
        if sym not in ticker_data:
            ticker_data[sym] = {"scores": [], "tweets": []}
        ticker_data[sym]["scores"].append(r["score"])
        ticker_data[sym]["tweets"].append(r)

    agg = []
    for sym, d in ticker_data.items():
        scores = d["scores"]
        avg = sum(scores) / len(scores)
        agg.append({
            "symbol": sym,
            "count": len(scores),
            "avg_score": round(avg, 3),
            "stance": "看多" if avg > 0.2 else ("看空" if avg < -0.2 else "中性"),
        })
    agg.sort(key=lambda x: abs(x["avg_score"]), reverse=True)
    return agg


def format_message(new_tweets, results, agg, user_info):
    """格式化飞书消息"""
    lines = []
    lines.append(f"📡 @{user_info} 发了 {len(new_tweets)} 条新推文")
    n_stock = len(set(r["tweet"]["id"] for r in results))
    lines.append(f"其中 {n_stock} 条提及股票")

    if not agg:
        lines.append("")
        lines.append("(无股票提及，或情绪中性)")
        return "\n".join(lines)

    lines.append("")
    lines.append("━" * 20)
    lines.append("📊 个股多空态度")
    lines.append("━" * 20)
    for a in agg:
        arrow = "🟢" if a["stance"] == "看多" else ("🔴" if a["stance"] == "看空" else "⚪")
        lines.append(
            f"{arrow} ${a['symbol']}  {a['stance']}  "
            f"分数 {a['avg_score']:+.2f}  ({a['count']}次)"
        )

    # 最看多/最看空的单条推文
    lines.append("")
    lines.append("━" * 20)
    lines.append("💬 重点推文")
    lines.append("━" * 20)
    # 按绝对分数排序，取前3条
    top = sorted(results, key=lambda r: abs(r["score"]), reverse=True)[:3]
    for r in top:
        arrow = "🟢" if r["score"] > 0.2 else ("🔴" if r["score"] < -0.2 else "⚪")
        text = r["tweet"]["text"].replace("\n", " ")[:120]
        lines.append(f"{arrow} ${r['symbol']} {r['score']:+.2f}")
        lines.append(f"   {text}")
        lines.append(f"   {r['tweet'].get('url', '')}")
        lines.append("")

    return "\n".join(lines)


def main():
    dry_run = "--dry-run" in sys.argv

    # 初始化数据库
    init_db()

    # 1. 获取用户 ID（诊断信息转 stderr，保持 stdout 纯净）
    with contextlib.redirect_stdout(sys.stderr):
        uid = get_user_id(TARGET_SCREEN_NAME)
    if not uid:
        print("⚠️ 无法获取用户信息（cookie 可能过期）", flush=True)
        sys.exit(1)

    # 2. 抓最新推文
    tweets = fetch_recent_tweets(uid, count=1)

    # 3. 过滤新推文
    state = load_state()
    last_id = state.get("last_tweet_id")

    if dry_run:
        new_tweets = tweets
    else:
        new_tweets = filter_new_tweets(tweets, last_id)

    # 4. 更新状态（记录最新的推文 ID）
    if tweets:
        max_id = max(t["id"] for t in tweets)
        if dry_run:
            pass
        else:
            state["last_tweet_id"] = max_id
            state["last_check"] = datetime.now().isoformat()
            save_state(state)

    # 5. 无新推文 → 静默
    if not new_tweets and not dry_run:
        return  # stdout 空 = 飞书不发送

    # 首次运行（无状态）→ 只记录，不发消息
    if last_id is None and not dry_run:
        print(f"✅ 监控已启动，基准点：{len(tweets)} 条最新推文已记录。下次有新推文会提醒你。", flush=True)
        return

    # 6. 写入数据库 + 情绪分析（统一接口）
    save_tweets_to_db(new_tweets)
    extract_and_save_tickers(new_tweets)
    results = analyze_and_save_sentiment(new_tweets)

    # 7. 聚合
    agg = aggregate_sentiment(results)

    # 8. 输出消息
    msg = format_message(new_tweets, results, agg, TARGET_SCREEN_NAME)
    print(msg, flush=True)


if __name__ == "__main__":
    main()
