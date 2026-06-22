"""FinTwitBERT 情绪分析器 — StephanAkkerman/FinTwitBERT-sentiment (GPU 加速)

模型谱系：
  yiyanghkust/finbert-pretrain
      └── StephanAkkerman/FinTwitBERT  (1000万条金融推文预训练)
              └── StephanAkkerman/FinTwitBERT-sentiment  (情感微调)

为什么用这个：
  - 专门用金融推特数据训练（FinTwit 社区语言）
  - 直接理解 $TICKER 、🚀 、moon 等散户语言
  - 38K 人工标注 + 142万合成金融推文微调
  - MIT 开源，可离线使用
"""
import os
import torch
import warnings
from typing import Dict
from dataclasses import dataclass

warnings.filterwarnings("ignore")

from transformers import AutoTokenizer, AutoModelForSequenceClassification

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CACHE_DIR = os.path.join(_BASE, "data", "models", "fintwitbert")

# 标签顺序由 config.json 中的 id2label 决定，下面是运行时动态读取的
# 默认假设: {0: neutral, 1: positive, 2: negative}（运行后自动覆盖）
_DEFAULT_LABEL_MAP = {0: "neutral", 1: "positive", 2: "negative"}


@dataclass
class FinBERTResult:
    score: float                    # -1.0 ~ +1.0
    label: str                      # positive / neutral / negative
    prob_dist: Dict[str, float]
    confidence: float
    device: str                     # cuda / cpu


class FinBERTAnalyzer:
    """
    FinTwitBERT-sentiment — 金融推特专用情感分析器。

    GPU 优先: 自动检测 CUDA，有 GPU 则在 GPU 上推理。
    单例加载: 全进程只加载一次权重，避免重复初始化。
    批量推理: analyze_batch() 在 GPU 上比逐条快 5-10x。
    """

    MODEL_NAME = "StephanAkkerman/FinTwitBERT-sentiment"
    _tokenizer = None
    _model = None
    _device = None
    _label_map: Dict[int, str] = None

    @classmethod
    def _load(cls):
        if cls._model is not None:
            return cls._tokenizer, cls._model, cls._device

        # 选设备
        cls._device = (
            torch.device("cuda") if torch.cuda.is_available()
            else torch.device("cpu")
        )

        # 优先使用本地模型路径，避免联网下载
        local_model_path = _CACHE_DIR
        if os.path.exists(os.path.join(local_model_path, "config.json")):
            model_source = local_model_path
        else:
            model_source = cls.MODEL_NAME
        
        load_kwargs = {"cache_dir": _CACHE_DIR}

        cls._tokenizer = AutoTokenizer.from_pretrained(
            model_source, **load_kwargs
        )
        cls._model = AutoModelForSequenceClassification.from_pretrained(
            model_source, **load_kwargs
        )
        cls._model.eval()
        cls._model.to(cls._device)

        # 从模型 config 读取真实标签顺序
        if hasattr(cls._model.config, "id2label"):
            cls._label_map = {
                int(k): v.lower()
                for k, v in cls._model.config.id2label.items()
            }
        else:
            cls._label_map = _DEFAULT_LABEL_MAP

        device_name = (
            torch.cuda.get_device_name(0)
            if cls._device.type == "cuda"
            else "CPU"
        )
        from loguru import logger
        logger.info(
            "FinTwitBERT loaded on {} | labels: {}",
            device_name,
            cls._label_map,
        )
        return cls._tokenizer, cls._model, cls._device

    def _probs_to_result(self, probs: torch.Tensor) -> FinBERTResult:
        label_map = self.__class__._label_map or _DEFAULT_LABEL_MAP
        pred_idx = probs.argmax().item()
        label = label_map[pred_idx]

        prob_dict = {label_map[i]: round(probs[i].item(), 4) for i in range(len(probs))}

        # 动态匹配正面/负面标签（bullish/bearish 或 positive/negative）
        pos_keys = {"positive", "bullish"}
        neg_keys = {"negative", "bearish"}
        score = 0.0
        for k, v in prob_dict.items():
            if k in pos_keys:
                score += v
            elif k in neg_keys:
                score -= v

        return FinBERTResult(
            score=round(score, 4),
            label=label,
            prob_dist=prob_dict,
            confidence=round(probs[pred_idx].item(), 4),
            device=self.__class__._device.type,
        )

    def analyze(self, text: str, symbol: str = "") -> FinBERTResult:
        """单条推理（建议用 analyze_batch 获得更好 GPU 利用率）"""
        return self.analyze_batch([text])[0]

    def analyze_batch(self, texts: list[str]) -> list[FinBERTResult]:
        """批量推理，GPU 时比逐条快 5-10x"""
        tokenizer, model, device = self._load()

        inputs = tokenizer(
            texts,
            return_tensors="pt",
            truncation=True,
            max_length=512,
            padding=True,
        )
        inputs = {k: v.to(device) for k, v in inputs.items()}

        with torch.no_grad():
            outputs = model(**inputs)
            probs_batch = torch.softmax(outputs.logits, dim=-1)

        return [self._probs_to_result(p) for p in probs_batch]


# ── 快速测试 ──────────────────────────────────────────
if __name__ == "__main__":
    import sys, io, time
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    analyzer = FinBERTAnalyzer()
    tests = [
        "$NVDA is absolutely crushing it! Strong buy 🚀🚀",
        "$TSLA downgrade to SELL, declining margins, overvalued",
        "$AAPL reported earnings in line with expectations",
        "Huge opportunity in $META, don't miss this dip",
        "$INTC disaster quarter, avoid at all costs 📉",
        "Nice 9% pre market move for $PARA, pump my calls 🤑",
    ]

    print(f"\n{'='*65}")
    print(f"Model: {FinBERTAnalyzer.MODEL_NAME}")
    print(f"{'='*65}")

    # 单条测试
    t0 = time.time()
    for text in tests:
        r = analyzer.analyze(text)
        print(
            f"[{r.label:>8}] score={r.score:+.3f} "
            f"conf={r.confidence:.3f} dev={r.device} | {text[:55]}"
        )
    print(f"\nSingle: {time.time()-t0:.2f}s for {len(tests)} texts")

    # 批量测试
    t0 = time.time()
    results = analyzer.analyze_batch(tests)
    print(f"Batch : {time.time()-t0:.2f}s for {len(tests)} texts (should be faster on GPU)")
