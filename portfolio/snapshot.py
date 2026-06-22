"""
静态复盘引擎 — 时间衰减 + sigmoid 映射 + 固定权重收益

核心公式：
  decay(k) = 1 / ln(1 + α·T_tweet(k) + β·T_days(k))
  score(k) = Σ sentiment_i × decay_i
  alloc(k) = C_base × σ(score(k) / scale)

输出：持仓快照 + 1Y/3M/YTD 收益
"""

import math
from datetime import date, datetime, timedelta
from collections import defaultdict
from typing import Optional

from storage.models import get_session, StockMention, Tweet
from portfolio.price_fetcher import PriceFetcher
import config


def _sigmoid(x: float) -> float:
    """S 型曲线: (-∞→0, 0→0.5, +∞→1)"""
    return 1.0 / (1.0 + math.exp(-x))


class SnapshotEngine:
    """
    静态复盘引擎

    从所有历史情绪记录计算一份"如果今天下单"的持仓快照，
    不迭代、不调仓，只做一次加权汇总。

    使用方式:
        engine = SnapshotEngine(author_id="aleaborteddit")
        snap = engine.build()
        # snap["positions"] → 持仓列表
        # snap["returns"]   → {"1y": 0.15, "3m": 0.08, "ytd": 0.12}
    """

    # ══ 衰减参数 ══
    DECAY_ALPHA = 1.0        # 推文序数权重（每多一条推文衰减一档）
    DECAY_BETA  = 0.02       # 日历天数权重（每天衰减 0.02 档）

    # ══ sigmoid 映射参数 ══
    ALLOC_SCALE      = 0.5   # 分数缩放系数（越小 → sigmoid 越陡 → 区分度越高）
    BASE_ALLOC_RATIO = 0.10  # 单股基准分配 = 初始资金 × 此比例

    def __init__(
        self,
        author_id: str,
        initial_cash: Optional[float] = None,
    ):
        self.author_id = author_id
        self.initial_cash = initial_cash or config.PORTFOLIO_INITIAL_CASH
        self._pf = PriceFetcher()
        self.C_base = self.initial_cash * self.BASE_ALLOC_RATIO

    # ── 主入口 ──────────────────────────────────────
    def build(self, as_of: Optional[date] = None) -> dict:
        """
        构建静态持仓快照。

        返回结构见 _result()。
        """
        if as_of is None:
            as_of = date.today()

        session = get_session()

        # ═══ 1. 加载数据 ═══
        mentions = (
            session.query(StockMention)
            .join(Tweet, StockMention.tweet_id == Tweet.id)
            .filter(Tweet.author_id == self.author_id)
            .order_by(StockMention.analyzed_at.asc())
            .all()
        )

        if not mentions:
            session.close()
            return self._empty(as_of)

        # ═══ 2. 推文序数（按 tweet_id 去重排序） ═══
        tweet_earliest: dict[str, datetime] = {}
        for m in mentions:
            dt = m.analyzed_at
            if m.tweet_id not in tweet_earliest or dt < tweet_earliest[m.tweet_id]:
                tweet_earliest[m.tweet_id] = dt

        sorted_tweets = sorted(tweet_earliest.items(), key=lambda x: x[1])
        tweet_rank = {tid: i for i, (tid, _) in enumerate(sorted_tweets)}
        total_tweets = len(sorted_tweets)

        # ═══ 3. 时间衰减 + 分数聚合 ═══
        stock_raw     = defaultdict(float)   # 原始分数和
        stock_decayed = defaultdict(float)   # 衰减后分数和
        stock_names   = {}                   # {symbol: name}
        stock_count   = defaultdict(int)     # {symbol: mention_count}

        for m in mentions:
            if m.sentiment_score <= 0:
                continue  # 只看多

            # T_tweet: 此推文之后还有多少条推文
            rank = tweet_rank.get(m.tweet_id, 0)
            T_tweet = total_tweets - 1 - rank

            # T_days: 推文发出到快照截止日的天数
            T_days = max(0, (as_of - m.analyzed_at.date()).days)

            # 衰减因子
            decay = 1.0 / math.log(
                1.0 + self.DECAY_ALPHA * T_tweet + self.DECAY_BETA * T_days + 1e-6
            )

            stock_raw[m.symbol]     += m.sentiment_score
            stock_decayed[m.symbol] += m.sentiment_score * decay
            stock_names[m.symbol]    = m.name or m.symbol
            stock_count[m.symbol]   += 1

        if not stock_decayed:
            session.close()
            return self._empty(as_of)

        # ═══ 4. sigmoid 映射 → 绝对金额 ═══
        alloc = {}
        for sym, score in stock_decayed.items():
            alloc[sym] = self.C_base * _sigmoid(score / self.ALLOC_SCALE)

        total_alloc = sum(alloc.values())

        # 总仓位不超过初始资金（不加杠杆）
        if total_alloc > self.initial_cash:
            cap_ratio = self.initial_cash / total_alloc
            alloc = {k: v * cap_ratio for k, v in alloc.items()}
            total_alloc = self.initial_cash

        cash_reserve = self.initial_cash - total_alloc

        # ═══ 5. 权重 ═══
        weights = {}
        if total_alloc > 0:
            weights = {k: round(v / total_alloc, 4) for k, v in alloc.items()}

        # ═══ 6. 拉取价格 + 计算区间收益 ═══
        symbols = list(weights.keys())
        today = date.today()

        periods = {
            "ytd": (date(today.year, 1, 1), today),
            "3m":  (today - timedelta(days=90), today),
            "1y":  (today - timedelta(days=365), today),
        }

        earliest_start = min(s for s, _ in periods.values())
        price_series = self._pf.get_price_series(
            symbols, start_date=earliest_start, end_date=today, cache_only=True
        )

        returns = {}
        for period_name, (start_d, end_d) in periods.items():
            returns[period_name] = round(
                self._calc_period_return(weights, price_series, start_d, end_d), 4
            )

        session.close()

        # ═══ 7. 组装输出 ═══
        positions = []
        for sym in sorted(weights, key=lambda k: weights[k], reverse=True):
            price_now = self._get_price_on(price_series, sym, today)
            positions.append({
                "symbol":        sym,
                "name":          stock_names.get(sym, ""),
                "raw_score":     round(stock_raw[sym], 4),
                "decayed_score": round(stock_decayed[sym], 4),
                "mentions":      stock_count[sym],
                "weight":        weights[sym],
                "allocation":    round(alloc[sym], 2),
                "current_price": round(price_now, 2) if price_now else None,
            })

        return {
            "as_of":            as_of.isoformat(),
            "author_id":        self.author_id,
            "positions":        positions,
            "total_allocation": round(total_alloc, 2),
            "cash_reserve":     round(cash_reserve, 2),
            "returns":          returns,
            "parameters": {
                "decay_alpha":  self.DECAY_ALPHA,
                "decay_beta":   self.DECAY_BETA,
                "alloc_scale":  self.ALLOC_SCALE,
                "C_base":       round(self.C_base, 2),
                "initial_cash": self.initial_cash,
            },
            "stock_count":      len(weights),
            "total_mentions":   sum(stock_count.values()),
            "total_tweets":     total_tweets,
        }

    # ── 收益计算 ────────────────────────────────────
    def _calc_period_return(
        self,
        weights: dict[str, float],
        price_series: dict[str, dict[str, float]],
        from_date: date,
        to_date: date,
    ) -> float:
        """固定权重组合的区间收益"""
        if not weights:
            return 0.0

        start_val = 0.0
        end_val   = 0.0
        for sym, w in weights.items():
            p_start = self._get_price_on(price_series, sym, from_date)
            p_end   = self._get_price_on(price_series, sym, to_date)
            if p_start and p_end and p_start > 0:
                shares = w / p_start
                start_val += shares * p_start
                end_val   += shares * p_end

        return (end_val - start_val) / start_val if start_val > 0 else 0.0

    @staticmethod
    def _get_price_on(
        price_series: dict[str, dict[str, float]],
        symbol: str,
        target_date: date,
    ) -> Optional[float]:
        """从价格序列取最近交易日价格（向前搜索最多7天）"""
        ps = price_series.get(symbol, {})
        if not ps:
            return None
        for i in range(7):
            d = (target_date - timedelta(days=i)).isoformat()
            if d in ps:
                return ps[d]
        return None

    def _empty(self, as_of: date) -> dict:
        return {
            "as_of": as_of.isoformat(),
            "author_id": self.author_id,
            "positions": [],
            "total_allocation": 0.0,
            "cash_reserve": self.initial_cash,
            "returns": {"ytd": 0.0, "3m": 0.0, "1y": 0.0},
            "parameters": {
                "decay_alpha": self.DECAY_ALPHA,
                "decay_beta": self.DECAY_BETA,
                "alloc_scale": self.ALLOC_SCALE,
                "C_base": round(self.C_base, 2),
                "initial_cash": self.initial_cash,
            },
            "stock_count": 0,
            "total_mentions": 0,
            "total_tweets": 0,
        }
