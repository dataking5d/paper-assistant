"""Citation Coverage Engine — Phase 1 (revised).

Goal: ensure ALL references are cited at least once in the paper.
Algorithm: ref-centric assignment → coverage gap detection → section expansion.
"""

import json
import re
from dataclasses import dataclass, field
from collections import defaultdict

from .docx_parser import Paper
from .reference_kb import ReferenceKB


@dataclass
class RefAssignment:
    ref_index: int          # 1-based index in KB
    ref_title: str
    chapter: str
    section: str
    reason: str
    confidence: float       # 0.0-1.0
    placement_type: str     # "direct" | "expansion" | "forced"


@dataclass
class CoverageResult:
    assignments: list[RefAssignment]
    total_refs: int
    covered_refs: int
    uncovered_refs: list[int]     # ref indices with no good placement
    expansion_suggestions: list[dict]  # {section, refs_to_add, suggested_text}
    coverage_pct: float


class CitationMatcher:
    """Coverage-first citation matching. All references must be placed."""

    def __init__(self, client, model: str = "deepseek-v4-pro"):
        self.client = client
        self.model = model

    def match_all(
        self,
        paper: Paper,
        kb: ReferenceKB,
        system_prompt: str = "",
        vector_store=None,
    ) -> CoverageResult:
        """Assign every reference to the best paper section.

        Two-pass algorithm:
        1. Batch classification: assign each ref → best section
        2. Coverage check: flag unplaced refs, suggest expansions
        """
        if not kb.entries:
            return CoverageResult(
                assignments=[], total_refs=0, covered_refs=0,
                uncovered_refs=[], expansion_suggestions=[], coverage_pct=0.0,
            )

        # Build paper section catalog
        sections_catalog = self._build_section_catalog(paper)

        # Build ref catalog (title + summary)
        refs_catalog = self._build_ref_catalog(kb)

        # Semantic pre-filtering: build section→ref relevance matrix if vector_store available
        self._semantic_scores: dict[str, dict[int, float]] = {}
        if vector_store and hasattr(vector_store, 'is_ready') and vector_store.is_ready:
            for sec in sections_catalog:
                query_text = f"{sec['heading']} {sec['summary']}"
                results = vector_store.search(query_text, top_k=max(3, len(kb.entries) // max(len(sections_catalog), 1) + 1))
                for r in results:
                    ref_idx = r.get("ref_index", -1)
                    if ref_idx >= 0:
                        self._semantic_scores.setdefault(sec["id"], {})[ref_idx] = 1.0 - r.get("distance", 0.5)
            if self._semantic_scores:
                print(f"  语义预筛选: {sum(len(v) for v in self._semantic_scores.values())} 条 section→ref 关联")

        # Pass 1: batch assignment — all refs → best sections
        prompt = system_prompt or self._default_system_prompt()
        assignments, unplaced = self._batch_assign(refs_catalog, sections_catalog, prompt)

        # Pass 2: for unplaced refs, suggest section expansions
        expansion_suggestions = []
        if unplaced:
            expansion_suggestions = self._suggest_expansions(
                unplaced, refs_catalog, sections_catalog, prompt
            )
            # Try assigning unplaced refs to expansion sections
            for sugg in expansion_suggestions:
                for ref_idx in sugg.get("refs_to_add", []):
                    ref_title = kb.entries[ref_idx].title if ref_idx < len(kb.entries) else ""
                    assignments.append(RefAssignment(
                        ref_index=ref_idx + 1,
                        ref_title=ref_title,
                        chapter=sugg.get("chapter", ""),
                        section=sugg.get("section", ""),
                        reason=f"扩展段落: {sugg.get('reason', '')}",
                        confidence=0.6,
                        placement_type="expansion",
                    ))

        # Build coverage stats
        placed_indices = {a.ref_index for a in assignments}
        all_indices = set(range(1, len(kb.entries) + 1))
        still_unplaced = sorted(all_indices - placed_indices)

        return CoverageResult(
            assignments=assignments,
            total_refs=len(kb.entries),
            covered_refs=len(placed_indices),
            uncovered_refs=still_unplaced,
            expansion_suggestions=expansion_suggestions,
            coverage_pct=len(placed_indices) / max(len(kb.entries), 1),
        )

    # ------------------------------------------------------------------
    # Pass 1: Ref → Section assignment (batched, global indices)
    # ------------------------------------------------------------------

    def _batch_assign(
        self,
        refs: list[dict],
        sections: list[dict],
        system_prompt: str,
    ) -> tuple[list[RefAssignment], list[int]]:
        """Batch refs to avoid LLM output truncation. Uses global ref indices."""
        batch_size = 6
        all_assignments = []

        sections_text = "\n".join(
            f"[{s['id']}] {s['chapter']} > {s['heading']} | {s['summary'][:150]}"
            for s in sections
        )

        for batch_start in range(0, len(refs), batch_size):
            batch_refs = refs[batch_start:batch_start + batch_size]

            # Use GLOBAL indices in prompt (1-35, not local 1-12)
            refs_text = "\n\n".join(
                f"文献{ref['index']}: {ref['title']}\n"
                f"  角色: {ref.get('role', '未知')}\n"
                f"  摘要: {ref['abstract'][:200]}\n"
                f"  论点: {'; '.join(ref['arguments'][:3])}"
                for ref in batch_refs
            )

            try:
                message = self.client.messages.create(
                    model=self.model,
                    max_tokens=8192,
                    system=system_prompt,
                    messages=[{
                        "role": "user",
                        "content": (
                            f"## 论文结构（共{len(sections)}个位置）\n{sections_text}\n\n"
                            f"## 待分配文献（第{batch_start+1}-{batch_start+len(batch_refs)}篇，共{len(refs)}篇）\n{refs_text}\n\n"
                            f"请为以上每篇文献找到最合适的引用位置。输出JSON数组。"
                        ),
                    }],
                )
                raw = ""
                for block in message.content:
                    if hasattr(block, "text"):
                        raw += block.text

                items = self._parse_batch_response(raw)
                for item in items:
                    matched_ref = self._find_ref_by_title(
                        item.get("ref_title", ""), batch_refs, item.get("ref_id", 0)
                    )
                    if matched_ref is None:
                        continue
                    confidence = float(item.get("confidence", 0.5))
                    # Blend with semantic score if available
                    if getattr(self, '_semantic_scores', None):
                        sec_id = item.get("section_id", "")
                        ref_id = matched_ref["index"]
                        if sec_id in self._semantic_scores and ref_id in self._semantic_scores[sec_id]:
                            semantic_conf = self._semantic_scores[sec_id][ref_id]
                            confidence = 0.7 * confidence + 0.3 * semantic_conf
                    all_assignments.append(RefAssignment(
                        ref_index=matched_ref["index"],
                        ref_title=matched_ref["title"],
                        chapter=item.get("chapter", ""),
                        section=item.get("section", ""),
                        reason=item.get("reason", ""),
                        confidence=confidence,
                        placement_type="direct",
                    ))
            except Exception as exc:
                for ref in batch_refs:
                    all_assignments.append(RefAssignment(
                        ref_index=ref["index"],
                        ref_title=ref["title"],
                        chapter="",
                        section="",
                        reason=f"LLM调用失败: {exc}",
                        confidence=0.0,
                        placement_type="forced",
                    ))

        # Separate placed vs unplaced
        placed = [a for a in all_assignments if a.confidence >= 0.5 and a.section]
        unplaced_indices = []
        placed_indices_set = {a.ref_index for a in placed}
        for ref in refs:
            if ref["index"] not in placed_indices_set:
                unplaced_indices.append(ref["index"] - 1)  # 0-based

        return placed, unplaced_indices

    # ------------------------------------------------------------------
    # Pass 2: Expansion suggestions for unplaced refs
    # ------------------------------------------------------------------

    def _suggest_expansions(
        self,
        unplaced_indices: list[int],
        refs: list[dict],
        sections: list[dict],
        system_prompt: str,
    ) -> list[dict]:
        """Suggest where to add transitional paragraphs for unplaced refs."""
        unplaced_refs = [r for r in refs if r["index"] - 1 in unplaced_indices]
        if not unplaced_refs:
            return []

        refs_text = "\n\n".join(
            f"文献{ref['index']}: {ref['title']}\n  摘要: {ref['abstract'][:150]}"
            for ref in unplaced_refs
        )

        sections_text = "\n".join(
            f"[{s['id']}] {s['chapter']} > {s['heading']} | {s['summary']}"
            for s in sections
        )

        try:
            message = self.client.messages.create(
                model=self.model,
                max_tokens=2048,
                system=(
                    "你是学术论文写作助手。以下文献未能在论文中找到直接匹配的段落，"
                    "但需要在论文中被引用。请建议将这些文献插入到论文的哪个位置（如文献综述、理论基础部分），"
                    "并给出一个简短的过渡句示例（20字内）。输出JSON数组。"
                ),
                messages=[{
                    "role": "user",
                    "content": (
                        f"## 论文结构\n{sections_text}\n\n"
                        f"## 未分配文献\n{refs_text}\n\n"
                        f"请为每篇文献建议插入位置和过渡方式。输出JSON: "
                        '[{"ref_id": 数字, "chapter": "章节名", "section": "小节名", '
                        '"reason": "插入理由", "transition_hint": "过渡句示例"}]'
                    ),
                }],
            )
            raw = ""
            for block in message.content:
                if hasattr(block, "text"):
                    raw += block.text

            data = self._parse_batch_response(raw)
            result = []
            # Group by section
            by_section = defaultdict(list)
            for item in data:
                key = f"{item.get('chapter', '')} > {item.get('section', '')}"
                by_section[key].append({
                    "ref_idx": item.get("ref_id", 0) - 1,
                    "reason": item.get("reason", ""),
                    "transition_hint": item.get("transition_hint", ""),
                })

            for section_key, ref_entries in by_section.items():
                parts = section_key.split(" > ", 1)
                result.append({
                    "chapter": parts[0],
                    "section": parts[1] if len(parts) > 1 else "",
                    "refs_to_add": [e["ref_idx"] for e in ref_entries],
                    "reason": "; ".join(e["reason"] for e in ref_entries[:2]),
                    "suggested_text": "; ".join(e["transition_hint"] for e in ref_entries[:2]),
                })
            return result
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_section_catalog(self, paper: Paper) -> list[dict]:
        catalog = []
        idx = 1
        for ch in paper.chapters:
            catalog.append({
                "id": f"S{idx}",
                "chapter": ch.title,
                "heading": "引言/概述",
                "summary": ch.intro[:150] if ch.intro else "(无内容)",
            })
            idx += 1
            for sec in ch.sections:
                catalog.append({
                    "id": f"S{idx}",
                    "chapter": ch.title,
                    "heading": sec.heading,
                    "summary": sec.text[:200],
                })
                idx += 1
        return catalog

    def _build_ref_catalog(self, kb: ReferenceKB) -> list[dict]:
        catalog = []
        for i, entry in enumerate(kb.entries, 1):
            meta = getattr(entry, "meta", {}) or {}
            catalog.append({
                "index": i,
                "title": entry.title,
                "abstract": entry.abstract or entry.full_text[:300],
                "arguments": entry.key_arguments or [],
                "methods": entry.methods,
                "role": meta.get("classification_role", ""),
                "cited_by_count": meta.get("cited_by_count"),
                "quality": meta.get("kb_quality", ""),
            })
        return catalog

    @staticmethod
    def _default_system_prompt() -> str:
        return (
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

    @staticmethod
    def _parse_batch_response(raw: str) -> list[dict]:
        # Try ```json block
        m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
        # Try raw JSON
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        # Try first [ ... ] block
        m = re.search(r"\[[\s\S]*\]", raw)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        # Fallback: try to parse each line as a JSON object
        results = []
        for line in raw.split("\n"):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    results.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return results

    @staticmethod
    def _find_ref_by_title(title: str, batch_refs: list[dict], ref_id: int = 0) -> dict | None:
        """Match LLM-returned title back to a ref in the batch."""
        if not title:
            return None
        # 1. Exact match
        for ref in batch_refs:
            if title == ref["title"]:
                return ref
        # 2. Substring match
        for ref in batch_refs:
            if title in ref["title"] or ref["title"] in title:
                return ref
        # 3. Trust ref_id if it points to a valid ref in this batch
        if ref_id:
            for ref in batch_refs:
                if ref["index"] == ref_id:
                    return ref
        # 4. Fuzzy: 60% Chinese character overlap
        title_chars = set(title)
        for ref in batch_refs:
            ref_chars = set(ref["title"])
            if ref_chars and title_chars:
                overlap = len(title_chars & ref_chars) / min(len(title_chars), len(ref_chars))
                if overlap > 0.6:
                    return ref
        return None


def coverage_to_report(result: CoverageResult) -> list[dict]:
    """Flatten CoverageResult into rows for Excel export."""
    rows = []
    for a in result.assignments:
        rows.append({
            "文献编号": a.ref_index,
            "文献标题": a.ref_title,
            "分配章节": a.chapter,
            "分配小节": a.section,
            "匹配原因": a.reason,
            "置信度": a.confidence,
            "插入方式": a.placement_type,
        })

    # Add uncovered section
    if result.uncovered_refs:
        rows.append({})  # empty row as separator
        rows.append({
            "文献编号": "[!] 未覆盖",
            "文献标题": f"共{len(result.uncovered_refs)}篇文献未找到合理位置",
            "分配章节": "",
            "分配小节": "",
            "匹配原因": f"索引: {result.uncovered_refs}",
            "置信度": "",
            "插入方式": "",
        })

    # Add expansion suggestions
    if result.expansion_suggestions:
        rows.append({})
        rows.append({
            "文献编号": "[+] 扩展建议",
            "文献标题": "以下位置建议增加过渡段落以容纳更多引用",
            "分配章节": "",
            "分配小节": "",
            "匹配原因": "",
            "置信度": "",
            "插入方式": "",
        })
        for sugg in result.expansion_suggestions:
            rows.append({
                "文献编号": "扩展",
                "文献标题": sugg.get("suggested_text", ""),
                "分配章节": sugg.get("chapter", ""),
                "分配小节": sugg.get("section", ""),
                "匹配原因": sugg.get("reason", ""),
                "置信度": "",
                "插入方式": "expansion",
            })

    return rows
