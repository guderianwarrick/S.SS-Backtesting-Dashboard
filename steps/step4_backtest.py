"""Pipeline 步骤：回测。

用法: python3 steps/step4_backtest.py

功能:
  - 读取 stock_mentions 表（情绪分析结果）
  - 预热价格缓存（优先 AV，用完换 Yahoo）
  - 通过 PortfolioEngine 运行回测
  - 写入 rebalance_events 表
  - 输出回测结果
"""
import sys
from pathlib import Path
from datetime import date, timedelta

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger
from storage.models import init_db, session_scope, StockMention
import config as project_config
from portfolio.engine import PortfolioEngine
from portfolio.price_fetcher import PriceFetcher


def main():
    logger.info("=== Step 4: 回测 ===")
    
    # 初始化数据库
    init_db()
    
    # 获取情绪数据的时间范围和统计
    with session_scope() as session:
        total = session.query(StockMention).count()
        if total == 0:
            logger.warning("⚠️ 无情绪数据，无法回测")
            return
        
        symbols = session.query(StockMention.symbol).distinct().all()
        symbols = [s[0] for s in symbols]
    
    logger.info(f"情绪数据: {total} 条")
    logger.info(f"独立股票: {len(symbols)} 只")
    
    # 价格缓存（用已有的，不联网拉取）
    logger.info(f"使用已有价格缓存: {len(symbols)} 只股票")
    price_fetcher = PriceFetcher()
    
    from datetime import date as d
    from_date = d.fromisoformat("2025-07-01")
    
    # 运行回测
    logger.info("运行回测引擎...")
    engine = PortfolioEngine(
        username="aleabitoreddit",
        author_id="aleabitoreddit",
        initial_cash=project_config.PORTFOLIO_INITIAL_CASH,
        min_symbols=20,
    )
    
    result = engine.backtest(
        from_date=from_date,
        to_date=d.today(),
    )
    
    # 输出结果
    logger.info(f"✅ 回测完成:")
    logger.info(f"   初始资金: ${project_config.PORTFOLIO_INITIAL_CASH:,.2f}")
    logger.info(f"   最终价值: ${result.get('final_value', 0):,.2f}")
    logger.info(f"   累计收益: {result.get('cumulative_return', 0):.2%}")
    logger.info(f"   调仓次数: {result.get('total_events', 0)}")
    logger.info(f"   涉及股票: {result.get('total_symbols', 0)}")
    
    if result.get("note"):
        logger.warning(f"   注意: {result['note']}")


if __name__ == "__main__":
    main()