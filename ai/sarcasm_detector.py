"""反讽/讽刺检测规则层 — 零成本，纯词库 + 组合判定。

设计目标:
  金融推文中 FinTwitBERT 把"骂看空者蠢"误判为看空(词袋级情感极性)。
  本层用规则识别反讽成分(贬义评价 + 权威引用 + 明显反讽标记)，
  命中后建议上层调 LLM 二审，让 LLM 理解"谁对谁的态度"。

输出 SarcasmSignal，不改变情感结果，只决定"是否需要二审"。
"""
import re
from dataclasses import dataclass, field


# ── A. 贬义/讽刺评价词（作者在骂某人/某观点愚蠢）──────────────
# 命中即说明句中存在"负面评价的对象"，需判断对象是不是看空者本身
DEROGATORY = [
    # 直白骂蠢
    r"\bdumb(est)?\b", r"\bidiot(s)?\b", r"\bmoron(s)?\b", r"\bstupid\b",
    r"\bclueless\b", r"\bbraindead\b", r"\bretard(ed)?\b", r"\bdumbest\b",
    # 反讽式夸奖（正话反说）
    r"\bbrilliant\b", r"\bgenius\b", r"\bsmartest\b", r"\breally smart\b",
    r"\blegendary\b", r"\bmasterpiece\b", r"\bgreat call\b", r"\bnice call\b",
    # 贬义名词
    r"\bclown(s)?\b", r"🤡", r"\bjoke\b", r"\blaughable\b", r"\bridiculous\b",
    r"\bembarrass(ing|ed)?\b", r"\bsavage\b", r"\bdelusion(al)?\b",
    r"\bcopium\b", r"\bhopium\b", r"\bcry(ing)?\b", r"\bcrying\b",
]

# ── B. 明显反讽句式标记（高信号，几乎就是反讽）──────────────────
SARCASM_EXPLICIT = [
    r"/s\b",                       # "/s" 标记
    r"yeah,?\s*right",             # "yeah right"
    r"\bsure\b,?\s*(right|\.\.\.)",  # "sure right" / "sure..." (反讽式同意)
    r"\bthis is fine\b",           # 反讽梗
    r"🔥.*fine|fine.*🔥",          # 🔥+fine 反讽梗
    r"\btell me you\b.*\bwithout telling\b",  # 反讽梗
    r"\bwho would('?ve| have) thought\b",
    r"\bshocking\b(?!\s+news)",    # "shocking" 反讽(排除新闻)
    r"\bsurprise surprise\b",
    r"\bbig if true\b",           # 反讽梗
]

# ── C. 权威/分析师/机构引用（看空观点常来源于此）────────────────
AUTHORITY_REFS = [
    # 机构名
    r"\b(bernstein|goldman|sachs|morgan stanley|jpmorgan|jpm|wells?|citi|"
    r"baird|jefferies|wedbush|bofa|bank of america|barclays|ubs|credit suisse|"
    r"deutsche bank|piper|canaccord|needham|oppenheimer|cowen|stifel|rbc|"
    r"kbw|bmo|seaport|rosenblatt|citron|hindenburg|muddy waters|gordon)\b",
    # 角色词
    r"\banalyst(s)?\b", r"\bshort seller(s)?\b", r"\bbear(s)?\b",
    r"\bhedge fund(s)?\b", r"\bwall street\b", r"\bthe street\b",
    r"\bperma[- ]?bear(s)?\b", r"\bdoom(sday)?er(s)?\b",
]

# ── D. 看空/看多动作词（判断"被骂的观点"方向）────────────────────
BEARISH_ACTIONS = [
    r"\bdowngrade(d)?\b", r"\b(sell|sold|selling)\b", r"\bshort(ed|ing|s)?\b",
    r"\bunderperform\b", r"\breduce(d)?\b", r"\bcut to\b", r"\btrim(med)?\b",
    r"\bbearish\b", r"\bnegative rating\b", r"\bcrash\b", r"\btank(ed|ing|s)?\b",
    r"\bdump(ed|ing|s)?\b", r"\brip(ped|ping|s)?\s+(lower|down|apart)\b",
    r"\bprice target\b.*\b(cut|lower(ed)?|reduce(d)?)\b",
    r"\b(pt|price target)\b.*\b\$?\d+.*down\b",
]
BULLISH_ACTIONS = [
    r"\bupgrade(d)?\b", r"\b(buy|bought|buying)\b", r"\blong\b", r"\bbullish\b",
    r"\boutperform\b", r"\boverweight\b", r"\bmoon(ed|ing|s)?\b",
    r"\brocket(s|ed)?\b", r"🚀", r"\bpump(ed|ing|s)?\b", r"\bsqueeze(d|s)?\b",
]

# ── E. 引号/大写反讽（把对手口号"加引号"表讽刺）──────────────────
# 用 unicode 变量避免 raw-string 引号转义混乱
_QUOTE = r"[\u201c\u201d\"\x27]"
QUOTED_SLOGAN = [
    _QUOTE + r"(strong sell|sell|bearish|short)" + _QUOTE,
    _QUOTE + r"(buy the dip|to the moon|moon|rocket)" + _QUOTE,
]

# ── F. 反讽转折/对比连接词 ──────────────────────────────────────
CONTRAST = [
    r"\bactually\b.*\b(a |the )?bull\b",   # "actually bullish"
    r"\bturns out\b",                       # "turns out ..."
    r"\bsecretly\b",
]


# ── 预编译所有正则模式（性能优化）────────────────────────────────
_COMPILED = {
    "derog": [re.compile(p, re.IGNORECASE) for p in DEROGATORY],
    "expl": [re.compile(p, re.IGNORECASE) for p in SARCASM_EXPLICIT],
    "auth": [re.compile(p, re.IGNORECASE) for p in AUTHORITY_REFS],
    "bear": [re.compile(p, re.IGNORECASE) for p in BEARISH_ACTIONS],
    "bull": [re.compile(p, re.IGNORECASE) for p in BULLISH_ACTIONS],
    "quot": [re.compile(p, re.IGNORECASE) for p in QUOTED_SLOGAN],
    "contr": [re.compile(p, re.IGNORECASE) for p in CONTRAST],
}


@dataclass
class SarcasmSignal:
    hit: bool                          # 是否建议触发 LLM 二审
    confidence: float                  # 0.0~1.0 反讽置信度
    reasons: list[str] = field(default_factory=list)   # 命中的规则类别
    markers: list[str] = field(default_factory=list)   # 具体命中的词/模式

    def __repr__(self):
        return (f"SarcasmSignal(hit={self.hit}, conf={self.confidence:.2f}, "
                f"reasons={self.reasons}, markers={self.markers})")


def _scan_compiled(text: str, compiled_patterns: list) -> list[str]:
    """对文本执行一组预编译正则，返回所有命中项(去重保序)。"""
    t = text.lower()
    hits = []
    for p in compiled_patterns:
        m = p.search(t)
        if m:
            hits.append(m.group(0).strip())
    return hits


def detect(text: str) -> SarcasmSignal:
    """纯文本成分检测 → 反讽置信度 + 是否建议二审。

    判定逻辑(从强到弱):
      1. 贬义评价 + (权威引用 OR 看空动作)  → 典型反讽(骂看空者蠢) → 强
      2. 明显反讽标记 (/s, 🤡, "this is fine") → 强
      3. 贬义评价 + 权威引用 仅有贬义      → 中
      4. 引号口号                            → 弱(交 LLM)
    """
    derog = _scan_compiled(text, _COMPILED["derog"])
    expl  = _scan_compiled(text, _COMPILED["expl"])
    auth  = _scan_compiled(text, _COMPILED["auth"])
    bear  = _scan_compiled(text, _COMPILED["bear"])
    bull  = _scan_compiled(text, _COMPILED["bull"])
    quot  = _scan_compiled(text, _COMPILED["quot"])
    contr = _scan_compiled(text, _COMPILED["contr"])

    reasons, markers = [], []
    confidence = 0.0

    # 规则1: 贬义评价 + 权威引用 + 看空动作 = 骂看空者(最强反讽信号)
    if derog and (auth or bear):
        confidence = max(confidence, 0.9)
        reasons.append("贬义评价+权威/看空引用(疑似骂看空者)")
        markers += derog + auth + bear

    # 规则2: 明显反讽标记
    if expl:
        confidence = max(confidence, 0.85)
        reasons.append("明显反讽标记")
        markers += expl

    # 规则3: 贬义评价单独出现(中信号，需 LLM 判断指向)
    elif derog:
        confidence = max(confidence, 0.55)
        reasons.append("含贬义评价词")
        markers += derog

    # 规则4: 权威引用 + 看空动作 但无贬义(可能中性报告，弱信号)
    if (auth and bear) and not derog:
        confidence = max(confidence, 0.3)
        reasons.append("权威看空引用(可能是反讽或正常报告)")
        markers += auth + bear

    # 规则5: 引号口号(讽刺性引用)
    if quot:
        confidence = max(confidence, 0.5)
        reasons.append("引号/口号疑似讽刺性引用")
        markers += quot

    # 规则6: 转折连接暗示反转
    if contr:
        confidence = max(confidence, 0.45)
        reasons.append("转折/反转连接词")
        markers += contr

    # 去重 markers
    markers = list(dict.fromkeys(markers))
    # 建议二审阈值: 置信 >= 0.5
    hit = confidence >= 0.5
    if not reasons:
        return SarcasmSignal(hit=False, confidence=0.0)
    return SarcasmSignal(hit=hit, confidence=round(confidence, 2),
                         reasons=reasons, markers=markers)


if __name__ == "__main__":
    # 测试样本
    cases = [
        # 反讽: 骂 Bernstein 看空 INTC 是蠢的 → 实际看多
        ("Bernstein downgrade to sell on $INTC, dumbest call ever, 50% downside my ass 🤡",
         True, "典型反讽"),
        # 反讽: 明显标记
        ("$TSLA going bankrupt any day now /s", True, "/s标记"),
        # 正常看空
        ("$TSLA downgrade to sell, declining margins, overvalued", False, "正常报告"),
        # 正常看多
        ("$NVDA crushing it! Strong buy 🚀🚀", False, "正常看多"),
        # 反讽: 引号
        ('"Strong Sell" on $AMD right before it pumps 20%, geniuses these analysts',
         True, "引号+贬义"),
        # 反讽: this is fine
        ("Portfolio down 40% this week but this is fine 🔥", True, "反讽梗"),
        # 正常中性
        ("$AAPL reported earnings in line with expectations", False, "中性"),
    ]
    print(f"{'TEXT':<55} {'期望':<6} {'命中':<6} {'置信':<6} 类别")
    print("=" * 95)
    for text, expect, note in cases:
        sig = detect(text)
        ok = "✓" if sig.hit == expect else "✗"
        print(f"{text[:53]:<55} {str(expect):<6} {str(sig.hit):<6} "
              f"{sig.confidence:<6} {ok} {note}")
        if sig.hit:
            print(f"    → markers: {sig.markers}")
