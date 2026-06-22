"""
Browserbase 云端浏览器 X 推文抓取

基于 Browserbase (browserbase.com) 的云端无头浏览器，
从美国 IP 访问 X.com，绕过本地网络限制。

使用方式:
    python scraper/scrape_browserbase.py                     # 抓取默认用户
    python scraper/scrape_browserbase.py aleaborteddit       # 指定用户
    python scraper/scrape_browserbase.py aleaborteddit 30    # 指定滚动次数

环境变量:
    BROWSERBASE_API_KEY      Browserbase API 密钥
    BROWSERBASE_PROJECT_ID   Browserbase 项目 ID
"""

import os
import sys
import json
import time
import random
from datetime import datetime
from pathlib import Path
from typing import Optional

# 延迟导入 browserbase/playwright — 只在实际调用时加载
# 避免未安装时的 import 错误


class BrowserbaseScraper:
    """
    云端浏览器 X 推文抓取器

    原理:
      1. 通过 Browserbase API 在云端启动一个 Chromium 实例
      2. 通过 Playwright CDP 协议远程控制
      3. 模拟人类滚动行为，逐批提取推文
      4. 写入 data/latest_tweets.json

    优点:
      - 美国 IP，无视 GFW
      - 无需本地 Chromium / agent-browser
      - 与现有 PlaywrightScraper 输出格式完全兼容
    """

    # DOM 选择器（与 extract_tweets.js 保持一致）
    TWEET_SEL   = '[data-testid="tweet"]'
    TEXT_SEL    = '[data-testid="tweetText"]'
    TIME_SEL    = 'time'
    LINK_SEL    = 'a[href*="/status/"]'

    MAX_SCROLLS  = 20
    STALL_LIMIT  = 3
    SCROLL_WAIT_MIN = 2
    SCROLL_WAIT_MAX = 4

    def __init__(
        self,
        api_key: Optional[str] = None,
        project_id: Optional[str] = None,
    ):
        self.api_key = api_key or os.getenv("BROWSERBASE_API_KEY", "")
        self.project_id = project_id or os.getenv("BROWSERBASE_PROJECT_ID", "")

    def scrape(
        self,
        username: str = "aleaborteddit",
        max_scrolls: int = MAX_SCROLLS,
    ) -> list[dict]:
        """
        抓取指定用户的推文。

        Returns:
            [{"id": "...", "text": "...", "created_at": "..."}, ...]
            与 data/latest_tweets.json 格式一致。
        """
        if not self.api_key or not self.project_id:
            raise RuntimeError(
                "BROWSERBASE_API_KEY 和 BROWSERBASE_PROJECT_ID 环境变量未设置。\n"
                "请去 https://browserbase.com 注册获取。"
            )

        try:
            from browserbase import Browserbase
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise RuntimeError(
                "请先安装依赖:\n"
                "  pip install browserbase playwright\n"
                "  playwright install chromium"
            )

        bb = Browserbase(api_key=self.api_key)

        # Step 1: 创建云端浏览器会话
        print(f"[Browserbase] 创建云端浏览器会话...")
        session = bb.sessions.create(
            project_id=self.project_id,
            # 可以指定 region: "us-east-1" 等
        )
        session_id = session.id
        print(f"[Browserbase] 会话: {session_id}")
        print(f"[Browserbase] 实时查看: https://browserbase.com/sessions/{session_id}")

        tweets = []
        try:
            with sync_playwright() as p:
                # Step 2: 通过 CDP 连接到云端浏览器
                browser = p.chromium.connect_over_cdp(session.connect_url)
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                page = context.new_page() if not context.pages else context.pages[0]

                # Step 3: 导航到 X 主页
                url = f"https://x.com/{username}"
                print(f"[Browserbase] 导航到 {url}")
                page.goto(url, wait_until="domcontentloaded", timeout=30000)

                # 等待首屏推文加载
                page.wait_for_selector(self.TWEET_SEL, timeout=15000)
                print(f"[Browserbase] 首屏已加载，开始滚动...")

                # Step 4: 模拟人类滚动，提取推文
                seen_ids = set()
                stall_count = 0
                scroll_no = 0

                while scroll_no < max_scrolls:
                    scroll_no += 1

                    # 提取当前页面上的推文
                    batch = self._extract_tweets(page)
                    new_ids = [t["id"] for t in batch if t["id"] not in seen_ids]
                    new_count = len(new_ids)

                    for t in batch:
                        if t["id"] not in seen_ids:
                            seen_ids.add(t["id"])
                            tweets.append(t)

                    print(f"  [{scroll_no:2d}/{max_scrolls}] +{new_count} 新推文  (累计 {len(tweets)})")

                    if new_count == 0:
                        stall_count += 1
                        if stall_count >= self.STALL_LIMIT:
                            print(f"[Browserbase] 连续 {self.STALL_LIMIT} 次无新推文，停止滚动")
                            break
                    else:
                        stall_count = 0

                    # 人类化滚动
                    dist = random.randint(400, 1200)
                    page.evaluate(f"window.scrollBy(0, {dist})")
                    wait = self.SCROLL_WAIT_MIN + random.random() * (self.SCROLL_WAIT_MAX - self.SCROLL_WAIT_MIN)
                    time.sleep(wait)

        finally:
            # Step 5: 关闭云端会话
            print(f"[Browserbase] 关闭会话 {session_id}")
            try:
                bb.sessions.update(session_id, status="COMPLETED")
            except Exception:
                pass  # 会话可能已自动过期

        # 按时间排序（最新的在前）
        tweets.sort(key=lambda t: t.get("created_at", ""), reverse=True)
        return tweets

    def _extract_tweets(self, page) -> list[dict]:
        """从当前页面提取推文列表"""
        els = page.query_selector_all(self.TWEET_SEL)
        results = []
        for el in els:
            try:
                text_el = el.query_selector(self.TEXT_SEL)
                time_el = el.query_selector(self.TIME_SEL)
                link_el = el.query_selector(self.LINK_SEL)

                text = text_el.inner_text() if text_el else ""
                time_str = time_el.get_attribute("datetime") if time_el else ""
                href = link_el.get_attribute("href") if link_el else ""
                tweet_id = ""
                if "/status/" in href:
                    tweet_id = href.split("/status/")[1].split("?")[0].split("#")[0]

                results.append({
                    "id": tweet_id,
                    "text": text.strip(),
                    "created_at": time_str,
                })
            except Exception:
                continue
        return results

    @staticmethod
    def save(tweets: list[dict], output_path: Optional[Path] = None) -> Path:
        """保存推文到 JSON 文件"""
        if output_path is None:
            from config import DATA_DIR
            output_path = DATA_DIR / "latest_tweets.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(tweets, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return output_path


# ── CLI 入口 ──────────────────────────────────────────

def main():
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import config

    username = sys.argv[1] if len(sys.argv) > 1 else "aleaborteddit"
    max_scrolls = int(sys.argv[2]) if len(sys.argv) > 2 else BrowserbaseScraper.MAX_SCROLLS

    scraper = BrowserbaseScraper(
        api_key=os.getenv("BROWSERBASE_API_KEY", ""),
        project_id=os.getenv("BROWSERBASE_PROJECT_ID", ""),
    )

    print(f"[Browserbase CLI] 目标: @{username}, 最多滚动 {max_scrolls} 次")
    start = time.time()

    tweets = scraper.scrape(username=username, max_scrolls=max_scrolls)
    elapsed = time.time() - start

    path = scraper.save(tweets)
    print(f"\n[Browserbase CLI] 完成!")
    print(f"  推文: {len(tweets)} 条")
    print(f"  耗时: {elapsed:.0f}s")
    print(f"  保存: {path}")


if __name__ == "__main__":
    main()
