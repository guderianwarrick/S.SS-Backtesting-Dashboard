"""股票实体识别 (NER) — 从推文提取提到的股票代码/名称"""
import re
from dataclasses import dataclass
from typing import List, Dict
from loguru import logger

@dataclass
class StockMention:
    symbol: str      # 代码，如 AAPL
    name: str = ""   # 名称
    confidence: float = 1.0  # 匹配置信度（cashtag=1.0, 英文名=0.7）

class StockExtractor:
    """
    从推文文本中提取股票提及。
    支持：
      - $TICKER 格式（大小写均可，如 $AAPL, $aapl, $Tsla）
      - 常见中文美股/港股/A股简称（可扩展词典）
      - 英文公司名（预编译正则，忽略大小写）
    """

    # Cashtag 正则: $AAPL, $aapl, $BRK.A（支持大小写）
    CASHTAG_RE = re.compile(r"\$([A-Za-z]{1,5}(?:\.[A-Za-z])?)")

    # 非股票 Cashtag 黑名单（ETF / 加密货币 / 指数 / 通用词）
    STOCK_BLACKLIST: set[str] = {
        # 加密货币
        "BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX", "DOT", "LINK",
        "USDT", "USDC", "BNB", "MATIC", "UNI", "ATOM", "LTC", "XLM",
        # ETF / 指数
        "SPY", "QQQ", "IWM", "DIA", "VOO", "VTI", "ARKK", "TQQQ", "SQQQ",
        "BULL", "BEAR", "UVXY", "VIX", "VXX", "SVXY", "SPX", "NDX",
        # 基金/ETN
        "FLY", "YOLO", "FNGU", "FNGD", "SOXL", "SOXS", "TMF", "TMV",
        "LABU", "LABD", "JETS", "TAN", "ICLN", "LIT", "URA", "REMX",
        # 其他非股票标识
        "ETF", "IPO", "CEO", "CFO", "CTO", "COO", "API", "AI", "ML",
        "NFT", "DAO", "WEB3", "DEFI", "USD", "EUR", "GBP", "JPY",
        # 博主常用非股票标签
        "DM", "PM", "AMA", "FYI", "IMO", "LOL", "IDK", "TBH",
    }

    # 可扩展的中文股票词典（TODO: 从外部文件/数据库加载）
    CHINESE_STOCK_MAP: Dict[str, str] = {
        "英伟达": "NVDA",
        "特斯拉": "TSLA",
        "苹果": "AAPL",
        "谷歌": "GOOGL",
        "微软": "MSFT",
        "亚马逊": "AMZN",
        "Meta": "META",
        "脸书": "META",
        "奈飞": "NFLX",
        "拼多多": "PDD",
        "阿里": "BABA",
        "阿里巴巴": "BABA",
        "腾讯": "TCEHY",
        "台积电": "TSM",
        "AMD": "AMD",
        "英特尔": "INTC",
        "高通": "QCOM",
    }

    # 英文公司名 → Ticker（单词边界匹配，避免 "apple" 误匹配日常词）
    ENGLISH_STOCK_MAP: Dict[str, str] = {
        "Nvidia": "NVDA",
        "Apple": "AAPL",
        "Tesla": "TSLA",
        "Amazon": "AMZN",
        "Google": "GOOGL",
        "Alphabet": "GOOGL",
        "Microsoft": "MSFT",
        "Netflix": "NFLX",
        "Intel": "INTC",
        "Qualcomm": "QCOM",
        "Palantir": "PLTR",
        "GameStop": "GME",
        "Boeing": "BA",
        "Disney": "DIS",
        "NIO": "NIO",
        "Rivian": "RIVN",
        "Lucid": "LCID",
        "Uber": "UBER",
        "Coinbase": "COIN",
        "MicroStrategy": "MSTR",
        "Oracle": "ORCL",
        "Salesforce": "CRM",
        "Adobe": "ADBE",
        "IBM": "IBM",
        "Spotify": "SPOT",
        "Shopify": "SHOP",
        "Costco": "COST",
        "Walmart": "WMT",
        "BlackRock": "BLK",
        "Goldman": "GS",
        "JPMorgan": "JPM",
        "Berkshire": "BRK.B",
        "Robinhood": "HOOD",
        "SoFi": "SOFI",
    }

    def __init__(self):
        # 预编译英文公司名正则（性能优化，避免每次 extract 重复编译）
        self._en_patterns: Dict[str, re.Pattern] = {
            sym: re.compile(r"\b" + re.escape(name) + r"\b", re.IGNORECASE)
            for name, sym in self.ENGLISH_STOCK_MAP.items()
        }

    def extract(self, text: str) -> List[StockMention]:
        """返回去重后的股票提及列表"""
        if not text:
            return []

        mentions: dict[str, StockMention] = {}

        # 1. 匹配 $TICKER（支持大小写）
        for match in self.CASHTAG_RE.finditer(text):
            sym = match.group(1).upper()
            if sym not in self.STOCK_BLACKLIST and sym not in mentions:
                mentions[sym] = StockMention(symbol=sym, confidence=1.0)

        # 2. 匹配中文名称
        for cn_name, sym in self.CHINESE_STOCK_MAP.items():
            if cn_name in text and sym not in mentions:
                mentions[sym] = StockMention(symbol=sym, name=cn_name, confidence=0.8)

        # 3. 匹配英文公司名（使用预编译正则，忽略大小写）
        for sym, pattern in self._en_patterns.items():
            if sym not in mentions and pattern.search(text):
                # 获取原始公司名
                en_name = next(name for name, s in self.ENGLISH_STOCK_MAP.items() if s == sym)
                mentions[sym] = StockMention(symbol=sym, name=en_name, confidence=0.7)

        result = list(mentions.values())
        if result:
            logger.debug("Extracted stocks from tweet: {}", [m.symbol for m in result])
        return result
