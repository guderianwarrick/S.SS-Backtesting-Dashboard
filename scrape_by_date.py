#!/usr/bin/env python3
"""按天分段抓取 X 用户历史推文（绕过 UserTweets 深度限制）.

X SearchTimeline 单 query 最多 ~40 条, 但按天查询 `from:user since:YYYY-MM-DD until:...`
可以拿到更早历史（只要当天推文 < 40 条）. 本脚本从 end_date 倒推, 每天翻页抓完.
"""
import json
import sys
import time
from datetime import datetime, timedelta
from scrape_search_timeline import search_timeline, extract_tweets

SCREEN_NAME = sys.argv[1] if len(sys.argv) > 1 else "aleabitoreddit"
START_DATE = sys.argv[2] if len(sys.argv) > 2 else "2024-01-01"
END_DATE = sys.argv[3] if len(sys.argv) > 3 else "2026-03-28"
MAX_PAGES_PER_DAY = int(sys.argv[4]) if len(sys.argv) > 4 else 10
OUT_FILE = f"data/tweets_search_{SCREEN_NAME}.json"


def load_existing() -> dict:
    try:
        with open(OUT_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return {t["id"]: t for t in data}
    except FileNotFoundError:
        return {}


def save(tweets: list):
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(tweets, f, ensure_ascii=False, indent=2)


def fetch_day(day: datetime) -> list:
    next_day = day + timedelta(days=1)
    q = f"from:{SCREEN_NAME} since:{day.strftime('%Y-%m-%d')} until:{next_day.strftime('%Y-%m-%d')}"
    cursor = None
    all_tweets = []
    seen_ids = set()
    for page in range(MAX_PAGES_PER_DAY):
        status, data = search_timeline(q, cursor)
        if status != 200 or not data:
            break
        tweets, cursor = extract_tweets(data)
        new = [t for t in tweets if t["id"] not in seen_ids]
        seen_ids.update(t["id"] for t in new)
        all_tweets.extend(new)
        if not cursor or not new:
            break
        time.sleep(1)
    return all_tweets


def main():
    start = datetime.strptime(START_DATE, "%Y-%m-%d")
    end = datetime.strptime(END_DATE, "%Y-%m-%d")
    if end < start:
        raise ValueError("end_date must be >= start_date")

    existing = load_existing()
    all_by_id = {k: v for k, v in existing.items()}
    print(f"已存在 {len(existing)} 条, 开始按天抓取 {start.date()} ~ {end.date()}")

    current = end
    consecutive_empty = 0
    while current >= start:
        tweets = fetch_day(current)
        for t in tweets:
            all_by_id[t["id"]] = t

        # 每 10 天保存一次
        if (end - current).days % 10 == 0:
            save(list(all_by_id.values()))
            print(f"  [save] {current.date()}: 累计 {len(all_by_id)} 条", flush=True)

        if not tweets:
            consecutive_empty += 1
            # 连续 30 天没有 = 基本到账号开头了
            if consecutive_empty >= 30:
                print(f"连续 {consecutive_empty} 天无推文, 停止", flush=True)
                break
        else:
            consecutive_empty = 0
            print(f"  {current.date()}: +{len(tweets)} 条 (累计 {len(all_by_id)})", flush=True)

        current -= timedelta(days=1)
        time.sleep(1)

    save(list(all_by_id.values()))
    print(f"\n✓ Done! 共 {len(all_by_id)} 条保存到 {OUT_FILE}", flush=True)


if __name__ == "__main__":
    main()
