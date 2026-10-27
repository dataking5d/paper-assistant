"""CNKI Metadata Enricher — fill the Chinese-paper metadata vacuum.

Uses Chrome DevTools MCP via Claude subagents to search CNKI and extract
rich metadata (abstract, keywords, authors, journal info, citation counts)
for Chinese papers that OpenAlex/Crossref cannot find.

Architecture:
  Python side — batch splitting, task dispatch, result merge
  Claude Agent side — MCP browser automation (one subagent per batch)
"""

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .reference_kb import ReferenceKB, ReferenceEntry


@dataclass
class CnkiMetadata:
    title: str = ""
    title_en: str = ""
    authors: list[str] = field(default_factory=list)
    affiliations: list[str] = field(default_factory=list)
    abstract: str = ""
    abstract_en: str = ""
    keywords: list[str] = field(default_factory=list)
    keywords_en: list[str] = field(default_factory=list)
    journal: str = ""
    journal_en: str = ""
    pub_info: str = ""
    year: str = ""
    fund: str = ""
    classification: str = ""
    citation_count: str = ""
    download_count: str = ""
    is_online_first: bool = False
    gbt_citation: str = ""
    page_url: str = ""


class CnkiEnricher:
    """Orchestrate CNKI metadata enrichment for Chinese paper titles.

    Does NOT call MCP tools directly. Instead, produces batch task specs
    that Claude Code subagents execute via Chrome DevTools MCP. Results
    are collected and merged back into the reference KB.
    """

    # How many papers per subagent batch (each subagent does them sequentially)
    BATCH_SIZE = 5

    def __init__(self, kb_dir: str = "workspaces/default/kb"):
        self.kb_dir = Path(kb_dir)
        self.kb = ReferenceKB(kb_dir=kb_dir)

    # ------------------------------------------------------------------
    # Split: titles → batches for parallel subagent dispatch
    # ------------------------------------------------------------------

    def prepare_batches(self, titles: list[str]) -> list[list[str]]:
        """Split titles into subagent-sized batches."""
        batches = []
        for i in range(0, len(titles), self.BATCH_SIZE):
            batches.append(titles[i:i + self.BATCH_SIZE])
        return batches

    # ------------------------------------------------------------------
    # Build subagent prompt (one batch per subagent)
    # ------------------------------------------------------------------

    def build_agent_prompt(self, batch: list[str], batch_idx: int,
                           total_batches: int) -> str:
        """Build a self-contained prompt for a Claude subagent.

        The subagent receives cnki-skills skill definitions and uses
        Chrome DevTools MCP to execute the enrichment. This method
        returns the instruction text the subagent needs.
        """
        titles_formatted = "\n".join(
            f"  [{i+1}] {t}" for i, t in enumerate(batch)
        )

        return f"""You are enriching Chinese academic paper metadata from CNKI (中国知网).
This is batch {batch_idx+1}/{total_batches}.

## Papers to enrich

{titles_formatted}

## Workflow (per paper)

For each paper above, follow these steps using Chrome DevTools MCP:

### Step 1: Search CNKI
- Navigate to: https://kns.cnki.net/kns8s/search
- Use evaluate_script to search for the paper title (see cnki-search SKILL.md)
- Wait for results, check for captcha
- Find the best matching result (match by full title)

### Step 2: Extract detail
- Navigate to the paper detail page using the result's href
- Use evaluate_script to extract (see cnki-paper-detail SKILL.md):
  - Full title (Chinese + English if available)
  - All authors with affiliations
  - Abstract (Chinese + English if available)
  - Keywords (Chinese + English if available)
  - Journal name, publication date, volume/issue
  - Fund information
  - Classification codes
  - Citation/download counts

### Step 3: Get citation
- On the detail page, extract GB/T 7714 citation using export API
  (see cnki-export SKILL.md Step 1A)
- Or from the ELEARNING/GBTREFER export format

## Output format

Return a JSON array. One object per paper. Fields that couldn't be found should be empty strings or empty arrays:

```json
[
  {{
    "query_title": "原标题",
    "title": "中文标题",
    "title_en": "",
    "authors": ["作者1", "作者2"],
    "affiliations": ["单位1", "单位2"],
    "abstract": "摘要内容...",
    "abstract_en": "",
    "keywords": ["关键词1", "关键词2"],
    "keywords_en": [],
    "journal": "期刊名",
    "journal_en": "",
    "pub_info": "2024, (02): 15-22",
    "year": "2024",
    "fund": "基金项目...",
    "classification": "G206",
    "citation_count": "15",
    "download_count": "320",
    "is_online_first": false,
    "gbt_citation": "[1]作者.题名[J].刊名,年份(期):页码.",
    "page_url": "https://kns.cnki.net/kcms2/article/abstract?..."
  }}
]
```

## Important

- If captcha appears, note it and skip that paper, marking: {{"error": "captcha", "query_title": "..."}}
- If no result found, mark: {{"error": "not_found", "query_title": "..."}}
- Do NOT navigate rapidly — pace page loads 1-2 seconds apart
- Check `#tcaptcha_transform_dy` visibility before each operation
- Return ONLY valid JSON, no markdown wrapping, no extra text before/after the array"""

    # ------------------------------------------------------------------
    # Parse: subagent JSON result → CnkiMetadata list
    # ------------------------------------------------------------------

    @staticmethod
    def parse_results(raw_json: str) -> list[CnkiMetadata]:
        """Parse subagent JSON output into CnkiMetadata objects."""
        # Strip markdown wrappers
        raw = raw_json.strip()
        m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL)
        if m:
            raw = m.group(1)

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Try to find the first valid JSON array
            m = re.search(r"\[[\s\S]*\]", raw)
            if m:
                data = json.loads(m.group(0))
            else:
                return []

        if isinstance(data, dict):
            data = [data]

        results = []
        for item in data:
            results.append(CnkiMetadata(
                title=item.get("title", ""),
                title_en=item.get("title_en", ""),
                authors=item.get("authors", []),
                affiliations=item.get("affiliations", []),
                abstract=item.get("abstract", ""),
                abstract_en=item.get("abstract_en", ""),
                keywords=item.get("keywords", []),
                keywords_en=item.get("keywords_en", []),
                journal=item.get("journal", ""),
                journal_en=item.get("journal_en", ""),
                pub_info=item.get("pub_info", ""),
                year=item.get("year", ""),
                fund=item.get("fund", ""),
                classification=item.get("classification", ""),
                citation_count=item.get("citation_count", ""),
                download_count=item.get("download_count", ""),
                is_online_first=item.get("is_online_first", False),
                gbt_citation=item.get("gbt_citation", ""),
                page_url=item.get("page_url", ""),
            ))
        return results

    # ------------------------------------------------------------------
    # Merge: CnkiMetadata → ReferenceKB
    # ------------------------------------------------------------------

    def merge_to_kb(self, metadata_list: list[CnkiMetadata],
                    query_titles: list[str] | None = None) -> int:
        """Merge CNKI metadata into KB entries. Returns count of enriched entries.

        Matches by fuzzy title comparison against existing KB entries.
        Creates new entries for titles not already in KB.
        """
        self.kb.load_index()

        # Build title index
        title_to_idx = {}
        for i, entry in enumerate(self.kb.entries):
            title_to_idx[self._norm(entry.title)] = i

        enriched = 0
        for meta in metadata_list:
            if not meta.title and not meta.abstract:
                continue

            # Find matching KB entry
            norm_meta = self._norm(meta.title)
            idx = title_to_idx.get(norm_meta)

            if idx is None:
                # Fuzzy match
                for kb_norm, kb_idx in title_to_idx.items():
                    if self._fuzzy_match(norm_meta, kb_norm):
                        idx = kb_idx
                        break

            if idx is not None:
                entry = self.kb.entries[idx]
                self._apply_metadata(entry, meta)
                enriched += 1
            elif meta.title:
                # Create new entry
                entry = ReferenceEntry(title=meta.title)
                self._apply_metadata(entry, meta)
                self.kb.entries.append(entry)
                title_to_idx[self._norm(meta.title)] = len(self.kb.entries) - 1
                enriched += 1

        self.kb._save_index()
        return enriched

    @staticmethod
    def _apply_metadata(entry: ReferenceEntry, meta: CnkiMetadata) -> None:
        """Fill ReferenceEntry fields from CNKI metadata."""
        if meta.title and len(meta.title) > len(entry.title):
            entry.title = meta.title
        if meta.authors:
            entry.authors = "; ".join(meta.authors)
        if meta.year:
            entry.year = meta.year
        if meta.abstract:
            entry.abstract = meta.abstract
        elif meta.abstract_en:
            entry.abstract = meta.abstract_en
        if meta.keywords:
            if not entry.key_arguments:
                entry.key_arguments = meta.keywords[:5]
            else:
                # Merge unique keywords
                existing = set(entry.key_arguments)
                for kw in meta.keywords:
                    if kw not in existing and len(entry.key_arguments) < 8:
                        entry.key_arguments.append(kw)
                        existing.add(kw)
        if meta.journal:
            entry.methods = entry.methods or ""
            if meta.journal not in entry.methods:
                extra = f"[期刊: {meta.journal}]"
                if meta.pub_info:
                    extra = f"[期刊: {meta.journal} {meta.pub_info}]"
                entry.methods = (entry.methods + " " + extra).strip()

    @staticmethod
    def _norm(s: str) -> str:
        """Normalize title for fuzzy comparison."""
        return re.sub(r"[^\w一-鿿]", "", s.lower())

    @staticmethod
    def _fuzzy_match(a: str, b: str, threshold: float = 0.7) -> bool:
        """Check if two normalized titles likely refer to the same paper."""
        if not a or not b:
            return False
        if a == b:
            return True
        if a in b or b in a:
            return True
        chars_a, chars_b = set(a), set(b)
        if not chars_a or not chars_b:
            return False
        overlap = len(chars_a & chars_b) / min(len(chars_a), len(chars_b))
        return overlap > threshold

    # ------------------------------------------------------------------
    # Build enrichment report
    # ------------------------------------------------------------------

    def build_enrichment_report(self, metadata_list: list[CnkiMetadata],
                                input_titles: list[str]) -> dict:
        """Analyze enrichment completeness."""
        found = sum(1 for m in metadata_list if m.abstract)
        has_keywords = sum(1 for m in metadata_list if m.keywords)
        has_authors = sum(1 for m in metadata_list if m.authors)
        has_journal = sum(1 for m in metadata_list if m.journal)
        errors = [m for m in metadata_list
                  if hasattr(m, 'error') and getattr(m, 'error', '')]

        return {
            "total": len(input_titles),
            "enriched": found,
            "has_abstract": found,
            "has_keywords": has_keywords,
            "has_authors": has_authors,
            "has_journal": has_journal,
            "errors": [
                {"title": getattr(e, 'query_title', ''), "error": getattr(e, 'error', '')}
                for e in errors
            ],
        }


def build_agent_tasks(
    titles: list[str],
    batch_size: int = 5,
) -> list[dict[str, Any]]:
    """Generate subagent task specs for parallel dispatch.

    Each task is a dict ready for Agent tool use:
      { "description": ..., "prompt": ..., "batch": [...] }
    """
    enricher = CnkiEnricher()
    batches = enricher.prepare_batches(titles)
    total = len(batches)

    tasks = []
    for i, batch in enumerate(batches):
        tasks.append({
            "batch_index": i,
            "total_batches": total,
            "titles": batch,
            "prompt": enricher.build_agent_prompt(batch, i, total),
            "description": f"Enrich CNKI metadata batch {i+1}/{total}",
        })
    return tasks


# ------------------------------------------------------------------
# CLI entry point (called from main.py)
# ------------------------------------------------------------------

def cmd_cnki_enrich(args) -> None:
    """cnki-enrich: prepare batch tasks for subagent dispatch.

    This command does NOT execute the MCP calls itself. Instead it:
    1. Loads titles
    2. Splits into batches
    3. Prints the subagent prompts (human can trigger via Agent tool)
    4. Optionally loads existing enrichment results from JSON and merges to KB
    """
    titles_path = Path(args.titles)
    if not titles_path.exists():
        print(f"错误: 标题文件不存在: {args.titles}")
        return

    titles = _load_titles(args.titles)
    chinese_titles = [t for t in titles if re.search(r"[一-鿿]", t)]
    english_titles = [t for t in titles if not re.search(r"[一-鿿]", t)]

    print(f"总计 {len(titles)} 篇: {len(chinese_titles)} 中文, "
          f"{len(english_titles)} 英文")

    if args.results:
        # Merge mode: load JSON results from subagent output, merge to KB
        enricher = CnkiEnricher(kb_dir=args.kb)
        metadata = enricher.parse_results(Path(args.results).read_text(encoding="utf-8"))
        print(f"从 {args.results} 解析到 {len(metadata)} 条元数据")

        enriched = enricher.merge_to_kb(metadata, titles)
        report = enricher.build_enrichment_report(metadata, titles)
        print(f"已合并 {enriched} 篇文献元数据到知识库: {enricher.kb._index_path}")

        # Quality stats
        has_abstract = sum(1 for e in enricher.kb.entries if e.abstract and len(e.abstract) > 20)
        has_args = sum(1 for e in enricher.kb.entries if e.key_arguments)
        has_full = sum(1 for e in enricher.kb.entries if e.full_text and len(e.full_text) > 200)
        print(f"KB 质量: {has_abstract}/{len(enricher.kb.entries)} 有摘要, "
              f"{has_args}/{len(enricher.kb.entries)} 有论点, "
              f"{has_full}/{len(enricher.kb.entries)} 有全文")
    else:
        # Prepare mode: show batch plan
        tasks = build_agent_tasks(chinese_titles, batch_size=args.batch)
        print(f"\n中文文献需要 CNKI 元数据补全: {len(chinese_titles)} 篇")
        print(f"分批策略: {len(tasks)} 个子任务 (每批 {args.batch} 篇)")
        print()

        for task in tasks:
            batch = task["titles"]
            print(f"--- Batch {task['batch_index']+1}/{task['total_batches']} "
                  f"({len(batch)} papers) ---")
            for j, title in enumerate(batch, 1):
                print(f"  [{j}] {title[:80]}")
            print()

        print("=== 下一步 ===")
        print("在 Claude Code 中使用 Agent 工具并行派发上述批次。")
        print("每个 subagent 将使用 cnki-skills + Chrome DevTools MCP 抓取元数据。")
        print(f"\n收集结果后，运行合并命令:")
        print(f"  python main.py cnki-enrich --titles {args.titles} --kb {args.kb} --results subagent_output.json")


def _load_titles(path: str) -> list[str]:
    """Reuse titles loading logic from main."""
    ext = Path(path).suffix.lower()
    if ext == ".txt":
        with open(path, encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]
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
    """Extract titles from GB/T 7714 formatted lines."""
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
