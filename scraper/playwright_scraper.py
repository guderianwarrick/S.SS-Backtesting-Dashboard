"""X 推文抓取 — 通过 bash 脚本调用 agent-browser CLI。

由于沙箱限制，agent-browser 不能通过 Python subprocess 调用。
抓取通过 scrape_x.sh 脚本执行（Bash 工具），结果保存为 JSON，
Python 层直接读取 JSON 文件。

用法:
    # 1. 先运行抓取脚本: bash scraper/scrape_x.sh <username> <max_tweets>
    # 2. Python 读取结果:
    scraper = PlaywrightScraper()
    tweets = scraper.load_tweets()
"""

import json
from pathlib import Path
from loguru import logger

import config


class PlaywrightScraper:
    """从 JSON 文件加载已抓取的推文。"""

    def __init__(self):
        self._json_path = config.DATA_DIR / "latest_tweets.json"

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    @property
    def json_exists(self) -> bool:
        return self._json_path.exists()

    def load_tweets(self) -> list[dict]:
        """加载上次抓取的推文。"""
        if not self._json_path.exists():
            logger.warning("推文数据文件不存在: {}。请先运行 scraper/scrape_x.sh", self._json_path)
            return []

        try:
            with open(self._json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.error("读取推文数据失败: {}", e)
            return []

        logger.info("从 {} 加载了 {} 条推文", self._json_path.name, len(data))
        return data
