"""Capability handler protocol.

Every Capability must expose a handler function with the signature:
    handle(context: dict) -> dict

The handler receives a context dict with all necessary inputs and
returns a structured result dict.
"""

from typing import Any


# ── Standard context keys ──
# paper_path: str | None       — Path to thesis .docx
# kb_path: str | None          — Path to knowledge base directory
# request: str                 — Original user request text
# axes: RouteContext           — Detected axis values
# llm_client: Any | None       — Anthropic client instance
# model: str                   — LLM model name
# previous_results: dict       — Results from prior capabilities in pipeline
# manifest: dict               — This capability's manifest


# ── Standard result keys ──
# result_type: str             — "text" | "file" | "citation_report" | "error"
# content: str                 — Primary output text
# artifacts: list[str]         — Paths to generated files
# metadata: dict               — For passing to next capability in pipeline


def make_result(
    result_type: str = "text",
    content: str = "",
    artifacts: list[str] | None = None,
    error: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a standard capability result dict."""
    return {
        "result_type": result_type,
        "content": content,
        "artifacts": artifacts or [],
        "error": error,
        "metadata": metadata or {},
    }


def load_fragment_text(path: str) -> str:
    """Read a prompt fragment file and return its text content.

    Returns empty string if the file doesn't exist.
    """
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except (FileNotFoundError, OSError):
        return ""


def assemble_system_prompt(fragment_paths: list[str],
                           separator: str = "\n\n") -> str:
    """Load and concatenate multiple fragment files into a system prompt.

    Skips files that don't exist.
    """
    parts = []
    for path in fragment_paths:
        text = load_fragment_text(path)
        if text.strip():
            parts.append(text)
    return separator.join(parts)
