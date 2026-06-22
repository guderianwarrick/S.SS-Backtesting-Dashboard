"""Pipeline 主控制器 — 协调所有步骤。

用法:
  python3 pipeline.py              # 运行完整 pipeline
  python3 pipeline.py --status     # 查看当前状态
  python3 pipeline.py --step 1     # 只运行步骤 1
  python3 pipeline.py --step 1,2   # 运行步骤 1 和 2
  python3 pipeline.py --reset      # 清空所有数据
"""
import sys
import argparse
from loguru import logger
from storage.models import init_db, session_scope, Tweet, TweetTicker, StockMention, RebalanceEvent


def show_status():
    """显示 pipeline 当前状态。"""
    init_db()
    
    with session_scope() as session:
        tweet_count = session.query(Tweet).count()
        ticker_count = session.query(TweetTicker).count()
        mention_count = session.query(StockMention).count()
        event_count = session.query(RebalanceEvent).count()
    
    logger.info("=== Pipeline 状态 ===")
    logger.info(f"Step 1 - 推文导入:     {tweet_count:>6} 条")
    logger.info(f"Step 2 - 代码提取:     {ticker_count:>6} 次")
    logger.info(f"Step 3 - 情绪分析:     {mention_count:>6} 次")
    logger.info(f"Step 4 - 回测结果:     {event_count:>6} 条")
    
    if tweet_count == 0:
        logger.warning("⚠️ 请先运行 Step 1: python3 steps/step1_import.py")
    elif ticker_count == 0:
        logger.warning("⚠️ 请运行 Step 2: python3 steps/step2_extract_tickers.py")
    elif mention_count == 0:
        logger.warning("⚠️ 请运行 Step 3: python3 steps/step3_analyze.py")
    elif event_count == 0:
        logger.warning("⚠️ 请运行 Step 4: python3 steps/step4_backtest.py")
    else:
        logger.info("✅ Pipeline 完整")


def reset():
    """清空所有数据。"""
    init_db()
    
    with session_scope() as session:
        session.query(RebalanceEvent).delete()
        session.query(StockMention).delete()
        session.query(TweetTicker).delete()
        session.query(Tweet).delete()
    
    logger.info("✅ 已清空所有数据")


def run_pipeline(steps=None):
    """运行 pipeline。"""
    import subprocess
    
    all_steps = [
        ("steps/step1_import.py", "导入推文"),
        ("steps/step2_extract_tickers.py", "提取股票代码"),
        ("steps/step3_analyze.py", "情绪分析"),
        ("steps/step4_backtest.py", "回测"),
    ]
    
    if steps is None:
        steps_to_run = all_steps
    else:
        steps_to_run = [all_steps[i-1] for i in steps if i <= len(all_steps)]
    
    for script, desc in steps_to_run:
        logger.info(f"\n{'='*60}")
        logger.info(f"运行: {desc}")
        logger.info(f"{'='*60}\n")
        
        result = subprocess.run(
            [sys.executable, script],
            cwd="."
        )
        
        if result.returncode != 0:
            logger.error(f"❌ 步骤失败: {desc}")
            return False
    
    logger.info("\n✅ Pipeline 完成!")
    show_status()
    return True


def main():
    parser = argparse.ArgumentParser(description="Stock Sentiment Pipeline")
    parser.add_argument("--status", action="store_true", help="查看当前状态")
    parser.add_argument("--reset", action="store_true", help="清空所有数据")
    parser.add_argument("--step", type=str, help="运行指定步骤 (如: 1,2,3)")
    
    args = parser.parse_args()
    
    if args.status:
        show_status()
    elif args.reset:
        reset()
    elif args.step:
        steps = [int(s.strip()) for s in args.step.split(",")]
        run_pipeline(steps)
    else:
        run_pipeline()


if __name__ == "__main__":
    main()
