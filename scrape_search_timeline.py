#!/usr/bin/env python3
"""用 X GraphQL SearchTimeline 抓取用户更早历史推文.

UserTweets 有深度上限(约 800 条), SearchTimeline 通过 `from:<user>` 搜索
可以绕过该限制, 拿到更早推文.
"""
import json
import sys
import time
from curl_cffi import requests as cffi_requests

# 复用 scrape_cookie 的 cookie / proxy / bearer
from scrape_cookie import AUTH_TOKEN, CT0, BEARER, PROXIES

HEADERS = {
    "authorization": f"Bearer {BEARER}",
    "x-csrf-token": CT0,
    "x-twitter-auth-type": "OAuth2Session",
    "x-twitter-active-user": "yes",
    "cookie": f"auth_token={AUTH_TOKEN}; ct0={CT0}",
    "content-type": "application/json",
    "Referer": "https://x.com/search?q=from%3Aaleabitoreddit&src=typed_query",
}

QUERY_ID = "yIphfmxUO-hddQHKIOk9tA"

FEATURES = {
    "responsive_web_graphql_exclude_directive_enabled": True,
    "verified_phone_label_enabled": False,
    "responsive_web_home_pinned_timelines_enabled": True,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "tweetypie_unmention_optimization_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": False,
    "tweet_awards_web_tipping_enabled": False,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": True,
    "responsive_web_media_download_video_enabled": False,
    "responsive_web_enhance_cards_enabled": False,
}


def search_timeline(query: str, cursor: str = None, count: int = 20):
    variables = {
        "rawQuery": query,
        "product": "Latest",
        "count": count,
    }
    if cursor:
        variables["cursor"] = cursor

    r = None
    for attempt in range(4):
        try:
            r = cffi_requests.post(
                f"https://api.x.com/graphql/{QUERY_ID}/SearchTimeline",
                headers=HEADERS,
                json={"variables": variables, "features": FEATURES},
                impersonate="chrome",
                proxies=PROXIES,
                timeout=20,
            )
        except Exception as e:
            print(f"  [net] {e}, retry {attempt+1}", flush=True)
            time.sleep(3 * (attempt + 1))
            continue

        if r.status_code == 429:
            wait = 60
            try:
                if r.headers.get("Retry-After"):
                    wait = min(max(int(r.headers["Retry-After"]), 60), 900)
                elif r.headers.get("x-rate-limit-reset"):
                    wait = min(max(int(r.headers["x-rate-limit-reset"]) - int(time.time()) + 5, 60), 900)
            except Exception:
                pass
            print(f"  [rate] 429, wait {wait}s", flush=True)
            if attempt < 2:
                time.sleep(wait)
                continue
            print("  [rate] 429, 重试用尽", flush=True)
            return r.status_code, None

        try:
            return r.status_code, r.json()
        except ValueError:
            print(f"  [warn] non-JSON status={r.status_code} body={r.text[:80]}", flush=True)
            return r.status_code, None

    return (r.status_code if r else 0), None


def extract_tweets(data):
    tweets = []
    cursor = None
    if not data:
        return tweets, cursor
    timeline = (
        data.get("data", {})
        .get("search_by_raw_query", {})
        .get("search_timeline", {})
        .get("timeline", {})
    )
    instructions = timeline.get("instructions", [])
    for inst in instructions:
        for entry in inst.get("entries", []):
            content = entry.get("content", {})
            if content.get("entryType") == "TimelineTimelineItem":
                tr = content.get("itemContent", {}).get("tweet_results", {}).get("result", {})
                if tr.get("__typename") == "TweetWithVisibilityResults":
                    tr = tr.get("tweet", {})
                legacy = tr.get("legacy", {})
                tid = legacy.get("id_str") or tr.get("rest_id", "")
                text = legacy.get("full_text", "")
                if tid and text:
                    tweets.append({
                        "id": tid,
                        "text": text,
                        "created_at": legacy.get("created_at", ""),
                        "retweets": legacy.get("retweet_count", 0),
                        "favorites": legacy.get("favorite_count", 0),
                        "url": f"https://x.com/aleabitoreddit/status/{tid}",
                    })
            if content.get("entryType") == "TimelineTimelineCursor" and content.get("cursorType") == "Bottom":
                cursor = content.get("value")
    return tweets, cursor


def main():
    screen_name = sys.argv[1] if len(sys.argv) > 1 else "aleabitoreddit"
    max_pages = int(sys.argv[2]) if len(sys.argv) > 2 else 100
    out = f"data/tweets_search_{screen_name}.json"

    all_tweets = []
    seen_ids = set()
    cursor = None

    print(f"Fetching older tweets for @{screen_name} via SearchTimeline...", flush=True)
    for page in range(max_pages):
        status, data = search_timeline(f"from:{screen_name}", cursor)
        if status != 200 or not data:
            print(f"Page {page+1} stop: status={status}", flush=True)
            break

        tweets, cursor = extract_tweets(data)
        new = 0
        for t in tweets:
            if t["id"] not in seen_ids:
                seen_ids.add(t["id"])
                all_tweets.append(t)
                new += 1

        print(f"  page {page+1}: +{new} (total: {len(all_tweets)})", flush=True)

        # 每页保存
        with open(out, "w") as f:
            json.dump(all_tweets, f, ensure_ascii=False, indent=2)

        if not cursor or new == 0:
            print("No more tweets.", flush=True)
            break
        time.sleep(2)

    print(f"\n✓ Done! {len(all_tweets)} older tweets saved to {out}", flush=True)


if __name__ == "__main__":
    main()
