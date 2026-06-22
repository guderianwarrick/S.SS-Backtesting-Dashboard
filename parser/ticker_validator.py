"""Ticker 有效性验证 — 过滤噪声，只保留真实股票代码。"""
import re
from typing import Optional

# ── 已知非股票代码黑名单 ─────────────────────────
# 这些是推文中提取的常见假 ticker：通用词、品牌名、缩写、已退市公司等
NON_STOCK_BLACKLIST: set[str] = {
    # 通用词/缩写
    "X", "FI", "AL", "BOA", "CITI", "AWS", "BLSKY", "FTX",
    "RUT", "ZW", "ZEC", "XLS", "XUSS", "XBOT", "WLFI",
    "VVST", "VNP", "VLH", "VGP", "VGO", "UHR",
    "SMHSF", "SMHN", "SMHMD", "SKC", "SIV",
    "SGCG", "QBUT", "PDY", "ONET", "NTI", "NORBT",
    "NIDGY", "MVL", "MTL", "MKA", "LVMH", "LPKK",
    "LCRX", "KOSPI", "HXSCL", "HALEU", "GRZ",
    "GOGL", "FIT", "EXA", "ELOSE", "DRFT", "DNKG",
    "DLFI", "DGDX", "CXV", "CSPH", "CRCLQ", "CNEX",
    "CCCX", "ASMC", "ALCJ", "ACUVI", "ABB",
    "TPU", "PLSR", "ETORO", "CRBS", "AMSL", "ALPD",
    "SYSS", "PSTG", "ASE", "AXT", "CREDO", "DOWA",
    "ASHM", "TOWA", "QLCM", "APPL",
    "ALRIB", "SOI", "LPK", "RPI",
    # 非股票专有名词
    "CEO", "CFO", "CTO", "COO", "API", "AI", "ML",
    "IPO", "NFT", "DAO", "DEFI", "USD", "EUR", "GBP",
    "DM", "PM", "AMA", "FYI", "IMO", "LOL", "IDK",
    "ETF", "SPX", "NDX", "VIX",
    # 已退市/不存在的
    "FTX", "CRCLQ",
}

# 合法 ticker 格式：1~6 个大写字母，可选 . 或 - 后缀
VALID_TICKER_RE = re.compile(r"^[A-Z]{1,6}(\.[A-Z]{1,2}|-[A-Z])?$")

# 常见英文单词（短于 4 字母，容易被误认为 ticker）
COMMON_WORDS: set[str] = {
    "A", "I", "IN", "AT", "BY", "AS", "IS", "IT",
    "OR", "AN", "WE", "HE", "SHE", "MY", "NO", "GO",
    "UP", "DO", "SO", "IF", "OF", "FOR", "AND", "THE", "ARE",
    "BUT", "NOT", "ALL", "CAN", "HAS", "HAD", "WAS", "WERE",
    "GET", "GOT", "SEE", "SAY", "WAY", "USE", "NEW", "OLD",
    "BIG", "LOW", "TOP", "OUT", "NOW", "HOW", "WHO", "WHY",
    "MAN", "MEN", "DAY", "YEAR", "WEEK", "MONTH",
    "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN",
    "EIGHT", "NINE", "TEN",
    "RED", "BLUE", "GREEN", "BLACK", "WHITE",
    "HOT", "COLD", "BEST", "MORE", "LESS",
    "FAST", "SLOW", "HARD", "SOFT", "TRUE", "FALSE", "GOOD",
    "BAD", "RICH", "POOR", "FULL", "EMPTY",
    "LUCK", "FUN", "FREE", "SALE", "CASH", "DEBT",
    "LOSS", "GAIN", "COST",
}


def is_valid_ticker(symbol: str, strict: bool = True) -> bool:
    """判断符号是否为有效的股票代码。
    
    Args:
        symbol: 待验证的股票代码
        strict: 严格模式（默认 True），额外检查黑名单和常见词
    
    Returns:
        True 如果是有效的股票代码
    """
    if not symbol or not isinstance(symbol, str):
        return False
    
    sym = symbol.strip().upper()
    if not sym:
        return False
    
    # 黑名单检查
    if sym in NON_STOCK_BLACKLIST:
        return False
    
    # 常见英文单词检查（仅对短 ticker，避免误杀 V、F 等真实代码）
    if len(sym) <= 3 and sym in COMMON_WORDS:
        return False
    
    # 格式校验
    if not VALID_TICKER_RE.match(sym):
        return False
    
    return True


def cleanup_database(dry_run: bool = False) -> dict:
    """清理 StockMention 表中的无效 ticker。
    
    Args:
        dry_run: True 则只统计不删除
    
    Returns:
        统计信息 dict
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    
    from storage.models import init_db, session_scope, StockMention, TweetTicker
    from sqlalchemy import func
    
    init_db()
    
    stats = {"total": 0, "invalid": 0, "removed": 0}
    
    with session_scope() as s:
        # 统计所有独立 symbol
        all_symbols = [r[0] for r in s.query(StockMention.symbol).distinct().all()]
        stats["total"] = len(all_symbols)
        
        # 找出无效的
        invalid = [sym for sym in all_symbols if not is_valid_ticker(sym)]
        stats["invalid"] = len(invalid)
        
        print(f"StockMention 表: {stats['total']} 个独立 ticker")
        print(f"其中无效: {stats['invalid']} 个")
        
        if invalid:
            print(f"\n无效 ticker 列表:")
            with session_scope() as s2:
                for sym in sorted(invalid):
                    cnt = s2.query(func.count(StockMention.id)).filter(StockMention.symbol == sym).scalar()
                    print(f"  {sym:10s}  {cnt} 条记录")
        
        if not dry_run and invalid:
            for sym in invalid:
                deleted = s.query(StockMention).filter(StockMention.symbol == sym).delete()
                stats["removed"] += deleted
            s.commit()
            
            # 也清理 TweetTicker
            for sym in invalid:
                s.query(TweetTicker).filter(TweetTicker.symbol == sym).delete()
            s.commit()
            
            print(f"\n已删除: {stats['removed']} 条记录")
        elif dry_run:
            print(f"\n(dry_run 模式，未实际删除)")
    
    return stats