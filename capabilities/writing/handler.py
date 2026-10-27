"""Writing capability handler.

Loads prompt fragments based on detected axes, assembles a system prompt,
calls the LLM, and returns structured writing output.

The writing workflow:
1. Build terminology ledger from input material
2. Show confirmation gate (section + paragraph plan)
3. Draft from evidence outward
4. Run paragraph flow check
5. Return prose + notes

If kb_path is provided and calls_citation_engine=true, generates
[需要引用: 概念] markers for the Citation Engine to process later.
"""

from pathlib import Path
from typing import Any

from capabilities._shared.protocol import (
    make_result,
    load_fragment_text,
    assemble_system_prompt,
)


def _get_axes_values(context: dict[str, Any]) -> dict[str, str]:
    """Extract axis values from the RouteContext or context overrides."""
    axes = context.get("axes")
    if axes is None:
        return {}

    result: dict[str, str] = {}
    for attr in ["section", "language", "paper_type"]:
        val = getattr(axes, attr, None)
        if val:
            # section can be a list — take the first one
            if isinstance(val, list):
                result[attr] = val[0] if val else "intro"
            else:
                result[attr] = val
    return result


def _load_paper_context(paper_path: str | None,
                         section_hint: str = "") -> str:
    """Read the paper .docx and return context about the target section."""
    if not paper_path or not Path(paper_path).exists():
        return ""

    try:
        from core.docx_parser import DocxParser
        paper = DocxParser(paper_path).parse()

        lines = [f"论文标题: {paper.title}"]
        lines.append(f"章节数: {len(paper.chapters)}")

        # Find the closest chapter to the requested section
        for ch in paper.chapters:
            lines.append(f"\n## {ch.title}")
            for sec in ch.sections[:5]:  # first 5 sections only
                lines.append(f"  - {sec.heading}")
                if sec.text:
                    # Include first 200 chars of section content
                    preview = sec.text[:200].replace("\n", " ")
                    lines.append(f"    {preview}...")

        return "\n".join(lines)
    except Exception:
        return ""


def _load_kb_context(kb_path: str | None) -> str:
    """Load knowledge base context for citation-aware writing."""
    if not kb_path or not Path(kb_path).exists():
        return ""

    try:
        from core.reference_kb import ReferenceKB
        kb = ReferenceKB(kb_dir=kb_path)
        kb.load_index()

        lines = [f"知识库: {len(kb.entries)} 篇参考文献"]
        for entry in kb.entries[:20]:  # first 20 entries
            role = entry.meta.get("classification_role", "")
            lines.append(
                f"- [{role}] {entry.title[:80]} "
                f"({entry.authors}, {entry.year})"
            )
        return "\n".join(lines)
    except Exception:
        return ""


def _detect_section_from_request(request: str) -> str:
    """Detect target section from the user's request text.

    Returns one of: intro, lit-review, method, results, discussion,
    conclusion, abstract, outline.
    """
    mapping = {
        "intro": ["引言", "前言", "导论", "背景", "introduction", "研究背景"],
        "abstract": ["摘要", "abstract"],
        "lit-review": ["文献综述", "文献回顾", "综述", "literature review", "related work"],
        "method": ["方法", "方法论", "研究设计", "method", "实验设计", "研究方案"],
        "results": ["结果", "发现", "result", "findings"],
        "discussion": ["讨论", "分析", "discussion", "analysis"],
        "conclusion": ["结论", "总结", "conclusion", "summary"],
        "outline": ["大纲", "框架", "outline", "目录", "结构"],
    }
    request_lower = request.lower()
    for section, keywords in mapping.items():
        if any(kw in request_lower for kw in keywords):
            return section
    return "intro"  # default


def handle(context: dict[str, Any]) -> dict[str, Any]:
    """Execute the writing workflow.

    Context keys:
        paper_path, kb_path, request, axes, llm_client, model, manifest

    Returns structured result with generated text.
    """
    manifest = context.get("manifest", {})
    axes = context.get("axes")
    request = context.get("request", "")
    paper_path = context.get("paper_path")
    kb_path = context.get("kb_path")
    llm_client = context.get("llm_client")
    model = context.get("model", "deepseek-v4-pro")

    # Detect section from request if axes doesn't have it
    section = _detect_section_from_request(request)
    if axes and hasattr(axes, "section") and getattr(axes, "section", None):
        val = getattr(axes, "section")
        section = val[0] if isinstance(val, list) else val

    language = "zh"
    if axes and hasattr(axes, "language"):
        language = getattr(axes, "language", "zh")

    paper_type = "thesis"
    if axes and hasattr(axes, "paper_type"):
        paper_type = getattr(axes, "paper_type", "thesis")

    # Resolve fragment paths from manifest
    manifest_dir = Path(manifest.get("_manifest_dir", "."))
    axis_values = {
        "section": section,
        "language": language,
        "paper_type": paper_type,
    }

    # Load always_load fragments
    always_load = manifest.get("always_load", [])
    system_parts = []
    for path in always_load:
        text = load_fragment_text(path)
        if text.strip():
            system_parts.append(text)

    # Load axis-matched fragments
    fragments = manifest.get("fragments", {})
    for axis_name, axis_val in axis_values.items():
        axis_map = fragments.get(axis_name, {})
        if isinstance(axis_map, dict) and axis_val in axis_map:
            text = load_fragment_text(axis_map[axis_val])
            if text.strip():
                system_parts.append(text)

    system_prompt = "\n\n---\n\n".join(system_parts)

    # Load paper context
    paper_context = _load_paper_context(paper_path, section)

    # Load KB context if citation engine is needed
    kb_context = ""
    if manifest.get("calls_citation_engine") and kb_path:
        kb_context = _load_kb_context(kb_path)

    # If no LLM client, return the prompt assembly as result
    if not llm_client:
        return make_result(
            result_type="text",
            content=f"[Writing capability ready]\nSection: {section}\n"
                    f"Language: {language}\nPaper type: {paper_type}\n\n"
                    f"System prompt ({len(system_prompt)} chars assembled)\n\n"
                    f"Paper context: {paper_context[:500]}\n\n"
                    f"KB context: {kb_context[:500]}",
            metadata={
                "section": section,
                "language": language,
                "paper_type": paper_type,
                "prompt_length": len(system_prompt),
            },
        )

    # Build user message
    user_parts = []
    if paper_context:
        user_parts.append(f"## 论文上下文\n{paper_context}")
    if kb_context:
        user_parts.append(f"## 可用参考文献\n{kb_context}")
    user_parts.append(f"## 写作请求\n{request}")

    user_message = "\n\n".join(user_parts)

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
            "prompt_length": len(system_prompt),
        },
    )
