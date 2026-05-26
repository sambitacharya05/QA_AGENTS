"""
Regression tests against real ingest artifacts.

These tests read from the pre-generated .context_builder/ directory produced by
running ``ingest_workspace`` on the sample `ingest/` workspace.  They are
skipped automatically when the artifacts don't exist (CI without pre-run).

Since Spec 001 (Wave 1), ``graph.json`` and ``documents.json`` are wrapped in a
schema-version envelope::

    {"schema_version": 1, "generator": "context_builder", "written_at": "...",
     "payload": { ... actual data ... }}

``blueprint.json`` continues to be written as raw JSON (no envelope) so that
external tools can read it directly without unwrapping.

These helpers handle the envelope transparently.
"""

import json
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
INGEST_ROOT = REPO_ROOT / "ingest"
ARTIFACT_ROOT = INGEST_ROOT / ".context_builder"


def _load_raw(path: Path) -> dict:
    """Load JSON from *path*; skip the test if the file doesn't exist."""
    if not path.exists():
        pytest.skip(f"Artifact not found (run ingest first): {path}")
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _unwrap_payload(raw: dict) -> dict:
    """Unwrap a schema-versioned envelope, or return the dict as-is for raw files."""
    if "schema_version" in raw and "payload" in raw:
        return raw["payload"]
    return raw


def load_json(path: Path) -> dict:
    """Load and transparently unwrap an artifact file."""
    return _unwrap_payload(_load_raw(path))


def load_graph_nodes() -> list:
    graph = load_json(ARTIFACT_ROOT / "graph.json")
    return graph.get("nodes", [])


# ── Tests ────────────────────────────────────────────────────────────────────

def test_documents_cache_uses_current_workspace_paths_only():
    documents = load_json(ARTIFACT_ROOT / "documents.json")

    assert documents, "documents.json must not be empty after ingest"
    assert all("fitness tracker" not in key for key in documents), (
        "documents.json must not contain paths from a different workspace"
    )


def test_blueprint_reusable_capability_sources_exist_in_workspace():
    # blueprint.json is written raw (no envelope)
    blueprint = _load_raw(ARTIFACT_ROOT / "blueprint.json")
    capabilities = blueprint.get("reusable_capabilities", {})

    missing_sources = []
    for capability in capabilities.values():
        source_file = capability.get("source_file")
        if not source_file:
            continue
        source_path = Path(source_file)
        if not source_path.is_absolute():
            source_path = INGEST_ROOT / source_path
        if not source_path.exists():
            missing_sources.append(source_file)

    assert not missing_sources, (
        f"Blueprint references source files that no longer exist: {missing_sources}"
    )


def test_graph_normalizes_admin_reset_endpoint_to_single_identifier():
    nodes = load_graph_nodes()
    admin_reset_nodes = [
        node for node in nodes
        if node.get("type") == "api_endpoint"
        and node.get("metadata", {}).get("path") == "/admin/test-data/reset"
    ]

    assert len(admin_reset_nodes) == 1, (
        f"Expected exactly one /admin/test-data/reset endpoint node, "
        f"found {len(admin_reset_nodes)}"
    )


def test_blueprint_meta_framework_reflects_nested_bdd_and_qaf_manifests():
    blueprint = _load_raw(ARTIFACT_ROOT / "blueprint.json")
    target_framework = blueprint.get("generation_blueprints", {}).get(
        "target_meta_framework", ""
    )

    assert target_framework != "Playwright TypeScript (Standard Mode)", (
        "Mixed workspace should produce a mixed or QAF/BDD framework label, "
        f"not the default single-framework label.  Got: {target_framework!r}"
    )
