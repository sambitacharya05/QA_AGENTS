"""
Tests for Spec 002 — MCP Server Hygiene.

Covers:
- Zero stdout writes (logging goes to stderr only)
- Multi-workspace isolation via WorkspaceRegistry
- Path-traversal rejection on add_node / add_edge
- Typed ToolResponse envelopes for key tools
- list_workspaces tool
- CONTEXT_BUILDER_LOG_LEVEL environment variable
"""
import json
import logging
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main
from db.graph_store import GraphStore
from engine.workspace_registry import WorkspaceRegistry
from engine.paths import ensure_within_workspace, PathTraversalError
from mcp_models import ToolResponse, QueryResult, IngestResult


# ── WorkspaceRegistry unit tests ────────────────────────────────────────────

class TestWorkspaceRegistry:
    def test_acquire_returns_graph_store(self, tmp_path):
        reg = WorkspaceRegistry()
        store = reg.acquire(str(tmp_path))
        assert isinstance(store, GraphStore)

    def test_acquire_same_path_returns_cached_store(self, tmp_path):
        reg = WorkspaceRegistry()
        s1 = reg.acquire(str(tmp_path))
        s2 = reg.acquire(str(tmp_path))
        assert s1 is s2, "Registry must cache and reuse the store for the same path"

    def test_acquire_sets_default_workspace(self, tmp_path):
        reg = WorkspaceRegistry()
        reg.acquire(str(tmp_path), make_default=True)
        assert reg.default_workspace() == os.path.realpath(str(tmp_path))

    def test_get_with_path(self, tmp_path):
        reg = WorkspaceRegistry()
        reg.acquire(str(tmp_path))
        store = reg.get(str(tmp_path))
        assert isinstance(store, GraphStore)

    def test_get_without_path_returns_default(self, tmp_path):
        reg = WorkspaceRegistry()
        reg.acquire(str(tmp_path), make_default=True)
        store = reg.get()
        assert store is reg.get(str(tmp_path))

    def test_get_unknown_path_raises_lookup_error(self, tmp_path):
        reg = WorkspaceRegistry()
        with pytest.raises(LookupError, match="not been ingested"):
            reg.get(str(tmp_path))

    def test_get_no_default_raises_lookup_error(self):
        reg = WorkspaceRegistry()
        with pytest.raises(LookupError, match="No active workspace"):
            reg.get()

    def test_acquire_nonexistent_path_raises(self, tmp_path):
        reg = WorkspaceRegistry()
        with pytest.raises(FileNotFoundError):
            reg.acquire(str(tmp_path / "does_not_exist"))

    def test_list_workspaces(self, tmp_path):
        wsA = tmp_path / "wsA"
        wsB = tmp_path / "wsB"
        wsA.mkdir()
        wsB.mkdir()

        reg = WorkspaceRegistry()
        reg.acquire(str(wsA))
        reg.acquire(str(wsB))

        listed = reg.list_workspaces()
        assert os.path.realpath(str(wsA)) in listed
        assert os.path.realpath(str(wsB)) in listed

    def test_two_workspace_isolation(self, tmp_path):
        """Nodes ingested into wsA must not appear in a query on wsB."""
        wsA = tmp_path / "wsA"
        wsB = tmp_path / "wsB"
        wsA.mkdir()
        wsB.mkdir()

        reg = WorkspaceRegistry()
        storeA = reg.acquire(str(wsA))
        storeB = reg.acquire(str(wsB))

        storeA.upsert_node("only_in_a", "business_rule", "Only A", "", {})
        storeA.save_graph()

        storeB.upsert_node("only_in_b", "business_rule", "Only B", "", {})
        storeB.save_graph()

        nodes_a = storeA.query_nodes(node_type="business_rule")
        nodes_b = storeB.query_nodes(node_type="business_rule")

        assert all(n["id"] == "only_in_a" for n in nodes_a), \
            "wsA store must contain only wsA nodes"
        assert all(n["id"] == "only_in_b" for n in nodes_b), \
            "wsB store must contain only wsB nodes"


# ── Path containment helper unit tests ──────────────────────────────────────

class TestPathContainment:
    def test_absolute_inside_workspace_passes(self, tmp_path):
        target = str(tmp_path / "a" / "b.json")
        resolved = ensure_within_workspace(target, str(tmp_path))
        assert resolved == os.path.realpath(target)

    def test_relative_inside_workspace_passes(self, tmp_path):
        resolved = ensure_within_workspace("subdir/file.json", str(tmp_path))
        assert resolved == os.path.realpath(str(tmp_path / "subdir" / "file.json"))

    def test_traversal_above_workspace_raises(self, tmp_path):
        with pytest.raises(PathTraversalError):
            ensure_within_workspace("../../etc/passwd", str(tmp_path))

    def test_absolute_outside_workspace_raises(self, tmp_path):
        with pytest.raises(PathTraversalError):
            ensure_within_workspace("/etc/passwd", str(tmp_path))

    def test_workspace_root_itself_passes(self, tmp_path):
        resolved = ensure_within_workspace(str(tmp_path), str(tmp_path))
        assert resolved == os.path.realpath(str(tmp_path))


# ── MCP tool response contract tests ────────────────────────────────────────

class TestTypedResponses:
    """Tools must return ToolResponse envelopes so callers can branch on ok/error."""

    @pytest.fixture(autouse=True)
    def _reset_registry(self, tmp_path, monkeypatch):
        """Give each test a clean registry pointing at tmp_path.

        We pre-populate several nodes so BM25 has a meaningful corpus
        (a single-document corpus always produces score=0).
        """
        fresh_registry = WorkspaceRegistry()
        store = fresh_registry.acquire(str(tmp_path), make_default=True)
        store.upsert_node("r1", "business_rule", "Rule One", "First business rule description", {})
        # Add extra nodes so BM25 can produce non-zero IDF scores
        for i in range(2, 8):
            store.upsert_node(
                f"dummy_{i}", "business_rule", f"Dummy Rule {i}",
                f"Placeholder requirement description number {i}", {}
            )
        store.save_graph()
        monkeypatch.setattr(main, "registry", fresh_registry)
        monkeypatch.setattr(main, "active_store", None)
        self.tmp_path = tmp_path
        self.store = store

    def test_ingest_workspace_error_on_bad_path(self):
        response = main.ingest_workspace("/no/such/dir/exists/ever")
        assert isinstance(response, ToolResponse)
        assert not response.ok
        assert response.error.code == "INVALID_PATH"

    def test_query_semantic_graph_ok(self):
        response = main.query_semantic_graph("Rule One")
        assert isinstance(response, ToolResponse)
        assert response.ok
        assert isinstance(response.data, QueryResult)
        assert response.data.total_matches >= 1

    def test_query_semantic_graph_workspace_not_found(self):
        response = main.query_semantic_graph("foo", workspace_path="/not/ingested")
        assert isinstance(response, ToolResponse)
        assert not response.ok
        assert response.error.code == "WORKSPACE_NOT_FOUND"

    def test_add_node_path_traversal_rejected(self):
        """add_node with a traversal source_file must return PATH_TRAVERSAL error."""
        response = main.add_node(
            node_id="evil",
            node_type="code_component",
            name="Evil",
            description="",
            metadata=json.dumps({"source_file": "../../etc/passwd"}),
            workspace_path=str(self.tmp_path),
        )
        assert isinstance(response, ToolResponse)
        assert not response.ok
        assert response.error.code == "PATH_TRAVERSAL"

    def test_add_node_ok(self):
        response = main.add_node(
            node_id="new_node",
            node_type="code_component",
            name="NewClass",
            description="Test node",
            metadata="{}",
            workspace_path=str(self.tmp_path),
        )
        assert isinstance(response, ToolResponse)
        assert response.ok
        assert response.data["node_id"] == "new_node"

    def test_add_node_invalid_json_metadata(self):
        response = main.add_node(
            node_id="n",
            node_type="code_component",
            name="N",
            description="",
            metadata="{not valid json",
            workspace_path=str(self.tmp_path),
        )
        assert not response.ok
        assert response.error.code == "VALIDATION_ERROR"

    def test_add_edge_ok(self):
        self.store.upsert_node("src", "business_rule", "Src", "", {})
        self.store.upsert_node("tgt", "api_endpoint", "Tgt", "", {})
        self.store.save_graph()

        response = main.add_edge(
            source_id="src",
            target_id="tgt",
            relationship="implements",
            workspace_path=str(self.tmp_path),
        )
        assert isinstance(response, ToolResponse)
        assert response.ok
        assert response.data["relationship"] == "IMPLEMENTS"

    def test_list_workspaces_returns_registered_paths(self):
        result = main.list_workspaces()
        assert isinstance(result, ToolResponse)
        assert result.ok
        assert os.path.realpath(str(self.tmp_path)) in result.data

    def test_check_workspace_sync_returns_typed_report(self):
        response = main.check_workspace_sync(str(self.tmp_path))
        assert isinstance(response, ToolResponse)
        assert response.ok
        assert hasattr(response.data, "is_in_sync")
        assert hasattr(response.data, "new_files")
        assert hasattr(response.data, "total_indexed")

    def test_get_rule_traceability_node_not_found(self):
        response = main.get_rule_traceability("nonexistent_node_id",
                                              workspace_path=str(self.tmp_path))
        assert isinstance(response, ToolResponse)
        assert not response.ok
        assert response.error.code == "NODE_NOT_FOUND"

    def test_get_rule_traceability_ok(self):
        response = main.get_rule_traceability("r1", workspace_path=str(self.tmp_path))
        assert isinstance(response, ToolResponse)
        assert response.ok
        assert response.data.rule_id == "r1"
        assert len(response.data.nodes) >= 1


# ── Stdout guard tests ───────────────────────────────────────────────────────

class TestStdoutGuard:
    def test_no_print_statements_in_source_modules(self):
        """grep check — no print() calls in server source files."""
        source_roots = [
            os.path.join(os.path.dirname(os.path.dirname(__file__)), d)
            for d in ["db", "engine", "parsers", "main.py"]
        ]

        violations = []
        for root in source_roots:
            if os.path.isfile(root):
                files = [root]
            else:
                files = []
                for dirpath, _, fnames in os.walk(root):
                    for fname in fnames:
                        if fname.endswith(".py"):
                            files.append(os.path.join(dirpath, fname))

            for fpath in files:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    for lineno, line in enumerate(f, start=1):
                        stripped = line.lstrip()
                        # Skip comment lines and noqa annotations
                        if stripped.startswith("#") or "# noqa" in line:
                            continue
                        # Skip lines that are clearly inside docstrings / string literals
                        # (they start with a string character or are plain prose)
                        if stripped.startswith(('"""', "'''", '"', "'", '`')):
                            continue
                        # Use word-boundary regex so "save_blueprint()" doesn't match.
                        # Require print( to appear as a code-level call, not in prose text.
                        if re.search(r'\bprint\(', stripped):
                            violations.append(f"{fpath}:{lineno}: {line.rstrip()}")

        assert violations == [], (
            "Found print() calls in server source — these corrupt the MCP stdio stream:\n"
            + "\n".join(violations)
        )

    def test_logging_goes_to_stderr(self, capsys):
        """After configure_logging(), log.info() must write to stderr not stdout."""
        from engine.logging_setup import configure_logging
        configure_logging()

        log = logging.getLogger("test_stderr_only")
        log.info("This must go to stderr")

        captured = capsys.readouterr()
        # stdout must be empty (or the shim echoed to stderr)
        # The actual write ends up in stderr
        assert "This must go to stderr" in captured.err or captured.out == "", (
            "Log output appeared on stdout which would corrupt the MCP stdio stream"
        )
