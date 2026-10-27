"""Paper Agent v3 — Unified adaptive pipeline with per-reference intelligence.

Single pipeline with 7 decision nodes:
  A: Reference role classification (Theory Foundation / Methodology / etc.)
  B: Requirement level assessment (L1/L2/L3)
  C: Resource resolution (download / skip / proceed_without)
  D: KB entry quality assessment
  E: Match strategy selection (theory_priority / analysis_priority)
  F: Citation strategy (type / depth / annotation)
  G: Reference style configuration (export layer only)

Principle: every reference gets its own data sufficiency profile.
"""

import json
import os
import re
import shutil
import sys
import time
import zipfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import requests

from .docx_parser import DocxParser, Paper
from .reference_kb import ReferenceKB, ReferenceEntry
from .citation_matcher import CitationMatcher, coverage_to_report
from .citation_generator import CitationGenerator
from .docx_editor import DocxEditor, CitationEdit
from .classifier import (
    ClassificationInput, ClassificationResult,
    classify_references_batch, classify_reference,
    role_to_requirement_modifier,
    get_citation_type, get_citation_depth, get_match_strategy,
    _parse_json_response,
)


class PaperAgent:
    """Unified adaptive paper enhancement agent."""

    def __init__(self, llm_client=None, model: str = "deepseek-v4-pro",
                 email: str = "", delay: float = 1.0, explore: bool = False,
                 embedding_model: str = ""):
        self.client = llm_client
        self.model = model
        self.email = email
        self.delay = delay
        self.explore_mode = explore
        self.embedding_model = embedding_model
        self.session = requests.Session()
        self.session.headers["User-Agent"] = (
            "PaperAssistant/3.0 (mailto:research@example.com)"
        )
        self.mode_label = ""
        self.step = 0
        self.total_steps = 0

    # ==================================================================
    # Main entry point — single unified pipeline
    # ==================================================================

    def run(self, paper_path: str | None = None, pdf_dir: str | None = None,
            ref_style=None, kb_path: str | None = None):
        """Single unified pipeline. All refs go through the same 7 steps.

        Args:
            paper_path: Path to thesis .docx (required).
            pdf_dir: Optional path to reference PDFs directory or .zip.
            ref_style: Optional. str (style ID), ReferenceStyleConfig, or None (interactive).
            kb_path: Optional existing knowledge base directory (skips Steps 2-4).
        """
        print()
        print("=" * 60)
        print("  Paper Assistant v3 — Unified Adaptive Pipeline")
        print("  每篇文献独立评估，按需获取，分级生成")
        print("=" * 60)
        print()

        # ── Validate ──
        if not paper_path or not os.path.exists(paper_path):
            print(f"错误: 论文文件不存在: {paper_path}")
            return
        self.paper_path = paper_path
        self.ref_style = ref_style
        self.total_steps = 8
        self.mode_label = "统一自适应管道"

        # ── STEP 1: Parse & Extract ──
        self.step = 0
        self._progress("解析论文结构")

        parser = DocxParser(paper_path)
        paper = parser.parse()
        print(f"  论文: {paper.title}")
        print(f"  章节: {len(paper.chapters)}")

        # Resolve reference titles: companion .txt > paper references section
        paper_dir = os.path.dirname(os.path.abspath(paper_path))
        paper_stem = Path(paper_path).stem
        companion_paths = [
            os.path.join(paper_dir, f"{paper_stem}.refs.txt"),
            os.path.join(paper_dir, "文献.txt"),
        ]
        ref_titles = []
        refs_source = ""
        for cp in companion_paths:
            if os.path.exists(cp):
                ref_titles = self._load_titles_from_file(cp)
                refs_source = f"文件 {os.path.basename(cp)}"
                break

        if not ref_titles:
            ref_titles = self._extract_ref_titles_from_paper(paper)
            refs_source = "论文参考文献章节"

        if not ref_titles:
            print(f"  未找到参考文献列表。请提供以下任一来源：")
            print(f"  1. 在论文中标注参考文献章节（如 '参考文献' 标题）")
            print(f"  2. 在论文同目录下创建 {paper_stem}.refs.txt 或 文献.txt")
            return

        print(f"  参考文献: {len(ref_titles)} 篇 (来源: {refs_source})")

        # Auto-save extracted refs as companion file for future reuse
        auto_save_path = os.path.join(paper_dir, f"{paper_stem}.refs.txt")
        if not os.path.exists(auto_save_path):
            try:
                with open(auto_save_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(ref_titles))
                print(f"  已保存参考文献列表: {os.path.basename(auto_save_path)}")
            except OSError:
                pass

        # ── Fast path: reuse existing KB ──
        if kb_path:
            self._progress("加载已有知识库")
            kb = ReferenceKB(kb_dir=kb_path)
            kb.load_index()
            if not kb.entries:
                print("  错误: 知识库为空")
                return
            print(f"  KB: {len(kb.entries)} 篇 ({self._kb_quality(kb)})")

            # Load existing vector index if available
            try:
                from .vector_store import ReferenceVectorStore
                kb.vector_store = ReferenceVectorStore(kb_path)
                if kb.vector_store.load():
                    print(f"  向量索引: {kb.vector_store.chunk_count} 个块")
            except ImportError:
                pass
            except Exception:
                pass

            self._progress("匹配 → 生成引用 → 编辑文档")
            out = self._match_generate_edit(paper, kb, paper_path, "_enhanced_v2",
                                             ref_style=ref_style)
            if out:
                print(f"\n  输出: {out}")
            return out

        # ── STEP 2A: Fetch API metadata ──
        self._progress("检索文献元数据 (OpenAlex → Crossref → Semantic Scholar)")
        metas: list[dict] = []
        for i, title in enumerate(ref_titles, 1):
            meta = self._find_metadata(title)
            metas.append(meta)
            if i % 5 == 0 or i == len(ref_titles):
                print(f"  {i}/{len(ref_titles)}...", end=" ", flush=True)
        print()
        has_abs = sum(1 for m in metas if m.get("abstract") and len(m["abstract"]) > 20)
        has_cited = sum(1 for m in metas if m.get("cited_by_count"))
        print(f"  {has_abs}/{len(ref_titles)} 有摘要, "
              f"{has_cited}/{len(ref_titles)} 有被引量")

        # ── STEP 2B: LLM knowledge check (supplement sparse API metadata) ──
        self._progress("LLM 知识补充 (检查模型对文献的了解)")
        llm_knowledge = self._batch_llm_knowledge_check(
            list(zip(ref_titles, metas)))
        enriched = sum(1 for v in llm_knowledge.values() if v.get("abstract"))
        if enriched:
            print(f"  LLM 知识补充: {enriched} 篇获得额外信息")
            # Inject LLM knowledge into metas so it persists through pipeline
            for title, meta in zip(ref_titles, metas):
                if title in llm_knowledge:
                    lk = llm_knowledge[title]
                    if not meta.get("abstract") or len(str(meta["abstract"])) < 20:
                        meta["abstract"] = lk.get("abstract", "")
                    if not meta.get("keywords"):
                        meta["keywords"] = lk.get("keywords", [])
                    meta["llm_knowledge"] = True

        # ── STEP 2C: Classify references (Decision Node A) ──
        self._progress("文献角色分类 (Node A: 规则 → 元数据 → LLM)")

        class_inputs: list[ClassificationInput] = []
        for title, meta in zip(ref_titles, metas):
            year = None
            if meta.get("year") and str(meta["year"]).isdigit():
                year = int(str(meta["year"]))
            api_abstract = meta.get("abstract", "")
            api_keywords = meta.get("keywords", [])
            # Inject LLM knowledge if API metadata is sparse
            if (not api_abstract or len(str(api_abstract)) < 20) and title in llm_knowledge:
                api_abstract = llm_knowledge[title].get("abstract", "")
                api_keywords = llm_knowledge[title].get("keywords", [])
            class_inputs.append(ClassificationInput(
                title=title,
                authors=meta.get("authors", ""),
                year=year,
                abstract=api_abstract,
                keywords=api_keywords,
                venue=meta.get("venue", ""),
                cited_by_count=meta.get("cited_by_count"),
            ))

        classifications = classify_references_batch(
            class_inputs, client=self.client, model=self.model, verbose=True,
        )

        role_counts: dict[str, int] = defaultdict(int)
        llm_calls = 0
        for c in classifications:
            role_counts[c.role] += 1
            if c.source == "rule+metadata+llm":
                llm_calls += 1
        print()
        print("  角色分布:")
        for role in ["Theory Foundation", "Methodology", "Empirical Evidence",
                     "Review", "Frontier", "Unknown"]:
            if role_counts.get(role, 0) > 0:
                print(f"    {role}: {role_counts[role]} 篇")
        print(f"  LLM辅助分类: {llm_calls}/{len(classifications)} 篇")

        # ── STEP 2C: Assess requirements (Decision Node B) ──
        self._progress("文献需求等级评估 (Node B)")

        requirements: list[dict] = []
        for title, meta, cls in zip(ref_titles, metas, classifications):
            req = self._assess_requirement(title, meta, cls)
            requirements.append(req)

        l1 = sum(1 for r in requirements if r["level"] == "L1")
        l2 = sum(1 for r in requirements if r["level"] == "L2")
        l3 = sum(1 for r in requirements if r["level"] == "L3")
        print(f"  L1 无需PDF: {l1} 篇")
        print(f"  L2 建议PDF: {l2} 篇")
        print(f"  L3 需要PDF: {l3} 篇")
        if l3 > 0:
            l3_pairs = [(t, r) for t, r in zip(ref_titles, requirements) if r["level"] == "L3"]
            for t, r in l3_pairs[:5]:
                kc = r.get("knowledge_confidence", "")
                mc = r.get("metadata_completeness", "")
                print(f"    [L3] {t[:55]}... ({kc}, {mc}, score={r['score']})")
            if len(l3_pairs) > 5:
                print(f"    ... 及其他 {len(l3_pairs) - 5} 篇")

        # ── STEP 3: Resource Resolution (Decision Node C) ──
        self._progress("资源匹配与获取 (Node C)")

        user_pdf_stems: set[str] = set()
        resolved_pdf_dir: str | None = None
        if pdf_dir:
            resolved_pdf_dir = self._resolve_pdf_dir(pdf_dir)
            if resolved_pdf_dir:
                user_pdf_stems = {p.stem for p in Path(resolved_pdf_dir).glob("*.pdf")}
                print(f"  用户提供 PDF: {len(user_pdf_stems)} 篇")
        if not user_pdf_stems:
            print("  未提供 PDF 目录")

        pdf_target_dir = os.path.join(os.path.dirname(paper_path) or ".", "ref_pdfs")
        resources: list[dict] = []
        for title, meta, req in zip(ref_titles, metas, requirements):
            res = self._resolve_resource(title, meta, req, user_pdf_stems,
                                          resolved_pdf_dir or "", pdf_target_dir)
            resources.append(res)

        auto_dl = sum(1 for r in resources if r["action"] == "auto_downloaded")
        user_pdf = sum(1 for r in resources if r["action"] == "use_user_pdf")
        skipped = sum(1 for r in resources if r["action"] == "skip")
        proceed = sum(1 for r in resources if r["action"] == "proceed_without")
        print(f"  用户PDF: {user_pdf}  自动下载: {auto_dl}  "
              f"跳过(L1): {skipped}  继续(无PDF): {proceed}")

        # ── Checkpoint: warn about missing PDFs ──
        if proceed > 0:
            l3_without = sum(1 for t, r, res in zip(ref_titles, requirements, resources)
                           if res["action"] == "proceed_without" and r["level"] == "L3")
            l2_without = sum(1 for t, r, res in zip(ref_titles, requirements, resources)
                           if res["action"] == "proceed_without" and r["level"] == "L2")
            print()
            print(f"  ⚠ {proceed} 篇文献无PDF可用")
            if l3_without > 0:
                print(f"    其中 {l3_without} 篇为 L3（强烈建议获取），{l2_without} 篇为 L2（建议获取）")
                print(f"    无PDF时引用将标注 [需核实原文]，质量受限")
                # List L3 missing titles
                l3_missing = [t for t, r, res in zip(ref_titles, requirements, resources)
                             if res["action"] == "proceed_without" and r["level"] == "L3"]
                for t in l3_missing[:5]:
                    print(f"      - {t[:70]}")
                if len(l3_missing) > 5:
                    print(f"      ... 及其他 {len(l3_missing) - 5} 篇")
            print()
            choice = self._ask_yn(
                "是否继续（无PDF的引用将标注需核实）？",
                "Y=继续生成  N=中止，我去补PDF  P=提供PDF目录路径")
            if choice == "N":
                print("  已中止。请补充 PDF 后重新运行。")
                return
            if choice == "P":
                new_pdf_dir = input("  请输入 PDF 目录路径: ").strip().strip('"').strip("'")
                if new_pdf_dir and os.path.isdir(new_pdf_dir):
                    resolved_pdf_dir = new_pdf_dir
                    user_pdf_stems = {p.stem for p in Path(resolved_pdf_dir).glob("*.pdf")}
                    # Re-resolve resources with new PDFs
                    resources = []
                    for title, meta, req in zip(ref_titles, metas, requirements):
                        res = self._resolve_resource(title, meta, req, user_pdf_stems,
                                                      resolved_pdf_dir, pdf_target_dir)
                        resources.append(res)
                    new_user_pdf = sum(1 for r in resources if r["action"] == "use_user_pdf")
                    new_proceed = sum(1 for r in resources if r["action"] == "proceed_without")
                    print(f"  重新匹配: 命中 {new_user_pdf} 篇，仍缺 {new_proceed} 篇")
                else:
                    print("  路径无效，按继续处理。")

        # ── STEP 4: KB Construction (Decision Node D) ──
        self._progress("构建知识库 (Node D)")

        kb_dir = os.path.join(os.path.dirname(paper_path) or ".", "kb")
        kb = ReferenceKB(kb_dir=kb_dir)
        for title, meta, cls, req, res in zip(ref_titles, metas, classifications,
                                                requirements, resources):
            entry = self._build_kb_entry(title, meta, cls, req, res)
            kb.entries.append(entry)

        # LLM summarization for entries with full text
        has_full = sum(1 for e in kb.entries if e.full_text and len(e.full_text) > 200)
        if has_full > 0 and self.client:
            print(f"  LLM摘要提取 ({has_full} 篇有全文)...")
            kb.summarize_with_llm(self.client, model=self.model)
            # Update quality for entries that got LLM summaries
            for e in kb.entries:
                if e.key_arguments and e.abstract:
                    e.meta["kb_quality"] = "high"

        kb._save_index()
        print(f"  KB: {len(kb.entries)} 篇 ({self._kb_quality(kb)})")

        # ── Step 4.5: Vector Index + Exploration (optional, --explore flag) ──
        if self.explore_mode:
            self._progress("构建向量索引 + 文献探索")
            try:
                n_chunks = kb.build_vector_index()
                if n_chunks > 0:
                    self._run_exploration(kb, paper)
                else:
                    print("  无可索引内容（所有文献仅有标题），跳过探索模式")
            except ImportError as e:
                print(f"  缺少依赖，跳过探索模式: {e}")
                print(f"  请安装: pip install chromadb sentence-transformers")
            except Exception as e:
                print(f"  向量索引构建失败: {e}")

        # ── STEPS 5-7: Match → Generate → Style → Edit ──
        self._progress("匹配 → 生成引用 → 编辑文档")

        out = self._match_generate_edit(paper, kb, paper_path, "_enhanced_v2",
                                         ref_style=ref_style)
        if out:
            print(f"\n  输出: {out}")
        else:
            print("\n  未能生成输出。")
        return out

    # ==================================================================
    # Decision Node B: Requirement Level Assessment
    # ==================================================================

    # ── Knowledge confidence classification ──
    _KNOWN_CORE = {
        # Translation studies canon — LLM training data is saturated with these
        "nida", "newmark", "catford", "venuti", "toury", "bassnett",
        "jakobson", "halliday", "chomsky", "saussure", "gutt",
        "hermans", "lefevere", "nord", "reiss", "vermeer",
        "munday", "baker", "pym", "chesterman", "holmes",
        "hatim", "mason", "house", "bell", "gentzler",
    }
    _KNOWN_DOMAIN = {
        # Chinese translation studies scholars — LLM has likely encountered
        "刘宓庆", "许钧", "谢天振", "方梦之", "刘军平",
        "连淑能", "廖七一", "马会娟", "苗菊", "陈福康",
        "张美芳", "平洪", "辛献云", "毛颖",
    }

    @staticmethod
    def _assess_knowledge_confidence(title: str, meta: dict) -> tuple[str, int, str]:
        """Determine how much LLM already knows about this reference.

        Returns: (label, score, reason)
        """
        title_lower = title.lower()
        authors = str(meta.get("authors", "")).lower()
        full_text = f"{authors} {title_lower}"

        # Check KNOWN_CORE English authors
        for name in PaperAgent._KNOWN_CORE:
            if name in full_text:
                return ("KNOWN_CORE", -4, f"经典理论文献 ({name.title()})")

        # Check KNOWN_DOMAIN Chinese scholars
        for name in PaperAgent._KNOWN_DOMAIN:
            if name in title:
                return ("KNOWN_DOMAIN", -2, f"领域常见文献 ({name})")

        # Check if LLM already supplemented this (signal from Step 2B)
        if meta.get("llm_knowledge"):
            return ("KNOWN_DOMAIN", -2, "LLM 知识检查中已识别")

        cited = meta.get("cited_by_count")
        if cited and isinstance(cited, (int, float)) and cited >= 1000:
            return ("KNOWN_DOMAIN", -2, f"高被引 ({int(cited)} 次)")

        return ("UNKNOWN", +2, "模型可能未见过此文献")

    def _assess_requirement(self, title: str, meta: dict,
                            classification: ClassificationResult) -> dict:
        """Determine L1/L2/L3 — information gap model.

        NeedPDFScore = KnowledgeConfidence + MetadataCompleteness
                     + Recency + LanguageRisk + CitationRole

        Returns: {
            "level": "L1"|"L2"|"L3", "action": str, "score": int,
            "reasons": [str],
            "knowledge_confidence": str, "metadata_completeness": str,
        }
        """
        import datetime
        score = 0
        reasons: list[str] = []
        detail: dict = {}

        # ── 1. Knowledge Confidence ──
        kc_label, kc_score, kc_reason = self._assess_knowledge_confidence(title, meta)
        score += kc_score
        reasons.append(f"知识置信度: {kc_label} ({kc_score:+d}) — {kc_reason}")
        detail["knowledge_confidence"] = kc_label

        # ── 2. Metadata Completeness ──
        has_abstract = bool(meta.get("abstract") and len(str(meta.get("abstract", ""))) > 20)
        has_keywords = bool(meta.get("keywords"))
        has_author = bool(meta.get("authors"))
        year_val = meta.get("year")

        if has_abstract:
            score -= 2
            detail["metadata_completeness"] = "完整摘要"
            reasons.append(f"元数据: 完整摘要 (-2)")
        elif has_keywords and has_author:
            detail["metadata_completeness"] = "部分摘要/关键词"
            reasons.append(f"元数据: 部分摘要/关键词 (0)")
        else:
            score += 3
            detail["metadata_completeness"] = "仅标题"
            reasons.append(f"元数据: 仅标题 (+3)")

        # ── 3. Recency ──
        year = int(year_val) if year_val and str(year_val).isdigit() else None
        current_year = datetime.datetime.now().year
        if year and year >= current_year - 2:
            score += 3
            reasons.append(f"时效性: {year}年 (+3)")
        elif year and year >= current_year - 6:
            score += 1
            reasons.append(f"时效性: {year}年 (+1)")
        else:
            reasons.append(f"时效性: {year or '未知'} (0)")

        # ── 4. Language Risk ──
        is_chinese = bool(re.search(r"[一-鿿]", title))
        if is_chinese and not has_abstract:
            score += 3
            reasons.append("语言风险: 中文且无摘要 (+3)")
        elif is_chinese:
            score += 1
            reasons.append("语言风险: 中文有摘要 (+1)")
        else:
            reasons.append("语言风险: 英文 (0)")

        # ── 5. Citation Role ──
        role_score = role_to_requirement_modifier(classification)
        score += role_score
        reasons.append(f"引用角色: {classification.role} ({role_score:+d})")

        # ── Gate ──
        if score <= 0:
            level, action = "L1", "无需PDF"
        elif score <= 4:
            level, action = "L2", "建议PDF"
        else:
            level, action = "L3", "需要PDF"

        return {
            "level": level, "action": action, "score": score,
            "reasons": reasons,
            "knowledge_confidence": detail.get("knowledge_confidence", ""),
            "metadata_completeness": detail.get("metadata_completeness", "仅标题"),
        }

    # ==================================================================
    # Decision Node C: Resource Resolution
    # ==================================================================

    def _resolve_resource(self, title: str, meta: dict, requirement: dict,
                           user_pdf_stems: set[str], pdf_dir: str,
                           target_dir: str) -> dict:
        """Determine what action to take for this reference's PDF.

        Returns: {"action": str, "pdf_path": str|None, "warning": str|None}
        """
        stem = re.sub(r"[^\w\s\-]", "", title)
        stem = re.sub(r"\s+", "_", stem).strip("_")[:120]

        # 1. User provided a matching PDF
        if user_pdf_stems and stem in user_pdf_stems:
            if pdf_dir and os.path.isdir(pdf_dir):
                for p in Path(pdf_dir).glob("*.pdf"):
                    if p.stem == stem:
                        return {"action": "use_user_pdf", "pdf_path": str(p), "warning": None}
            # Fuzzy match
            if pdf_dir and os.path.isdir(pdf_dir):
                for p in Path(pdf_dir).glob("*.pdf"):
                    if stem[:40] in p.stem or p.stem[:40] in stem:
                        return {"action": "use_user_pdf", "pdf_path": str(p), "warning": None}

        # 2. L1 → no PDF needed
        if requirement["level"] == "L1":
            return {"action": "skip", "pdf_path": None, "warning": None}

        # 3. L2 or L3 → try auto-download
        doi = meta.get("doi", "")
        if doi:
            pdf_url = self._find_pdf_url(doi, meta)
            if pdf_url:
                os.makedirs(target_dir, exist_ok=True)
                local = self._download_pdf(pdf_url, title, target_dir)
                if local:
                    return {"action": "auto_downloaded", "pdf_path": local, "warning": None}

        # 4. Cannot obtain
        if requirement["level"] == "L2":
            return {"action": "proceed_without", "pdf_path": None,
                    "warning": "建议提供PDF以提高引用质量"}
        return {"action": "proceed_without", "pdf_path": None,
                "warning": "强烈建议获取PDF——引用将标注[需核实原文]"}

    # ==================================================================
    # Decision Node D: KB Entry Builder
    # ==================================================================

    def _build_kb_entry(self, title: str, meta: dict,
                         classification: ClassificationResult,
                         requirement: dict, resource: dict) -> ReferenceEntry:
        """Build a ReferenceEntry with quality-gated data assembly.

        Quality levels: high (PDF+LLM summary), medium (good API data),
        low (abstract only), minimal (title only).
        """
        entry = ReferenceEntry(
            title=title,
            authors=meta.get("authors", ""),
            year=str(meta.get("year", "")),
            abstract=meta.get("abstract", ""),
            key_arguments=meta.get("key_arguments", []),
            methods="",
            full_text="",
            source_file=resource.get("pdf_path", "") or "",
        )

        # Parse PDF if available
        pdf_path = resource.get("pdf_path")
        if pdf_path and os.path.exists(pdf_path):
            try:
                raw_text = self._parse_single_pdf(pdf_path)
                if raw_text:
                    entry.full_text = raw_text
                    entry.source_file = pdf_path
            except Exception:
                pass

        # Determine quality
        has_abstract = bool(entry.abstract and len(entry.abstract) > 20)
        if entry.full_text and len(entry.full_text) > 200:
            quality = "high"  # Will be confirmed after LLM summarization
        elif classification.role == "Theory Foundation" and classification.confidence >= 0.85:
            quality = "medium"
        elif has_abstract:
            quality = "medium" if requirement["level"] == "L1" else "low"
        elif classification.role == "Unknown":
            quality = "minimal"
        else:
            quality = "low"

        entry.meta = {
            "cited_by_count": meta.get("cited_by_count"),
            "classification_role": classification.role,
            "classification_confidence": classification.confidence,
            "classification_reasons": classification.reasons,
            "classification_source": classification.source,
            "requirement_level": requirement["level"],
            "requirement_score": requirement.get("score"),
            "requirement_reasons": requirement.get("reasons", []),
            "knowledge_confidence": requirement.get("knowledge_confidence", ""),
            "metadata_completeness": requirement.get("metadata_completeness", ""),
            "kb_quality": quality,
            "resource_action": resource["action"],
        }

        return entry

    def _parse_single_pdf(self, pdf_path: str) -> str | None:
        """Parse a single PDF, returning extracted text or None."""
        try:
            import pdfplumber
            pages = []
            with pdfplumber.open(pdf_path) as pdf:
                for page in pdf.pages:
                    text = page.extract_text()
                    if text:
                        pages.append(text)
            return "\n".join(pages)
        except Exception:
            return None

    # ==================================================================
    # Core pipeline: match → generate → edit (Steps 5-7)
    # ==================================================================

    def _match_generate_edit(self, paper: Paper, kb: ReferenceKB,
                              paper_path: str, output_suffix: str,
                              ref_style=None) -> str | None:
        """Run match → generate → edit. Uses meta fields for per-ref strategy."""
        if not self.client:
            print("  [!] 未配置 LLM 客户端，跳过 AI 步骤")
            return None
        if not kb.entries:
            print("  [!] 知识库为空，跳过")
            return None

        # ── Match ──
        vs = getattr(kb, 'vector_store', None)
        matcher = CitationMatcher(self.client, model=self.model)
        result = matcher.match_all(paper, kb, vector_store=vs)
        print(f"  匹配覆盖率: {result.covered_refs}/{result.total_refs} "
              f"({result.coverage_pct:.0%})")

        # ── Generate ──
        generator = CitationGenerator(self.client, model=self.model)
        edits: list[CitationEdit] = []
        for a in result.assignments:
            ref = kb.entries[a.ref_index - 1] if a.ref_index <= len(kb.entries) else None
            if ref is None:
                continue
            section_text = self._find_section_text(paper, a.chapter, a.section)

            # Retrieve source chunks for grounded generation if vector store available
            source_chunks = None
            if vs and vs.is_ready:
                try:
                    source_chunks = vs.search_by_document(
                        query=section_text or a.section,
                        document_title=ref.title,
                        top_k=5,
                    )
                except Exception:
                    pass

            # Apply citation strategy from meta
            gen = generator.generate(
                section_text or a.section, ref,
                source_chunks=source_chunks,
            )

            # Annotate low-quality citations
            citation_text = gen.citation_text
            quality = ref.meta.get("kb_quality", "medium")
            if quality == "minimal":
                if "[需核实原文]" not in citation_text:
                    citation_text = citation_text.rstrip() + "[需核实原文]"
            elif quality == "low" and ref.meta.get("requirement_level") in ("L2", "L3"):
                if "[建议核实]" not in citation_text:
                    citation_text = citation_text.rstrip() + "[AI推荐，建议核实]"

            ref_full = ref.title
            if ref.authors or ref.year:
                parts = [p for p in [ref.authors, ref.title, str(ref.year)] if p]
                ref_full = ". ".join(parts)

            edits.append(CitationEdit(
                section_heading=a.section or a.chapter,
                citation_text=citation_text,
                ref_title=ref.title,
                ref_authors=ref.authors,
                ref_year=str(ref.year) if ref.year else "",
                ref_full=ref_full,
                confidence=a.confidence,
                reason=a.reason,
            ))
            role = ref.meta.get("classification_role", "?")
            print(f"  [{a.section or a.chapter}] {ref.title[:40]}... ({role})")

        # ── Style check (Decision Node G) ──
        from .style_config import ReferenceStyleConfig, get_builtin_style

        if isinstance(ref_style, str):
            style_config = get_builtin_style(ref_style)
        elif isinstance(ref_style, ReferenceStyleConfig):
            style_config = ref_style
        else:
            style_config = self._ask_reference_style()

        # ── Edit ──
        base = os.path.splitext(paper_path)[0]
        output_path = f"{base}{output_suffix}.docx"
        editor = DocxEditor()
        edit_result = editor.apply_edits(
            paper_path, edits, output_path, style_config=style_config,
        )

        print(f"  新增引用: {edit_result.edits_applied} 处")
        print(f"  参考文献: {len(edit_result.new_references)} 条")

        # ── Quality report ──
        self._write_quality_report(edits, kb, base, style_config)
        return edit_result.output_path

    # ==================================================================
    # Decision Node G: Reference Style Configuration
    # ==================================================================

    def _ask_reference_style(self):
        """Interactive reference format selection. Returns ReferenceStyleConfig.

        Priority: Template > Requirements > Standard > Auto-detect.
        """
        from .style_config import ReferenceStyleConfig, STYLE_MENU, get_builtin_style
        from .template_analyzer import (
            analyze_template_docx,
            parse_requirements_text,
            auto_detect_from_paper,
        )

        print()
        print("━" * 50)
        print("  参考文献格式配置")
        print("━" * 50)
        print()
        print("请选择参考文献格式来源：")
        print("  A. 上传模板 [推荐] — 学校模板/导师要求/往届优秀论文/期刊模板")
        print("  B. 输入格式要求 — 文字说明（如\"中文在前，英文在后\"）")
        print("  C. 手动选择标准格式 — GB/T 7714 / APA / IEEE / MLA 等")
        print("  D. 自动检测论文现有格式")

        choice = self._ask_choice("ABCD")

        # ── A: Template upload ──
        if choice == "A":
            return self._handle_template_upload(analyze_template_docx, get_builtin_style)

        # ── B: Requirements text ──
        if choice == "B":
            return self._handle_requirements_text(parse_requirements_text, get_builtin_style)

        # ── C: Standard format selection ──
        if choice == "C":
            return self._handle_standard_select(STYLE_MENU, get_builtin_style)

        # ── D: Auto-detect ──
        print()
        print("  正在分析论文现有格式...")
        try:
            config = auto_detect_from_paper(self.paper_path)
            self._print_config_summary(config)
            confirm = self._ask_yn("确认使用此格式", "Y=确认 N=手动选择")
            if confirm == "Y":
                return config
        except Exception as e:
            print(f"  自动检测失败: {e}")
        print("  已切换到手动选择标准格式。")
        return self._handle_standard_select(STYLE_MENU, get_builtin_style)

    # ------------------------------------------------------------------
    # Option handlers
    # ------------------------------------------------------------------

    @staticmethod
    def _handle_template_upload(analyze_template_docx, get_builtin_style):
        """Handle option A: template upload and analysis."""
        from .style_config import STYLE_MENU as _SM
        while True:
            try:
                path = input("  模板 .docx 路径: ").strip().strip('"')
            except EOFError:
                raise SystemExit("非交互模式，请通过 --template 参数指定模板路径")
            if not path:
                print("  已取消。")
                return get_builtin_style("gbt7714-numeric")
            if not os.path.exists(path):
                print(f"  文件不存在: {path}")
                continue
            if not path.lower().endswith(".docx"):
                print("  仅支持 .docx 格式的模板。")
                continue
            break

        print("  正在分析模板格式...")
        try:
            config = analyze_template_docx(path)
        except Exception as e:
            print(f"  模板分析失败: {e}")
            print("  已切换到手动选择标准格式。")
            return PaperAgent._handle_standard_select(_SM, get_builtin_style)

        PaperAgent._print_config_summary(config)
        confirm = PaperAgent._ask_yn(
            "确认使用此格式配置",
            "Y=确认 N=手动选择",
        )
        if confirm == "Y":
            return config
        return PaperAgent._handle_standard_select(_SM, get_builtin_style)

    def _handle_requirements_text(self, parse_requirements_text, get_builtin_style):
        """Handle option B: text requirements parsing."""
        from .style_config import STYLE_MENU as _SM

        if not self.client:
            print("  [!] 未配置 LLM 客户端，无法解析文字要求。")
            print("  已切换到手动选择标准格式。")
            return self._handle_standard_select(_SM, get_builtin_style)

        print("  请输入参考文献格式要求（多行文字，空行结束）：")
        print("  示例：中文在前，英文在后，英文按作者姓氏字母排序。")
        lines = []
        while True:
            try:
                line = input()
            except EOFError:
                break
            if line == "" and lines:
                break
            elif line == "" and not lines:
                continue
            lines.append(line)
        text = "\n".join(lines)

        if not text.strip():
            print("  未输入要求，已切换到手动选择。")
            return self._handle_standard_select(_SM, get_builtin_style)

        print("  正在解析格式要求...")
        try:
            config = parse_requirements_text(text, self.client)
        except Exception as e:
            print(f"  解析失败: {e}")
            print("  已切换到手动选择标准格式。")
            return self._handle_standard_select(_SM, get_builtin_style)

        self._print_config_summary(config)
        confirm = self._ask_yn("确认使用此格式配置", "Y=确认 N=手动选择")
        if confirm == "Y":
            return config
        return self._handle_standard_select(
            __import__("core.style_config", fromlist=["STYLE_MENU"]).STYLE_MENU,
            get_builtin_style)

    @staticmethod
    def _handle_standard_select(style_menu: dict, get_builtin_style):
        """Handle option C: standard format selection (8-option menu)."""
        from .style_config import ReferenceStyleConfig

        print()
        print("请选择参考文献体系：")
        for key in sorted(style_menu, key=lambda k: int(k)):
            s = style_menu[key]
            print(f"  {key}. {s['name']}")
            print(f"     {s['desc']}")

        while True:
            try:
                sel = input(f"请输入 (1-{len(style_menu)}): ").strip()
            except EOFError:
                raise SystemExit("非交互模式，请通过 --style 参数指定参考文献格式")
            if sel in style_menu:
                style = style_menu[sel]
                print(f"  已选择: {style['name']}")
                print()
                return get_builtin_style(style["id"])
            print(f"  请输入 1-{len(style_menu)}")

    @staticmethod
    def _print_config_summary(config):
        """Print a human-readable summary of a ReferenceStyleConfig."""
        from .style_config import ReferenceStyleConfig

        print()
        print("  ── 格式配置摘要 ──")
        print(f"  来源: {config.source_type}")
        print(f"  样式: {config.style_name}")
        print(f"  排序: {config.ordering_rule}")
        print(f"  编号: {config.numbering_rule}")
        print(f"  中英文: {config.language_split_rule}")
        print(f"  正文: {config.font_rule['cn']['name']} {config.font_rule['cn']['size']}pt "
              f"/ {config.font_rule['en']['name']}")
        print(f"  行距: {config.spacing_rule['line_spacing']}")
        print(f"  缩进: {config.indent_rule['type']} {config.indent_rule['value']}")
        print()

    # ==================================================================
    # Metadata search (OpenAlex → Crossref → Semantic Scholar)
    # ==================================================================

    def _find_metadata(self, title: str) -> dict:
        """Search OpenAlex → Crossref → Semantic Scholar for metadata.

        Cleans the extracted title before searching (strips leading author fragments,
        dots, and numbering artifacts common in GB/T 7714 reference parsing).
        """
        # Clean title for API search: strip artifacts from GB/T 7714 parsing
        clean = title.strip()
        clean = re.sub(r"^\[\d+\]\s*", "", clean)        # numbering
        clean = re.sub(r"^[A-Z]\.[A-Z]\.", "", clean)    # "A.." → "" (双首字母残片)
        clean = re.sub(r"^[A-Z]\.\s+", "", clean)        # "C. " → ""
        clean = re.sub(r"^[\.．\s，,]+", "", clean)        # leading dots/whitespace

        for searcher in [self._search_openalex, self._search_crossref,
                          self._search_semantic_scholar]:
            try:
                meta = searcher(clean)
                if not meta:
                    continue
                matched = meta.get("matched_title", "")
                sim = self._title_similarity(clean, matched)
                if sim < 0.3:
                    # Also try with original title
                    sim2 = self._title_similarity(title, matched)
                    if sim2 < 0.3:
                        continue
                # Accept if similarity OK, whether or not DOI exists
                return meta
            except Exception:
                pass
            time.sleep(0.5)
        return {}

    def _batch_llm_knowledge_check(self, refs: list[tuple[str, dict]],
                                    batch_size: int = 10) -> dict[str, dict]:
        """Ask the LLM what it knows about refs with sparse API metadata.

        For references with no abstract and no keywords from APIs, ask the LLM
        to recall what it knows from training data. Returns knowledge snippets
        that can be injected as pseudo-abstracts into ClassificationInput.

        Returns dict mapping original_title → {"abstract": str, "keywords": [str]}
        """
        if not self.client:
            return {}

        needs_check: list[tuple[str, dict]] = []
        for title, meta in refs:
            has_abstract = bool(meta.get("abstract")
                                and len(str(meta.get("abstract", ""))) > 20)
            has_keywords = bool(meta.get("keywords"))
            # Trigger if no meaningful abstract (keywords alone are weak signal)
            if not has_abstract:
                needs_check.append((title, meta))

        if not needs_check:
            return {}

        system = (
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

        results: dict[str, dict] = {}

        for batch_start in range(0, len(needs_check), batch_size):
            batch = needs_check[batch_start:batch_start + batch_size]
            titles_list = "\n".join(
                f"{i+1}. {title}"
                for i, (title, _) in enumerate(batch, batch_start)
            )

            user_msg = (
                f"请逐一检查以下 {len(batch)} 篇文献，告诉我你对每篇文献的了解：\n\n"
                f"{titles_list}"
            )

            try:
                message = self.client.messages.create(
                    model=self.model,
                    max_tokens=2048,
                    system=system,
                    messages=[{"role": "user", "content": user_msg}],
                )
                raw = ""
                for block in message.content:
                    if hasattr(block, "text"):
                        raw += block.text

                data = _parse_json_response(raw)
                if isinstance(data, dict):
                    data = [data]
                if not isinstance(data, list):
                    continue

                for item in data:
                    idx = int(item.get("index", 0)) - 1
                    if 0 <= idx < len(batch):
                        title = batch[idx][0]
                        if item.get("known"):
                            results[title] = {
                                "abstract": item.get("abstract_like", ""),
                                "keywords": item.get("keywords", []),
                                "role_hint": item.get("role_hint", ""),
                            }
            except Exception:
                pass
            time.sleep(0.5)

        return results

    def _search_openalex(self, title: str) -> dict | None:
        try:
            url = f"https://api.openalex.org/works?search={quote(title)}&per_page=3"
            resp = self.session.get(url, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            if not results:
                return None
            best = max(results, key=lambda r: self._title_similarity(
                title, r.get("title", "")))
            doi = (best.get("doi") or "").replace("https://doi.org/", "")
            cited = best.get("cited_by_count", 0) or 0
            concepts = best.get("concepts") or []
            primary_loc = best.get("primary_location") or {}
            return {
                "doi": doi, "matched_title": best.get("title", ""),
                "authors": ", ".join(
                    a.get("author", {}).get("display_name", "")
                    for a in (best.get("authorships") or [])[:5]
                ),
                "year": str(best.get("publication_date", ""))[:4],
                "abstract": self._extract_abstract_from_openalex(best),
                "cited_by_count": cited,
                "keywords": [c.get("display_name", "") for c in concepts[:5]],
                "venue": primary_loc.get("source", {}).get("display_name", ""),
                "source_db": "OpenAlex",
                "oa_pdf_url": (best.get("best_oa_location") or {}).get("pdf_url"),
            }
        except Exception:
            return None

    def _search_crossref(self, title: str) -> dict | None:
        try:
            url = f"https://api.crossref.org/works?rows=3&query.title={quote(title)}"
            resp = self.session.get(url, timeout=15)
            resp.raise_for_status()
            items = resp.json().get("message", {}).get("items", [])
            if not items:
                return None
            best = max(items, key=lambda r: self._title_similarity(
                title, (r.get("title") or [""])[0]))
            r_title = (best.get("title") or [""])[0]
            date_parts = (
                best.get("published-print", {}).get("date-parts", [[None]])[0]
                or best.get("created", {}).get("date-parts", [[None]])[0]
            )
            return {
                "doi": best.get("DOI", ""), "matched_title": r_title,
                "authors": ", ".join(
                    f"{a.get('given','')} {a.get('family','')}".strip()
                    for a in (best.get("author") or [])[:5]
                ),
                "year": str(date_parts[0]) if date_parts and date_parts[0] else "",
                "abstract": (best.get("abstract") or ""),
                "source_db": "Crossref",
            }
        except Exception:
            return None

    def _search_semantic_scholar(self, title: str) -> dict | None:
        try:
            url = (
                "https://api.semanticscholar.org/graph/v1/paper/search"
                f"?query={quote(title)}&limit=3"
                "&fields=title,authors,year,externalIds,openAccessPdf,abstract"
            )
            resp = self.session.get(url, timeout=15)
            resp.raise_for_status()
            items = resp.json().get("data", [])
            if not items:
                return None
            best = max(items, key=lambda r: self._title_similarity(
                title, r.get("title", "")))
            ext = best.get("externalIds") or {}
            oa = best.get("openAccessPdf") or {}
            return {
                "doi": ext.get("DOI", ""), "matched_title": best.get("title", ""),
                "authors": ", ".join(
                    a.get("name", "") for a in (best.get("authors") or [])[:5]),
                "year": str(best.get("year") or ""),
                "abstract": best.get("abstract", ""),
                "source_db": "Semantic Scholar",
                "oa_pdf_url": oa.get("url"),
            }
        except Exception:
            return None

    # ==================================================================
    # Resource: scan, find, download
    # ==================================================================

    def _scan_availability(self, titles: list[str]) -> dict:
        auto: list[tuple[str, str, str]] = []
        manual: list[str] = []
        missing: list[str] = []
        for i, title in enumerate(titles, 1):
            is_chinese = bool(re.search(r"[一-鿿]", title))
            if is_chinese:
                missing.append(title)
                time.sleep(0.2)
                continue
            meta = self._find_metadata(title)
            if not meta or not meta.get("doi"):
                missing.append(title)
                continue
            doi = meta["doi"]
            pdf_url = self._find_pdf_url(doi, meta)
            if pdf_url:
                auto.append((title, doi, pdf_url))
            else:
                manual.append(title)
            time.sleep(self.delay)
        return {"auto": auto, "manual": manual, "missing": missing}

    def _find_pdf_url(self, doi: str, meta: dict | None = None) -> str | None:
        if self.email and doi:
            try:
                url = f"https://api.unpaywall.org/v2/{quote(doi)}?email={quote(self.email)}"
                resp = self.session.get(url, timeout=15)
                if resp.status_code == 200:
                    best_oa = resp.json().get("best_oa_location") or {}
                    if best_oa.get("url_for_pdf"):
                        return best_oa["url_for_pdf"]
            except Exception:
                pass
            time.sleep(0.5)
        if meta:
            arxiv_id = meta.get("arxiv_id", "")
            if arxiv_id:
                return f"https://arxiv.org/pdf/{arxiv_id}.pdf"
            oa_url = meta.get("oa_pdf_url")
            if oa_url:
                return oa_url
        if doi:
            try:
                url = f"https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi?id={quote(doi)}"
                resp = self.session.get(url, timeout=15)
                m = re.search(
                    r'format="pdf".*?href="(https?://[^"]+\.pdf)"', resp.text)
                if m:
                    return m.group(1)
            except Exception:
                pass
        return None

    def _download_pdf(self, url: str, title: str, output_dir: str) -> str | None:
        safe_name = re.sub(r"[^\w\s\-]", "", title)
        safe_name = re.sub(r"\s+", "_", safe_name).strip("_")[:120]
        filepath = os.path.join(output_dir, f"{safe_name}.pdf")
        if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            return filepath
        try:
            headers = {"Accept": "application/pdf"} if "doi.org" in url else {}
            resp = self.session.get(url, headers=headers, timeout=60, stream=True)
            resp.raise_for_status()
            first_chunk = b""
            try:
                first_chunk = next(resp.iter_content(chunk_size=4), b"")
            except StopIteration:
                pass
            ct = resp.headers.get("Content-Type", "")
            if first_chunk != b"%PDF" and "application/pdf" not in ct:
                return None
            with open(filepath, "wb") as f:
                f.write(first_chunk)
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            if os.path.getsize(filepath) == 0:
                os.unlink(filepath)
                return None
            return filepath
        except Exception:
            return None

    # ==================================================================
    # Reference extraction from paper
    # ==================================================================

    _END_MARKERS = {"致谢", "Acknowledgments", "附录", "Appendix"}

    def _extract_ref_titles_from_paper(self, paper: Paper) -> list[str]:
        raw = paper.references_section
        if not raw:
            return []
        lines = raw.split("\n")
        refs = []
        current = None
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if line in self._END_MARKERS or any(
                line.startswith(m) for m in self._END_MARKERS
            ):
                if current:
                    refs.append(current)
                current = None
                break
            if re.match(r"^\[\d+\]", line):
                if current:
                    refs.append(current)
                current = line
            elif current is not None:
                current += " " + line
            else:
                if re.search(r"\[[JMDCP]\]", line) or re.match(
                    r"^[A-Z一-鿿][^.]+\.", line
                ):
                    refs.append(line)
        if current:
            refs.append(current)
        titles = []
        for ref in refs:
            ref = re.sub(r"^\[\d+\]\s*", "", ref).strip()
            ref_norm = ref.replace("．", ".").replace("、", ",")
            m = re.match(r"^[^\]\.]+[\.．](.+?)\[[JMCDP]\]", ref_norm)
            if m:
                titles.append(m.group(1).strip())
                continue
            m = re.match(r"^[A-Z][^\.]+\.[\s．]*(.+?)\[[JMCDP]\]", ref_norm, re.DOTALL)
            if m:
                titles.append(m.group(1).strip())
                continue
            if len(ref) > 5:
                titles.append(ref)
        return titles

    @staticmethod
    def _load_titles_from_file(path: str) -> list[str]:
        """Load reference titles from .txt / .csv / .xlsx. Auto-detects GB/T 7714 format."""
        ext = os.path.splitext(path)[1].lower()
        if ext == ".txt":
            with open(path, encoding="utf-8") as f:
                lines = [line.strip() for line in f if line.strip()]
            if lines and re.match(r"^\[\d+\]", lines[0]):
                return PaperAgent._parse_gbt7714_titles(lines)
            return lines
        elif ext == ".csv":
            import csv
            with open(path, encoding="utf-8-sig") as f:
                return [row[0].strip() for row in csv.reader(f) if row and row[0].strip()]
        elif ext in (".xlsx", ".xls"):
            from openpyxl import load_workbook
            wb = load_workbook(path, read_only=True)
            ws = wb.active
            rows = list(ws.iter_rows(values_only=True))
            return [str(r[0]).strip() for r in rows[1:] if r and r[0]]
        return []

    @staticmethod
    def _parse_gbt7714_titles(lines: list[str]) -> list[str]:
        titles = []
        for line in lines:
            line = re.sub(r"^\[\d+\]\s*", "", line)
            m = re.match(r"^[^．\]]+．(.+?)\[[JMCDP]\]", line)
            if m:
                titles.append(m.group(1).strip())
                continue
            m = re.match(r"^[A-Z][^.]+\.[^.]+\.[\s．]*(.+?)\[[JMCDP]\]", line)
            if m:
                titles.append(m.group(1).strip())
                continue
            if len(line) > 5:
                titles.append(line)
        return titles

    # ==================================================================
    # Helpers
    # ==================================================================

    def _progress(self, description: str):
        self.step += 1
        print()
        print(f"[{self.step}/{self.total_steps}] {description}...")
        print(f"  模式: {self.mode_label}")

    @staticmethod
    def _kb_quality(kb: ReferenceKB) -> str:
        has_abs = sum(1 for e in kb.entries if e.abstract and len(e.abstract) > 20)
        has_args = sum(1 for e in kb.entries if e.key_arguments)
        has_full = sum(1 for e in kb.entries if e.full_text and len(e.full_text) > 200)
        return (f"{has_abs}/{len(kb.entries)}有摘要, "
                f"{has_args}/{len(kb.entries)}有论点, "
                f"{has_full}/{len(kb.entries)}有全文")

    @staticmethod
    def _resolve_pdf_dir(path: str) -> str | None:
        if not os.path.exists(path):
            return None
        if os.path.isdir(path):
            return path
        if path.lower().endswith(".zip"):
            extract_dir = os.path.join(os.path.dirname(path) or ".", "ref_pdfs")
            os.makedirs(extract_dir, exist_ok=True)
            with zipfile.ZipFile(path, "r") as zf:
                for name in zf.namelist():
                    if name.lower().endswith(".pdf"):
                        zf.extract(name, extract_dir)
            print(f"  从 zip 解压到: {extract_dir}")
            return extract_dir
        return None

    @staticmethod
    def _find_missing_refs(ref_titles: list[str],
                           existing_stems: set[str]) -> list[str]:
        missing = []
        for title in ref_titles:
            stem = re.sub(r"[^\w\s\-]", "", title)
            stem = re.sub(r"\s+", "_", stem).strip("_")[:120]
            if stem not in existing_stems:
                found = False
                for es in existing_stems:
                    if stem[:40] in es or es[:40] in stem:
                        found = True
                        break
                if not found:
                    missing.append(title)
        return missing

    @staticmethod
    def _extract_abstract_from_openalex(result: dict) -> str:
        ai = result.get("abstract_inverted_index")
        if not ai:
            return ""
        try:
            word_pos = {}
            for word, positions in ai.items():
                for pos in positions:
                    word_pos[pos] = word
            return " ".join(word_pos[i] for i in sorted(word_pos))
        except Exception:
            return ""

    @staticmethod
    def _title_similarity(query: str, candidate: str) -> float:
        def tokens(s):
            return set(re.sub(r"[^\w\s]", "", s.lower()).split())
        q, c = tokens(query), tokens(candidate)
        if not q or not c:
            return 0.0
        return len(q & c) / len(q | c)

    @staticmethod
    def _find_section_text(paper: Paper, chapter: str, section: str) -> str:
        for ch in paper.chapters:
            if ch.title == chapter:
                if section in ("引言", "引言/概述", ""):
                    return ch.intro or ""
                for sec in ch.sections:
                    if sec.heading == section:
                        return sec.text
        return ""

    def _write_quality_report(self, edits: list[CitationEdit], kb: ReferenceKB,
                               base_path: str, style_config: dict) -> str:
        """Export per-reference quality report as Excel."""
        try:
            from openpyxl import Workbook
            wb = Workbook()
            ws = wb.active
            ws.title = "引用质量报告"
            headers = [
                "文献编号", "文献标题", "角色", "角色置信度", "需求等级",
                "知识置信度", "元数据完整度", "NeedPDFScore",
                "KB质量", "引用深度", "置信度标注", "被引量",
                "推荐章节", "匹配原因",
            ]
            ws.append(headers)
            for i, edit in enumerate(edits, 1):
                ref = kb.get_entry_by_index(i - 1)
                meta = ref.meta if ref else {}
                role = meta.get("classification_role", "")
                role_conf = meta.get("classification_confidence", "")
                req_level = meta.get("requirement_level", "")
                kc = meta.get("knowledge_confidence", "")
                mc = meta.get("metadata_completeness", "")
                need_score = meta.get("requirement_score", "")
                quality = meta.get("kb_quality", "")
                cited = meta.get("cited_by_count", "")
                annotation = ""
                if "[需核实原文]" in edit.citation_text:
                    annotation = "需核实原文"
                elif "[建议核实]" in edit.citation_text:
                    annotation = "建议核实"
                depth = get_citation_depth(role) if role else ""
                ws.append([
                    i, edit.ref_title[:120], role, role_conf, req_level,
                    kc, mc, need_score,
                    quality, depth, annotation, cited,
                    edit.section_heading, edit.reason,
                ])
            report_path = f"{base_path}_quality_report.xlsx"
            wb.save(report_path)
            print(f"  质量报告: {report_path}")
            return report_path
        except Exception:
            return ""

    def _run_exploration(self, kb, paper) -> None:
        """Interactive Q&A loop: semantic search → LLM synthesis.

        User asks questions about the literature corpus.
        Each question triggers vector search, then LLM synthesizes an answer
        with source citations.
        """
        print()
        print("━" * 50)
        print("  文献探索模式")
        print("  输入问题，空行或 q 退出，继续流水线")
        print("━" * 50)
        print()

        if not self.client:
            print("  [!] 未配置LLM客户端，探索模式需要LLM进行答案合成")
            return

        vs = kb.vector_store
        while True:
            print()
            try:
                question = input("  > ").strip()
            except EOFError:
                break
            if not question or question.lower() in ("q", "quit", "exit"):
                print("  退出探索模式，继续流水线...")
                break

            print(f"  搜索中...", end=" ", flush=True)
            results = kb.search(question, top_k=8)
            if not results:
                print("未找到相关内容")
                continue
            print(f"{len(results)} 条结果")

            # Build context from search results
            context_parts = []
            for r in results:
                context_parts.append(
                    f"[{r['ref_title']}] ({r.get('authors', '')}, {r.get('year', '')})\n"
                    f"  [{r.get('section', '')}] {r['text'][:300]}"
                )
            context = "\n\n".join(context_parts)

            try:
                message = self.client.messages.create(
                    model=self.model,
                    max_tokens=1024,
                    system=(
                        "你是学术文献研究助手。请基于提供的文献内容回答用户问题。"
                        "引用具体文献时标注文献来源。用中文回答。"
                    ),
                    messages=[{
                        "role": "user",
                        "content": (
                            f"论文主题: {paper.title}\n\n"
                            f"文献检索结果:\n{context}\n\n"
                            f"用户问题: {question}\n\n"
                            f"请基于以上文献内容回答。标注具体文献来源。"
                        ),
                    }],
                )
                answer = ""
                for block in message.content:
                    if hasattr(block, "text"):
                        answer += block.text
                print()
                print(f"  {answer}")
            except Exception as e:
                print(f"  [合成失败: {e}]")

    @staticmethod
    def _ask_yn(prompt: str, hint: str = "Y/N") -> str:
        """Ask a Y/N(/P) question. Returns 'Y', 'N', or 'P'."""
        valid = "YNP"
        while True:
            try:
                choice = input(f"  {prompt} [{hint}]: ").strip().upper()
            except EOFError:
                raise SystemExit(
                    "非交互模式无法获取用户输入。"
                    "请通过命令行参数补全: --template <模板.docx> | --style <格式>"
                )
            if choice and choice[0] in valid:
                return choice[0]
            print(f"  请输入 {'/'.join(valid)}")

    @staticmethod
    def _ask_choice(valid: str) -> str:
        while True:
            try:
                choice = input(f"请输入 ({'/'.join(valid)}): ").strip().upper()
            except EOFError:
                raise SystemExit(
                    "非交互模式无法获取用户输入。"
                    "请通过命令行参数补全: --template <模板.docx> | --style <格式>"
                )
            if choice and choice[0] in valid:
                return choice[0]
            print(f"  请输入 {'/'.join(valid)}")

    def _write_missing_checklist(self, downloaded: list[str],
                                  missing: list[str], avail: dict,
                                  pdf_dir: str) -> str:
        base_dir = os.path.dirname(pdf_dir) or "."
        path = os.path.join(base_dir, "missing_checklist.md")
        lines = [
            "# 缺失文献获取清单",
            f"  生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
            f"## 已获取 ({len(downloaded)} 篇)",
        ]
        for t in downloaded:
            lines.append(f"- [x] {t}")
        lines += ["", f"## 需手动提供 ({len(missing)} 篇)", ""]
        for t in missing:
            cnki_url = ""
            if re.search(r"[一-鿿]", t):
                cnki_url = f" [知网](https://kns.cnki.net/kns8s/search?classid=YSTT4HG0&kw={quote(t)})"
            lines.append(f"- [ ] **{t}**{cnki_url}")
        lines += ["", f"> 请将 PDF 放入: `{pdf_dir}/`"]
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return path

    def _write_download_report(self, downloaded: list[str],
                                avail: dict, pdf_dir: str,
                                paper_path: str) -> str:
        base_dir = os.path.dirname(paper_path) or "."
        path = os.path.join(base_dir, "download_report.md")
        lines = [
            "# 文献获取报告",
            f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
            f"## 1. 已自动获取 ({len(downloaded)} 篇)", "",
        ]
        for t in downloaded:
            lines.append(f"- [x] {t}")
        lines += [
            "",
            f"## 2. 需手动获取 ({len(avail['manual'])} 篇)",
            "",
            "> 以下文献有 DOI 但无开放获取版本，请通过机构订阅或 Sci-Hub 获取",
            "",
        ]
        for t in avail["manual"]:
            lines.append(f"- [ ] {t}")
        lines += [
            "",
            f"## 3. 未找到元数据 ({len(avail['missing'])} 篇)",
            "",
        ]
        for t in avail["missing"]:
            cnki_url = ""
            if re.search(r"[一-鿿]", t):
                cnki_url = f" [知网](https://kns.cnki.net/kns8s/search?classid=YSTT4HG0&kw={quote(t)})"
            lines.append(f"- [ ] {t}{cnki_url}")
        lines += [
            "",
            "## 推荐获取方式",
            "1. **英文文献**: Sci-Hub + DOI",
            "2. **中文文献**: 知网或全国图书馆参考咨询联盟",
            "",
            f"> PDF 存放目录: `{pdf_dir}/`",
        ]
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return path
