"""Polishing capability handler.

Loads polishing-specific prompt fragments based on detected axes,
assembles a system prompt with failure-mode diagnosis, and calls the LLM
to polish the given text.

Unlike writing (which generates from scratch), polishing operates on
existing text. If paper_path is provided, the target section's text
is extracted and polished in-place.
"""

from pathlib import Path
from typing import Any

from capabilities._shared.protocol import (
    make_result,
    load_fragment_text,
    assemble_system_prompt,
)


def _detect_section_from_request(request: str) -> str:
    """Detect target section from the request. Default to full-paper."""
    mapping = {
        "intro": ["引言", "前言", "导论", "背景", "introduction"],
        "abstract": ["摘要", "abstract"],
        "lit-review": ["文献综述", "文献回顾", "综述", "literature review"],
        "method": ["方法", "方法论", "研究设计", "method"],
        "results": ["结果", "发现", "result"],
        "discussion": ["讨论", "分析", "discussion", "analysis"],
        "conclusion": ["结论", "总结", "conclusion"],
    }
    request_lower = request.lower()
    for section, keywords in mapping.items():
        if any(kw in request_lower for kw in keywords):
            return section
    return "full-paper"


def _extract_section_text(paper_path: str | None, section: str) -> str:
    """Extract text from a specific section of the paper."""
    if not paper_path or not Path(paper_path).exists():
        return ""

    try:
        from core.docx_parser import DocxParser
        paper = DocxParser(paper_path).parse()

        if section == "full-paper":
            parts = []
            for ch in paper.chapters:
                parts.append(f"## {ch.title}")
                for sec in ch.sections:
                    if sec.text:
                        parts.append(sec.text)
            return "\n\n".join(parts)

        # Try to find the matching section
        for ch in paper.chapters:
            for sec in ch.sections:
                title_lower = sec.heading.lower()
                if section in title_lower or any(
                    kw in title_lower for kw in _SECTION_ALIASES.get(section, [])
                ):
                    return f"## {sec.heading}\n{sec.text}"

        # Fallback: return first few sections
        parts = []
        for ch in paper.chapters[:2]:
            for sec in ch.sections[:3]:
                if sec.text:
                    parts.append(f"## {sec.heading}\n{sec.text[:500]}")
        return "\n\n".join(parts)
    except Exception:
        return ""


_SECTION_ALIASES = {
    "intro": ["引言", "前言", "导论", "研究背景", "绪论"],
    "abstract": ["摘要"],
    "lit-review": ["文献综述", "文献回顾", "研究综述", "相关研究"],
    "method": ["方法", "研究设计", "研究方案", "方法论"],
    "results": ["结果", "研究发现", "数据分析"],
    "discussion": ["讨论", "分析", "结果讨论"],
    "conclusion": ["结论", "总结", "结语"],
}


def handle(context: dict[str, Any]) -> dict[str, Any]:
    """Execute the polishing workflow.

    Context keys:
        paper_path, request, axes, llm_client, model, manifest

    Returns structured result with polished text.
    """
    manifest = context.get("manifest", {})
    request = context.get("request", "")
    paper_path = context.get("paper_path")
    llm_client = context.get("llm_client")
    model = context.get("model", "deepseek-v4-pro")

    # Detect section
    section = _detect_section_from_request(request)
    language = "zh"
    paper_type = "thesis"

    axes = context.get("axes")
    if axes:
        if hasattr(axes, "language"):
            language = getattr(axes, "language", "zh")
        if hasattr(axes, "paper_type"):
            paper_type = getattr(axes, "paper_type", "thesis")

    # Load always_load fragments
    always_load = manifest.get("always_load", [])
    system_parts = []
    for path in always_load:
        text = load_fragment_text(path)
        if text.strip():
            system_parts.append(text)

    # Load axis-matched fragments
    fragments = manifest.get("fragments", {})
    for axis_name, axis_val in {"section": section, "language": language, "paper_type": paper_type}.items():
        axis_map = fragments.get(axis_name, {})
        if isinstance(axis_map, dict) and axis_val in axis_map:
            text = load_fragment_text(axis_map[axis_val])
            if text.strip():
                system_parts.append(text)

    system_prompt = "\n\n---\n\n".join(system_parts)

    # Extract text to polish
    source_text = ""
    if paper_path:
        source_text = _extract_section_text(paper_path, section)

    # Build user message
    if source_text:
        user_message = (
            f"## 原文 (待润色)\n{source_text}\n\n"
            f"## 润色要求\n{request}\n\n"
            f"请对以上文本进行学术润色。输出格式：先输出润色后的完整文本，"
            f"然后在'## 修改说明'下列出关键修改点。"
        )
    else:
        user_message = (
            f"## 润色要求\n{request}\n\n"
            f"请说明你需要润色的文本内容。如果需要我读取论文中的特定章节，"
            f"请使用 --paper 参数指定论文文件路径。"
        )

    # If no LLM client, return diagnostic info
    if not llm_client:
        return make_result(
            result_type="text",
            content=f"[Polishing capability ready]\nSection: {section}\n"
                    f"Language: {language}\nPaper type: {paper_type}\n\n"
                    f"System prompt ({len(system_prompt)} chars assembled)\n"
                    f"Source text: {len(source_text)} chars",
            metadata={
                "section": section,
                "language": language,
                "paper_type": paper_type,
                "source_length": len(source_text),
            },
        )

    # Call LLM
    try:
        response = llm_client.messages.create(
            model=model,
            max_tokens=4096,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        text = "".join(
            block.text for block in response.content
            if hasattr(block, "text")
        )
    except Exception as e:
        return make_result(result_type="error", error=f"LLM call failed: {e}")

    return make_result(
        result_type="text",
        content=text,
        metadata={
            "section": section,
            "language": language,
            "paper_type": paper_type,
            "source_length": len(source_text),
        },
    )
