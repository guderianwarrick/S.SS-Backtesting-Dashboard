"""
统一推文抓取入口 — 本地优先，云端备用

优先级:
  1. scrape_x.sh (agent-browser 本地 Chromium)
  2. Browserbase 云端浏览器 (美国 IP，无需代理)

用法:
    python scraper/scrape.py                        # 默认用户
    python scraper/scrape.py aleaborteddit          # 指定用户
    python scraper/scrape.py aleaborteddit 30       # 指定滚动次数
"""

import sys
import os
import subprocess
import json
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config


def try_local(username: str) -> bool:
    """尝试用本地 agent-browser 抓取"""
    scrape_script = Path(__file__).parent / "scrape_x.sh"
    if not scrape_script.exists():
        print("[unified] scrape_x.sh 不存在，跳过本地尝试")
        return False

    print("[unified] 尝试本地抓取 (agent-browser)...")
    try:
        result = subprocess.run(
            ["bash", str(scrape_script)],
            env={**os.environ, "USERNAME": username},
            capture_output=True,
            text=True,
            timeout=180,
            cwd=str(config.BASE_DIR),
        )
        if result.returncode == 0:
            # 检查产出
            output = config.DATA_DIR / "latest_tweets.json"
            if output.exists():
                data = json.loads(output.read_text(encoding="utf-8"))
                if data:
                    print(f"[unified] 本地抓取成功: {len(data)} 条推文")
                    return True
        print(f"[unified] 本地抓取失败: exit={result.returncode}")
        if result.stderr:
            print(f"  stderr: {result.stderr[:200]}")
    except subprocess.TimeoutExpired:
        print("[unified] 本地抓取超时")
    except Exception as e:
        print(f"[unified] 本地抓取异常: {e}")

    return False


def try_browserbase(username: str, max_scrolls: int = 20) -> bool:
    """备用：Browserbase 云端抓取"""
    api_key = os.getenv("BROWSERBASE_API_KEY") or config.BROWSERBASE_API_KEY
    project_id = os.getenv("BROWSERBASE_PROJECT_ID") or config.BROWSERBASE_PROJECT_ID

    if not api_key or not project_id:
        print("[unified] Browserbase 凭证未配置，跳过云端尝试")
        return False

    print("[unified] 启动 Browserbase 云端抓取...")
    try:
        from scraper.browserbase_scraper import BrowserbaseScraper

        scraper = BrowserbaseScraper(api_key=api_key, project_id=project_id)
        tweets = scraper.scrape(username=username, max_scrolls=max_scrolls)

        if tweets:
            scraper.save(tweets)
            print(f"[unified] Browserbase 抓取成功: {len(tweets)} 条推文")
            return True
        else:
            print("[unified] Browserbase 未获取到推文")
    except Exception as e:
        print(f"[unified] Browserbase 异常: {e}")

    return False


def main():
    username = sys.argv[1] if len(sys.argv) > 1 else "aleaborteddit"
    max_scrolls = int(sys.argv[2]) if len(sys.argv) > 2 else 20

    print(f"{'='*55}")
    print(f"  统一推文抓取 — @{username}")
    print(f"  {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*55}")

    # 1. 本地
    if try_local(username):
        print("\n[unified] 本地抓取完成 ✓")
        return

    # 2. 云端备用
    print("\n[unified] 本地抓取未成功，启用 Browserbase 云端备用...")
    if try_browserbase(username, max_scrolls):
        print("\n[unified] 云端抓取完成 ✓")
        return

    print("\n[unified] 所有抓取方式均失败 ✗")
    sys.exit(1)


if __name__ == "__main__":
    main()
