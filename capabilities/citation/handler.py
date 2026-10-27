"""Citation capability handler — thin wrapper over Core Citation Engine.

This handler delegates all actual work to core modules without modifying them.
It exists so the Router can treat Citation as a composable Capability.

When invoked:
1. If kb_path is provided → match → generate → edit (fast path)
2. If no kb_path but paper_path → run full agent pipeline
3. Returns the path to the enhanced document
"""

from typing import Any


def handle(context: dict[str, Any]) -> dict[str, Any]:
    """Execute citation pipeline via existing Core modules.

    Context keys used:
        paper_path: Path to thesis .docx
        kb_path: Path to knowledge base (optional - triggers fast path)
        llm_client: Anthropic client
        model: LLM model name
        axes: RouteContext (for style detection)

    Returns:
        dict with result_type="file", content=output_path, artifacts=[output_path]
    """
    from capabilities._shared.protocol import make_result

    paper_path = context.get("paper_path")
    kb_path = context.get("kb_path")
    llm_client = context.get("llm_client")
    model = context.get("model", "deepseek-v4-pro")
    axes = context.get("axes")

    if not paper_path:
        return make_result(
            result_type="error",
            error="Citation capability requires --paper path to thesis .docx",
        )

    # Resolve style from axes
    ref_style = None
    if axes and hasattr(axes, "style"):
        style_map = {
            "gbt7714-numeric": "gbt7714-numeric",
            "gbt7714-author-year": "gbt7714-author-year",
            "apa7": "apa7",
            "ieee": "ieee",
            "mla": "mla",
            "chicago": "chicago",
            "harvard": "harvard",
        }
        ref_style = style_map.get(axes.style)

    try:
        from core.agent import PaperAgent

        agent = PaperAgent(
            llm_client=llm_client,
            model=model,
            email=context.get("email", ""),
            delay=context.get("delay", 1.0),
            explore=context.get("explore", False),
        )

        # Only the exact path returned by this invocation may be reported.
        output_path = agent.run(
            paper_path=paper_path,
            pdf_dir=context.get("pdf_dir"),
            ref_style=ref_style,
            kb_path=kb_path,
        )

        from pathlib import Path
        if not output_path or not Path(output_path).is_file():
            return make_result(result_type='error', error='No output produced by this invocation')

        return make_result(
            result_type="file",
            content=f"Enhanced paper saved to: {output_path}",
            artifacts=[output_path] if output_path else [],
            metadata={"output_path": output_path},
        )

    except ImportError as e:
        return make_result(
            result_type="error",
            error=f"Core citation engine not available: {e}",
        )
    except Exception as e:
        return make_result(
            result_type="error",
            error=f"Citation pipeline failed: {e}",
        )
