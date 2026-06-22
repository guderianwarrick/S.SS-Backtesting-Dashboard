"""股票历史价格获取 — 多源 fallback 架构（开盘价+收盘价）

优先级：本地缓存 → Yahoo Finance → Alpha Vantage
缓存命中直接返回，未命中按序尝试数据源，成功后写入缓存。

缓存格式：{date_str: {"o": open_price, "c": close_price}}

Alpha Vantage 限额：25 次/天（免费层），仅作 fallback。
"""

import json
import time
from datetime import date, timedelta, datetime
from pathlib import Path
from typing import Optional
from loguru import logger

import config


# ── Yahoo Finance ─────────────────────────────────────

try:
    import yfinance as yf
    HAS_YFINANCE = True
except ImportError:
    HAS_YFINANCE = False

try:
    import requests as _requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

HAS_ALPHA = bool(config.ALPHA_VANTAGE_API_KEY)


# ══════════════════════════════════════════════════════
# Alpha Vantage 数据源
# ══════════════════════════════════════════════════════

class AlphaVantageFetcher:
    """
    Alpha Vantage TIME_SERIES_DAILY_ADJUSTED 端点。
    免费层：25 次/天，outputsize=full 一次请求可获取 20+ 年数据。
    """

    BASE = "https://www.alphavantage.co/query"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._call_count_today = 0
        self._date_today = date.today()

    def _check_quota(self) -> bool:
        if self._date_today != date.today():
            self._call_count_today = 0
            self._date_today = date.today()
        return self._call_count_today < 25

    def fetch(self, symbol: str, from_date: Optional[date] = None,
              to_date: Optional[date] = None) -> dict[str, dict[str, float]]:
        """
        拉取全量日线 → {date_str: {"o": open, "c": close}}
        """
        from .price_cache_util import make_oc  # lazy import to avoid circular

        if not self._check_quota():
            logger.warning("AV quota exhausted for today")
            return {}

        self._call_count_today += 1
        logger.debug("AV #{}/25: {}", self._call_count_today, symbol)

        try:
            r = _requests.get(self.BASE, params={
                "function": "TIME_SERIES_DAILY_ADJUSTED",
                "symbol": symbol,
                "outputsize": "full",
                "apikey": self.api_key,
            }, timeout=30)

            if not r.ok:
                return {}

            data = r.json()
            if "Error Message" in data:
                logger.warning("AV error for {}: {}", symbol, data["Error Message"][:80])
                return {}
            if "Information" in data:
                logger.warning("AV info: {}", data["Information"][:80])
                return {}

            ts = data.get("Time Series (Daily)", {})
            if not ts:
                logger.warning("AV no Time Series for {}", symbol)
                return {}

            prices: dict[str, dict[str, float]] = {}
            for date_str, fields in ts.items():
                try:
                    prices[date_str] = {
                        "o": float(fields["1. open"]),
                        "c": float(fields["4. close"]),
                    }
                except (KeyError, ValueError):
                    continue

            logger.info("AV: {} → {} data points", symbol, len(prices))
            return prices

        except Exception as e:
            logger.warning("AV fetch {} failed: {}", symbol, e)
            return {}

    @property
    def quota_remaining(self) -> int:
        if self._date_today != date.today():
            self._call_count_today = 0
            self._date_today = date.today()
        return max(0, 25 - self._call_count_today)


# ══════════════════════════════════════════════════════
# Yahoo Finance 数据源 (chart API 直连)
# ══════════════════════════════════════════════════════

class YahooDirectFetcher:
    """requests 直连 Yahoo chart API — 无需 cookie/无需 crumb/无需认证"""

    def __init__(self):
        self._session = None

    def _ensure_session(self):
        if self._session is None:
            if not HAS_REQUESTS:
                return
            self._session = _requests.Session()
            if config.SOCKS5_ENABLED and config.SOCKS5_PROXY:
                self._session.proxies = {
                    "http": config.SOCKS5_PROXY,
                    "https": config.SOCKS5_PROXY,
                }
            else:
                self._session.trust_env = True

    def fetch(self, symbol: str, from_date: date, to_date: date) -> dict[str, dict[str, float]]:
        """
        拉取区间日线 → {date_str: {"o": open, "c": close}}；失败返回空
        """
        self._ensure_session()
        if not self._session:
            return {}

        p1 = int(datetime(from_date.year, from_date.month, from_date.day).timestamp())
        p2 = int(datetime(to_date.year, to_date.month, to_date.day, 23, 59, 59).timestamp())

        url = (
            f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}"
            f"?period1={p1}&period2={p2}&interval=1d"
        )

        try:
            r = self._session.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                timeout=30,
            )

            if r.status_code == 429:
                logger.debug("Yahoo rate limited for {}", symbol)
                return {}

            if not r.ok:
                logger.debug("Yahoo HTTP {} for {}", r.status_code, symbol)
                return {}

            data = r.json()
            result = data["chart"]["result"][0]
            ts = result.get("timestamp")
            if not ts:
                return {}

            quote = result["indicators"]["quote"][0]
            closes = quote.get("close", [])
            opens = quote.get("open", [])
            prices = {}
            for i, t in enumerate(ts):
                c = closes[i] if i < len(closes) else None
                o = opens[i] if i < len(opens) else None
                if c is not None or o is not None:
                    dt = datetime.fromtimestamp(t).strftime("%Y-%m-%d")
                    entry = {}
                    if o is not None:
                        entry["o"] = round(o, 4)
                    if c is not None:
                        entry["c"] = round(c, 4)
                    prices[dt] = entry
            return prices

        except Exception:
            return {}


# ══════════════════════════════════════════════════════
# YFinance 数据源 (回退备选)
# ══════════════════════════════════════════════════════

class YFinanceFetcher:
    """yfinance 库封装"""

    def fetch(self, symbol: str, from_date: date, to_date: date) -> dict[str, dict[str, float]]:
        if not HAS_YFINANCE:
            return {}

        try:
            import requests as _r

            session = _r.Session()
            if config.SOCKS5_ENABLED and config.SOCKS5_PROXY:
                session.proxies = {
                    "http": config.SOCKS5_PROXY,
                    "https": config.SOCKS5_PROXY,
                }
            else:
                session.trust_env = True

            df = yf.download(
                symbol,
                start=str(from_date - timedelta(days=5)),
                end=str(to_date + timedelta(days=5)),
                progress=False,
                auto_adjust=True,
                session=session,
            )
            if df.empty:
                logger.debug("yfinance empty for {}", symbol)
                return {}

            prices = {}
            for idx, row in df.iterrows():
                ds = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]
                entry = {}
                for col_key, dest_key in [("Open", "o"), ("Close", "c")]:
                    if col_key in row:
                        val = float(row[col_key]) if not hasattr(row[col_key], "iloc") else float(row[col_key].iloc[0])
                        entry[dest_key] = round(val, 4)
                if entry:
                    prices[ds] = entry
            return prices

        except Exception as e:
            logger.debug("yfinance {} failed: {}", symbol, str(e)[:60])
            return {}


# ══════════════════════════════════════════════════════
# Sources 管理器 — 统一调度
# ══════════════════════════════════════════════════════

class Sources:
    """多源调度器，按序 fallback"""

    def __init__(self, alpha_api_key: str = ""):
        self.yahoo_direct = YahooDirectFetcher()
        self.yfinance = YFinanceFetcher() if HAS_YFINANCE else None
        self.alpha = AlphaVantageFetcher(alpha_api_key) if HAS_ALPHA and alpha_api_key else None
        self.source_stats = {"yahoo_direct": 0, "yfinance": 0, "av": 0}

    def fetch(self, ticker: str, from_date: date, to_date: date) -> dict[str, dict[str, float]]:
        """按序尝试数据源，返回并集"""
        result: dict[str, dict[str, float]] = {}

        # 1. Yahoo Direct（最快）
        prices = self.yahoo_direct.fetch(ticker, from_date, to_date)
        if prices:
            self.source_stats["yahoo_direct"] += 1
            result.update(prices)
            if self._is_complete(prices, from_date, to_date):
                return result

        # 2. yfinance（备选）
        if self.yfinance:
            prices = self.yfinance.fetch(ticker, from_date, to_date)
            if prices:
                self.source_stats["yfinance"] += 1
                result.update(prices)
                if self._is_complete(prices, from_date, to_date):
                    return result

        # 3. Alpha Vantage（最后兜底）
        if self.alpha:
            prices = self.alpha.fetch(ticker)
            if prices:
                self.source_stats["av"] += 1
                result.update(prices)

        return result

    @staticmethod
    def _is_complete(prices: dict, from_date: date, to_date: date) -> bool:
        """粗略判断数据是否完整覆盖区间"""
        if not prices:
            return False
        dates = sorted(prices.keys())
        return dates[0] <= str(from_date) and dates[-1] >= str(to_date)


# ══════════════════════════════════════════════════════
# PriceFetcher — 对外 API
# ══════════════════════════════════════════════════════

class PriceFetcher:
    """
    多源股票价格获取器。对外统一接口。
    缓存优先，未命中按序尝试数据源。
    """

    def __init__(self):
        self.cache_dir = config.DATA_DIR / "price_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, dict[str, dict[str, float]]] = {}
        self._sources = Sources(alpha_api_key=config.ALPHA_VANTAGE_API_KEY)

    # ── 兼容旧缓存格式 ─────────────────────────────

    @staticmethod
    def _normalize_entry(val) -> dict[str, float]:
        """兼容旧缓存格式（纯数字=close only）和新格式（{o: ..., c: ...}）"""
        if isinstance(val, dict):
            return val  # 新格式
        # 旧格式：纯 float → 当作 close
        return {"c": float(val)}

    # ── 对外接口 ──────────────────────────────────

    def get_price_series(
        self,
        symbols: list[str],
        start_date: date,
        end_date: Optional[date] = None,
        cache_only: bool = False,
    ) -> dict[str, dict[str, dict[str, float]]]:
        """
        获取多只股票在区间内的每日价格。
        cache_only=True → 仅本地缓存，不走网络。
        返回 {symbol: {date: {"o": open, "c": close}}}
        """
        if end_date is None:
            end_date = date.today()

        result: dict[str, dict[str, dict[str, float]]] = {}
        for sym in symbols:
            series = self._get_series(sym, start_date, end_date, cache_only=cache_only)
            if series:
                result[sym] = series
        return result

    # ── 缓存 IO ──────────────────────────────────

    def _get_cache_path(self, symbol: str) -> Path:
        return self.cache_dir / f"{symbol}.json"

    def _load_cache(self, symbol: str) -> dict[str, dict[str, float]]:
        if symbol in self._cache:
            return self._cache[symbol]
        path = self._get_cache_path(symbol)
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                # 兼容旧格式
                normalized = {}
                for k, v in data.items():
                    normalized[k] = self._normalize_entry(v)
                self._cache[symbol] = normalized
                return normalized
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save_cache(self, symbol: str, data: dict[str, dict[str, float]]):
        self._cache[symbol] = data
        path = self._get_cache_path(symbol)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── 核心逻辑 ─────────────────────────────────

    def _get_series(
        self, symbol: str, start: date, end: date, cache_only: bool = False,
    ) -> dict[str, dict[str, float]]:
        """单只股票时间序列 — 缓存优先，未命中时多源 fallback"""
        ticker = self._fix_ticker(symbol)
        if ticker is None:
            return {}

        cache = self._load_cache(symbol)
        start_str = str(start)
        end_str = str(end)

        if cache:
            cache_dates = sorted(cache.keys())
            cache_earliest = cache_dates[0]
            cache_latest = cache_dates[-1]
            need_earlier = start_str < cache_earliest
            need_later = end_str > cache_latest

            if not need_earlier and not need_later:
                self._sources.source_stats.setdefault("cache", 0)
                self._sources.source_stats["cache"] += 1
                return {k: v for k, v in cache.items() if start_str <= k <= end_str}

            if cache_only:
                return {k: v for k, v in cache.items() if start_str <= k <= end_str}

            logger.info("Cache gap {}: [{}, {}] vs [{}, {}]",
                        symbol, cache_earliest, cache_latest, start_str, end_str)
            self._fill_gap(symbol, ticker, cache, need_earlier, need_later, start, end)
            return {k: v for k, v in cache.items() if start_str <= k <= end_str}

        # 缓存为空
        if cache_only:
            return {}

        # 多源拉取全量
        prices = self._sources.fetch(ticker, start - timedelta(days=30), end + timedelta(days=5))
        if not prices:
            return {}

        cache.update(prices)
        self._save_cache(symbol, cache)
        return {k: v for k, v in cache.items() if start_str <= k <= end_str}

    def _fill_gap(
        self, symbol: str, ticker: str, cache: dict[str, dict[str, float]],
        need_earlier: bool, need_later: bool, start: date, end: date,
    ):
        """补充缓存缺失区间"""
        cache_dates = sorted(cache.keys())

        if need_earlier:
            gap_end = date.fromisoformat(cache_dates[0])
            gap_start = start - timedelta(days=30)
            logger.debug("  Pulling earlier {}: {} → {}", symbol, gap_start, gap_end)
            prices = self._sources.fetch(ticker, gap_start, gap_end)
            for dt, p in prices.items():
                if dt not in cache:
                    cache[dt] = p

        if need_later:
            gap_start_date = date.fromisoformat(cache_dates[-1]) + timedelta(days=1)
            gap_end_date = end + timedelta(days=5)
            logger.debug("  Pulling later {}: {} → {}", symbol, gap_start_date, gap_end_date)
            prices = self._sources.fetch(ticker, gap_start_date, gap_end_date)
            for dt, p in prices.items():
                if dt not in cache:
                    cache[dt] = p

        self._save_cache(symbol, cache)

    # ── 缓存预热 ─────────────────────────────────

    def extend_cache(
        self, symbol: str, target_start: date,
        prefer_alpha: bool = False,
    ) -> int:
        """扩展缓存到目标起始日期。返回新增点数。"""
        ticker = self._fix_ticker(symbol)
        if ticker is None:
            return 0

        cache = self._load_cache(symbol)
        target_start_str = str(target_start)

        if cache:
            cache_dates = sorted(cache.keys())
            cache_earliest = cache_dates[0]
            cache_latest = cache_dates[-1]
            need_earlier = target_start_str < cache_earliest
            need_later = str(date.today()) > cache_latest

            if not need_earlier and not need_later:
                return 0

            before = len(cache)
            if prefer_alpha and self._sources.alpha:
                all_prices = self._sources.alpha.fetch(ticker)
                for dt, p in all_prices.items():
                    if dt not in cache:
                        cache[dt] = p
            else:
                self._fill_gap(symbol, ticker, cache, need_earlier, need_later,
                              target_start, date.today())

            self._save_cache(symbol, cache)
            added = len(cache) - before
            logger.info("{} extended: {} → {} pts (+{})", symbol, before, len(cache), added)
            return added
        else:
            if prefer_alpha and self._sources.alpha:
                prices = self._sources.alpha.fetch(ticker)
            else:
                prices = self._sources.fetch(
                    ticker,
                    target_start - timedelta(days=30),
                    date.today() + timedelta(days=5),
                )

            if not prices:
                return 0

            self._save_cache(symbol, prices)
            logger.info("{} initial fetch: {} pts", symbol, len(prices))
            return len(prices)

    def warmup_all(self, target_start: date, symbols: Optional[list[str]] = None) -> dict:
        """批量预热：尝试 Yahoo Direct（已走代理），失败的标记即可"""
        if symbols is None:
            symbols = sorted([
                f.stem for f in self.cache_dir.glob("*.json") if f.stem
            ])

        stats = {"total": len(symbols), "complete": 0, "extended": 0, "failed": 0,
                 "source": {"av": 0, "yahoo": 0, "cache": 0}}

        for i, sym in enumerate(symbols):
            cache = self._load_cache(sym)
            cache_dates = sorted(cache.keys()) if cache else []

            if cache_dates and cache_dates[0] <= str(target_start) and cache_dates[-1] >= str(date.today()):
                stats["source"]["cache"] += 1
                stats["complete"] += 1
                continue

            try:
                added = self.extend_cache(sym, target_start)
                if added > 0:
                    stats["extended"] += 1
                stats["complete"] += 1
            except Exception as e:
                logger.warning("warmup {} failed: {}", sym, str(e)[:60])
                stats["failed"] += 1

            if (i + 1) % 50 == 0:
                logger.info("Warmup {}/{} | ok={} fail={}", i + 1, len(symbols),
                            stats["complete"], stats["failed"])

        return stats

    # ── 统一价格取值 ─────────────────────────────

    @staticmethod
    def get_price(
        price_series: dict[str, dict[str, dict[str, float]]],
        symbol: str,
        target_date: date,
        field: str = "c",
    ) -> Optional[float]:
        """
        从 price_series 取某只股票某日的指定价格。
        field: "c"=收盘价, "o"=开盘价
        向前最多找 7 个交易日。
        """
        ps = price_series.get(symbol, {})
        if not ps:
            return None

        for i in range(7):
            d = (target_date - timedelta(days=i)).isoformat()
            if d in ps:
                entry = ps[d]
                if isinstance(entry, dict) and field in entry:
                    return entry[field]
                # 兼容旧格式
                if isinstance(entry, dict) and "c" in entry:
                    return entry["c"]
                if not isinstance(entry, dict):
                    return float(entry) if field == "c" else None
        return None

    # ── 修正 ticker ──────────────────────────────

    _TICKER_FIXES = {
        "BRK.A": "BRK-A",
        "BRK.B": "BRK-B",
        "BF.B": "BF-B",
        "BF.A": "BF-A",
        "CWR.L": "CWR.L",
        "IQE.L": "IQE.L",
        # 欧洲交易所映射
        "SIVE": "SIVE.ST",      # 瑞典 Nasdaq Stockholm
        "XFAB": "XFAB.PA",      # 法国 Euronext Paris
        "BESI": "BESI.AS",      # 荷兰 Euronext Amsterdam
        "AIXA": "AIXA.DE",      # 德国 Xetra
        "HPS.A": "HPS-A",
        "EOS.A": "EOS-A",
        "EQR.A": "EQR-A",
        "MOG.A": "MOG-A",
        # 伦敦交易所（显式映射，已有 .L 后缀自动通过）
        "IQE": "IQE.L",
        # OTC 映射
        "BITF": "BITF",         # Bitfarms 在纳斯达克上市
    }

    def _fix_ticker(self, symbol: str) -> Optional[str]:
        if symbol in self._TICKER_FIXES:
            return self._TICKER_FIXES[symbol]
        if symbol.endswith(".L"):
            return symbol
        return symbol