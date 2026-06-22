"""X API v2 客户端 — 获取用户推文"""
import httpx
from datetime import datetime, timezone
from typing import Optional
from loguru import logger
import config

class XAPIClient:
    """X (Twitter) API v2 只读客户端"""

    def __init__(self):
        self.headers = {
            "Authorization": f"Bearer {config.X_BEARER_TOKEN}",
            "User-Agent": config.X_USER_AGENT,
        }
        # 配置代理
        client_kwargs = {
            "base_url": config.X_API_BASE,
            "headers": self.headers,
            "timeout": 30.0,
        }
        if config.HTTPS_PROXY:
            client_kwargs["proxy"] = config.HTTPS_PROXY
            logger.info("Using proxy: {}", config.HTTPS_PROXY)
        self.client = httpx.Client(**client_kwargs)

    # ── 用户 ID 查询 ──────────────────────────────
    def get_user_id(self, username: str) -> Optional[str]:
        """根据用户名获取用户 ID（去掉 @ 符号）"""
        username = username.lstrip("@")
        params = {"usernames": username}
        resp = self.client.get("/users/by", params=params)
        resp.raise_for_status()
        data = resp.json()
        users = data.get("data", [])
        if users:
            uid = users[0]["id"]
            logger.info("Resolved user '@{}' -> id={}", username, uid)
            return uid
        logger.warning("User '@{}' not found", username)
        return None

    # ── 获取推文 ──────────────────────────────────
    def fetch_user_tweets(
        self,
        user_id: str,
        max_results: int = 100,
        since_id: Optional[str] = None,
        end_time: Optional[datetime] = None,
        pagination_token: Optional[str] = None,
    ) -> dict:
        """
        获取指定用户的推文列表（排除回复/转发）。
        X API 返回格式见 https://developer.x.com/en/docs/x-api/tweets/timelines/api-reference/get-users-id-tweets
        """
        params: dict = {
            "tweet.fields": "created_at,public_metrics,lang,source",
            "exclude": "retweets,replies",      # 只看原创推文
            "max_results": min(max_results, config.MAX_RESULTS_PER_REQUEST),
        }
        if since_id:
            params["since_id"] = since_id
        if end_time:
            # ISO-8601 UTC
            params["end_time"] = end_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if pagination_token:
            params["pagination_token"] = pagination_token

        url = f"/users/{user_id}/tweets"
        resp = self.client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    # ── 分页拉取全部 ──────────────────────────────
    def fetch_all_tweets(
        self,
        user_id: str,
        max_tweets: int = 200,
        since_id: Optional[str] = None,
    ) -> list[dict]:
        """分页拉取用户所有符合条件的推文，返回扁平列表。"""
        tweets: list[dict] = []
        next_token: Optional[str] = None

        while len(tweets) < max_tweets:
            batch = self.fetch_user_tweets(
                user_id=user_id,
                max_results=min(100, max_tweets - len(tweets)),
                since_id=since_id,
                pagination_token=next_token,
            )
            batch_tweets = batch.get("data", [])
            if not batch_tweets:
                break

            tweets.extend(batch_tweets)
            logger.info("Fetched {} tweets (total {})", len(batch_tweets), len(tweets))

            next_token = batch.get("meta", {}).get("next_token")
            if not next_token:
                break

        return tweets

    # ── 资源释放 ──────────────────────────────────
    def close(self):
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
