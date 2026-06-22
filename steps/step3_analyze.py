"""Pipeline 步骤：情绪分析（两阶段）。

用法: python3 steps/step3_analyze.py [--phase 1|2|all]

功能:
  阶段一: FinBERT 全量分析（本地，快）
    - 读取 tweets + tweet_tickers
    - FinBERT 打分 + 规则层反讽检测
    - 写入 stock_mentions (method=finbert, needs_llm_review=True/False)
  
  阶段二: LLM 二审（远程 API，慢）
    - 读取 needs_llm_review=True 的记录
    - 调用 LLM 复审
    - 更新 stock_mentions (method=llm_review, needs_llm_review=False)

数据流:
  tweets + tweet_tickers → FinBERT → stock_mentions (method=finbert)
  stock_mentions (needs_llm_review=True) → LLM → stock_mentions (method=llm_review)

支持断点续传（已分析的跳过）
"""
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger
from storage.models import init_db, session_scope, Tweet, TweetTicker, StockMention
from ai.finbert_analyzer import FinBERTAnalyzer
from ai.sarcasm_detector import detect as detect_sarcasm
from parser.ticker_validator import is_valid_ticker


def phase1_finbert():
    """阶段一：FinBERT 全量分析"""
    logger.info("=== 阶段一：FinBERT 全量分析 ===")
    
    finbert = FinBERTAnalyzer()
    
    # 读取数据
    with session_scope() as session:
        ticker_data = [
            (t.tweet_id, t.symbol, t.name) 
            for t in session.query(TweetTicker).all()
        ]
        analyzed = set(
            (m.tweet_id, m.symbol) 
            for m in session.query(StockMention.tweet_id, StockMention.symbol).all()
        )
        tweets = {t.id: t.text for t in session.query(Tweet).all()}
    
    logger.info(f"总提及: {len(ticker_data)} 次")
    logger.info(f"已分析: {len(analyzed)} 次")
    logger.info(f"待分析: {len(ticker_data) - len(analyzed)} 次")
    
    # 分析
    stats = {"total": 0, "skipped": 0, "analyzed": 0, "need_llm": 0}
    batch_size = 100
    batch = []
    
    for i, (tweet_id, symbol, name) in enumerate(ticker_data, 1):
        stats["total"] += 1
        
        # 跳过已分析的
        if (tweet_id, symbol) in analyzed:
            stats["skipped"] += 1
            continue
        
        # 跳过无效 ticker（噪声过滤）
        if not is_valid_ticker(symbol):
            stats["skipped"] += 1
            continue
        
        # 获取推文文本
        text = tweets.get(tweet_id)
        if not text:
            logger.warning(f"找不到推文 {tweet_id}")
            continue
        
        # FinBERT 分析
        result = finbert.analyze(text, symbol)
        
        # 检查是否需要 LLM 二审
        sig = detect_sarcasm(text)
        need_review = False
        
        if sig and sig.hit:
            need_review = True
        elif abs(result.score) >= 0.30 and result.confidence < 0.55:
            need_review = True
        
        if need_review:
            stats["need_llm"] += 1
        
        # 构建 reason
        reason = (
            f"FinTwitBERT({result.device}) "
            + " ".join(f"{k}={v:.3f}" for k, v in result.prob_dist.items())
        )
        
        # 添加到批次
        batch.append(StockMention(
            tweet_id=tweet_id,
            symbol=symbol,
            name=name,
            sentiment_score=result.score,
            sentiment_label=result.label,
            llm_reason=reason,
            method="finbert",
            needs_llm_review=need_review,
        ))
        
        stats["analyzed"] += 1
        
        # 每 100 条输出进度
        if i % 100 == 0:
            logger.info(
                f"进度: {i}/{len(ticker_data)} | "
                f"已分析: {stats['analyzed']} | "
                f"待二审: {stats['need_llm']} | "
                f"批次: {len(batch)}"
            )
        
        # 批量提交
        if len(batch) >= batch_size:
            with session_scope() as session:
                session.add_all(batch)
            batch = []
            
            logger.info(
                f"✅ 提交批次 | 总进度: {i}/{len(ticker_data)} | "
                f"已分析: {stats['analyzed']} | "
                f"待二审: {stats['need_llm']}"
            )
    
    # 提交剩余的
    if batch:
        with session_scope() as session:
            session.add_all(batch)
    
    logger.info(f"✅ 阶段一完成:")
    logger.info(f"   总提及: {stats['total']} 次")
    logger.info(f"   跳过: {stats['skipped']} 次")
    logger.info(f"   新分析: {stats['analyzed']} 次")
    logger.info(f"   待二审: {stats['need_llm']} 次")


def phase2_llm_review():
    """阶段二：LLM 二审（批量模式 — 一次 prompt 100 条）"""
    logger.info("=== 阶段二：LLM 二审（批量模式） ===")
    
    from ai.llm_reviewer import LLMReviewer
    
    reviewer = LLMReviewer()
    
    # 读取需要二审的
    with session_scope() as session:
        needs_review = [
            (m.id, m.tweet_id, m.symbol, m.name, m.sentiment_score, m.sentiment_label, m.llm_reason)
            for m in session.query(StockMention).filter(
                StockMention.needs_llm_review == True
            ).all()
        ]
        tweets = {t.id: t.text for t in session.query(Tweet).all()}
    
    logger.info(f"需要二审: {len(needs_review)} 次")
    
    if not needs_review:
        logger.info("无需二审，跳过")
        return
    
    stats = {"total": len(needs_review), "reviewed": 0, "flipped": 0, "failed": 0}
    batch_size = 50  # 每批 50 条
    updates = []
    
    # 分批处理，每批 50 条调用一次 LLM
    for chunk_start in range(0, len(needs_review), batch_size):
        chunk = needs_review[chunk_start:chunk_start + batch_size]
        
        # 构造批量输入
        items = []
        for mention_id, tweet_id, symbol, name, score, label, reason in chunk:
            text = tweets.get(tweet_id)
            if not text:
                continue
            sig = detect_sarcasm(text)
            label_map = {"positive": "bullish", "negative": "bearish", "neutral": "neutral"}
            items.append({
                "id": mention_id,
                "text": text,
                "symbol": symbol,
                "finbert_label": label_map.get(label, label),
                "sarcasm_reasons": sig.reasons if sig else None,
            })
        
        if not items:
            continue
        
        # 批量调用 LLM
        try:
            batch_results = reviewer.batch_review(items)
        except Exception as e:
            logger.warning("批量调用失败 ({}), 这批次全部保留 FinBERT 原判", e)
            batch_results = {}
        
        # 处理结果
        for mention_id, tweet_id, symbol, name, score, label, reason in chunk:
            if mention_id not in batch_results:
                # LLM 没返回这个条目 → 保留 FinBERT
                updates.append({
                    "id": mention_id,
                    "method": "finbert",
                    "needs_llm_review": False,
                    "llm_reason": reason + " [二审超时]",
                })
                stats["failed"] += 1
                continue
            
            reviewed = batch_results[mention_id]
            if reviewed is None:
                stats["failed"] += 1
                updates.append({
                    "id": mention_id,
                    "method": "finbert",
                    "needs_llm_review": False,
                    "llm_reason": reason + " [二审失败]",
                })
            else:
                stats["reviewed"] += 1
                label_map_rev = {"bullish": "positive", "bearish": "negative", "neutral": "neutral"}
                new_label = label_map_rev.get(reviewed.label, reviewed.label)
                flipped = (new_label != label)
                if flipped:
                    stats["flipped"] += 1
                    logger.info(f"情绪翻转 {symbol} {label}→{new_label}")
                
                new_reason = (
                    f"LLM二审 "
                    f"{'翻转' if flipped else '确认'} "
                    + " ".join(f"{k}={v:.3f}" for k, v in reviewed.prob_dist.items())
                    + f" | {reviewed.reason}"
                )
                updates.append({
                    "id": mention_id,
                    "method": "llm_review",
                    "needs_llm_review": False,
                    "sentiment_score": reviewed.score,
                    "sentiment_label": new_label,
                    "llm_reason": new_reason,
                })
        
        # 提交本批次结果到数据库
        with session_scope() as session:
            for item in updates[-len(chunk):]:
                mid = item.pop("id")
                session.query(StockMention).filter(
                    StockMention.id == mid
                ).update(item)
        
        logger.info(
            f"✅ 批次完成 | 总进度: {chunk_start + len(chunk)}/{len(needs_review)} | "
            f"已二审: {stats['reviewed']} | "
            f"翻转: {stats['flipped']} | "
            f"失败: {stats['failed']}"
        )
    
    logger.info(f"✅ 阶段二完成:")
    logger.info(f"   总需二审: {stats['total']} 次")
    logger.info(f"   已二审: {stats['reviewed']} 次")
    logger.info(f"   翻转: {stats['flipped']} 次")
    logger.info(f"   失败: {stats['failed']} 次")


def main():
    # 解析命令行参数
    phase = "all"
    if "--phase" in sys.argv:
        idx = sys.argv.index("--phase")
        if idx + 1 < len(sys.argv):
            phase = sys.argv[idx + 1]
    
    logger.info(f"=== Step 3: 情绪分析（两阶段） | phase={phase} ===")
    
    # 初始化数据库
    init_db()
    
    # 阶段一：FinBERT
    if phase in ("1", "all"):
        phase1_finbert()
    
    # 阶段二：LLM 二审
    if phase in ("2", "all"):
        phase2_llm_review()
    
    logger.info("✅ 全部完成")


if __name__ == "__main__":
    main()
