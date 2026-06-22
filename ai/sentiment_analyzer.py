"""股票推文情绪分析 — 三层管道: FinBERT > 规则层反讽预检 > LLM 二审；VADER 兜底。

三层管道设计（保证评分数据干净、一致）:
  1. FinTwitBERT 全量打分（主力，CPU，快）
  2. 规则层 sarcasm_detector 预检（零成本，识别反讽成分）
  3. LLM 二审（仅当 规则命中 或 高影响低置信 时触发，复用 DashScope GLM）
     → 输出对齐 FinBERT 的 schema，可无缝覆盖，数据干净一致

LLM 二审可由 use_llm_review 开关控制，默认开启；关闭时退化为原 FinBERT>VADER。
"""
import os
import json
import re
from typing import Optional
from dataclasses import dataclass
from loguru import logger

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

# ── 可选依赖 ──────────────────────────────────────────
try:
    from ai.finbert_analyzer import FinBERTAnalyzer
    HAS_FINBERT = True
except Exception:
    HAS_FINBERT = False

try:
    from ai.sarcasm_detector import detect as detect_sarcasm
    HAS_SARCASM = True
except Exception:
    HAS_SARCASM = False

try:
    from ai.llm_reviewer import LLMReviewer
    HAS_LLM_REVIEW = True
except Exception:
    HAS_LLM_REVIEW = False


@dataclass
class SentimentResult:
    score: float          # -1.0 (极度看空) ~ +1.0 (极度看多)
    label: str            # positive / neutral / negative
    reason: str           # 判断理由
    confidence: float     # 0.0 ~ 1.0
    method: str           # "vader" | "finbert" | "llm_review"
    prob_dist: Optional[dict] = None  # FinBERT/LLM 概率分布 {bullish, bearish, neutral}


class SentimentAnalyzer:
    """
    三层情绪分析管道（按优先级）:

    1. FinBERT (CPU)         — 金融语料微调，离线可用（主力）
    2. 规则层反讽预检        — 零成本，识别反讽成分
    3. LLM 二审              — 仅当规则命中或高影响低置信时触发
    4. VADER                 — 社交媒体专用规则引擎，无需模型，保底
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        use_finbert: bool = True,
        use_llm_review: bool = True,
    ):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        # LLM 二审（默认开启；仅对 规则命中 / 高影响低置信 的推文触发）
        self.use_llm_review = use_llm_review and HAS_LLM_REVIEW
        # 触发二审的阈值
        self.low_confidence_threshold = 0.55   # FinBERT confidence 低于此 + 命中反讽 → 二审
        self.high_impact_threshold = 0.30      # |score| 高于此的强信号若低置信也二审
        self._vader = SentimentIntensityAnalyzer()

        # FinBERT 初始化（懒加载，首次 analyze 时才真正加载权重）
        self._finbert: Optional[FinBERTAnalyzer] = None
        if use_finbert and HAS_FINBERT:
            try:
                self._finbert = FinBERTAnalyzer()
                logger.info("SentimentAnalyzer: FinTwitBERT-sentiment ready (CPU)")
            except Exception as e:
                logger.warning("FinTwitBERT init failed ({}), fallback to VADER", e)
                self._finbert = None

        # LLM 二审器（懒加载，避免无 key 时崩溃）
        self._reviewer: Optional[LLMReviewer] = None
        if self.use_llm_review:
            try:
                self._reviewer = LLMReviewer()
                logger.info("SentimentAnalyzer: LLM review layer ready (DashScope GLM)")
            except Exception as e:
                logger.warning("LLM reviewer init failed ({}), 二审关闭", e)
                self._reviewer = None
                self.use_llm_review = False

        layers = []
        if self._finbert:
            layers.append("FinBERT")
        if self.use_llm_review:
            layers.append("LLM-Review")
        if HAS_SARCASM:
            layers.append("Sarcasm")
        if self._finbert:
            logger.info("SentimentAnalyzer: {} primary, VADER fallback",
                        "+".join(layers) if layers else "VADER")
        else:
            logger.info("SentimentAnalyzer: VADER only")

    # ── 对外接口 ──────────────────────────────────────
    def analyze(
        self,
        tweet_text: str,
        stock_symbol: str,
        stock_name: str = "",
    ) -> SentimentResult:
        """分析推文中对某只股票的情绪态度（三层管道）"""
        # 1. FinBERT 路径
        if self._finbert is not None:
            try:
                result = self._finbert_analyze(tweet_text, stock_symbol)
                # 2+3. 规则层预检 + LLM 二审（仅必要时触发）
                return self._maybe_llm_review(tweet_text, stock_symbol, result)
            except Exception as e:
                logger.warning("FinTwitBERT failed: {}, fallback to VADER", e)

        # 0. VADER 保底
        return self._vader_analyze(tweet_text, stock_symbol)

    def _maybe_llm_review(
        self, text: str, symbol: str, finbert_result: SentimentResult
    ) -> SentimentResult:
        """规则层预检 → 命中 或 高影响低置信 时调 LLM 二审，否则保留 FinBERT 原判。

        数据一致性: LLM 二审输出与 FinBERT 同 schema（label/score/prob/confidence），
        成功则无缝覆盖；失败则保留 FinBERT 原判，绝不污染数据。
        """
        if not self._reviewer:
            return finbert_result

        # 规则层预检（零成本）
        sig = detect_sarcasm(text) if HAS_SARCASM else None
        need_review = False
        reason = ""

        # 条件 A: 反讽规则命中 → 二审
        if sig and sig.hit:
            need_review = True
            reason = f"sarcasm({sig.confidence:.2f})"

        # 条件 B: 高影响 + 低置信（强信号但 FinBERT 不确定）→ 二审
        elif (abs(finbert_result.score) >= self.high_impact_threshold
              and finbert_result.confidence < self.low_confidence_threshold):
            need_review = True
            reason = (f"high-impact+low-conf"
                      f"(s={finbert_result.score:+.2f},c={finbert_result.confidence:.2f})")

        if not need_review:
            return finbert_result

        # 调 LLM 二审
        # label 统一回 bullish/bearish/neutral 供 reviewer
        rev_label_map = {"positive": "bullish", "negative": "bearish", "neutral": "neutral"}
        fb_label = rev_label_map.get(finbert_result.label, finbert_result.label)
        reviewed = self._reviewer.review(
            text=text,
            symbol=symbol,
            finbert_label=fb_label,
            finbert_prob=finbert_result.prob_dist,
            sarcasm_reasons=sig.reasons if sig else None,
        )

        if reviewed is None:
            # 二审失败 → 保留 FinBERT 原判（数据干净）
            logger.debug("二审失败保留原判: {} {}", symbol, reason)
            return finbert_result

        # 用二审结果覆盖（schema 一致），method 标记为 llm_review 以便追溯
        label_map = {"bullish": "positive", "bearish": "negative", "neutral": "neutral"}
        flipped = (label_map.get(reviewed.label) != finbert_result.label)
        new_reason = (
            f"LLM二审[{reason}] "
            f"{'翻转' if flipped else '确认'} "
            + " ".join(f"{k}={v:.3f}" for k, v in reviewed.prob_dist.items())
            + f" | {reviewed.reason}"
        )
        if flipped:
            logger.info("情绪翻转 {} {}→{} ({})",
                        symbol, finbert_result.label, label_map.get(reviewed.label), reason)
        return SentimentResult(
            score=reviewed.score,
            label=label_map.get(reviewed.label, reviewed.label),
            reason=new_reason,
            confidence=reviewed.confidence,
            method="llm_review",
            prob_dist=reviewed.prob_dist,
        )

    # ── FinTwitBERT 分析 ──────────────────────────────
    def _finbert_analyze(self, text: str, symbol: str) -> SentimentResult:
        result = self._finbert.analyze(text, symbol)
        # FinTwitBERT 标签: bullish/bearish/neutral → 统一为 positive/negative/neutral
        label_map = {"bullish": "positive", "bearish": "negative", "neutral": "neutral"}
        reason = (
            f"FinTwitBERT({result.device}) "
            + " ".join(f"{k}={v:.3f}" for k, v in result.prob_dist.items())
        )
        return SentimentResult(
            score=result.score,
            label=label_map.get(result.label, result.label),
            reason=reason,
            confidence=result.confidence,
            method="finbert",
            prob_dist=result.prob_dist,
        )

    # ── VADER 分析（保底） ────────────────────────────
    def _vader_analyze(self, text: str, symbol: str) -> SentimentResult:
        """
        VADER 专为社交媒体设计:
        - 识别 emoji: 🚀🔥 → +sentiment, 📉💀 → -sentiment
        - 识别大写强调: "AMAZING!!!" → 强度加成
        - 识别否定翻转: "not good" → negative
        - 识别程度副词: "very", "extremely", "slightly"
        """
        scores = self._vader.polarity_scores(text)
        compound = scores["compound"]

        if compound >= 0.05:
            label = "positive"
        elif compound <= -0.05:
            label = "negative"
        else:
            label = "neutral"

        pos_neg_ratio = scores["pos"] + scores["neg"]
        confidence = min(abs(compound) + pos_neg_ratio * 0.3, 1.0)

        reason = (
            f"VADER compound={compound:.3f} "
            f"(pos={scores['pos']:.2f}, neg={scores['neg']:.2f}, neu={scores['neu']:.2f})"
        )

        return SentimentResult(
            score=round(compound, 4),
            label=label,
            reason=reason,
            confidence=round(confidence, 4),
            method="vader",
        )
