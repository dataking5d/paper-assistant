"""KB Builder with Global Knowledge Cache integration.

Wraps ReferenceKB construction to check the Global Knowledge Cache
before parsing each PDF. If cached data exists, it's reused directly
without re-parsing.

This module is ADDITIVE. It does not modify reference_kb.py.
It provides a convenience function that orchestrates KB building
with Global Cache awareness.
"""

import os
from pathlib import Path
from typing import Optional

from .global_cache import GlobalKnowledgeCache
from .reference_kb import ReferenceKB, ReferenceEntry


def build_kb_with_cache(
    pdf_dir: str,
    kb_dir: str,
    titles: Optional[list[str]] = None,
    metadata_csv: str = "",
    use_cache: bool = True,
    llm_client=None,
    model: str = "deepseek-v4-pro",
) -> ReferenceKB:
    """Build a ReferenceKB, checking Global Cache for each PDF first.

    This wraps the standard KB building process with cache awareness:
    1. For each PDF, check Global Cache
    2. If hit → use cached data (skip PDF parsing)
    3. If miss → parse PDF normally → store in cache

    Args:
        pdf_dir: Directory containing reference PDFs.
        kb_dir: Output directory for the knowledge base.
        titles: Optional list of reference titles.
        metadata_csv: Optional CSV with metadata.
        use_cache: Whether to use the Global Knowledge Cache.
        llm_client: Optional LLM client for summarization.
        model: LLM model name.

    Returns:
        Built ReferenceKB instance (also saved to kb_dir/kb_index.json).
    """
    kb = ReferenceKB(kb_dir=kb_dir)
    cache = GlobalKnowledgeCache() if use_cache else None

    pdf_dir_path = Path(pdf_dir)
    pdf_files = list(pdf_dir_path.glob("*.pdf"))

    if not pdf_files:
        # No PDFs — build from titles + metadata only
        if titles:
            for title in titles:
                kb.entries.append(ReferenceEntry(title=title))
        kb._save_index()
        return kb

    hits = 0
    misses = 0

    for pdf_path in pdf_files:
        # Try cache first
        cached = None
        if cache:
            cached = cache.lookup(str(pdf_path))

        if cached:
            # Reuse cached data
            entry = _cached_to_entry(cached)
            kb.entries.append(entry)
            hits += 1
            continue

        misses += 1

        # Parse PDF normally
        try:
            raw_text = _parse_pdf(str(pdf_path))
        except Exception:
            raw_text = ""

        if not raw_text:
            # Can't parse — create minimal entry
            entry = ReferenceEntry(
                title=pdf_path.stem,
                full_text="",
                source_file=str(pdf_path),
            )
            kb.entries.append(entry)
            continue

        # Build entry
        entry = ReferenceEntry(
            title=pdf_path.stem,
            full_text=raw_text,
            source_file=str(pdf_path),
        )

        # LLM summarization if client available
        if llm_client and raw_text:
            try:
                _summarize_entry(entry, llm_client, model)
            except Exception:
                pass

        kb.entries.append(entry)

        # Store in cache
        if cache and raw_text:
            cache_data = _entry_to_cache_data(entry)
            cache.store(str(pdf_path), cache_data)

    if hits or misses:
        print(f"  KB build: {hits} cache hits, {misses} parsed from PDF")

    kb._save_index()
    return kb


def _parse_pdf(pdf_path: str) -> str:
    """Parse a single PDF and return extracted text."""
    try:
        import pdfplumber
        pages = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    pages.append(text)
        return "\n".join(pages)
    except ImportError:
        pass
    except Exception:
        pass
    return ""


def _summarize_entry(entry: ReferenceEntry, llm_client, model: str):
    """Run LLM summarization on a single entry."""
    content = entry.full_text[:15000]
    message = llm_client.messages.create(
        model=model,
        max_tokens=2048,
        system=(
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
    data = _parse_json_response(raw)
    entry.abstract = data.get("abstract", entry.abstract)
    entry.key_arguments = data.get("key_arguments", [])
    entry.methods = data.get("methods", "")


def _parse_json_response(raw: str) -> dict:
    """Parse JSON from LLM response, handling markdown code blocks."""
    import json
    import re

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
    # Try first { ... } block
    m = re.search(r"\{[\s\S]*\}", raw)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return {}


def _cached_to_entry(cached: dict) -> ReferenceEntry:
    """Convert cached data back to a ReferenceEntry."""
    return ReferenceEntry(
        title=cached.get("title", ""),
        authors=cached.get("authors", ""),
        year=str(cached.get("year", "")),
        abstract=cached.get("abstract", ""),
        key_arguments=cached.get("key_arguments", []),
        methods=cached.get("methods", ""),
        full_text=cached.get("full_text", ""),
        source_file=cached.get("_source_file", ""),
    )


def _entry_to_cache_data(entry: ReferenceEntry) -> dict:
    """Convert a ReferenceEntry to cacheable dict."""
    return {
        "title": entry.title,
        "authors": entry.authors,
        "year": entry.year,
        "abstract": entry.abstract,
        "key_arguments": entry.key_arguments,
        "methods": entry.methods,
        "full_text": entry.full_text,
    }
