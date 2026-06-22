"""Pipeline 步骤：从推文提取股票代码。

用法: python3 steps/step2_extract_tickers.py

功能:
  - 读取 tweets 表
  - 用 StockExtractor 提取每条推文中的股票代码
  - 写入 tweet_tickers 表
  - 输出统计信息
"""
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger
from storage.models import init_db, session_scope, Tweet, TweetTicker
from parser.stock_ner import StockExtractor


def main():
    logger.info("=== Step 2: 提取股票代码 ===")
    
    # 初始化数据库
    init_db()
    
    # 清空旧数据
    with session_scope() as session:
        old_count = session.query(TweetTicker).count()
        session.query(TweetTicker).delete()
        logger.info(f"清空旧数据: {old_count} 条")
    
    # 读取推文
    with session_scope() as session:
        tweets = session.query(Tweet).all()
        tweets_data = [(t.id, t.text) for t in tweets]
    
    logger.info(f"读取 {len(tweets_data)} 条推文")
    
    # 提取股票代码
    extractor = StockExtractor()
    
    with session_scope() as session:
        for i, (tweet_id, text) in enumerate(tweets_data, 1):
            mentions = extractor.extract(text)
            
            for m in mentions:
                session.add(TweetTicker(
                    tweet_id=tweet_id,
                    symbol=m.symbol,
                    name=m.name,
                    confidence=m.confidence,
                ))
            
            if i % 1000 == 0:
                logger.info(f"进度: {i}/{len(tweets_data)}")
    
    # 统计
    with session_scope() as session:
        count = session.query(TweetTicker).count()
        unique_symbols = session.query(TweetTicker.symbol).distinct().count()
        tweets_with_tickers = session.query(TweetTicker.tweet_id).distinct().count()
        
        logger.info(f"✅ 提取完成:")
        logger.info(f"   总提及: {count} 次")
        logger.info(f"   独立股票: {unique_symbols} 只")
        logger.info(f"   有提及的推文: {tweets_with_tickers} 条 ({tweets_with_tickers/len(tweets_data)*100:.1f}%)")


if __name__ == "__main__":
    main()
