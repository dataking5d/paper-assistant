"""Lightweight vector search layer for paper-assistant.

LanceDB + BAAI/bge-large-zh-v1.5. Zero external service dependencies.
All lancedb/sentence_transformers imports are lazy — the module is importable
without them installed as long as no vector methods are called.

Layered indexing:
  kb_quality "high"   → full_text chunked (section-aware, ~500 chars)
  kb_quality "medium" / "low" → abstract as single chunk
  kb_quality "minimal"        → skip
"""

import os
import re
from pathlib import Path


# ── Embedding Provider (lazy model loading) ──

DEFAULT_EMBEDDING_MODEL = "BAAI/bge-large-zh-v1.5"


class EmbeddingProvider:
    """Lazy sentence-transformers wrapper for Chinese academic text.

    The model (~1.3 GB) is downloaded on first embed() call, not at init.
    """

    def __init__(self, model_name: str = DEFAULT_EMBEDDING_MODEL, device: str = "cpu"):
        self.model_name = model_name
        self.device = device
        self._model = None

    def _ensure_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.model_name, device=self.device)

    def embed(self, texts: list[str]) -> list[list[float]]:
        self._ensure_model()
        return self._model.encode(
            texts, normalize_embeddings=True, show_progress_bar=False
        ).tolist()

    def embed_query(self, text: str) -> list[float]:
        return self.embed([text])[0]


# ── Academic Chunker ──

class AcademicChunker:
    """Section-aware chunking for Chinese/English academic papers.

    Detects section headings then splits text into chunks.
    Never splits across a heading boundary.
    """

    HEADING_PATTERNS = [
        re.compile(r"^第[一二三四五六七八九十\d]+[章节部分]"),
        re.compile(r"^\d+[\.\s]+\w"),                       # "1. Introduction"
        re.compile(r"^\d+\.\d+[\.\s]"),                     # "1.1 Subsection"
        re.compile(r"^(?:Abstract|摘要|Introduction|引言|"
                    r"绪论|前言|Literature Review|文献综述|"
                    r"Methodology|研究方法|方法|"
                    r"Results|结果|Discussion|讨论|"
                    r"Conclusion|结论|References|参考文献|"
                    r"Appendix|附录|Acknowledgments?|致谢)",
                    re.IGNORECASE),
    ]

    CHUNK_SIZE = 500      # characters
    OVERLAP = 100

    def chunk(self, text: str, title: str = "") -> list[dict]:
        """Split text into overlapping chunks, preserving section context.

        Returns list of {"text": str, "section": str, "chunk_index": int}
        """
        paragraphs = self._split_paragraphs(text)
        sections = self._group_by_headings(paragraphs)
        chunks = []
        for section_heading, paras in sections:
            section_text = "\n".join(paras)
            section_chunks = self._chunk_text(section_text)
            for ci, chunk_text in enumerate(section_chunks):
                chunks.append({
                    "text": chunk_text,
                    "section": section_heading or "(正文)",
                    "chunk_index": ci,
                })
        return chunks

    def _split_paragraphs(self, text: str) -> list[str]:
        paras = text.split("\n")
        return [p.strip() for p in paras if p.strip()]

    def _group_by_headings(self, paragraphs: list[str]) -> list[tuple[str, list[str]]]:
        groups: list[tuple[str, list[str]]] = []
        current_heading = ""
        current_paras: list[str] = []

        for para in paragraphs:
            if self._is_heading(para):
                if current_paras:
                    groups.append((current_heading, current_paras))
                current_heading = para
                current_paras = []
            else:
                current_paras.append(para)

        if current_paras:
            groups.append((current_heading, current_paras))
        return groups

    def _is_heading(self, text: str) -> bool:
        if len(text) > 80:
            return False
        for pattern in self.HEADING_PATTERNS:
            if pattern.match(text):
                return True
        if len(text) <= 30 and not text.endswith((".", "。", "!", "！", "?", "？")):
            return True
        return False

    def _chunk_text(self, text: str) -> list[str]:
        if len(text) <= self.CHUNK_SIZE:
            return [text] if text.strip() else []

        chunks = []
        start = 0
        while start < len(text):
            end = min(start + self.CHUNK_SIZE, len(text))
            chunk = text[start:end]
            if chunk.strip():
                chunks.append(chunk.strip())
            start += (self.CHUNK_SIZE - self.OVERLAP)
            if start >= len(text):
                break

        return chunks


# ── Reference Vector Store (LanceDB) ──

TABLE_NAME = "reference_chunks"


class ReferenceVectorStore:
    """LanceDB-backed semantic search over ReferenceEntry objects.

    Persistence: LanceDB at kb_dir / "lancedb"
    Single table "reference_chunks" — cross-document search by default,
    within-document via SQL where filter.
    """

    def __init__(self, kb_dir: str | Path,
                 embedding_provider: EmbeddingProvider | None = None):
        self.kb_dir = Path(kb_dir)
        self.db_path = str(self.kb_dir / "lancedb")
        self._db = None
        self._table = None
        self.embedding_provider = embedding_provider or EmbeddingProvider()
        self.chunker = AcademicChunker()

    # ── Indexing ──

    def build_index(self, entries) -> int:
        """Build LanceDB table from ReferenceEntry list.

        Layering:
          - full_text (kb_quality=high) → AcademicChunker → multiple chunks
          - abstract only (kb_quality=medium/low) → single chunk
          - title only (kb_quality=minimal) → skip

        Returns number of chunks indexed.
        """
        import lancedb

        os.makedirs(self.db_path, exist_ok=True)
        self._db = lancedb.connect(self.db_path)
        self._table = None

        rows = []
        for i, entry in enumerate(entries):
            quality = (entry.meta or {}).get("kb_quality", "low")

            if quality == "minimal":
                continue

            if entry.full_text and len(entry.full_text) > 200:
                chunks = self.chunker.chunk(entry.full_text, entry.title)
            elif entry.abstract and len(str(entry.abstract)) > 20:
                chunks = [{"text": str(entry.abstract), "section": "Abstract",
                           "chunk_index": 0}]
            else:
                continue

            base_meta = entry.meta or {}
            for chunk in chunks:
                rows.append({
                    "chunk_id": f"ref{i}_{chunk['chunk_index']}",
                    "text": chunk["text"],
                    "ref_index": i,
                    "ref_title": entry.title[:200],
                    "authors": (entry.authors or "")[:100],
                    "year": str(entry.year or ""),
                    "section": chunk["section"][:100],
                    "chunk_index": chunk["chunk_index"],
                    "source_file": (entry.source_file or "")[:200],
                    "kb_quality": quality,
                    "classification_role": base_meta.get("classification_role", ""),
                })

        if not rows:
            return 0

        # Embed in batches
        batch_size = 32
        all_vectors = []
        for b_start in range(0, len(rows), batch_size):
            b_end = min(b_start + batch_size, len(rows))
            batch_texts = [r["text"] for r in rows[b_start:b_end]]
            vectors = self.embedding_provider.embed(batch_texts)
            all_vectors.extend(vectors)

        for i, vec in enumerate(all_vectors):
            rows[i]["vector"] = vec

        # Power of 2 batch size for LanceDB ingestion
        self._table = self._db.create_table(
            TABLE_NAME, rows, mode="overwrite",
        )
        return len(rows)

    # ── Search ──

    def search(self, query: str, top_k: int = 5,
               quality_filter: str | None = None,
               role_filter: str | None = None) -> list[dict]:
        """Cross-document semantic search."""
        table = self._ensure_table()
        if table is None:
            return []

        query_vector = self.embedding_provider.embed_query(query)
        q = table.search(query_vector).metric("cosine").limit(top_k)

        if quality_filter:
            q = q.where(f"kb_quality='{quality_filter}'", prefilter=True)
        if role_filter:
            q = q.where(f"classification_role='{role_filter}'", prefilter=True)

        results = q.to_list()
        return self._format_results(results)

    def search_by_document(self, query: str, document_title: str,
                           top_k: int = 5) -> list[dict]:
        """Within-document search: only chunks from a specific paper title."""
        table = self._ensure_table()
        if table is None:
            return []

        # Escape single quotes in title for SQL safety
        safe_title = document_title[:200].replace("'", "''")
        query_vector = self.embedding_provider.embed_query(query)
        results = table.search(query_vector).metric("cosine") \
            .where(f"ref_title='{safe_title}'", prefilter=True) \
            .limit(top_k).to_list()
        return self._format_results(results)

    # ── Persistence ──

    def load(self) -> bool:
        """Load existing table. Returns True if data exists."""
        import lancedb
        import pyarrow as pa

        if not os.path.isdir(self.db_path):
            return False
        try:
            self._db = lancedb.connect(self.db_path)
            self._table = self._db.open_table(TABLE_NAME)
            return self._table.count_rows() > 0
        except Exception:
            return False

    @property
    def is_ready(self) -> bool:
        table = self._ensure_table()
        if table is None:
            return False
        try:
            return table.count_rows() > 0
        except Exception:
            return False

    @property
    def chunk_count(self) -> int:
        table = self._ensure_table()
        if table is None:
            return 0
        try:
            return table.count_rows()
        except Exception:
            return 0

    # ── Internal ──

    def _ensure_table(self):
        import lancedb
        if self._table is not None:
            return self._table
        try:
            if self._db is None and os.path.isdir(self.db_path):
                self._db = lancedb.connect(self.db_path)
            if self._db is not None:
                self._table = self._db.open_table(TABLE_NAME)
                return self._table
        except Exception:
            pass
        return None

    @staticmethod
    def _format_results(lance_results: list[dict]) -> list[dict]:
        """Normalize LanceDB results to the same format ChromaDB returned."""
        output = []
        for row in lance_results:
            distance = row.get("_distance", 1.0)
            output.append({
                "chunk_id": row.get("chunk_id", ""),
                "text": row.get("text", ""),
                "ref_index": row.get("ref_index", -1),
                "ref_title": row.get("ref_title", ""),
                "authors": row.get("authors", ""),
                "year": row.get("year", ""),
                "section": row.get("section", ""),
                "chunk_index": row.get("chunk_index", 0),
                "source_file": row.get("source_file", ""),
                "kb_quality": row.get("kb_quality", ""),
                "classification_role": row.get("classification_role", ""),
                "distance": round(distance, 4) if isinstance(distance, (int, float)) else distance,
            })
        return output
