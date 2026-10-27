"""Deterministic axis detection with keyword + regex matching.

Unlike Nature Skills (which uses LLM for detection), Paper Assistant uses
deterministic matching for reliability. LLM fallback only for ambiguous cases.
"""

import re
from dataclasses import dataclass, field


# ── Task type keywords ──

TASK_KEYWORDS: dict[str, list[str]] = {
    "citation": [
        "引用", "参考文献", "citation", "bibliography", "reference",
        "插入引用", "匹配引用", "生成引用", "引用管理", "参考文献格式",
        "编号", "重编号", "文献管理", "引用插入",
    ],
    "writing": [
        "写", "写作", "起草", "撰写", "write", "draft",
        "写论文", "写引言", "写摘要", "写方法", "写结论",
        "帮我写", "需要写", "重新写", "写一段", "写文献综述",
        "大纲", "outline", "框架", "写研究背景",
    ],
    "polishing": [
        "润色", "修改", "改进", "提升", "优化", "polish", "revise",
        "improve", "enhance", "edit", "改写", "润饰", "润稿",
        "语言润色", "学术化", "修饰", "改一下", "改改",
    ],
    "review": [
        "审稿", "审核", "检查", "评估", "review", "evaluate",
        "审阅", "评审", "audit", "审稿人", "预审",
    ],
    "response": [
        "回复", "返修", "rebuttal", "response", "回答审稿",
        "修改意见", "审稿意见", "revision",
    ],
}

# ── Language detection ──

_CHINESE_CHAR = re.compile(r"[一-鿿㐀-䶿]")

# ── Paper type keywords ──

PAPER_TYPE_KEYWORDS: dict[str, list[str]] = {
    "thesis": [
        "毕业论文", "学位论文", "硕士", "博士", "本科",
        "毕业设计", "thesis", "dissertation",
    ],
    "journal": [
        "期刊", "投稿", "发表", "paper", "journal", "article",
        "manuscript", "SCI", "SSCI", "Nature", "Science",
    ],
}

# ── Style keywords ──

STYLE_KEYWORDS: dict[str, list[str]] = {
    "gbt7714-numeric": ["GB/T 7714", "国标", "顺序编码", "顺序编码制", "gbt", "gbt7714"],
    "gbt7714-author-year": ["著者-出版年", "著者出版年", "作者-年份", "著者年"],
    "apa7": ["APA", "apa", "APA7", "apa7"],
    "ieee": ["IEEE", "ieee"],
    "mla": ["MLA", "mla"],
    "chicago": ["Chicago", "chicago"],
    "harvard": ["Harvard", "harvard"],
}


@dataclass
class RouteContext:
    """Detected routing context from user input."""

    task_types: list[str] = field(default_factory=lambda: ["citation"])
    language: str = "zh"
    paper_type: str = "thesis"
    style: str = "gbt7714-numeric"
    raw_request: str = ""

    @property
    def primary_task(self) -> str:
        return self.task_types[0] if self.task_types else "citation"


def detect_task_types(text: str) -> list[str]:
    """Determine what the user wants to do. Returns all matching task types."""
    text_lower = text.lower()
    scores: dict[str, int] = {}
    for task, keywords in TASK_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scores[task] = score

    if not scores:
        return ["citation"]  # default fallback

    # Sort by score descending
    ranked = sorted(scores, key=scores.get, reverse=True)
    # Return all tasks with score > 0, best first
    return ranked


def detect_language(text: str) -> str:
    """Detect output language. zh if >30% Chinese characters, else en."""
    if not text:
        return "zh"
    chinese_chars = len(_CHINESE_CHAR.findall(text))
    total_chars = len(text.replace(" ", "").replace("\n", ""))
    if total_chars == 0:
        return "zh"
    return "zh" if chinese_chars / total_chars > 0.3 else "en"


def detect_paper_type(text: str) -> str:
    """Detect whether user is writing a thesis or journal article."""
    text_lower = text.lower()
    thesis_score = sum(1 for kw in PAPER_TYPE_KEYWORDS["thesis"] if kw in text_lower)
    journal_score = sum(1 for kw in PAPER_TYPE_KEYWORDS["journal"] if kw in text_lower)
    if thesis_score > journal_score:
        return "thesis"
    if journal_score > thesis_score:
        return "journal"
    return "thesis"  # default


def detect_style(text: str) -> str:
    """Detect reference style from user input."""
    text_lower = text.lower()
    for style, keywords in STYLE_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            return style
    return "gbt7714-numeric"  # default


def detect_context(request: str, **overrides) -> RouteContext:
    """Full axis detection from user request + explicit overrides.

    Args:
        request: The user's natural language request.
        **overrides: Explicit axis values (task_type, language, paper_type, style).

    Returns:
        RouteContext with all detected axis values.
    """
    return RouteContext(
        task_types=overrides.get("task_types") or detect_task_types(request),
        language=overrides.get("language") or detect_language(request),
        paper_type=overrides.get("paper_type") or detect_paper_type(request),
        style=overrides.get("style") or detect_style(request),
        raw_request=request,
    )
