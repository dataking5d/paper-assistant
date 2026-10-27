"""Paper Assistant — 论文引用全流程助手.

Commands:
  agent        交互式 Agent: 需求判断 → 4 条工作流自适应
  check        扫描文献列表，生成可获取/需手动提供的清单
  retrieve     从标题列表检索并下载 OA PDF
  extract      从论文 PDF 的参考文献页解析文献列表
  kb           构建参考文献知识库 (PDF → 摘要/论点提取)
  parse        解析论文 .docx 结构化内容
  match        匹配引擎: 参考文献 → 论文章节
  generate     生成学术化引用内容
  edit         插入引用 + 重编号 + 导出增强版论文
  full         一键: match → generate → edit
  cnki-enrich  中文文献知网元数据补全 (需 Chrome DevTools MCP)
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# ═══════════════════════════════════════════════════════════════════════
# check — pre-flight PDF availability scan
# ═══════════════════════════════════════════════════════════════════════

def cmd_check(args):
    from core.retriever import PaperRetriever

    retriever = PaperRetriever(
        input_path=args.input,
        output_dir=args.output,
        email=args.email,
        delay=args.delay,
    )
    retriever.check()


# ═══════════════════════════════════════════════════════════════════════
# retrieve — download OA PDFs for a title list
# ═══════════════════════════════════════════════════════════════════════

def cmd_retrieve(args):
    from core.retriever import PaperRetriever

    retriever = PaperRetriever(
        input_path=args.input,
        output_dir=args.output,
        max_retries=args.retry,
        email=args.email,
        delay=args.delay,
    )
    retriever.run()


# ═══════════════════════════════════════════════════════════════════════
# extract — parse reference list from a PDF bibliography
# ═══════════════════════════════════════════════════════════════════════

def cmd_extract(args):
    from core.retriever import parse_references_from_pdf

    refs = parse_references_from_pdf(args.input)
    print(f"解析到 {len(refs)} 条参考文献:")

    if args.titles_only:
        out_path = args.output or Path(args.input).with_suffix(".titles.txt")
        titles = [r["title"] for r in refs]
        with open(out_path, "w", encoding="utf-8") as f:
            for t in titles:
                f.write(t + "\n")
        for i, r in enumerate(refs, 1):
            print(f"  [{i}] {r['title'][:100]}")
        print(f"\n标题列表已保存: {out_path}")
    else:
        out_path = args.output or Path(args.input).with_suffix(".parsed.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(refs, f, ensure_ascii=False, indent=2)
        for i, r in enumerate(refs, 1):
            print(f"  [{i}] {r['authors']} ({r['year']}) — {r['title'][:80]}")
        print(f"\nJSON 已保存: {out_path}")


# ═══════════════════════════════════════════════════════════════════════
# parse — parse thesis .docx structure
# ═══════════════════════════════════════════════════════════════════════

def cmd_parse(args):
    from core.docx_parser import DocxParser, find_uncited_sections
    from io import BytesIO
    raw = Path(args.input).read_bytes()
    parser = DocxParser(BytesIO(raw))
    paper = parser.parse()

    if args.json:
        from core.docx_parser import document_snapshot
        result = _paper_to_dict(paper)
        result.update(document_snapshot(raw))
        result['references_section'] = paper.references_section
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    print(f"论文标题: {paper.title}\n")
    print(f"章节数: {len(paper.chapters)}")

    uncited = find_uncited_sections(paper)
    print(f"缺少引用的段落: {len(uncited)}")

    for ch in paper.chapters:
        print(f"\n{'=' * 50}")
        print(f"## {ch.title}")
        if ch.intro:
            print(f"  {ch.intro[:150]}...")
        for sec in ch.sections:
            cites = f" [引用: {', '.join(sec.citations)}]" if sec.citations else " [无引用]"
            print(f"\n  ### {sec.heading}{cites}")
            print(f"  {sec.text[:200]}...")


# ═══════════════════════════════════════════════════════════════════════
# kb — build reference knowledge base
# ═══════════════════════════════════════════════════════════════════════

def cmd_kb(args):
    from core.reference_kb import ReferenceKB

    kb = ReferenceKB(kb_dir=args.output)
    client = _require_llm() if args.summarize else None

    if args.pdfs:
        print(f"从PDF目录构建知识库: {args.pdfs}")
        kb.build_from_pdfs(args.pdfs, metadata_csv=args.metadata)
    elif args.titles:
        print(f"从标题文件构建知识库: {args.titles}")
        titles = _load_titles(args.titles)
        # Try to enrich titles with metadata from APIs
        retriever = None
        if not args.no_metadata:
            from core.retriever import PaperRetriever
            retriever = PaperRetriever(
                input_path=args.titles,
                output_dir=os.path.join(args.output, ".retriever_cache"),
                email=args.email,
                delay=args.delay,
            )
        kb.build_from_titles(titles, paper_retriever=retriever)
    else:
        print("错误: 需要 --pdfs 或 --titles 参数")
        sys.exit(1)

    if args.summarize and client:
        print("LLM 摘要提取中...")
        kb.summarize_with_llm(client, model=args.model)

    print(f"知识库已保存至: {kb._index_path}")
    print(f"文献数量: {len(kb.entries)}")
    for e in kb.entries:
        print(f"  - {e.title} ({e.authors}, {e.year})")


# ═══════════════════════════════════════════════════════════════════════
# match — reference → section matching
# ═══════════════════════════════════════════════════════════════════════

def cmd_match(args):
    from core.docx_parser import DocxParser
    from core.reference_kb import ReferenceKB
    from core.citation_matcher import CitationMatcher, coverage_to_report

    client = _require_llm()

    print(f"解析论文: {args.paper}")
    docx_parser = DocxParser(args.paper)
    paper = docx_parser.parse()
    print(f"  章节: {len(paper.chapters)}")

    print(f"加载知识库: {args.kb}")
    kb = ReferenceKB(kb_dir=args.kb)
    kb.load_index()
    if not kb.entries:
        print("错误: 知识库为空")
        sys.exit(1)
    print(f"  文献数量: {len(kb.entries)}")

    print(f"开始全覆盖引用匹配（目标: 100%）...")
    with open(args.prompt, encoding="utf-8") as f:
        system_prompt = f.read()

    matcher = CitationMatcher(client, model=args.model)
    result = matcher.match_all(paper, kb, system_prompt=system_prompt)

    rows = coverage_to_report(result)
    _write_excel_report(rows, args.output)
    print(f"覆盖率: {result.covered_refs}/{result.total_refs} ({result.coverage_pct:.0%})")
    if result.uncovered_refs:
        print(f"[!] 未覆盖: {len(result.uncovered_refs)} 篇 → 见报告扩展建议")
    if result.expansion_suggestions:
        print(f"扩展建议: {len(result.expansion_suggestions)} 处")
    print(f"报告已保存: {args.output}")


# ═══════════════════════════════════════════════════════════════════════
# generate — generate citation text
# ═══════════════════════════════════════════════════════════════════════

def cmd_generate(args):
    from core.reference_kb import ReferenceKB
    from core.citation_generator import CitationGenerator
    from openpyxl import load_workbook
    from collections import defaultdict

    client = _require_llm()

    print(f"加载知识库: {args.kb}")
    kb = ReferenceKB(kb_dir=args.kb)
    kb.load_index()
    if not kb.entries:
        print("错误: 知识库为空")
        sys.exit(1)
    print(f"  文献数量: {len(kb.entries)}")

    print(f"加载匹配报告: {args.report}")
    wb = load_workbook(args.report, read_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if len(rows) < 2:
        print("错误: 匹配报告为空")
        sys.exit(1)
    headers = [str(h) for h in rows[0]]
    data_rows = [dict(zip(headers, r)) for r in rows[1:]]
    wb.close()

    sections = defaultdict(list)
    for row in data_rows:
        key = row.get("小节", row.get("章节", ""))
        ref_title = row.get("推荐文献", "")
        if ref_title:
            sections[key].append(ref_title)

    print(f"开始生成引用内容: {len(sections)} 个位置")
    with open(args.prompt, encoding="utf-8") as f:
        system_prompt = f.read()

    generator = CitationGenerator(client, model=args.model)
    all_generated = []

    for section_name, ref_titles in sections.items():
        section_text = ""
        for row in data_rows:
            if row.get("小节", "") == section_name:
                section_text = row.get("段落内容", "")
                break

        for ref_title in ref_titles:
            ref = _find_ref_by_title(kb, ref_title)
            if ref is None:
                continue
            result = generator.generate(section_text or section_name, ref, system_prompt)
            all_generated.append({
                "小节": section_name,
                "文献": ref.title,
                "引用内容": result.citation_text,
                "引用类型": result.citation_type,
            })
            print(f"  [{section_name}] -> {ref.title[:40]}... 完成")

    _write_excel_report(all_generated, args.output)
    print(f"引用内容已保存: {args.output} ({len(all_generated)} 条)")


# ═══════════════════════════════════════════════════════════════════════
# edit — insert citations into .docx
# ═══════════════════════════════════════════════════════════════════════

def cmd_edit(args):
    if getattr(args, 'edits', None):
        from core.agent_workflow import apply_edit_plan
        if not args.attempt:
            raise ValueError('--attempt is required for structured editing')
        if args.output != 'enhanced_paper.docx' or args.report:
            raise ValueError('Use --attempt, not --output/--report, with --edits')
        plan_path = Path(args.edits).resolve()
        plan = json.loads(plan_path.read_text(encoding='utf-8-sig'))
        result = apply_edit_plan(args.paper, plan, args.attempt,
                                 base_dir=plan_path.parent, validate_only=args.validate_only)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if getattr(args, 'attempt', None) or getattr(args, 'validate_only', False):
        raise ValueError('--attempt and --validate-only require --edits')
    from core.docx_editor import DocxEditor, CitationEdit, export_citation_report
    from openpyxl import load_workbook

    print(f"加载引用内容: {args.citations}")
    wb = load_workbook(args.citations, read_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if len(rows) < 2:
        print("错误: 引用内容为空")
        sys.exit(1)
    headers = [str(h) for h in rows[0]]
    data_rows = [dict(zip(headers, r)) for r in rows[1:]]
    wb.close()

    edits = []
    for row in data_rows:
        edits.append(CitationEdit(
            section_heading=row.get("小节", ""),
            citation_text=row.get("引用内容", ""),
            ref_title=row.get("文献", ""),
            ref_authors="",
            ref_year="",
            ref_full=row.get("文献", ""),
            confidence=float(row.get("置信度", 0.8)) if row.get("置信度") else 0.8,
            reason=row.get("插入原因", "AI推荐"),
        ))

    report_path = args.report or str(Path(args.output).with_name(Path(args.output).stem + '_report.xlsx'))
    if Path(report_path).resolve() == Path(args.output).resolve():
        raise ValueError('Report and document output paths must differ')
    DocxEditor.check_output(args.paper, report_path)
    editor = DocxEditor()
    result = editor.apply_edits(args.paper, edits, args.output)

    print(f"增强版论文: {result.output_path}")
    print(f"新增引用: {result.edits_applied} 处")
    print(f"参考文献: {len(result.new_references)} 条 (按首次出现顺序重编号)")

    export_citation_report(result.report_rows, report_path)
    print(f"引用报告: {report_path}")


def cmd_evidence(args):
    from core.agent_workflow import read_evidence
    print(json.dumps(read_evidence(args.input), ensure_ascii=False, indent=2))


def cmd_humanize_scan(args):
    """Read-only AI writing trace scan; never creates an attempt or edits the DOCX."""
    from core.humanize_scanner import scan_docx
    result = scan_docx(args.input, language=args.language)
    print(json.dumps(result, ensure_ascii=False, indent=2))


# ═══════════════════════════════════════════════════════════════════════
# cnki-enrich — Chinese paper metadata completion via CNKI
# ═══════════════════════════════════════════════════════════════════════

def cmd_agent(args):
    """Unified adaptive pipeline: per-reference classification → auto-generate citations."""
    client = _get_llm_client()
    if not client:
        print("警告: 未配置 API Key (设置 DEEPSEEK_API_KEY 环境变量)")
        print("  文献分类和引用生成将受限")
        print()

    from core.agent import PaperAgent
    from core.style_config import get_builtin_style
    from core.template_analyzer import analyze_template_docx, parse_requirements_text

    # Build style config from CLI args (priority: template > requirements > style)
    ref_style = None
    if args.template:
        if not os.path.exists(args.template):
            print(f"错误: 模板文件不存在: {args.template}")
            return
        print(f"分析模板: {args.template}")
        ref_style = analyze_template_docx(args.template)
        print(f"  模板分析完成: {ref_style.style_name}")
    elif args.requirements:
        if not client:
            print("错误: --requirements 需要 LLM 客户端 (设置 DEEPSEEK_API_KEY)")
            return
        print("解析格式要求...")
        ref_style = parse_requirements_text(args.requirements, client)
        print(f"  解析完成: {ref_style.style_name}")
    elif args.style:
        ref_style = get_builtin_style(args.style)

    agent = PaperAgent(
        llm_client=client,
        model=args.model,
        email=args.email,
        delay=args.delay,
        explore=getattr(args, 'explore', False),
        embedding_model=getattr(args, 'embedding_model', ""),
    )

    agent.run(
        paper_path=args.paper,
        pdf_dir=args.pdfs,
        ref_style=ref_style,
        kb_path=args.kb,
    )


# ═══════════════════════════════════════════════════════════════════════
# query — semantic search over a knowledge base
# ═══════════════════════════════════════════════════════════════════════

def cmd_query(args):
    """Semantic search over a knowledge base. Interactive or one-shot."""
    from core.reference_kb import ReferenceKB

    print(f"加载知识库: {args.kb}")
    kb = ReferenceKB(kb_dir=args.kb)
    kb.load_index()
    if not kb.entries:
        print("错误: 知识库为空")
        sys.exit(1)
    print(f"  文献数量: {len(kb.entries)}")

    # Try loading existing vector index first
    try:
        from core.vector_store import ReferenceVectorStore, EmbeddingProvider
        embed_model = getattr(args, 'embedding_model', "") or ""
        provider = EmbeddingProvider(model_name=embed_model) if embed_model else None
        kb.vector_store = ReferenceVectorStore(
            args.kb,
            embedding_provider=provider,
        )
        if not kb.vector_store.load():
            print("构建向量索引...")
            kb.build_vector_index()
        else:
            print(f"  向量索引: {kb.vector_store.chunk_count} 个块")
    except ImportError as e:
        print(f"错误: 缺少依赖: {e}")
        print("请安装: pip install chromadb sentence-transformers")
        sys.exit(1)

    if not kb.vector_store or not kb.vector_store.is_ready:
        print("错误: 向量索引不可用")
        sys.exit(1)

    if args.question:
        # One-shot mode
        results = kb.search(
            args.question, top_k=args.top_k, quality_filter=args.quality,
        )
        for r in results:
            print(f"\n[{r['ref_title']}] ({r.get('year', '')}) "
                  f"dist={r.get('distance', 0):.4f}")
            print(f"  [{r.get('section', '')}] {r['text'][:200]}")
        if results and args.export:
            _write_excel_report(results, args.export)
            print(f"\n结果已导出: {args.export}")
        elif not results:
            print("未找到相关内容")
    else:
        # Interactive mode
        client = _get_llm_client()
        print()
        print("输入问题进行语义搜索，空行或 q 退出")
        print(f"知识库: {len(kb.entries)} 篇文献, {kb.vector_store.chunk_count} 个文本块")
        while True:
            try:
                q = input("> ").strip()
            except EOFError:
                break
            if not q or q.lower() in ("q", "quit", "exit"):
                break
            results = kb.search(q, top_k=args.top_k, quality_filter=args.quality)
            if not results:
                print("  未找到相关内容")
                continue
            # Synthesize answer with LLM if available
            if client:
                context_parts = []
                for r in results:
                    context_parts.append(
                        f"[{r['ref_title']}] ({r.get('authors', '')}, {r.get('year', '')})\n"
                        f"  {r['text'][:300]}"
                    )
                context = "\n\n".join(context_parts)
                try:
                    message = client.messages.create(
                        model=getattr(args, 'model', 'deepseek-v4-pro'),
                        max_tokens=1024,
                        system="你是学术文献研究助手。请基于提供的文献内容回答。标注文献来源。",
                        messages=[{
                            "role": "user",
                            "content": (
                                f"文献检索结果:\n{context}\n\n"
                                f"用户问题: {q}\n\n"
                                f"请基于以上文献内容回答（中文）。标注具体文献来源。"
                            ),
                        }],
                    )
                    answer = ""
                    for block in message.content:
                        if hasattr(block, "text"):
                            answer += block.text
                    print(f"\n  {answer}\n")
                except Exception:
                    _print_search_results(results)
            else:
                _print_search_results(results)


def _print_search_results(results: list[dict]) -> None:
    """Print raw search results."""
    for r in results:
        print(f"\n  [{r['ref_title']}] ({r.get('year', '')}) "
              f"dist={r.get('distance', 0):.4f}")
        print(f"    [{r.get('section', '')}] {r['text'][:200]}")


def cmd_cnki_enrich(args):
    from core.cnki_enricher import cmd_cnki_enrich as _impl
    _impl(args)


def _get_llm_client():
    api_key = os.environ.get("DEEPSEEK_API_KEY", "") or os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
    base_url = os.environ.get("LLM_BASE_URL", "") or "https://api.deepseek.com/anthropic"
    if not api_key:
        return None
    from anthropic import Anthropic
    return Anthropic(api_key=api_key, base_url=base_url)


def _require_llm():
    client = _get_llm_client()
    if not client:
        print("错误: 未配置 API Key (设置 DEEPSEEK_API_KEY 环境变量)")
        sys.exit(1)
    return client


def _load_titles(path: str) -> list[str]:
    ext = Path(path).suffix.lower()
    if ext == ".txt":
        with open(path, encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]
        # Auto-detect GB/T 7714 format: lines start with [N]
        if lines and re.match(r"^\[\d+\]", lines[0]):
            return _parse_gbt7714_titles(lines)
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
    else:
        raise ValueError(f"Unsupported format: {ext}")


def _parse_gbt7714_titles(lines: list[str]) -> list[str]:
    """Extract titles from GB/T 7714 formatted reference lines.

    Handles both:
      Chinese: [1]张美芳．文本类型理论及其对翻译研究的启示[J]．中国翻译，2009（5）：53-60．
      English: [2]Newmark, P.．Approaches to Translation[M]．Oxford：Pergamon Press，1981．
    """
    titles = []
    for line in lines:
        line = re.sub(r"^\[\d+\]\s*", "", line)
        # Chinese: author．TITLE[J/M]...
        m = re.match(r"^[^．\]]+．(.+?)\[[JMCDP]\]", line)
        if m:
            titles.append(m.group(1).strip())
            continue
        # English: Author, N. TITLE[M]...
        m = re.match(r"^[A-Z][^.]+\.[^.]+\.[\s．]*(.+?)\[[JMCDP]\]", line)
        if m:
            titles.append(m.group(1).strip())
            continue
        # Fallback: use the whole line without ref number
        if len(line) > 5:
            titles.append(line)
    return titles


def _find_ref_by_title(kb, title: str):
    for e in kb.entries:
        if e.title == title or title in e.title:
            return e
    return None


def _write_excel_report(rows: list[dict], output_path: str):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "报告"

    if not rows:
        ws.cell(row=1, column=1, value="无结果")
        wb.save(output_path)
        return

    headers = list(rows[0].keys())
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    header_font = Font(bold=True, color="FFFFFF", size=11)
    wrap = Alignment(wrap_text=True, vertical="top")

    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = wrap

    for i, row in enumerate(rows, 1):
        for col, key in enumerate(headers, 1):
            cell = ws.cell(row=i + 1, column=col, value=row.get(key, ""))
            cell.alignment = wrap

    for col_cells in ws.columns:
        max_len = max(min(len(str(cell.value or "")), 60) for cell in col_cells)
        ws.column_dimensions[col_cells[0].column_letter].width = max_len + 2

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    wb.save(output_path)


def _paper_to_dict(paper) -> dict:
    return {
        "title": paper.title,
        "chapters": [
            {
                "title": ch.title,
                "intro": ch.intro,
                "sections": [
                    {"heading": s.heading, "text": s.text, "citations": s.citations}
                    for s in ch.sections
                ],
            }
            for ch in paper.chapters
        ],
    }


# ═══════════════════════════════════════════════════════════════════════
# full — one-shot: match → generate → edit
# ═══════════════════════════════════════════════════════════════════════

def cmd_full(args):
    """One-shot pipeline: match → generate → edit."""

    match_out = args.match_output or "matching_report.xlsx"
    gen_out = args.gen_output or "generated_citations.xlsx"
    edit_out = args.output or "enhanced_paper.docx"
    prompt_dir = args.prompt_dir or "prompts"

    # Step 1: match
    print("=" * 60)
    print("Step 1/3: 匹配参考文献到论文章节")
    print("=" * 60)
    match_args = argparse.Namespace(
        paper=args.paper, kb=args.kb,
        prompt=os.path.join(prompt_dir, "matching_prompt.md"),
        model=args.model, output=match_out,
    )
    cmd_match(match_args)

    # Step 2: generate
    print()
    print("=" * 60)
    print("Step 2/3: 生成引用内容")
    print("=" * 60)
    gen_args = argparse.Namespace(
        report=match_out, kb=args.kb,
        prompt=os.path.join(prompt_dir, "generation_prompt.md"),
        model=args.model, output=gen_out,
    )
    cmd_generate(gen_args)

    # Step 3: edit
    print()
    print("=" * 60)
    print("Step 3/3: 插入引用并导出增强版论文")
    print("=" * 60)
    edit_args = argparse.Namespace(
        paper=args.paper, citations=gen_out, output=edit_out,
        report=None,
    )
    cmd_edit(edit_args)

    print()
    print("=" * 60)
    print("  全流程完成")
    print(f"  增强版论文: {edit_out}")
    print(f"  匹配报告: {match_out}")
    print(f"  引用内容: {gen_out}")
    print("=" * 60)


# ═══════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Paper Assistant — 论文引用全流程助手")
    parser.add_argument('--legacy-api', action='store_true', help='显式启用尚未全面验收的旧模型 API 流程')
    sub = parser.add_subparsers(dest="command", help="子命令")

    # check
    p_check = sub.add_parser("check", help="扫描文献列表，生成可获取/需手动提供的状态清单")
    p_check.add_argument("input", help="标题文件 (.txt/.csv/.xlsx)")
    p_check.add_argument("-o", "--output", default="workspaces/default/outputs/check", help="输出目录")
    p_check.add_argument("--email", default="", help="Unpaywall API email")
    p_check.add_argument("--delay", type=float, default=1.0, help="请求间隔秒数")

    # retrieve
    p_retrieve = sub.add_parser("retrieve", help="从标题列表检索并下载 OA PDF")
    p_retrieve.add_argument("input", help="标题文件 (.txt/.csv/.xlsx)")
    p_retrieve.add_argument("-o", "--output", default="papers", help="输出目录")
    p_retrieve.add_argument("--retry", type=int, default=3, help="下载重试次数")
    p_retrieve.add_argument("--email", default="", help="Unpaywall API email")
    p_retrieve.add_argument("--delay", type=float, default=1.0, help="请求间隔秒数")

    # extract
    p_extract = sub.add_parser("extract", help="从论文 PDF 的参考文献页解析文献列表")
    p_extract.add_argument("input", help="含参考文献的 PDF 文件")
    p_extract.add_argument("-o", "--output", default="", help="输出文件路径")
    p_extract.add_argument("--titles-only", action="store_true", help="仅输出标题列表")

    # parse
    p_parse = sub.add_parser("parse", help="解析论文 .docx 结构")
    p_parse.add_argument("input", help="论文 .docx 路径")
    p_parse.add_argument("--json", action="store_true", help="JSON 格式输出")

    p_evidence = sub.add_parser('evidence', help='离线导出 PDF/txt/md 原文、页码与文件指纹（JSON）')
    p_evidence.add_argument('input', help='本地证据文件')

    p_humanize = sub.add_parser('humanize-scan', help='只读扫描 AI 写作痕迹；不自动改写')
    p_humanize.add_argument('input', help='论文 .docx 路径')
    p_humanize.add_argument('--language', choices=['zh', 'en'], default=None, help='强制语言；默认自动检测')
    p_humanize.add_argument('--json', action='store_true', help='JSON 输出（默认）')

    # kb
    p_kb = sub.add_parser("kb", help="构建参考文献知识库")
    p_kb.add_argument("--pdfs", help="PDF 目录")
    p_kb.add_argument("--titles", help="标题文件 (.txt/.csv/.xlsx)")
    p_kb.add_argument("--metadata", help="元数据 CSV")
    p_kb.add_argument("--summarize", action="store_true", help="LLM 摘要提取")
    p_kb.add_argument("--no-metadata", action="store_true", help="跳过 API 元数据查询")
    p_kb.add_argument("--model", default="deepseek-v4-pro", help="LLM 模型")
    p_kb.add_argument("--email", default="", help="Unpaywall API email")
    p_kb.add_argument("--delay", type=float, default=1.0, help="请求间隔秒数")
    p_kb.add_argument("-o", "--output", default="workspaces/default/kb", help="输出目录")

    # match
    p_match = sub.add_parser("match", help="匹配参考文献到论文章节")
    p_match.add_argument("--paper", required=True, help="论文 .docx")
    p_match.add_argument("--kb", required=True, help="知识库目录")
    p_match.add_argument("--prompt", default="prompts/matching_prompt.md", help="匹配提示词")
    p_match.add_argument("--model", default="deepseek-v4-pro", help="LLM 模型")
    p_match.add_argument("-o", "--output", default="matching_report.xlsx", help="输出路径")

    # generate
    p_gen = sub.add_parser("generate", help="生成引用内容")
    p_gen.add_argument("--report", required=True, help="匹配报告 .xlsx")
    p_gen.add_argument("--kb", required=True, help="知识库目录")
    p_gen.add_argument("--prompt", default="prompts/generation_prompt.md", help="生成提示词")
    p_gen.add_argument("--model", default="deepseek-v4-pro", help="LLM 模型")
    p_gen.add_argument("-o", "--output", default="generated_citations.xlsx", help="输出路径")

    # edit
    p_edit = sub.add_parser("edit", help="插入引用并导出增强版论文")
    p_edit.add_argument("--paper", required=True, help="论文 .docx")
    edit_input = p_edit.add_mutually_exclusive_group(required=True)
    edit_input.add_argument("--citations", help="兼容模式：引用内容 .xlsx")
    edit_input.add_argument('--edits', help='当前 Agent 生成的结构化修改清单 JSON')
    p_edit.add_argument('--attempt', help='必须不存在的本次尝试目录')
    p_edit.add_argument('--validate-only', action='store_true', help='仅校验整批修改，不写文件')
    p_edit.add_argument("-o", "--output", default="enhanced_paper.docx", help="输出路径")
    p_edit.add_argument("--report", help="引用报告路径")

    # cnki-enrich
    p_cnki = sub.add_parser("cnki-enrich", help="中文文献知网元数据补全 (需 Chrome DevTools MCP)")
    p_cnki.add_argument("--titles", required=True, help="标题文件 (.txt/.csv/.xlsx，支持 GB/T 7714 格式)")
    p_cnki.add_argument("--kb", default="workspaces/default/kb", help="知识库目录 (合并模式时使用)")
    p_cnki.add_argument("--batch", type=int, default=5, help="每批处理的文献数 (默认: 5)")
    p_cnki.add_argument("--results", help="subagent 输出的 JSON 结果文件 (合并模式)")

    # full — one-shot pipeline
    p_full = sub.add_parser("full", help="一键: match → generate → edit")
    p_full.add_argument("--paper", required=True, help="论文 .docx")
    p_full.add_argument("--kb", required=True, help="知识库目录")
    p_full.add_argument("--model", default="deepseek-v4-pro", help="LLM 模型")
    p_full.add_argument("--prompt-dir", default="prompts", help="提示词目录")
    p_full.add_argument("--match-output", help="匹配报告输出路径")
    p_full.add_argument("--gen-output", help="引用内容输出路径")
    p_full.add_argument("-o", "--output", default="enhanced_paper.docx", help="最终输出路径")

    # agent — unified adaptive pipeline
    p_agent = sub.add_parser("agent", help="统一自适应管道: 文献分类 → 需求评估 → 引用生成")
    p_agent.add_argument("--paper", help="论文 .docx 路径")
    p_agent.add_argument("--pdfs", help="参考文献 PDF 目录或 .zip 路径（可选）")
    p_agent.add_argument("--model", default="deepseek-v4-pro", help="LLM 模型")
    p_agent.add_argument("--email", default="", help="Unpaywall API email")
    p_agent.add_argument("--delay", type=float, default=1.0, help="API 请求间隔")
    p_agent.add_argument("--kb", help="复用已有知识库目录（跳过 Steps 2-4）")
    p_agent.add_argument("--style", choices=["gbt7714-numeric", "gbt7714-author-year",
                          "apa7", "ieee", "harvard", "mla", "chicago", "custom"],
                          help="参考文献格式体系（跳过交互询问）")
    p_agent.add_argument("--template", metavar="PATH",
                          help="模板 .docx 路径（学校/期刊模板，优先级最高）")
    p_agent.add_argument("--requirements", metavar="TEXT",
                          help="参考文献格式要求文字描述（中文在前/英文在后等）")
    p_agent.add_argument("--explore", action="store_true",
                          help="匹配前进入文献探索模式（交互式语义搜索 Q&A）")

    # query
    p_query = sub.add_parser("query", help="语义搜索知识库")
    p_query.add_argument("--kb", required=True, help="知识库目录")
    p_query.add_argument("question", nargs="?", help="搜索问题（省略则进入交互模式）")
    p_query.add_argument("--top-k", type=int, default=5, help="返回结果数")
    p_query.add_argument("--quality", choices=["high", "medium", "low"],
                          help="按知识质量过滤")
    p_query.add_argument("--export", help="导出结果到 Excel 文件")

    parser.add_argument("--embedding-model", default="",
                        help="嵌入模型名称 (默认: BAAI/bge-large-zh-v1.5)")

    args = parser.parse_args()
    if args.command in {'agent', 'full', 'match', 'generate'} and not args.legacy_api:
        parser.error('旧 API 流程需要 --legacy-api。默认由当前 Agent 读取 USAGE.md，使用 parse/evidence/edit。')

    commands = {
        "agent": cmd_agent, "full": cmd_full,
        "check": cmd_check, "retrieve": cmd_retrieve, "extract": cmd_extract,
        "parse": cmd_parse, "kb": cmd_kb, "match": cmd_match,
        "generate": cmd_generate, "edit": cmd_edit, 'evidence': cmd_evidence,
        "cnki-enrich": cmd_cnki_enrich,
        "query": cmd_query,
        "humanize-scan": cmd_humanize_scan,
    }

    if args.command in commands:
        try:
            commands[args.command](args)
        except Exception as exc:
            machine = (args.command == 'evidence' or
                       args.command == 'parse' and args.json or
                       args.command == 'edit' and getattr(args, 'edits', None) or
                       args.command == 'humanize-scan')
            if not machine:
                raise
            print(json.dumps({'status': 'needs_input' if isinstance(exc, (ValueError, FileNotFoundError)) else 'failed',
                              'error': str(exc), 'error_type': type(exc).__name__, 'artifacts': {}}, ensure_ascii=False))
            sys.exit(1)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
