#!/usr/bin/env python3
"""Paper Assistant — Modular Academic Capability Engine.

New Router-based entry point. Composable capabilities for academic work:
  citation  — reference matching, generation, and insertion (Core Engine)
  writing   — academic writing assistance
  polishing — text polishing and academic register enhancement

This is ADDITIVE. The existing main.py CLI is preserved unchanged.
Use this entry point for Router-orchestrated multi-capability workflows.

Usage:
  python paper_assistant.py "帮我给这篇论文插入引用" --paper thesis.docx --kb kb/
  python paper_assistant.py "帮我写一段文献综述" --paper thesis.docx --kb kb/
  python paper_assistant.py "润色这篇论文的引言" --paper thesis.docx
  python paper_assistant.py --list                        # List all capabilities
"""

import argparse
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def build_llm_client(model: str = "deepseek-v4-pro"):
    """Build an Anthropic client pointed at the configured API."""
    try:
        from anthropic import Anthropic
    except ImportError:
        print("Error: anthropic package required. pip install anthropic")
        return None

    api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    if not api_key:
        print("Warning: No API key set (DEEPSEEK_API_KEY or ANTHROPIC_AUTH_TOKEN)")
        return None

    base_url = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/anthropic")
    return Anthropic(api_key=api_key, base_url=base_url)


def cmd_list_capabilities(router):
    """List all available capabilities."""
    print("Available Capabilities:")
    print("-" * 40)
    for name in router.available_capabilities:
        manifest = router.get_manifest(name)
        if manifest:
            desc = manifest.get("description", "").strip().split("\n")[0]
            category = manifest.get("category", "capability")
            print(f"  {name:<15} [{category}] {desc[:80]}")
    print()
    print("Usage: python paper_assistant.py \"<your request>\" [--paper PATH] [--kb PATH]")


def cmd_run(router, args):
    """Run a user request through the Router."""
    request = args.request

    if not request:
        print("Error: Please provide a request describing what you want to do.")
        print("Example: python paper_assistant.py \"帮我给论文插入引用\" --paper thesis.docx")
        sys.exit(1)

    # Detect context
    ctx = router.detect(
        request,
        language=args.language,
        paper_type=args.paper_type,
        style=args.style,
    )

    print()
    print(f"  Task:    {', '.join(ctx.task_types)}")
    print(f"  Language: {ctx.language}")
    print(f"  Type:    {ctx.paper_type}")
    print(f"  Style:   {ctx.style}")
    print()

    # Resolve pipeline
    pipeline = router.resolve_pipeline(ctx)
    print(f"  Pipeline: {' → '.join(pipeline)}")
    print()

    # Build LLM client if any capability needs it
    llm_client = None
    for cap_name in pipeline:
        manifest = router.get_manifest(cap_name)
        if manifest and manifest.get("requires"):
            llm_client = build_llm_client(args.model)
            break

    # Execute
    result = router.execute(
        ctx,
        paper_path=args.paper,
        kb_path=args.kb,
        pdf_dir=args.pdfs,
        llm_client=llm_client,
        model=args.model,
        email=args.email,
        delay=args.delay,
        explore=args.explore,
        template=args.template,
        requirements=args.requirements,
    )

    # Print results
    print("-" * 50)
    for i, r in enumerate(result["results"]):
        cap_name = result["pipeline"][i] if i < len(result["pipeline"]) else "?"
        if r.get("result_type") == "error":
            print(f"  [{cap_name}] Error: {r.get('error', 'unknown')}")
        else:
            content = r.get("content", "")
            if content:
                print(f"  [{cap_name}] {content}")

    if result["artifacts"]:
        print(f"\n  Generated files:")
        for a in result["artifacts"]:
            print(f"    - {a}")

    print()
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Paper Assistant — Modular Academic Capability Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python paper_assistant.py "帮我给这篇论文插入引用" --paper thesis.docx --kb kb/
  python paper_assistant.py "帮我写一段文献综述" --paper thesis.docx --kb kb/
  python paper_assistant.py "润色这篇论文的引言" --paper thesis.docx
  python paper_assistant.py --list
        """,
    )

    parser.add_argument(
        "request", nargs="?", default="",
        help="Natural language request describing what you want to do",
    )
    parser.add_argument(
        "--paper", default=None,
        help="Path to thesis .docx file",
    )
    parser.add_argument(
        "--kb", default=None,
        help="Path to knowledge base directory (enables fast path)",
    )
    parser.add_argument(
        "--pdfs", default=None,
        help="Path to reference PDFs directory or .zip",
    )
    parser.add_argument(
        "--model", default="deepseek-v4-pro",
        help="LLM model name (default: deepseek-v4-pro)",
    )
    parser.add_argument(
        "--language", choices=["zh", "en", "zh-to-en"],
        help="Override language detection",
    )
    parser.add_argument(
        "--paper-type", dest="paper_type", choices=["thesis", "journal"],
        help="Override paper type detection",
    )
    parser.add_argument(
        "--style", choices=[
            "gbt7714-numeric", "gbt7714-author-year",
            "apa7", "ieee", "harvard", "mla", "chicago",
        ],
        help="Override reference style detection",
    )
    parser.add_argument(
        "--email", default="",
        help="Email for Unpaywall API access",
    )
    parser.add_argument(
        "--delay", type=float, default=1.0,
        help="API request interval in seconds (default: 1.0)",
    )
    parser.add_argument(
        "--explore", action="store_true",
        help="Enable interactive literature exploration mode",
    )
    parser.add_argument(
        "--template", default=None,
        help="Path to template .docx for format analysis",
    )
    parser.add_argument(
        "--requirements", default=None,
        help="Natural language description of formatting requirements",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List all available capabilities",
    )
    parser.add_argument('--legacy-api', action='store_true', help='Explicitly enable the unvalidated legacy API entry')

    args = parser.parse_args()
    if not args.list and not args.legacy_api:
        parser.error('Default: host Agent reads USAGE.md and uses main.py parse/evidence/edit. Old API mode requires --legacy-api.')

    # Import Router (add project root to path if needed)
    project_root = Path(__file__).parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    from router.router import Router

    router = Router()

    if args.list:
        cmd_list_capabilities(router)
        return

    cmd_run(router, args)


if __name__ == "__main__":
    main()
