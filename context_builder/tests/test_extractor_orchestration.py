"""Tests for the ContextExtractor orchestrator — Spec 003 (Wave 2).

Verifies:
  1. BlueprintStore is constructed exactly once per ContextExtractor instance.
  2. The orchestrator accepts both a str path and a GraphStore.
  3. ingest_workspace() returns the expected summary dict shape.
  4. Backward-compat shims delegate correctly.
  5. Sub-modules are wired correctly (verified via mocks).
"""

import os
import sys
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.graph_store import GraphStore
from db.blueprint_store import BlueprintStore
from engine.extractor import ContextExtractor


# ---------------------------------------------------------------------------
# AC3: BlueprintStore constructed exactly once per ContextExtractor instance
# ---------------------------------------------------------------------------

class TestBlueprintStoreConstructedOnce:
    def test_blueprint_store_init_called_once(self, tmp_path):
        """Monkeypatch BlueprintStore.__init__ to a counter and assert count == 1."""
        init_call_count = {"n": 0}
        original_init = BlueprintStore.__init__

        def counting_init(self_inner, *args, **kwargs):
            init_call_count["n"] += 1
            original_init(self_inner, *args, **kwargs)

        with patch.object(BlueprintStore, "__init__", counting_init):
            _extractor = ContextExtractor(str(tmp_path))

        assert init_call_count["n"] == 1, (
            f"BlueprintStore.__init__ should be called exactly once per "
            f"ContextExtractor instance, called {init_call_count['n']} time(s)."
        )

    def test_ingest_workspace_does_not_construct_additional_blueprint_stores(self, tmp_path):
        """Full ingest must not trigger extra BlueprintStore constructions."""
        init_call_count = {"n": 0}
        original_init = BlueprintStore.__init__

        def counting_init(self_inner, *args, **kwargs):
            init_call_count["n"] += 1
            original_init(self_inner, *args, **kwargs)

        # Write a minimal .feature file so ingest has something to do
        feat = tmp_path / "login.feature"
        feat.write_text(
            "Feature: Login\n  Scenario: User logs in\n    Given I open login\n"
        )

        with patch.object(BlueprintStore, "__init__", counting_init):
            extractor = ContextExtractor(str(tmp_path))
            count_after_init = init_call_count["n"]
            extractor.ingest_workspace()
            count_after_ingest = init_call_count["n"]

        assert count_after_init == 1
        assert count_after_ingest == 1, (
            "ingest_workspace() must not construct additional BlueprintStore instances"
        )


# ---------------------------------------------------------------------------
# Constructor: accepts str path and GraphStore
# ---------------------------------------------------------------------------

class TestConstructorOverloading:
    def test_str_path_constructor(self, tmp_path):
        extractor = ContextExtractor(str(tmp_path))
        assert extractor.workspace_path == str(tmp_path)
        assert isinstance(extractor.store, GraphStore)

    def test_graph_store_constructor(self, tmp_path):
        store = GraphStore(str(tmp_path))
        extractor = ContextExtractor(store)
        assert extractor.store is store
        assert extractor.workspace_path == str(tmp_path)

    def test_shared_store_is_same_instance(self, tmp_path):
        """Passing a GraphStore means extractor.store IS the same object."""
        store = GraphStore(str(tmp_path))
        extractor = ContextExtractor(store)
        assert extractor.store is store


# ---------------------------------------------------------------------------
# ingest_workspace() return value shape
# ---------------------------------------------------------------------------

class TestIngestWorkspaceSummary:
    def test_returns_expected_keys(self, tmp_path):
        extractor = ContextExtractor(str(tmp_path))
        result = extractor.ingest_workspace()
        assert "status" in result
        assert "parsed_files" in result
        assert "cached_files" in result
        assert "total_nodes" in result

    def test_status_is_success(self, tmp_path):
        extractor = ContextExtractor(str(tmp_path))
        result = extractor.ingest_workspace()
        assert result["status"] == "success"

    def test_empty_workspace_parsed_count_is_zero(self, tmp_path):
        extractor = ContextExtractor(str(tmp_path))
        result = extractor.ingest_workspace()
        assert result["parsed_files"] == 0
        assert result["cached_files"] == 0

    def test_parsed_count_increments_for_new_files(self, tmp_path):
        (tmp_path / "login.feature").write_text(
            "Feature: Login\n  Scenario: Log in\n    Given I open login\n"
        )
        extractor = ContextExtractor(str(tmp_path))
        result = extractor.ingest_workspace()
        assert result["parsed_files"] >= 1


# ---------------------------------------------------------------------------
# Backward-compat shims
# ---------------------------------------------------------------------------

class TestBackwardCompatShims:
    def test_map_relationships_shim_calls_edge_mappers(self, tmp_path):
        """map_relationships() must delegate to the edge mappers."""
        extractor = ContextExtractor(str(tmp_path))

        call_counts = {"heuristic": 0, "test": 0}

        def fake_heuristic_map(store):
            call_counts["heuristic"] += 1
            return []

        def fake_test_map(store):
            call_counts["test"] += 1
            return []

        extractor._edge_mappers[0].map = fake_heuristic_map
        extractor._edge_mappers[1].map = fake_test_map

        extractor.map_relationships()
        assert call_counts["heuristic"] == 1
        assert call_counts["test"] == 1

    def test_map_relationships_accepts_nodes_param_for_compat(self, tmp_path):
        """Calling map_relationships(nodes_list) must not raise."""
        extractor = ContextExtractor(str(tmp_path))
        # Should not raise even though the param is ignored
        extractor.map_relationships([{"id": "x", "type": "business_rule"}])

    def test_map_test_relationships_shim_delegates_to_test_mapper(self, tmp_path):
        """_map_test_relationships() must call TestEdgeMapper.map()."""
        extractor = ContextExtractor(str(tmp_path))

        called = {"n": 0}

        def fake_map(store):
            called["n"] += 1
            return []

        extractor._edge_mappers[1].map = fake_map
        extractor._map_test_relationships()
        assert called["n"] == 1

    def test_map_test_relationships_accepts_nodes_param_for_compat(self, tmp_path):
        """Existing tests that pass a nodes list must not see an error."""
        extractor = ContextExtractor(str(tmp_path))
        extractor._map_test_relationships([])  # should not raise

    def test_compile_utility_blueprints_shim_delegates_to_compiler(self, tmp_path):
        """_compile_utility_blueprints() must call UtilityCompiler.compile()."""
        extractor = ContextExtractor(str(tmp_path))
        called = {"n": 0}

        def fake_compile():
            called["n"] += 1

        extractor._utility_compiler.compile = fake_compile
        extractor._compile_utility_blueprints()
        assert called["n"] == 1


# ---------------------------------------------------------------------------
# Sub-module wiring
# ---------------------------------------------------------------------------

class TestSubModuleWiring:
    def test_walker_is_ingestion_walker(self, tmp_path):
        from engine.ingestion.walker import IngestionWalker
        extractor = ContextExtractor(str(tmp_path))
        assert isinstance(extractor._walker, IngestionWalker)

    def test_dispatcher_is_parse_dispatcher(self, tmp_path):
        from engine.ingestion.parse_dispatcher import ParseDispatcher
        extractor = ContextExtractor(str(tmp_path))
        assert isinstance(extractor._dispatcher, ParseDispatcher)

    def test_edge_mappers_contain_heuristic_and_test(self, tmp_path):
        from engine.edges.heuristic import HeuristicEdgeMapper
        from engine.edges.test_framework import TestEdgeMapper
        extractor = ContextExtractor(str(tmp_path))
        types = [type(m).__name__ for m in extractor._edge_mappers]
        assert "HeuristicEdgeMapper" in types
        assert "TestEdgeMapper" in types

    def test_endpoint_deduper_is_correct_type(self, tmp_path):
        from engine.edges.endpoint_deduper import EndpointDeduper
        extractor = ContextExtractor(str(tmp_path))
        assert isinstance(extractor._endpoint_deduper, EndpointDeduper)

    def test_utility_compiler_shares_blueprint_store(self, tmp_path):
        """UtilityCompiler and BlueprintSynthesizer must share the same blueprint."""
        extractor = ContextExtractor(str(tmp_path))
        assert extractor._utility_compiler.blueprint is extractor._blueprint
        assert extractor._blueprint_synth.blueprint is extractor._blueprint
