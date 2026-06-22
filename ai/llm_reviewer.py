"""LLM 二审模块 — 用 DashScope GLM (即 Hermes 同款模型) 复审 FinTwitBERT 结果。

触发时机: 规则层命中反讽成分 / 或 高影响低置信 时，由 SentimentAnalyzer 调用。
设计核心: 输出严格对齐 FinBERT 的 schema，保证评分数据干净一致 ——
         label ∈ {bullish, bearish, neutral}, prob_dist 三项和≈1, confidence。
         不返回自由文本判断，返回可无缝替换 FinBERTResult 的结构。

Key 来源: 复用 ~/.hermes/.env 中的 DASHSCOPE_API_KEY（与 Hermes 同一 GLM）。
"""
import os
import json
import re
from typing import Optional
from dataclasses import dataclass
from pathlib import Path

from loguru import logger


def _load_dashscope_env():
    """从 ~/.hermes/.env 加载 DASHSCOPE_API_KEY / BASE_URL（若无则用项目 .env）。"""
    hermes_env = Path.home() / ".hermes" / ".env"
    if hermes_env.exists():
        for line in hermes_env.read_text().splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip()
            if k in ("DASHSCOPE_API_KEY", "DASHSCOPE_BASE_URL") and not os.getenv(k):
                os.environ[k] = v

# 延迟到 LLMReviewer.__init__ 时调用，避免模块导入副作用

DASHSCOPE_API_KEY  = os.getenv("DASHSCOPE_API_KEY", "")
DASHSCOPE_BASE_URL = os.getenv("DASHSCOPE_BASE_URL",
                               "https://dashscope.aliyuncs.com/compatible-mode/v1")
LLM_MODEL = os.getenv("LLM_REVIEW_MODEL", "deepseek-v4-flash")

# 延迟导入，避免无 key 时整个模块崩
try:
    from openai import OpenAI
    _HAS_OPENAI = True
except ImportError:
    _HAS_OPENAI = False


@dataclass
class ReviewResult:
    """与 FinBERTResult 同构，可直接覆盖。"""
    score: float                       # -1.0 ~ +1.0
    label: str                         # bullish / bearish / neutral
    prob_dist: dict                    # {bullish, bearish, neutral}
    confidence: float
    reason: str
    reviewed: bool = True


_SYSTEM = """你是金融推文情感分析二审员。FinTwitBERT(一个BERT模型)做了初判，但你更懂反讽、
讽刺和"谁对谁的态度"——比如"骂某分析师的看空观点蠢"实际是看多。

任务: 给出推文作者对所提股票的真实情绪方向，并输出概率分布(三项相加≈1)。

输出必须是单行 JSON，严格遵循此格式，不要任何额外文字:
{"label": "bullish|bearish|neutral", "bullish": 0.x, "bearish": 0.x, "neutral": 0.x, "confidence": 0.x, "reason": "≤20字理由"}

规则:
- bullish=看多, bearish=看空, neutral=中性/无关
- label 取三项概率最高者
- 概率三项相加=1, 保留4位小数
- confidence=你对该判断的确信度 0~1
- 只输出 JSON, 不要 markdown, 不要解释"""

_JSON_RE = re.compile(r'\{[^{}]*"label"[^{}]*\}', re.S)


def _parse(text: str) -> Optional[dict]:
    m = _JSON_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def _normalize(d: dict) -> Optional[ReviewResult]:
    label = str(d.get("label", "")).lower().strip()
    if label not in ("bullish", "bearish", "neutral"):
        # fallback: 取概率最高项
        probs = {k: float(d.get(k, 0)) for k in ("bullish", "bearish", "neutral")}
        label = max(probs, key=probs.get)
    p = {k: max(0.0, float(d.get(k, 0.0))) for k in ("bullish", "bearish", "neutral")}
    s = sum(p.values()) or 1.0
    p = {k: round(v / s, 4) for k, v in p.items()}   # 归一化到和=1
    if max(p, key=p.get) != label:
        label = max(p, key=p.get)
    score = round(p["bullish"] - p["bearish"], 4)
    return ReviewResult(
        score=score,
        label=label,
        prob_dist=p,
        confidence=round(min(1.0, float(d.get("confidence", p[label]))), 4),
        reason=str(d.get("reason", "llm_review"))[:60],
    )


class LLMReviewer:
    # 重试配置
    MAX_RETRIES = 1  # 减少重试次数，避免长时间等待
    RETRY_DELAY = 1.0  # 秒，指数退避基数
    TIMEOUT = 30  # 增加超时时间，DashScope API 响应较慢

    def __init__(self):
        if not _HAS_OPENAI:
            raise ImportError("需要 openai 包: pip install openai")
        _load_dashscope_env()  # 加载环境变量
        api_key = os.getenv("DASHSCOPE_API_KEY", "")
        base_url = os.getenv("DASHSCOPE_BASE_URL",
                             "https://dashscope.aliyuncs.com/compatible-mode/v1")
        if not api_key:
            raise RuntimeError("DASHSCOPE_API_KEY 未找到（~/.hermes/.env 或环境变量）")
        self._client = OpenAI(api_key=api_key, base_url=base_url)

    def review(
        self,
        text: str,
        symbol: str = "",
        finbert_label: str = "",
        finbert_prob: Optional[dict] = None,
        sarcasm_reasons: Optional[list] = None,
    ) -> Optional[ReviewResult]:
        """复审单条推文。失败返回 None（上层应保留 FinBERT 原判）。"""
        import time

        user = (
            f"股票: {symbol}\n"
            f"FinTwitBERT初判: {finbert_label} {finbert_prob or {}}\n"
            f"反讽规则层命中: {sarcasm_reasons or []}\n\n"
            f"推文: {text}\n\nJSON:"
        )

        last_error = None
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                resp = self._client.chat.completions.create(
                    model=LLM_MODEL,
                    messages=[
                        {"role": "system", "content": _SYSTEM},
                        {"role": "user", "content": user},
                    ],
                    temperature=0.1,
                    max_tokens=200,  # 增大以支持中文 reason
                    timeout=self.TIMEOUT,
                )
                raw = resp.choices[0].message.content.strip()
                break  # 成功，跳出重试循环
            except Exception as e:
                last_error = e
                if attempt < self.MAX_RETRIES:
                    delay = self.RETRY_DELAY * (2 ** attempt)
                    logger.debug("LLM review attempt {} failed ({}), retry in {:.1f}s",
                                 attempt + 1, e, delay)
                    time.sleep(delay)
                else:
                    logger.warning("LLM review failed after {} attempts ({}), 保留 FinBERT 原判",
                                   self.MAX_RETRIES + 1, e)
                    return None

        d = _parse(raw)
        if d is None:
            logger.warning("LLM 输出无法解析 JSON: {}", raw[:100])
            return None
        return _normalize(d)

    def batch_review(
        self,
        items: list[dict],
    ) -> dict[int, Optional[ReviewResult]]:
        """批量复审多条推文。

        items: [{id, text, symbol, finbert_label, sarcasm_reasons?}, ...]
        returns: {id: ReviewResult or None}
        """
        import time

        # 构造批量 prompt
        lines = []
        for item in items:
            reasons = item.get("sarcasm_reasons") or []
            text_clean = item["text"].replace("\n", " ").replace("\r", " ")[:300]
            lines.append(
                f"[{item['id']}] 股票:{item['symbol']} | "
                f"FinBERT:{item['finbert_label']} | "
                f"反讽:{'; '.join(reasons) if reasons else '无'} | "
                f"推文:{text_clean}"
            )

        user = "批量分析以下推文对各自股票的情绪态度。每行的序号是[ID]，不要改。\n\n"
        user += "\n".join(lines)
        user += "\n\n返回 JSON 数组，每个元素格式如上一对一任务。不要 markdown。示例:\n"
        user += '[{"id": 1, "label": "bullish", "bullish": 0.8, "bearish": 0.1, "neutral": 0.1, "confidence": 0.9, "reason": "看多理由"}]'

        _BATCH_SYSTEM = ("你是金融推文情感分析二审员。FinTwitBERT做了初判，但你更懂反讽。\n"
                         "任务: 给出每行推文作者对所提股票的真实情绪。\n"
                         "输出必须是 JSON 数组，每项: "
                         '{"id": 序号, "label": "bullish|bearish|neutral", '
                         '"bullish": 概率, "bearish": 概率, "neutral": 概率, '
                         '"confidence": 确信度, "reason": "≤20字理由"}')

        last_error = None
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                resp = self._client.chat.completions.create(
                    model=LLM_MODEL,
                    messages=[
                        {"role": "system", "content": _BATCH_SYSTEM},
                        {"role": "user", "content": user},
                    ],
                    temperature=0.1,
                    max_tokens=2000,
                    timeout=90,  # 批量处理需要更长时间
                )
                raw = resp.choices[0].message.content.strip()
                break
            except Exception as e:
                last_error = e
                if attempt < self.MAX_RETRIES:
                    delay = self.RETRY_DELAY * (2 ** attempt)
                    logger.debug("Batch LLM attempt {} failed ({}), retry in {:.1f}s",
                                 attempt + 1, e, delay)
                    time.sleep(delay)
                else:
                    logger.warning("Batch LLM failed after {} attempts ({}), 全部保留 FinBERT 原判",
                                   self.MAX_RETRIES + 1, e)
                    return {}

        # 解析 JSON 数组
        import json as _json
        try:
            arr = _json.loads(raw)
            if isinstance(arr, dict) and "id" in arr:
                arr = [arr]  # 单条处理
        except _json.JSONDecodeError:
            m = _JSON_RE.search(raw)
            if m:
                try:
                    arr = _json.loads("[" + m.group(0) + "]")
                except _json.JSONDecodeError:
                    logger.warning("Batch LLM JSON 解析失败: {}", raw[:200])
                    return {}
            else:
                logger.warning("Batch LLM JSON 解析失败: {}", raw[:200])
                return {}

        results: dict[int, Optional[ReviewResult]] = {}
        for entry in arr:
            if not isinstance(entry, dict):
                continue
            item_id = entry.get("id")
            if item_id is None:
                continue
            r = _normalize(entry)
            if r:
                r.reason = str(entry.get("reason", ""))[:60]
                results[int(item_id)] = r
            else:
                results[int(item_id)] = None

        return results


if __name__ == "__main__":
    import sys
    r = LLMReviewer()
    # 经典反讽: 骂 Bernstein 看空 INTC 是蠢的 → 实际看多
    res = r.review(
        text="Bernstein downgrade to sell on $INTC, dumbest call ever, 50% downside my ass",
        symbol="INTC",
        finbert_label="bearish",
        finbert_prob={"bullish": 0.0145, "bearish": 0.9841, "neutral": 0.0013},
        sarcasm_reasons=["贬义评价+权威/看空引用(疑似骂看空者)"],
    )
    print("二审结果:", res)
    if res:
        print(f"  label={res.label} score={res.score:+.3f} prob={res.prob_dist}")
        print(f"  (FinBERT 原判 bearish, 二审纠正 → {'✓ 识别为反讽/看多' if res.label!='bearish' else '仍看空'})")
