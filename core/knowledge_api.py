"""Unified Knowledge API — stable public interface for Core + Capabilities.

This is the recommended interface for Capabilities to access Core knowledge
without understanding internal data structures of ReferenceKB, VectorStore, etc.

Design principles:
1. Stable API — method signatures don't change when internal modules refactor
2. Read-only — does not modify the knowledge base
3. Façade pattern — delegates to core modules internally
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class SearchResult:
    """A single result from semantic search."""
    title: str
    authors: str
    year: str
    text: str
    section: str = ""
    distance: float = 1.0
    ref_id: int = 0


@dataclass
class ReferenceInfo:
    """Structured information about a single reference."""
    title: str
    authors: str
    year: str
    abstract: str = ""
    key_arguments: list[str] = field(default_factory=list)
    methods: str = ""
    classification_role: str = ""
    classification_confidence: float = 0.0
    kb_quality: str = ""
    cited_by_count: int | None = None


class KnowledgeAPI:
    """Stable public API for knowledge operations.

    Capabilities call this, not ReferenceKB directly.

    Usage:
        api = KnowledgeAPI(kb_path)
        results = api.search("样本量论证")
        ref = api.get_reference(3)
        refs = api.list_references(role="Theory Foundation")
    """

    def __init__(self, kb_path: str):
        self._kb_path = Path(kb_path)
        self._kb = None
        self._vs = None
        self._ensure_loaded()

    def _ensure_loaded(self):
        """Lazy-load the knowledge base."""
        if self._kb is not None:
            return
        from .reference_kb import ReferenceKB
        self._kb = ReferenceKB(kb_dir=str(self._kb_path))
        if (self._kb_path / "kb_index.json").exists():
            self._kb.load_index()

    def _ensure_vector_store(self):
        """Lazy-load the vector store if available."""
        if self._vs is not None:
            return
        try:
            from .vector_store import ReferenceVectorStore
            self._vs = ReferenceVectorStore(str(self._kb_path))
            if not self._vs.load():
                self._vs = None
        except ImportError:
            self._vs = None

    @property
    def is_ready(self) -> bool:
        """Whether the knowledge base is loaded and usable."""
        self._ensure_loaded()
        return self._kb is not None and len(self._kb.entries) > 0

    @property
    def reference_count(self) -> int:
        """Number of references in the knowledge base."""
        self._ensure_loaded()
        return len(self._kb.entries) if self._kb else 0

    # ── Search ──

    def search(
        self,
        query: str,
        top_k: int = 5,
        quality_filter: str | None = None,
    ) -> list[SearchResult]:
        """Semantic search over the knowledge base.

        Args:
            query: Search query text.
            top_k: Number of results to return.
            quality_filter: Optional quality filter (high/medium/low).

        Returns:
            List of SearchResult objects sorted by relevance.
        """
        self._ensure_loaded()
        if not self._kb:
            return []

        self._ensure_vector_store()

        results: list[SearchResult] = []

        if self._vs and self._vs.is_ready:
            raw_results = self._vs.search(query, top_k=top_k)
            for r in raw_results:
                if quality_filter and r.get("quality", "") != quality_filter:
                    continue
                results.append(SearchResult(
                    title=r.get("ref_title", ""),
                    authors=r.get("authors", ""),
                    year=str(r.get("year", "")),
                    text=r.get("text", ""),
                    section=r.get("section", ""),
                    distance=r.get("distance", 1.0),
                ))

        # Fallback: keyword search if no vector store
        if not results:
            query_lower = query.lower()
            for i, entry in enumerate(self._kb.entries):
                score = 0
                if query_lower in entry.title.lower():
                    score += 5
                if query_lower in entry.abstract.lower():
                    score += 3
                for arg in entry.key_arguments:
                    if query_lower in arg.lower():
                        score += 2
                if score > 0:
                    results.append(SearchResult(
                        title=entry.title,
                        authors=entry.authors,
                        year=entry.year,
                        text=entry.abstract[:300],
                        ref_id=i + 1,
                        distance=1.0 / score,
                    ))
            results.sort(key=lambda x: x.distance)

        return results[:top_k]

    # ── Reference access ──

    def get_reference(self, ref_id: int) -> Optional[ReferenceInfo]:
        """Get structured info for a specific reference by its 1-based index.

        Args:
            ref_id: 1-based reference index (matching the KB entry order).

        Returns:
            ReferenceInfo or None if index out of bounds.
        """
        self._ensure_loaded()
        if not self._kb:
            return None

        idx = ref_id - 1
        if idx < 0 or idx >= len(self._kb.entries):
            return None

        entry = self._kb.entries[idx]
        meta = getattr(entry, "meta", {}) or {}
        cited = meta.get("cited_by_count")

        return ReferenceInfo(
            title=entry.title,
            authors=entry.authors,
            year=entry.year,
            abstract=entry.abstract,
            key_arguments=entry.key_arguments,
            methods=entry.methods,
            classification_role=meta.get("classification_role", ""),
            classification_confidence=meta.get("classification_confidence", 0.0),
            kb_quality=meta.get("kb_quality", ""),
            cited_by_count=int(cited) if cited else None,
        )

    def list_references(
        self,
        role: str | None = None,
        quality: str | None = None,
    ) -> list[ReferenceInfo]:
        """List all references, optionally filtered.

        Args:
            role: Filter by classification role (Theory Foundation, etc.).
            quality: Filter by KB quality (high/medium/low/minimal).

        Returns:
            List of ReferenceInfo objects.
        """
        self._ensure_loaded()
        if not self._kb:
            return []

        results: list[ReferenceInfo] = []
        for i, entry in enumerate(self._kb.entries):
            meta = getattr(entry, "meta", {}) or {}

            if role and meta.get("classification_role") != role:
                continue
            if quality and meta.get("kb_quality") != quality:
                continue

            cited = meta.get("cited_by_count")
            results.append(ReferenceInfo(
                title=entry.title,
                authors=entry.authors,
                year=entry.year,
                abstract=entry.abstract,
                key_arguments=entry.key_arguments,
                methods=entry.methods,
                classification_role=meta.get("classification_role", ""),
                classification_confidence=meta.get("classification_confidence", 0.0),
                kb_quality=meta.get("kb_quality", ""),
                cited_by_count=int(cited) if cited else None,
            ))

        return results

    def get_context_for_section(self, section_name: str) -> list[ReferenceInfo]:
        """Get references likely relevant to a specific section.

        Uses keyword matching against section names and classification roles.
        More sophisticated matching is done by core/citation_matcher.py.

        Args:
            section_name: Name of the section (e.g., '引言', '方法', '讨论').

        Returns:
            List of ReferenceInfo objects relevant to this section.
        """
        # Role-to-section mapping (heuristic)
        section_lower = section_name.lower()
        role_map = {
            "引言": "Theory Foundation",
            "introduction": "Theory Foundation",
            "文献综述": "Review",
            "literature review": "Review",
            "方法": "Methodology",
            "method": "Methodology",
            "结果": "Empirical Evidence",
            "results": "Empirical Evidence",
            "讨论": "Empirical Evidence",
            "discussion": "Empirical Evidence",
        }

        target_roles: set[str] = set()
        for key, role in role_map.items():
            if key in section_lower:
                target_roles.add(role)

        if not target_roles:
            return self.list_references()

        results = []
        for role in target_roles:
            results.extend(self.list_references(role=role))
        return results

    def role_summary(self) -> dict[str, int]:
        """Get a summary count of references by classification role.

        Returns:
            Dict mapping role name → count.
        """
        self._ensure_loaded()
        counts: dict[str, int] = {}
        if not self._kb:
            return counts

        for entry in self._kb.entries:
            meta = getattr(entry, "meta", {}) or {}
            role = meta.get("classification_role", "Unknown")
            counts[role] = counts.get(role, 0) + 1
        return counts

    def quality_summary(self) -> dict[str, int]:
        """Get a summary count of references by KB quality.

        Returns:
            Dict mapping quality level → count.
        """
        self._ensure_loaded()
        counts: dict[str, int] = {}
        if not self._kb:
            return counts

        for entry in self._kb.entries:
            meta = getattr(entry, "meta", {}) or {}
            quality = meta.get("kb_quality", "minimal")
            counts[quality] = counts.get(quality, 0) + 1
        return counts
