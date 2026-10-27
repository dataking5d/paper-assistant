"""Prompt loader — loads prompts from files with hardcoded fallbacks.

This module is ADDITIVE. It does not modify any existing core module.
Core modules keep their hardcoded _default_system_prompt() as safety fallbacks.
The Router and Capabilities use this loader to resolve prompts from files.

Usage:
    from core.prompt_loader import load_prompt, resolve_prompt

    # Load prompt from file (returns hardcoded fallback if file missing)
    prompt = load_prompt("citation_generator")

    # Resolve: explicit override > file > hardcoded fallback
    prompt = resolve_prompt("citation_generator", override=custom_prompt)
"""

from pathlib import Path

_PROMPTS_ROOT = Path(__file__).parent.parent / "prompts"

# ── Fallback prompts (current hardcoded strings from core modules) ──

_GENERATION_FALLBACK = (
    "你是一位学术论文写作助手，擅长为论文段落生成自然的文献引用内容。\n\n"
    "要求：\n"
    "1. 用中文撰写引用内容，根据文献角色调整深度和类型\n"
    "2. 理论基础文献：说明其学术地位和核心理论贡献\n"
    "3. 方法文献：说明具体方法内容及本论文如何借鉴\n"
    "4. 实证研究：概括发现并与本论文论点关联\n"
    "5. 信息有限的文献：保持简洁和审慎\n"
    "6. 避免逐字复制原文，用你自己的语言概括\n"
    "7. 保持学术表达风格，避免常见的AI写作痕迹\n\n"
    "输出格式（严格JSON）：\n"
    '{"citation_text": "引用内容", '
    '"citation_type": "supporting|contrasting|foundational|methodological"}'
)

_MATCHING_FALLBACK = (
    "你是一位学术论文写作指导。你的任务是将一批参考文献分配到论文的各个章节/小节中，"
    "确保每篇文献都能在论文中获得合理引用。\n\n"
    "要求：\n"
    "1. 每篇文献必须分配到一个具体位置（不允许跳过任何一篇）\n"
    "2. 基于文献的真实内容（摘要、核心论点、角色）进行匹配，不要仅看标题\n"
    "3. 角色匹配策略：\n"
    "   - Theory Foundation → 优先匹配理论基础/文献综述/理论框架章节\n"
    "   - Methodology → 优先匹配方法/研究设计/分析框架章节\n"
    "   - Empirical Evidence → 优先匹配分析/讨论/实证章节\n"
    "   - Review → 优先匹配文献综述/研究背景章节\n"
    "   - Frontier → 优先匹配引言/研究背景/讨论章节\n"
    "   - Unknown → 优先匹配文献综述或扩展建议位置\n"
    "4. 如果文献与论文主题关联较弱，请将它分配到文献综述或理论基础部分\n"
    "5. 相关性始终是第一信号。角色策略仅在相关性相近时做 tie-breaking。\n\n"
    "输出JSON数组（严格JSON格式，不要输出其他内容）：\n"
    '[{"ref_id": 文献编号, "ref_title": "文献标题", '
    '"chapter": "分配的章节", "section": "分配的小节", '
    '"reason": "匹配理由(30字内)", "confidence": 0.85}]\n\n'
    "confidence: 0.9+ 高度匹配, 0.7-0.89 合理匹配, 0.5-0.69 勉强可放, <0.5 无法合理匹配"
)

_KB_SUMMARIZE_FALLBACK = (
    "你是一位学术研究助手。请从以下文献内容中提取关键信息。"
    "只输出JSON，不要输出其他内容。"
)

_KNOWLEDGE_CHECK_FALLBACK = (
    "你是一位学术文献目录专家，熟悉翻译学、语言学、文学领域的中英文文献。"
    "用户会给你一个文献标题列表，请对每篇文献简要说明你所知道的信息。\n\n"
    "## 输出要求\n"
    "严格输出 JSON 数组，每个元素对应一篇文献：\n"
    '[\n'
    '  {\n'
    '    "index": 1,\n'
    '    "known": true,\n'
    '    "abstract_like": "该文献的核心理论/贡献/内容的简要描述（1-3句中文）",\n'
    '    "keywords": ["关键词1", "关键词2"],\n'
    '    "role_hint": "Theory Foundation|Methodology|Empirical Evidence|'
    'Review|Frontier|Unknown"\n'
    '  },\n'
    '  ...\n'
    ']\n\n'
    "## 重要提醒\n"
    "- 只写你有把握的内容，不确定的内容不要编造\n"
    "- 如果是你确实不了解的文献，设置 known=false\n"
    "- known=false 时 abstract_like 留空字符串，keywords 留空数组\n"
    "- role_hint 是你对文献学术角色的最佳判断"
)

_EXPLORATION_FALLBACK = (
    "你是学术文献研究助手。请基于提供的文献内容回答用户问题。"
    "引用具体文献时标注文献来源。用中文回答。"
)

# ── Registry: key → (file_path, fallback) ──

PROMPT_REGISTRY: dict[str, dict] = {
    "citation_generator": {
        "file": "generation_prompt.md",
        "fallback": _GENERATION_FALLBACK,
    },
    "citation_matcher": {
        "file": "matching_prompt.md",
        "fallback": _MATCHING_FALLBACK,
    },
    "kb_summarize": {
        "file": "fragments/kb_summarize.md",
        "fallback": _KB_SUMMARIZE_FALLBACK,
    },
    "llm_knowledge_check": {
        "file": "fragments/llm_knowledge_check.md",
        "fallback": _KNOWLEDGE_CHECK_FALLBACK,
    },
    "exploration_synthesize": {
        "file": "fragments/exploration_synthesize.md",
        "fallback": _EXPLORATION_FALLBACK,
    },
}


def _load_from_path(key: str) -> str:
    """Read a prompt file from disk."""
    entry = PROMPT_REGISTRY.get(key)
    if not entry:
        return ""
    file_path = _PROMPTS_ROOT / entry["file"]
    if file_path.exists():
        return file_path.read_text(encoding="utf-8")
    return ""


def load_prompt(key: str) -> str:
    """Load prompt from file. Returns hardcoded fallback if file missing."""
    content = _load_from_path(key)
    if content:
        return content
    entry = PROMPT_REGISTRY.get(key)
    return entry["fallback"] if entry else ""


def resolve_prompt(key: str, override: str = "") -> str:
    """Resolve a prompt: explicit override > file on disk > hardcoded fallback."""
    if override:
        return override
    return load_prompt(key)


def has_file(key: str) -> bool:
    """Check if a prompt file exists on disk."""
    entry = PROMPT_REGISTRY.get(key)
    if not entry:
        return False
    return (_PROMPTS_ROOT / entry["file"]).exists()
