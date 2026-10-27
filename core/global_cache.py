"""Global Knowledge Cache — cross-project knowledge reuse layer.

Stores: PDF parse results, embeddings, LLM summaries, chunks, metadata.
Keyed by: content hash (SHA-256 of PDF) + DOI.

This is a cache, NOT a replacement for the project KB.
The Citation Engine reads only from the project KB.
The Global Cache serves as a pre-population source when building a project KB.

Design principles:
1. Additive — does not modify any existing core module
2. Citation Engine never reads from Global Cache directly
3. Cache hits → skip PDF parsing when building project KB
4. LRU eviction with configurable limits
"""

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Optional

DEFAULT_CACHE_DIR = Path.home() / ".paper-assistant" / "global_cache"
DEFAULT_MAX_ENTRIES = 500
INDEX_FILE = "cache_index.json"


class GlobalKnowledgeCache:
    """L2 cache: stores processed knowledge for cross-project reuse.

    An entry is keyed by (content_hash, doi). When building a project KB,
    we check the global cache first. If a hit is found, we copy the cached
    data into the project KB without re-parsing the PDF.
    """

    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ):
        self.cache_dir = Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_entries = max_entries
        self._index: dict[str, dict] = {}
        self._load_index()

    # ── Public API ──

    def lookup(self, pdf_path: str = "", doi: str = "") -> Optional[dict]:
        """Check if a PDF's data is already cached.

        Args:
            pdf_path: Path to PDF file (used for content hashing).
            doi: DOI of the paper (alternative lookup key).

        Returns:
            Cached entry dict, or None if not found.
        """
        # Try content hash first
        if pdf_path and os.path.exists(pdf_path):
            h = self._content_hash(pdf_path)
            entry = self._load_entry(h)
            if entry:
                # Update last_accessed
                self._touch(h)
                return entry

        # Try DOI
        if doi:
            doi_key = self._doi_key(doi)
            h = self._index.get("_doi_map", {}).get(doi_key, "")
            if h:
                entry = self._load_entry(h)
                if entry:
                    self._touch(h)
                    return entry

        return None

    def store(
        self,
        pdf_path: str,
        entry_data: dict,
        doi: str = "",
    ) -> str:
        """Cache parse results for future reuse.

        Args:
            pdf_path: Path to PDF file (for content hashing).
            entry_data: Dict with parsed data (text, summary, chunks, metadata).
            doi: DOI of the paper.

        Returns:
            The content hash key for this entry.
        """
        h = self._content_hash(pdf_path) if os.path.exists(pdf_path) else self._new_id()

        # Add metadata
        entry_data["_cached_at"] = time.time()
        entry_data["_last_accessed"] = time.time()
        entry_data["_content_hash"] = h
        entry_data["_doi"] = doi
        entry_data["_source_file"] = os.path.basename(pdf_path)

        # Save entry
        self._save_entry(h, entry_data)

        # Update index
        self._index[h] = {
            "cached_at": entry_data["_cached_at"],
            "doi": doi,
            "source_file": entry_data["_source_file"],
        }

        # Update DOI map
        if doi:
            doi_key = self._doi_key(doi)
            if "_doi_map" not in self._index:
                self._index["_doi_map"] = {}
            self._index["_doi_map"][doi_key] = h

        self._save_index()
        self._evict_if_needed()
        return h

    def remove(self, content_hash: str):
        """Remove an entry from the cache."""
        entry_path = self._entry_path(content_hash)
        if entry_path.exists():
            entry_path.unlink()

        if content_hash in self._index:
            doi = self._index[content_hash].get("doi", "")
            del self._index[content_hash]
            if doi and "_doi_map" in self._index:
                self._index["_doi_map"].pop(self._doi_key(doi), None)

        self._save_index()

    def stats(self) -> dict:
        """Return cache statistics."""
        entries = [v for k, v in self._index.items() if not k.startswith("_")]
        total_size = sum(
            self._entry_path(h).stat().st_size
            for h in entries
            if self._entry_path(h).exists()
        )
        return {
            "entry_count": len(entries),
            "max_entries": self.max_entries,
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "cache_dir": str(self.cache_dir),
        }

    # ── Internal helpers ──

    def _content_hash(self, file_path: str) -> str:
        """SHA-256 of head + tail of a PDF (fast, avoids reading full file)."""
        file_size = os.path.getsize(file_path)
        with open(file_path, "rb") as f:
            head = f.read(65536)
            if file_size > 131072:
                f.seek(-65536, os.SEEK_END)
            tail = f.read(65536)
        return hashlib.sha256(head + tail).hexdigest()[:32]

    def _new_id(self) -> str:
        """Generate a unique ID for entries without a PDF."""
        return hashlib.sha256(os.urandom(16)).hexdigest()[:32]

    @staticmethod
    def _doi_key(doi: str) -> str:
        """Normalize DOI for consistent lookup."""
        return doi.strip().lower()

    def _entry_path(self, content_hash: str) -> Path:
        """File path for a cached entry."""
        return self.cache_dir / f"{content_hash}.json"

    def _load_entry(self, content_hash: str) -> Optional[dict]:
        """Load a cached entry from disk."""
        path = self._entry_path(content_hash)
        if not path.exists():
            return None
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def _save_entry(self, content_hash: str, data: dict):
        """Save a cached entry to disk."""
        path = self._entry_path(content_hash)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _touch(self, content_hash: str):
        """Update last_accessed time for an entry (lazy — updates index only)."""
        if content_hash in self._index:
            self._index[content_hash]["_last_accessed"] = time.time()

    def _load_index(self):
        """Load the cache index from disk."""
        index_path = self.cache_dir / INDEX_FILE
        if index_path.exists():
            try:
                with open(index_path, encoding="utf-8") as f:
                    self._index = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._index = {}

    def _save_index(self):
        """Save the cache index to disk."""
        index_path = self.cache_dir / INDEX_FILE
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(self._index, f, ensure_ascii=False, indent=2)

    def _evict_if_needed(self):
        """LRU eviction: remove oldest entries if over max_entries."""
        entries = {
            k: v for k, v in self._index.items()
            if not k.startswith("_")
        }
        if len(entries) <= self.max_entries:
            return

        # Sort by last_accessed (oldest first)
        sorted_entries = sorted(
            entries.items(),
            key=lambda kv: kv[1].get("_last_accessed", 0),
        )

        # Remove oldest entries
        to_remove = len(entries) - self.max_entries
        for h, _ in sorted_entries[:to_remove]:
            self.remove(h)
