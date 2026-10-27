"""Citation Generator — Phase 2.

Generate academic-style citation content based on matched references.
Requirements: summarize viewpoint, avoid copying, avoid AI traces, maintain academic tone.
"""

import json
import re
from dataclasses import dataclass, field

from .reference_kb import ReferenceEntry


@dataclass
class GeneratedCitation:
    ref_index: int  # 1-based reference index
    ref_title: str
    paper_context: str  # the paper paragraph where citation will be placed
    citation_text: str  # generated citation content in Chinese
    citation_type: str  # "supporting", "contrasting", "foundational", "methodological"


class CitationGenerator:
    """Generate natural academic citations from matched references."""

    def __init__(self, client, model: str = "deepseek-v4-pro"):
        self.client = client
        self.model = model

    def generate(
        self,
        paper_section: str,
        ref: ReferenceEntry,
        system_prompt: str = "",
        source_chunks: list[dict] | None = None,
    ) -> GeneratedCitation:
        """Generate a citation paragraph linking a paper section to a reference.

        Args:
            paper_section: The paper paragraph text that needs a citation.
            ref: The reference to cite.
            system_prompt: Custom generation prompt.
        """
        try:
            message = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=system_prompt or self._default_system_prompt(),
                messages=[{
                    "role": "user",
                    "content": (
                        self._build_grounded_query(paper_section, ref, source_chunks)
                        if source_chunks
                        else self._build_generation_query(paper_section, ref)
                    ),
                }],
            )
            raw = ""
            for block in message.content:
                if hasattr(block, "text"):
                    raw += block.text
            result = self._parse_generation_response(raw, ref)

            # Post-generation quality check
            validation = self._validate_citation(result.citation_text, ref)
            if validation["flags"]:
                import sys
                print(
                    f"  [质量警告] {ref.title[:30]}... "
                    f"score={validation['score']:.2f} flags={validation['flags']}",
                    file=sys.stderr,
                )

            return result
        except Exception as exc:
            raise RuntimeError(f'Citation generation failed for {ref.title}: {exc}') from exc

    def generate_batch(
        self,
        paper_section: str,
        refs: list[ReferenceEntry],
        kb,
        system_prompt: str = "",
        source_chunks_map: dict[str, list[dict]] | None = None,
    ) -> list[GeneratedCitation]:
        """Generate citations for multiple references against one paper section.

        Args:
            source_chunks_map: Optional mapping of ref.title → source chunks for
                               grounded generation.
        """
        results = []
        for ref in refs:
            chunks = None
            if source_chunks_map:
                chunks = source_chunks_map.get(ref.title)
            result = self.generate(paper_section, ref, system_prompt,
                                   source_chunks=chunks)
            results.append(result)
        return results

    @staticmethod
    def _default_system_prompt() -> str:
        return (
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

    def _build_generation_query(self, paper_section: str, ref: ReferenceEntry) -> str:
        meta = getattr(ref, "meta", {}) or {}
        role = meta.get("classification_role", "")
        cited = meta.get("cited_by_count")
        quality = meta.get("kb_quality", "")
        req_level = meta.get("requirement_level", "")

        ref_info = (
            f"文献: {ref.title}\n"
            f"作者: {ref.authors} ({ref.year})\n"
            f"角色: {role}\n"
            f"摘要: {ref.abstract[:300]}\n"
            f"核心论点: {'; '.join(ref.key_arguments[:3])}\n"
            f"方法: {ref.methods[:150]}"
        )
        if cited is not None:
            ref_info += f"\n学术影响力: 已被引用 {cited} 次"

        instruction = "请为该段落生成引用该文献的学术化表述。"

        if role == "Theory Foundation":
            instruction += (
                "\n该文献是理论基础文献。"
                "请生成3-5句深度引用，说明其学术地位、核心理论贡献及其与本论文研究问题的关联。"
                "引用类型: foundational"
            )
        elif role == "Methodology":
            instruction += (
                "\n该文献是方法文献。请生成2-3句引用，"
                "说明其方法内容及本论文如何借鉴该方法。引用类型: methodological"
            )
        elif role == "Empirical Evidence":
            instruction += (
                "\n请生成2-3句引用，概括其实证发现并与本论文论点关联。"
                "引用类型: supporting 或 contrasting"
            )
        elif role == "Frontier":
            instruction += (
                "\n该文献是前沿研究。请生成2-3句引用，"
                "说明其探索的新方向及与本论文的关联。引用类型: contrasting 或 supporting"
            )
        elif role == "Unknown" or quality == "minimal":
            instruction += (
                "\n该文献信息有限。请生成1-2句简洁引用，仅将文献主题与段落关联。"
                "避免声称具体数据或观点。使用\"探讨了\"\"关注了\"等中性表述。"
            )

        if quality == "minimal" or req_level == "L3":
            instruction += "\n由于无法核实原文细节，请保持表述审慎。"
        elif quality == "low" and req_level in ("L2",):
            instruction += "\n摘要信息有限，请保持表述保守。"

        return (
            f"## 论文段落\n{paper_section[:500]}\n\n"
            f"## 参考文献\n{ref_info}\n\n"
            f"{instruction}"
        )

    def _build_grounded_query(
        self, paper_section: str, ref: ReferenceEntry,
        source_chunks: list[dict],
    ) -> str:
        """Build generation prompt with actual source text for grounded citation.

        source_chunks: list of {"text": str, "section": str, "chunk_index": int}
        """
        meta = getattr(ref, "meta", {}) or {}
        role = meta.get("classification_role", "")
        cited = meta.get("cited_by_count")

        ref_info = (
            f"文献: {ref.title}\n"
            f"作者: {ref.authors} ({ref.year})\n"
            f"角色: {role}\n"
            f"摘要: {ref.abstract[:300]}\n"
            f"核心论点: {'; '.join(ref.key_arguments[:3])}\n"
            f"方法: {ref.methods[:150]}"
        )
        if cited is not None:
            ref_info += f"\n学术影响力: 已被引用 {cited} 次"

        # Gather source passages (up to 2000 chars total)
        passages = []
        total_chars = 0
        for chunk in source_chunks:
            section_label = chunk.get("section", "")
            text = chunk.get("text", "")
            if total_chars + len(text) > 2000:
                remaining = 2000 - total_chars
                if remaining > 100:
                    passages.append(f"[{section_label}] {text[:remaining]}")
                break
            passages.append(f"[{section_label}] {text}")
            total_chars += len(text)

        passages_text = "\n\n".join(passages) if passages else "(无原文片段)"

        instruction = (
            "请为该段落生成引用该文献的学术化表述。\n\n"
            "重要：以上提供了该文献的真实原文片段。你必须基于原文内容生成引用，"
            "确保每条事实性论断都能在原文中找到依据。"
            "不要引用原文中未出现的数据、数字或观点。"
        )

        if role == "Theory Foundation":
            instruction += (
                "\n该文献是理论基础文献。请生成3-5句深度引用，"
                "说明其学术地位、核心理论贡献及其与本论文研究问题的关联。"
                "引用类型: foundational"
            )
        elif role == "Methodology":
            instruction += (
                "\n该文献是方法文献。请生成2-3句引用，"
                "说明其方法内容及本论文如何借鉴该方法。引用类型: methodological"
            )
        elif role == "Empirical Evidence":
            instruction += (
                "\n请生成2-3句引用，概括其实证发现并与本论文论点关联。"
                "引用类型: supporting 或 contrasting"
            )
        elif role == "Frontier":
            instruction += (
                "\n该文献是前沿研究。请生成2-3句引用，"
                "说明其探索的新方向及与本论文的关联。"
            )

        return (
            f"## 论文段落\n{paper_section[:500]}\n\n"
            f"## 参考文献\n{ref_info}\n\n"
            f"## 该文献的原文片段\n{passages_text}\n\n"
            f"{instruction}"
        )

    # Post-generation quality checks (adapted from academic writing quality rules)
    _AI_TRACE_PATTERNS = [
        (r"在当今(时代|社会)", "模糊时间锚定"),
        (r"众所周知|毋庸置疑|不言而喻", "冗余强调词"),
        (r"具有重要意义|深远影响|重大意义", "空洞价值判断"),
        (r"提供了(宝贵的|重要的)?(参考|借鉴|启示)", "泛泛赞誉"),
        (r"值得注意的是|值得强调的是|需要指出的是", "过度使用的过渡词"),
    ]

    _WEAK_LINK_PATTERNS = [
        (r"^.{1,20}(也|同样)(研究了|探讨了|分析了)", "弱关联-仅提及未深入"),
        (r"这与.{1,20}(的研究)?(一致|相符|吻合)", "弱关联-未说明一致内容"),
    ]

    @classmethod
    def _validate_citation(cls, text: str, ref: ReferenceEntry) -> dict:
        """Post-generation quality check. Returns {'score': 0-1, 'flags': [...]}."""
        flags = []
        score = 1.0

        # Length check (40-120 Chinese chars)
        char_count = len(text)
        if char_count < 20:
            flags.append(f"过短({char_count}字)")
            score -= 0.3
        elif char_count > 200:
            flags.append(f"过长({char_count}字)")
            score -= 0.1

        # AI trace check
        for pattern, label in cls._AI_TRACE_PATTERNS:
            if re.search(pattern, text):
                flags.append(f"AI痕迹-{label}")
                score -= 0.15

        # Weak link check
        for pattern, label in cls._WEAK_LINK_PATTERNS:
            if re.search(pattern, text):
                flags.append(label)
                score -= 0.15

        # Structural honesty: check if citation references content not in ref
        if ref.abstract and len(ref.abstract) > 20:
            # Quick check: if citation mentions specific numbers/percentages
            # not in abstract, it might be fabricated
            nums_in_citation = set(re.findall(r"\d+\.?\d*%?", text))
            nums_in_abstract = set(re.findall(r"\d+\.?\d*%?", ref.abstract))
            extra_nums = nums_in_citation - nums_in_abstract
            if extra_nums and len(nums_in_citation) > 1:
                flags.append("含参考文献摘要中未出现的数值")
                score -= 0.1

        return {"score": max(0.0, score), "flags": flags}

    @staticmethod
    def _parse_generation_response(raw: str, ref: ReferenceEntry) -> GeneratedCitation:
        data = {}
        # Try JSON in code block
        m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
        # Try raw JSON
        if not data:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                pass
        # Try first { ... }
        if not data:
            m = re.search(r"\{[\s\S]*\}", raw)
            if m:
                try:
                    data = json.loads(m.group(0))
                except json.JSONDecodeError:
                    pass
        if not isinstance(data, dict) or not isinstance(data.get('citation_text'), str) or not data['citation_text'].strip():
            raise ValueError('Model response lacks a valid citation_text')

        return GeneratedCitation(
            ref_index=0,
            ref_title=ref.title,
            paper_context="",
            citation_text=data.get("citation_text", raw.strip()),
            citation_type=data.get("citation_type", "supporting"),
        )
