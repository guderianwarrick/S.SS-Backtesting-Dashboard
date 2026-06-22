"""Stock Sentiment 虚拟持仓引擎：情绪驱动仓位计算 + 调仓回测 + 收益统计

核心逻辑：
  - 博主每次发推文提及股票 → 产生一条情绪分（-1.0 ~ +1.0）
  - 情绪分带指数衰减（半衰期可配置），旧分数随时间平滑降低影响
  - 将所有正分股票按分数等比分配权重（负分股票不进组合）
  - 每次有新推文 → 触发调仓 → 记录旧持仓 / 新持仓 / 收益率
  - 积累阶段：累积至少 min_symbols 只不同股票后才开始建仓交易
"""

from datetime import date, datetime, timezone, timedelta
from collections import defaultdict
from typing import Optional
import math
from loguru import logger

import config
from storage.models import (
    get_session, StockMention as StockMentionModel,
    Tweet as TweetModel,
    RebalanceEvent as RebalanceModel,
)
from portfolio.price_fetcher import PriceFetcher


# 指数衰减半衰期（天）— 可通过 config 或环境变量覆盖
SCORE_HALF_LIFE_DAYS = getattr(config, "SCORE_HALF_LIFE_DAYS", 14)


class PortfolioEngine:
    """
    从数据库中的 StockMention 记录反推虚拟持仓演化历史。
    """

    def __init__(self, username: str, author_id: str = "", initial_cash: float = 100_000.0, min_symbols: int = 20):
        self.username = username
        self.author_id = author_id
        self.initial_cash = initial_cash
        self.min_symbols = min_symbols
        self.price_fetcher = PriceFetcher()

    # ── 主入口：构建回测 ──────────────────────────
    def backtest(
        self,
        from_date: date,
        to_date: Optional[date] = None,
    ) -> dict:
        """
        从 from_date 到 to_date 回测博主虚拟持仓的演化过程。

        返回结构：
        {
            "rebalances": [...],        # 每次调仓明细
            "cumulative_return": float, # 累计收益率 (如 0.15 = +15%)
            "final_positions": {...},   # 最终持仓 {symbol: weight}
            "total_events": int,
        }
        """
        if to_date is None:
            to_date = date.today()

        session = get_session()

        # 1. 按推文发布时间升序拉取该博主的所有 StockMention
        mentions = (
            session.query(StockMentionModel)
            .join(TweetModel, StockMentionModel.tweet_id == TweetModel.id)
            .filter(TweetModel.author_id == self.author_id)
            .order_by(TweetModel.created_at.asc())
            .all()
        )

        # 按日分组：{date_str: {symbol: [(score, tweet_date), ...]}}
        daily_scores: dict[str, dict[str, list[tuple[float, date]]]] = defaultdict(
            lambda: defaultdict(list)
        )
        all_symbols: set[str] = set()

        for m in mentions:
            m_date = m.tweet.created_at.date()
            if m_date < from_date or m_date > to_date:
                continue

            date_key = m_date.isoformat()
            daily_scores[date_key][m.symbol].append((m.sentiment_score, m_date))
            all_symbols.add(m.symbol)

        if not daily_scores:
            session.close()
            logger.info("No stock mentions found between {} and {}", from_date, to_date)
            return self._empty_result()

        # 2. 拉取所有涉及的股票价格序列
        symbols = list(all_symbols)
        price_series = self.price_fetcher.get_price_series(
            symbols, start_date=from_date, end_date=to_date, cache_only=True  # 已离线更新主力股，其余用缓存
        )

        if not price_series:
            session.close()
            logger.warning("No price data available for any symbol; "
                           "returning weights-only result")
            return self._weights_only_result(daily_scores, from_date, to_date)

        # 3. 按时序模拟调仓
        sorted_dates = sorted(daily_scores.keys())
        rebalances = []
        cumulative_scores: dict[str, float] = defaultdict(float)

        cash = self.initial_cash
        holdings: dict[str, float] = {}
        previous_positions: dict[str, float] = {}

        decay_factor = math.exp(-math.log(2) / SCORE_HALF_LIFE_DAYS)
        prev_date_key = None

        # 累积去重股票集合 — 仓位不足 min_symbols 只不同代码时只累积情绪分不交易
        all_symbols_seen: set[str] = set()
        started = False

        for i, date_key in enumerate(sorted_dates):
            dt = date.fromisoformat(date_key)

            # 更新已见过股票集合
            all_symbols_seen.update(daily_scores[date_key].keys())

            # 先衰减旧分数（按天数差）
            if prev_date_key is not None:
                delta_days = (dt - date.fromisoformat(prev_date_key)).days
                if delta_days > 0:
                    factor = decay_factor ** delta_days
                    for sym in cumulative_scores:
                        cumulative_scores[sym] *= factor
            prev_date_key = date_key

            # 再添加今日新分数
            for sym, score_pairs in daily_scores[date_key].items():
                daily_total = sum(s for s, _ in score_pairs)
                cumulative_scores[sym] += daily_total

            # 检查是否达到最小股票数门槛
            if not started and len(all_symbols_seen) >= self.min_symbols:
                started = True
                logger.info("已达到 {} 只股票门槛，开始建仓 (日期: {})", self.min_symbols, date_key)

            if not started:
                # 积累阶段：只计分不交易
                continue

            # 计算新权重
            new_weights = self._calculate_weights(cumulative_scores)

            # 如果没有权重变化，跳过
            if new_weights == previous_positions:
                continue

            # 调仓前：计算持仓市值，将持仓全部"变现"
            if holdings:
                holdings_value = self._calc_holdings_value(holdings, price_series, dt)
                cash += holdings_value
                holdings = {}

            # 调仓：按新权重分配仓位
            holdings, buy_records = self._rebalance(
                cash, new_weights, price_series, dt
            )

            total_invested = sum(r["allocation"] for r in buy_records)
            cash -= total_invested

            # 记录调仓信息
            holdings_value = self._calc_holdings_value(holdings, price_series, dt)
            total_value = cash + holdings_value
            for sym, weight in new_weights.items():
                old_w = previous_positions.get(sym, 0)
                scores_today = [s for s, _ in daily_scores[date_key].get(sym, [])]
                reason = self._build_reason(scores_today)
                price = self._get_price_on(price_series, sym, dt)

                # 跳过查不到价格的股票（实际买不了，不记入持仓）
                if price is None or price <= 0:
                    continue

                rebalances.append({
                    "date": date_key,
                    "symbol": sym,
                    "old_weight": round(old_w, 4),
                    "new_weight": round(weight, 4),
                    "sentiment_score": round(
                        sum(scores_today) / max(len(scores_today), 1), 4
                    ),
                    "price": round(price, 2) if price else None,
                    "reason": reason,
                    "portfolio_value": round(total_value, 2),
                })

            previous_positions = dict(new_weights)

        # 4. 持久化到数据库
        session.query(RebalanceModel).filter(
            RebalanceModel.username == self.username,
            RebalanceModel.date >= from_date.isoformat(),
            RebalanceModel.date <= to_date.isoformat(),
        ).delete()

        for r in rebalances:
            session.add(RebalanceModel(
                username=self.username,
                date=r["date"],
                symbol=r["symbol"],
                old_weight=r["old_weight"],
                new_weight=r["new_weight"],
                sentiment_score=r["sentiment_score"],
                price=r["price"],
                reason=r["reason"],
                portfolio_value=r["portfolio_value"],
            ))

        # 最终收益
        if holdings:
            final_value = self._calc_holdings_value(holdings, price_series, to_date)
            final_value += cash
        else:
            final_value = cash

        cumulative_return = (final_value - self.initial_cash) / self.initial_cash

        session.commit()
        session.close()

        return {
            "rebalances": rebalances,
            "cumulative_return": round(cumulative_return, 4),
            "final_value": round(final_value, 2),
            "final_positions": previous_positions,
            "total_events": len(rebalances),
            "total_symbols": len(all_symbols),
        }

    # ── 权重计算 ──────────────────────────────────
    def _calculate_weights(self, scores: dict[str, float]) -> dict[str, float]:
        """
        情绪分 → 持仓权重。
        规则：
          1. 过滤负分股票（看空不持仓）
          2. 剩余按分值等比分配
          3. 单只股票权重不超过 PORTFOLIO_MAX_WEIGHT（超限裁剪）
          4. 如果全部为负 → 空仓（100% 现金）
        """
        positive = {k: v for k, v in scores.items() if v > 0}
        if not positive:
            return {}

        max_w = config.PORTFOLIO_MAX_WEIGHT
        total = sum(positive.values())
        weights = {k: v / total for k, v in positive.items()}

        # 裁剪超过上限的权重，最多迭代 10 次
        for _ in range(10):
            overflow = []
            overflow_sum = 0.0
            for k, w in list(weights.items()):
                if w > max_w:
                    overflow_sum += w - max_w
                    overflow.append(k)
            if not overflow:
                break

            for k in overflow:
                weights[k] = max_w

            eligible = {k: w for k, w in weights.items() if w < max_w}
            eligible_total = sum(eligible.values())
            if eligible_total > 0:
                for k in eligible:
                    weights[k] += overflow_sum * (eligible[k] / eligible_total)
            else:
                n = len(weights)
                for k in weights:
                    weights[k] = 1.0 / n
                break

        final_total = sum(weights.values())
        return {k: round(w / final_total, 4) for k, w in weights.items()}

    # ── 调仓执行 ──────────────────────────────────
    def _rebalance(
        self,
        cash: float,
        weights: dict[str, float],
        price_series: dict[str, dict[str, float]],
        target_date: date,
    ) -> tuple[dict[str, float], list[dict]]:
        holdings: dict[str, float] = {}
        records = []

        for sym, weight in weights.items():
            price = self._get_price_on(price_series, sym, target_date)
            if price is None or price <= 0:
                continue

            allocation = cash * weight
            shares = allocation / price
            holdings[sym] = shares

            records.append({
                "symbol": sym,
                "weight": weight,
                "price": price,
                "shares": round(shares, 4),
                "allocation": round(allocation, 2),
            })

        return holdings, records

    # ── 区间收益计算 ──────────────────────────────
    def _calc_holdings_value(
        self,
        holdings: dict[str, float],
        price_series: dict[str, dict[str, float]],
        target_date: date,
    ) -> float:
        total = 0.0
        for sym, shares in holdings.items():
            price = self._get_price_on(price_series, sym, target_date)
            if price is not None:
                total += shares * price
        return total

    def _calc_period_return(
        self,
        holdings: dict[str, float],
        price_series: dict[str, dict[str, float]],
        from_date: date,
        to_date: date,
    ) -> float:
        if not holdings:
            return 0.0

        total_start = 0.0
        total_end = 0.0

        for sym, shares in holdings.items():
            p_start = self._get_price_on(price_series, sym, from_date)
            p_end = self._get_price_on(price_series, sym, to_date)
            if p_start is None or p_end is None:
                continue
            total_start += shares * p_start
            total_end += shares * p_end

        if total_start <= 0:
            return 0.0
        return (total_end - total_start) / total_start

    @staticmethod
    def _get_price_on(
        price_series: dict[str, dict[str, dict[str, float]]],
        symbol: str,
        target_date: date,
        field: str = "c",
    ) -> Optional[float]:
        """取指定日期的价格，field='c'=收盘价, 'o'=开盘价"""
        # 委托给 PriceFetcher 的统一方法
        return PriceFetcher.get_price(price_series, symbol, target_date, field=field)

    # ── 辅助方法 ──────────────────────────────────
    @staticmethod
    def _build_reason(scores: list[float]) -> str:
        if not scores:
            return "无明确信号"

        avg = sum(scores) / len(scores)
        if avg > 0.5:
            return f"强烈看多 (均分 {avg:+.2f})"
        elif avg > 0.15:
            return f"温和看多 (均分 {avg:+.2f})"
        elif avg > -0.15:
            return f"中性观望 (均分 {avg:+.2f})"
        elif avg > -0.5:
            return f"温和看空 (均分 {avg:+.2f})"
        else:
            return f"强烈看空 (均分 {avg:+.2f})"

    def _empty_result(self) -> dict:
        return {
            "rebalances": [],
            "cumulative_return": 0.0,
            "final_value": self.initial_cash,
            "final_positions": {},
            "total_events": 0,
            "total_symbols": 0,
        }

    def _weights_only_result(
        self, daily_scores: dict, from_date: date, to_date: date
    ) -> dict:
        cumulative: dict[str, float] = defaultdict(float)
        for date_key in sorted(daily_scores.keys()):
            for sym, score_pairs in daily_scores[date_key].items():
                cumulative[sym] += sum(s for s, _ in score_pairs)

        weights = self._calculate_weights(cumulative)

        return {
            "rebalances": [],
            "cumulative_return": 0.0,
            "final_value": self.initial_cash,
            "final_positions": weights,
            "total_events": 0,
            "total_symbols": len(cumulative),
            "note": "⚠️ 无价格数据，仅展示最新持仓权重",
        }