"""Build reference knowledge base from PDF files via MinerU parsing + LLM summarization."""

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from dataclasses import dataclass, field

import pdfplumber


@dataclass
class ReferenceEntry:
    title: str
    authors: str = ""
    year: str = ""
    abstract: str = ""
    key_arguments: list[str] = field(default_factory=list)
    methods: str = ""
    full_text: str = ""
    source_file: str = ""
    meta: dict = field(default_factory=dict)
    # meta fields: cited_by_count, classification_role, classification_confidence,
    #   classification_reasons, classification_source, requirement_level,
    #   requirement_reasons, kb_quality, llm_knowledge_level


class ReferenceKB:
    """Manage a collection of parsed reference papers."""

    def __init__(self, kb_dir: str = "workspaces/default/kb"):
        self.kb_dir = Path(kb_dir)
        self.kb_dir.mkdir(parents=True, exist_ok=True)
        self.entries: list[ReferenceEntry] = []
        self._index_path = self.kb_dir / "kb_index.json"
        self.vector_store = None  # ReferenceVectorStore | None — set by build_vector_index()

    def build_from_pdfs(self, pdf_dir: str, metadata_csv: str | None = None) -> list[ReferenceEntry]:
        """Parse all PDFs in a directory and build the knowledge base.

        Args:
            pdf_dir: Directory containing reference PDF files.
            metadata_csv: Optional CSV with columns title,authors,year for each PDF.
        """
        metadata = {}
        if metadata_csv and Path(metadata_csv).exists():
            metadata = self._load_metadata(metadata_csv)

        pdf_files = sorted(Path(pdf_dir).glob("*.pdf"))
        total = len(pdf_files)
        for i, pdf_path in enumerate(pdf_files, 1):
            print(f"  [{i}/{total}] 解析: {pdf_path.name[:80]}...", flush=True)
            entry = self._process_pdf(pdf_path, metadata)
            self.entries.append(entry)
            print(f"       -> 提取 {len(entry.full_text)} 字符", flush=True)

        self._save_index()
        return self.entries

    def build_from_titles(self, titles: list[str], paper_retriever=None) -> list[ReferenceEntry]:
        """Build KB from title list using paper_retriever for metadata lookup.
        When PDFs are not available, create entries from metadata only.
        """
        for title in titles:
            meta = {}
            if paper_retriever:
                try:
                    meta = paper_retriever._find_metadata(title)
                except Exception:
                    pass

            entry = ReferenceEntry(
                title=meta.get("matched_title", title),
                authors=meta.get("authors", ""),
                year=meta.get("year", ""),
                abstract=meta.get("raw", {}).get("abstract", ""),
                key_arguments=[],
                methods="",
                full_text="",
                source_file="",
            )
            self.entries.append(entry)

        self._save_index()
        return self.entries

    def summarize_with_llm(self, client, model: str, system_prompt: str = "") -> list[ReferenceEntry]:
        """Use LLM to extract structured summaries from each reference's full text."""
        total = len(self.entries)
        for i, entry in enumerate(self.entries, 1):
            if not entry.full_text:
                print(f"  [{i}/{total}] 跳过(无全文): {entry.title[:60]}...", flush=True)
                continue
            print(f"  [{i}/{total}] LLM摘要: {entry.title[:60]}...", flush=True)
            content = entry.full_text[:15000]
            try:
                message = client.messages.create(
                    model=model,
                    max_tokens=2048,
                    system=system_prompt or (
                        "你是一位学术研究助手。请从以下文献内容中提取关键信息。"
                        "只输出JSON，不要输出其他内容。"
                    ),
                    messages=[{
                        "role": "user",
                        "content": (
                            f"文献标题：{entry.title}\n\n"
                            f"文献内容：\n{content}\n\n"
                            "请提取以下信息，以JSON格式输出：\n"
                            '{{"abstract": "摘要(200字内)", '
                            '"key_arguments": ["论点1", "论点2", "论点3"], '
                            '"methods": "研究方法简述"}}'
                        ),
                    }],
                )
                raw = ""
                for block in message.content:
                    if hasattr(block, "text"):
                        raw += block.text
                data = self._parse_json_response(raw)
                entry.abstract = data.get("abstract", entry.abstract)
                entry.key_arguments = data.get("key_arguments", [])
                entry.methods = data.get("methods", "")
            except Exception:
                pass

        self._save_index()
        return self.entries

    def get_context_for_matching(self) -> str:
        """Build a compact context string of all references for the matching LLM."""
        parts = []
        for i, entry in enumerate(self.entries):
            parts.append(
                f"[Ref-{i + 1}] {entry.title}\n"
                f"  作者: {entry.authors} ({entry.year})\n"
                f"  摘要: {entry.abstract[:200]}\n"
                f"  核心论点: {'; '.join(entry.key_arguments[:3])}\n"
                f"  方法: {entry.methods[:100]}"
            )
        return "\n\n".join(parts)

    def get_entry_by_index(self, idx: int) -> ReferenceEntry | None:
        if 0 <= idx < len(self.entries):
            return self.entries[idx]
        return None

    def build_vector_index(self, embed_fn=None) -> int:
        """Build a ChromaDB vector index from current entries.

        Args:
            embed_fn: Optional callable(texts: list[str]) -> list[list[float]].
                      If None, uses default BAAI/bge-large-zh-v1.5.

        Returns number of chunks indexed.
        """
        from .vector_store import ReferenceVectorStore, EmbeddingProvider

        provider = EmbeddingProvider() if embed_fn is None else None
        self.vector_store = ReferenceVectorStore(self.kb_dir, embedding_provider=provider)
        if embed_fn is not None:
            self.vector_store.embedding_provider._model = None  # force custom embed
            self.vector_store.embedding_provider.embed = embed_fn
        n = self.vector_store.build_index(self.entries)
        if n > 0:
            print(f"  向量索引: {n} 个文本块 → {self.vector_store.db_path}")
        return n

    def search(self, query: str, top_k: int = 5,
               quality_filter: str | None = None,
               by_document: str | None = None) -> list[dict]:
        """Semantic search over the vector index.

        Returns empty list if vector_store is not built.
        """
        if self.vector_store is None or not self.vector_store.is_ready:
            return []
        if by_document:
            return self.vector_store.search_by_document(query, by_document, top_k)
        return self.vector_store.search(query, top_k, quality_filter)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _process_pdf(self, pdf_path: Path, metadata: dict[str, dict]) -> ReferenceEntry:
        filename = pdf_path.name
        meta = metadata.get(pdf_path.stem, {})
        raw_text = self._extract_text(pdf_path)

        return ReferenceEntry(
            title=meta.get("title", pdf_path.stem),
            authors=meta.get("authors", ""),
            year=str(meta.get("year", "")),
            full_text=raw_text,
            source_file=str(pdf_path),
        )

    def _extract_text(self, pdf_path: Path) -> str:
        """Extract text from PDF. Prefer MinerU if available, fallback to pdfplumber."""
        # Try MinerU first
        mineru_text = self._try_mineru(pdf_path)
        if mineru_text:
            return mineru_text

        # Fallback to pdfplumber
        print(f"       (MinerU失败，使用pdfplumber)", flush=True)
        pages = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    pages.append(text)
        return "\n".join(pages)

    def _try_mineru(self, pdf_path: Path) -> str | None:
        """Attempt MinerU parsing. Returns None on failure."""
        mineru_exe = os.environ.get(
            "MINERU_EXE",
            "mineru.exe",
        )
        if not Path(mineru_exe).exists():
            return None

        tmp_dir = tempfile.mkdtemp(prefix="mineru_ref_")
        try:
            env = os.environ.copy()
            env["HF_HUB_DISABLE_SYMLINKS"] = "1"
            result = subprocess.run(
                [mineru_exe, "-p", str(pdf_path), "-o", tmp_dir, "-b", "pipeline"],
                capture_output=True, text=True, timeout=600, env=env,
            )
            if result.returncode != 0:
                return None
            md_files = list(Path(tmp_dir).rglob("*.md"))
            if not md_files:
                return None
            with open(md_files[0], encoding="utf-8") as f:
                return f.read()
        except Exception:
            return None
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def _load_metadata(self, csv_path: str) -> dict[str, dict]:
        import csv
        result = {}
        with open(csv_path, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                key = row.get("file_stem", row.get("title", ""))
                result[key] = {"title": row.get("title", ""), "authors": row.get("authors", ""), "year": row.get("year", "")}
        return result

    @staticmethod
    def _parse_json_response(raw: str) -> dict:
        """Robust JSON extraction from LLM response."""
        # Try direct parse
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        # Try ```json ... ``` block
        m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
        # Try first { ... } block
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        return {}

    def _save_index(self) -> None:
        data = []
        for e in self.entries:
            data.append({
                "title": e.title,
                "authors": e.authors,
                "year": e.year,
                "abstract": e.abstract,
                "key_arguments": e.key_arguments,
                "methods": e.methods,
                "full_text": e.full_text,
                "source_file": e.source_file,
                "meta": e.meta,
            })
        fd, temporary = tempfile.mkstemp(dir=self.kb_dir, suffix='.json.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(temporary, self._index_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def load_index(self) -> None:
        if self._index_path.exists():
            with open(self._index_path, encoding="utf-8") as f:
                data = json.load(f)
            entries = []
            for item in data:
                item.setdefault("meta", {})
                if not item.get('full_text'):
                    item['meta']['evidence_gap'] = 'Full text unavailable; read source_file before citing.'
                entries.append(ReferenceEntry(**item))
            self.entries = entries
