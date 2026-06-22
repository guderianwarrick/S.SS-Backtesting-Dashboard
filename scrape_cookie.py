"""Scrape X.com tweets using session cookies + curl_cffi (bypasses Cloudflare TLS fingerprint).

Pipeline: VLESS proxy (xray SOCKS5:10808) → curl_cffi (chrome impersonation) → X GraphQL API.
"""
import json
import time
import sys

from curl_cffi import requests as cffi_requests

# ── Config ──────────────────────────────────────────────────────────────────
PROXY = "socks5h://127.0.0.1:10808"
PROXIES = {"http": PROXY, "https": PROXY}

# Session cookies (refresh from browser ~weekly — see X_COOKIE_REFRESH below)
AUTH_TOKEN = "e9f9caf389eae814973ec94aec2654ac02176f80"
CT0 = "bf9ec62c4cb86b34947e9833718dba7b0c4cd54f0ae13d9e9bb4090ed790d50d0d45ea963d38c70005703da7ac0abfeff4dbc4310e5bf4859afd0162f44bf4b91f654fc73840ec72ce3f96c987409491"

# Web bearer token (current X web client) — extracted from main.[hash].js
BEARER = "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"

# Target screen name (pass as first CLI argument)
TARGET_SCREEN_NAME = sys.argv[1] if len(sys.argv) > 1 else "aleabitoreddit"

# GraphQL queryIds (current version) — update if X ships a new client build
QID_USER_BY_SCREEN_NAME = "681MIj51w00Aj6dY0GXnHw"
QID_USER_TWEETS = "RyDU3I9VJtPF-Pnl6vrRlw"

MAX_PAGES = 100
PAGE_SIZE = 40

FEATURES = {
    "responsive_web_graphql_exclude_directive_enabled": True,
    "verified_phone_label_enabled": False,
    "responsive_web_home_pinned_timelines_enabled": False,
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


def make_headers():
    return {
        "authorization": f"Bearer {BEARER}",
        "x-csrf-token": CT0,
        "x-twitter-auth-type": "OAuth2Session",
        "x-twitter-active-user": "yes",
        "cookie": f"auth_token={AUTH_TOKEN}; ct0={CT0}",
        "content-type": "application/json",
    }


def gql_get(query_id, operation, variables, extra_features=None):
    """Call a GraphQL endpoint, return parsed JSON. 健壮: 非 JSON / 限流不崩溃。"""
    features = {**FEATURES, **(extra_features or {})}
    params = {
        "variables": json.dumps(variables),
        "features": json.dumps(features),
    }
    for attempt in range(4):  # 限流自动重试
        try:
            r = cffi_requests.get(
                f"https://api.x.com/graphql/{query_id}/{operation}",
                impersonate="chrome",
                proxies=PROXIES,
                params=params,
                headers=make_headers(),
                timeout=20,
            )
        except Exception as e:
            print(f"  [net] {e}, retry {attempt+1}", flush=True)
            time.sleep(3 * (attempt + 1))
            continue
        # 429 限流 → 必须先于 JSON 解析(限流响应是纯文本), 读真实重置时间等待重试
        if r.status_code == 429:
            ra = r.headers.get("Retry-After")
            reset = r.headers.get("x-rate-limit-reset")
            if ra:
                try: wait = min(max(int(ra), 60), 900)
                except ValueError: wait = 900
            elif reset:
                try: wait = min(max(int(reset) - int(time.time()) + 5, 60), 900)
                except ValueError: wait = 900
            else:
                wait = 900
            if attempt < 2:
                print(f"  [rate] 429 限流, 第{attempt+1}次等待 {wait}s 重试", flush=True)
                time.sleep(wait)
                continue
            print("  [rate] 429 限流, 已重试2次仍失败, 停止", flush=True)
            return r.status_code, None
        # 非 JSON 响应(空/HTML错误页) → 返回 status, None
        try:
            data = r.json()
        except ValueError:
            print(f"  [warn] 非 JSON 响应 status={r.status_code} body={r.text[:80]}", flush=True)
            return r.status_code, None
        return r.status_code, data
    return r.status_code, None  # 重试用尽


def get_user_id(screen_name):
    """Resolve screen_name → numeric user ID via UserByScreenName."""
    variables = {
        "screen_name": screen_name,
        "withSafetyModeUserFields": True,
    }
    extra_features = {
        "hidden_profile_subscriptions_enabled": True,
        "rweb_tipjar_consumption_enabled": True,
        "responsive_web_graphql_exclude_directive_enabled": True,
    }
    status, data = gql_get(QID_USER_BY_SCREEN_NAME, "UserByScreenName", variables, extra_features)
    if status != 200 or not data:
        print(f"UserByScreenName failed: {status}", flush=True)
        return None
    result = data.get("data", {}).get("user", {}).get("result", {})
    uid = result.get("rest_id", "")
    legacy = result.get("legacy", {})
    print(f"User: @{legacy.get('screen_name', screen_name)} (ID: {uid})", flush=True)
    print(f"  Followers: {legacy.get('followers_count')}  Tweets: {legacy.get('statuses_count')}", flush=True)
    return uid


def extract_tweets(data):
    """Pull tweet dicts (id_str + full_text) out of the timeline response."""
    tweets = []
    timeline = (
        data.get("data", {}).get("user", {}).get("result", {}).get("timeline", {})
        .get("timeline", {})
    )
    instructions = timeline.get("instructions", [])
    cursor = None
    for inst in instructions:
        for entry in inst.get("entries", []):
            content = entry.get("content", {})
            # Cursor for pagination
            if content.get("entryType") == "TimelineTimelineCursor" and content.get("cursorType") == "Bottom":
                cursor = content.get("value")
            # A tweet entry
            if content.get("entryType") == "TimelineTimelineItem":
                tr = content.get("itemContent", {}).get("tweet_results", {}).get("result", {})
                # Unwrap visibility wrapper if present
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
                        "url": f"https://x.com/{TARGET_SCREEN_NAME}/status/{tid}",
                    })
    return tweets, cursor


def fetch_user_tweets(user_id, max_pages=MAX_PAGES):
    all_tweets = []
    seen_ids = set()
    cursor = None
    out = f"data/tweets_cookie_{TARGET_SCREEN_NAME}.json"

    def save():
        import os, shutil
        if os.path.exists(out) and os.path.getsize(out) > 5:
            shutil.copy2(out, out + ".bak")   # 覆盖前备份, 防再次丢失
        with open(out, "w") as f:
            json.dump(all_tweets, f, ensure_ascii=False, indent=2)

    for page in range(max_pages):
        variables = {
            "userId": user_id,
            "count": PAGE_SIZE,
            "includePromotedContent": False,
            "withQuickPromoteEligibilityTweetFields": False,
            "withVoice": False,
        }
        if cursor:
            variables["cursor"] = cursor
            variables["count"] = 20

        status, data = gql_get(QID_USER_TWEETS, "UserTweets", variables)
        if status != 200 or not data:
            print(f"UserTweets page {page+1} stop: status={status} data={'None' if not data else 'err'}", flush=True)
            if status == 453 or (data and any(e.get("code") == 453 for e in data.get("errors", []))):
                print("*** COOKIE EXPIRED (453) — 需刷新 cookie ***", flush=True)
            break

        tweets, cursor = extract_tweets(data)
        new = 0
        for t in tweets:
            if t["id"] not in seen_ids:
                seen_ids.add(t["id"])
                all_tweets.append(t)
                new += 1
        print(f"  page {page+1}: +{new} (total: {len(all_tweets)})", flush=True)
        save()  # 每页增量保存，崩溃也不丢

        if not cursor or new == 0:
            print("No more tweets.", flush=True)
            break
        time.sleep(2)

    return all_tweets

def main():
    uid = get_user_id(TARGET_SCREEN_NAME)
    if not uid:
        sys.exit(1)
    print(f"\nFetching tweets for user {uid}...", flush=True)
    tweets = fetch_user_tweets(uid)
    out = f"data/tweets_cookie_{TARGET_SCREEN_NAME}.json"
    with open(out, "w") as f:
        json.dump(tweets, f, ensure_ascii=False, indent=2)
    print(f"\n✓ Done! {len(tweets)} tweets saved to {out}", flush=True)


# X_COOKIE_REFRESH:
# Cookies expire ~7 days. When you see "code 453" errors, re-grab from
# browser: x.com (logged in) → F12 → Application → Cookies → copy
# auth_token and ct0 values, paste into AUTH_TOKEN / CT0 above.

if __name__ == "__main__":
    main()
