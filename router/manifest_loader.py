"""Load and validate capability manifest.yaml files."""

import os
from pathlib import Path
from typing import Any

import yaml


# ── Minimal manifest schema ──

REQUIRED_FIELDS = ["name", "version", "description", "entry_point"]
OPTIONAL_FIELDS = [
    "axes", "always_load", "fragments", "references",
    "requires", "calls_citation_engine", "cli_command",
    "category",
]


def load_manifest(manifest_path: str | Path) -> dict[str, Any]:
    """Load and validate a single manifest.yaml file.

    Returns the parsed manifest dict, or raises ValueError.
    """
    path = Path(manifest_path)
    if not path.exists():
        raise FileNotFoundError(f"Manifest not found: {path}")

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    # Validate required fields
    for field in REQUIRED_FIELDS:
        if field not in data:
            raise ValueError(f"Manifest {path}: missing required field '{field}'")

    # Warn about unknown fields
    for key in data:
        if key not in REQUIRED_FIELDS and key not in OPTIONAL_FIELDS:
            pass  # Non-critical: unknown fields are ignored

    # Resolve relative paths to absolute (relative to manifest dir)
    manifest_dir = path.parent
    data["_manifest_dir"] = str(manifest_dir)

    # Resolve always_load paths
    if "always_load" in data:
        resolved = []
        for p in data["always_load"]:
            abs_path = (manifest_dir / p).resolve()
            resolved.append(str(abs_path))
        data["always_load"] = resolved

    # Resolve fragment paths
    if "fragments" in data:
        for axis_name, axis_values in data["fragments"].items():
            if isinstance(axis_values, dict):
                for val_name, val_path in axis_values.items():
                    if isinstance(val_path, str):
                        abs_path = (manifest_dir / val_path).resolve()
                        data["fragments"][axis_name][val_name] = str(abs_path)

    # Resolve reference paths
    if "references" in data and "on_demand" in data["references"]:
        for ref in data["references"]["on_demand"]:
            if "path" in ref:
                abs_path = (manifest_dir / ref["path"]).resolve()
                ref["path"] = str(abs_path)

    return data


def discover_capabilities(capabilities_root: str | Path) -> dict[str, dict[str, Any]]:
    """Scan a directory tree for manifest.yaml files.

    Returns: dict mapping capability name → manifest dict.
    """
    root = Path(capabilities_root)
    if not root.is_dir():
        return {}

    manifests: dict[str, dict[str, Any]] = {}
    for manifest_path in root.rglob("manifest.yaml"):
        # Skip manifests in _shared directory
        if "_shared" in str(manifest_path):
            continue
        try:
            data = load_manifest(manifest_path)
            manifests[data["name"]] = data
        except (ValueError, yaml.YAMLError):
            pass  # Skip invalid manifests

    return manifests


def get_fragment_paths(manifest: dict[str, Any],
                       axes: dict[str, str]) -> list[str]:
    """Get the fragment file paths for the given axis values.

    Args:
        manifest: Loaded manifest dict.
        axes: Detected axis values, e.g. {"section": "intro", "language": "zh"}.

    Returns:
        List of resolved file paths to load.
    """
    paths: list[str] = []
    fragments = manifest.get("fragments", {})

    for axis_name, axis_value in axes.items():
        if axis_name not in fragments:
            continue
        axis_map = fragments[axis_name]
        if not isinstance(axis_map, dict):
            continue

        # Handle multi-value axes (value can be a list)
        values = axis_value if isinstance(axis_value, list) else [axis_value]
        for v in values:
            if v in axis_map:
                paths.append(axis_map[v])

    return paths
