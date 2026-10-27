"""Central Router — manifest-driven capability orchestration.

Flow:
1. Receive user request
2. Detect axis values (task_type, language, paper_type, style)
3. Determine required Capabilities
4. Load Capability manifests and fragments
5. Execute pipeline in order
6. Merge and return results
"""

import importlib
import os
from pathlib import Path
from typing import Any

from .axis_detector import RouteContext, detect_context
from .manifest_loader import discover_capabilities, get_fragment_paths


class Router:
    """Central orchestrator for Paper Assistant capabilities.

    The Router sits above Core. It depends on Core, never vice versa.
    Capabilities depend on Core, never on each other.
    """

    def __init__(self, capabilities_root: str | Path | None = None):
        if capabilities_root is None:
            capabilities_root = Path(__file__).parent.parent / "capabilities"
        self.capabilities_root = Path(capabilities_root)
        self._manifests: dict[str, dict[str, Any]] = {}
        self._discover()

    def _discover(self):
        """Scan capabilities/ for manifest.yaml files."""
        self._manifests = discover_capabilities(self.capabilities_root)

    @property
    def available_capabilities(self) -> list[str]:
        """List registered capability names."""
        return sorted(self._manifests.keys())

    def get_manifest(self, name: str) -> dict[str, Any] | None:
        """Get the manifest for a specific capability."""
        return self._manifests.get(name)

    def detect(self, request: str, **overrides) -> RouteContext:
        """Detect axis values from a user request.

        Args:
            request: Natural language request from the user.
            **overrides: Explicit axis values to bypass detection.

        Returns:
            RouteContext with all detected axis values.
        """
        return detect_context(request, **overrides)

    def resolve_pipeline(self, ctx: RouteContext) -> list[str]:
        """Given a RouteContext, return ordered capability names to execute.

        Rules:
        - "citation" is always last (it edits the final document)
        - "writing" and "polishing" can coexist (writing → polishing)
        - "review" and "response" are standalone
        - If writing+polishing are both requested, writing runs first
        """
        tasks = ctx.task_types.copy()

        # Always place citation last if present
        if "citation" in tasks and len(tasks) > 1:
            tasks.remove("citation")
            tasks.append("citation")

        # Only keep tasks that have registered capabilities
        available = set(self._manifests.keys())
        unsupported = [t for t in tasks if t not in available]
        if unsupported or not tasks:
            raise ValueError(f'Unsupported requested capabilities: {unsupported or tasks}')
        if len(tasks) > 1:
            raise ValueError('Legacy multi-capability handoff is not verified; use the host Agent workflow')
        return tasks

    def execute(self, ctx: RouteContext,
                paper_path: str | None = None,
                kb_path: str | None = None,
                llm_client=None,
                model: str = "deepseek-v4-pro",
                **extra) -> dict[str, Any]:
        """Execute the resolved pipeline.

        Args:
            ctx: Detected RouteContext.
            paper_path: Path to the thesis .docx file.
            kb_path: Path to the knowledge base directory.
            llm_client: Anthropic client instance.
            model: LLM model name.
            **extra: Additional context passed through to handlers.

        Returns:
            {
                "results": [per-capability result dicts],
                "artifacts": [paths to generated files],
                "pipeline": [capability names executed],
            }
        """
        pipeline = self.resolve_pipeline(ctx)
        all_results: list[dict] = []
        all_artifacts: list[str] = []
        previous_results: dict[str, Any] = {}

        for cap_name in pipeline:
            manifest = self._manifests.get(cap_name)
            if not manifest:
                continue

            # Build context for this capability
            context = {
                "paper_path": paper_path,
                "kb_path": kb_path,
                "request": ctx.raw_request,
                "axes": ctx,
                "llm_client": llm_client,
                "model": model,
                "previous_results": previous_results,
                "manifest": manifest,
                **extra,
            }

            # Load and execute handler
            try:
                handler = self._load_handler(manifest)
                result = handler(context)
                all_results.append(result)
                if result.get('result_type') in {'error', 'ready'}:
                    break
                if "artifacts" in result:
                    all_artifacts.extend(result["artifacts"])
                # Pass metadata forward
                if "metadata" in result:
                    previous_results[cap_name] = result["metadata"]
            except Exception as e:
                all_results.append({
                    "capability": cap_name,
                    "result_type": "error",
                    "error": str(e),
                })
                break

        return {
            "results": all_results,
            "artifacts": all_artifacts,
            "pipeline": pipeline,
        }

    def execute_single(self, capability_name: str, **context) -> dict[str, Any]:
        """Execute a single capability directly.

        Args:
            capability_name: Name of the capability to execute.
            **context: Context dict passed to the handler.

        Returns:
            Handler result dict.
        """
        manifest = self._manifests.get(capability_name)
        if not manifest:
            return {"result_type": "error",
                    "error": f"Capability '{capability_name}' not found"}

        try:
            handler = self._load_handler(manifest)
            return handler(context)
        except Exception as e:
            return {"result_type": "error",
                    "error": str(e)}

    def _load_handler(self, manifest: dict[str, Any]):
        """Dynamically import a capability handler from its manifest entry_point."""
        entry_point = manifest.get("entry_point", "")
        if ":" not in entry_point:
            raise ValueError(
                f"Invalid entry_point '{entry_point}': "
                f"must be 'module.path:function_name'"
            )

        module_path, func_name = entry_point.split(":", 1)

        try:
            module = importlib.import_module(module_path)
        except ImportError as e:
            raise ImportError(
                f"Cannot import handler module '{module_path}' "
                f"for capability '{manifest['name']}': {e}"
            ) from e

        handler_func = getattr(module, func_name, None)
        if handler_func is None:
            raise AttributeError(
                f"Module '{module_path}' has no function '{func_name}'"
            )

        return handler_func

    def loaded_fragments(self, manifest: dict[str, Any],
                         axes: dict[str, str]) -> list[str]:
        """Resolve which prompt fragments to load for given axes.

        Returns list of absolute file paths to Read.
        """
        return get_fragment_paths(manifest, axes)
