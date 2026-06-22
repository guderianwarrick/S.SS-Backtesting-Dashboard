"""Pipeline 步骤：导入推文到数据库。

用法: python3 steps/step1_import.py [tweets_file]
默认: data/tweets_clean.json

功能:
  - 读取 JSON 推文文件
  - 清空 tweets 表（避免重复）
  - 写入 tweets 表
  - 输出统计信息
"""
import json
import sys
from pathlib import Path
from datetime import datetime, timezone

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger
from storage.models import init_db, session_scope, Tweet


def parse_time(created: str) -> datetime:
    """解析时间字符串（支持多种格式）。"""
    if not created:
        return datetime.now(timezone.utc)
    
    # ISO 格式
    if "T" in created and "-" in created.split("T")[0]:
        try:
            return datetime.fromisoformat(created.replace("Z", "+00:00"))
        except:
            pass
    
    # Twitter 格式: Wed Jun 17 15:46:21 +0000 2026
    for fmt in ("%a %b %d %H:%M:%S %z %Y", "%a %b %d %H:%M:%S +0000 %Y"):
        try:
            return datetime.strptime(created.strip(), fmt)
        except ValueError:
            continue
    
    return datetime.now(timezone.utc)


def main():
    tweets_file = sys.argv[1] if len(sys.argv) > 1 else "data/tweets_clean.json"
    logger.info(f"=== Step 1: 导入推文 ({tweets_file}) ===")
    
    # 初始化数据库
    init_db()
    
    # 读取推文
    tweets = json.load(open(tweets_file, encoding="utf-8"))
    logger.info(f"读取 {len(tweets)} 条推文")
    
    # 清空旧数据
    with session_scope() as session:
        old_count = session.query(Tweet).count()
        session.query(Tweet).delete()
        logger.info(f"清空旧数据: {old_count} 条")
    
    # 写入新数据
    with session_scope() as session:
        for t in tweets:
            session.add(Tweet(
                id=str(t["id"]),
                text=t["text"],
                created_at=parse_time(t.get("created_at", "")),
                author_id="aleabitoreddit",
                retweet_count=t.get("retweet_count", 0) or t.get("retweets", 0),
                like_count=t.get("like_count", 0) or t.get("favorites", 0),
            ))
    
    # 统计
    with session_scope() as session:
        count = session.query(Tweet).count()
        dates = session.query(Tweet.created_at).order_by(Tweet.created_at).all()
        if dates:
            logger.info(f"✅ 导入完成: {count} 条推文")
            logger.info(f"   时间范围: {dates[0][0].strftime('%Y-%m-%d')} ~ {dates[-1][0].strftime('%Y-%m-%d')}")
        else:
            logger.warning("⚠️ 导入完成但无数据")


if __name__ == "__main__":
    main()
