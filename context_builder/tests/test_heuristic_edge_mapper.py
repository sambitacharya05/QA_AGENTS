"""Tests for engine/edges/heuristic.py — HeuristicEdgeMapper.

Spec 003 (Wave 2) — original file.
Spec 006 (Wave 3) — updated to reflect TF-IDF scoring and type-gating.

Key behaviour changes in Spec 006:
  - Token-overlap threshold replaced by TF-IDF cosine similarity (default 0.75).
  - Domain stopwords (policy, coverage, claim, premium, rule, …) are filtered
    before scoring so ubiquitous insurance vocabulary no longer drives edges.
  - Applicable-type gating: only declared (source_type, target_type) pairs for
    a given relationship are eligible.
  - Every emitted edge carries ``metadata.confidence`` (float) and
    ``metadata.evidence.method``.
  - The admin-endpoint path-gate and the class-name structural match are
    preserved with their original semantics.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.graph_store import GraphStore
from engine.edges.heuristic import HeuristicEdgeMapper


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_store(tmp_path, nodes: list[dict]) -> GraphStore:
    store = GraphStore(str(tmp_path))
    for n in nodes:
        store.upsert_node(
            n["id"], n["type"], n["name"],
            n.get("description", ""),
            n.get("metadata", {}),
        )
    return store


def _edge_set(edges: list[dict]) -> set[tuple]:
    return {(e["source_id"], e["target_id"], e["relationship"]) for e in edges}


# ---------------------------------------------------------------------------
# Scenario → Business Rule (TESTS) — TF-IDF scoring
# ---------------------------------------------------------------------------

class TestScenarioToBusinessRule:

    def test_discriminating_tokens_produce_tests_edge(self, tmp_path):
        """Nodes sharing rare (high-IDF) non-stopword tokens score > 0.75."""
        # sc1 token set is a *subset* of br1's → cosine is high even in a
        # small corpus because the IDF penalty of the extra token in br1 is small.
        store = _seed_store(tmp_path, [
            {"id": "sc1", "type": "test_scenario",
             "name": "youthful driver surcharge",
             "description": ""},
            {"id": "br1", "type": "business_rule",
             "name": "youthful driver surcharge calculation",
             "description": ""},
            # Padding nodes so shared tokens aren't trivially universal
            {"id": "br2", "type": "business_rule",
             "name": "endorsement eligibility window", "description": ""},
            {"id": "sc2", "type": "test_scenario",
             "name": "renewal reinstatement", "description": ""},
        ])
        mapper = HeuristicEdgeMapper()
        edges = mapper.map(store)
        assert ("sc1", "br1", "TESTS") in _edge_set(edges)

    def test_domain_stopword_only_overlap_produces_no_edge(self, tmp_path):
        """Spec 006 AC #1: nodes sharing ONLY domain-stopword tokens get zero edges.

        Words like 'policy', 'coverage', 'claim' are filtered before scoring.
        After filtering, these nodes share zero discriminating tokens → cosine=0.
        """
        store = _seed_store(tmp_path, [
            {"id": "br1", "type": "business_rule",
             "name": "insurance policy coverage review", "description": ""},
            {"id": "br2", "type": "business_rule",
             "name": "insurance policy coverage assessment", "description": ""},
            {"id": "br3", "type": "business_rule",
             "name": "insurance policy coverage approval", "description": ""},
            {"id": "sc1", "type": "test_scenario",
             "name": "insurance policy coverage verification", "description": ""},
            {"id": "sc2", "type": "test_scenario",
             "name": "insurance policy coverage validation", "description": ""},
            {"id": "sc3", "type": "test_scenario",
             "name": "insurance policy coverage rejection", "description": ""},
        ])
        mapper = HeuristicEdgeMapper()
        edges = mapper.map(store)
        tests_edges = [e for e in edges if e["relationship"] == "TESTS"]
        assert len(tests_edges) == 0, (
            f"Expected no TESTS edges from domain-stopword-only overlap, "
            f"but got {len(tests_edges)}: {tests_edges}"
        )

    def test_short_tokens_under_3_chars_excluded_from_match(self, tmp_path):
        """Tokens < 3 chars are excluded by the tokeniser regex."""
        store = _seed_store(tmp_path, [
            {"id": "sc1", "type": "test_scenario",
             "name": "An or is it", "description": ""},
            {"id": "br1", "type": "business_rule",
             "name": "An or is it", "description": ""},
        ])
        mapper = HeuristicEdgeMapper()
        edges = mapper.map(store)
        # All tokens are ≤ 2 chars → no match after tokenise()
        assert ("sc1", "br1", "TESTS") not in _edge_set(edges)


# ---------------------------------------------------------------------------
# Scenario → Endpoint (TESTS) — path-based gate (unchanged)
# ---------------------------------------------------------------------------

class TestScenarioToEndpoint:
    def test_scenario_references_endpoint_path(self, tmp_path):
        """Scenario whose text includes a non-excluded endpoint token → TESTS."""
        store = _seed_store(tmp_path, [
            {"id": "sc1", "type": "test_scenario",
             "name": "User requests insurance quotes from provider", "description": ""},
            {"id": "ep1", "type": "api_endpoint",
             "name": "POST /quotes", "description": "",
             "metadata": {"path": "/quotes", "http_method": "POST"}},
        ])
        mapper = HeuristicEdgeMapper()
        edges = mapper.map(store)
        assert ("sc1", "ep1", "TESTS") in _edge_set(edges)

    def test_excluded_tokens_do_not_trigger_match(self, tmp_path):
        """Tokens in the excluded set (get/post/api/v1/…) are filtered out."""
        store = _seed_store(tmp_path, [
            {"id": "sc1", "type": "test_scenario",
             "name": "API GET test data", "description": ""},
            {"id": "ep1", "type": "api_endpoint",
             "name": "GET /api/v1/data",
             "description": "",
             "metadata": {"path": "/api/v1/data", "http_method": "GET"}},
        ])
        mapper = HeuristicEdgeMapper()
        edges = mapper.map(store)
        assert ("sc1", "ep1", "TESTS") not in _edge_set(edges)

    def test_admin_endpoint_requires_explicit_path_match(self, tmp_path):
        """Admin/reset endpoints need path present in scenario text."""
        store = _seed_store(tmp_path, [
            {"id": "sc1", "type": "test_scenario",
             "name": "Reset test data before run",
             "description": "Calls admin/test-data/reset endpoint"},
            {"id": "ep1", "type": "api_endpoint",
             "name": "DELETE /admin/test-data/reset",
             "description": "",
             "metadata": {"path": "/admin/test-data/reset", "http_method": "DELETE"}},
        ])
        mapper = HeuristicEdgeMapper()
        edges = mapper.map(store)
        assert ("sc1", "ep1", "TESTS") in _edge_set(edges)

    def test_admin_endpoint_no_match_without_path_in_scenario(self, tmp_path):
        """Admin endpoint without path mention in scenario → no TESTS edge."""
        store = _seed_store(tmp_path, [
            {"id": "sc1", "type": "test_scenario",
             "name": "User views their profile", "description": ""},
            {"id": "ep1", "type": "api_endpoint",
             "name": "DELETE /admin/test-data/reset",
             "description": "",
             "metadata": {"path": "/admin/test-data/reset", "http_method": "DELETE"}},
        ])
        mapper = HeuristicEdgeMapper()
        edges = mapper.map(store)
        assert ("sc1", "ep1", "TESTS") not in _edge_set(edges)


# ---------------------------------------------------------------------------
# Endpoint → Business Rule / Feature (IMPLEMENTS) — TF-IDF
# ---------------------------------------------------------------------------

class TestEndpointToBusinessRule:
    def test_high_overlap_produces_implements_edge(self, tmp_path):
        """Endpoint whose tokens are a subset of rule tokens scores high cosine."""
        # ep1 has 3 tokens all present in br1; br1 has one extra token.
        # cosine ≈ 0.777 > default threshold.
        store = _seed_store(tmp_path, [
            {"id": "ep1", "type": "api_endpoint",
             "name": "youthful driver surcharge", "description": ""},
            {"id": "br1", "type": "business_rule",
             "name": "youthful driver surcharge calculation",
             "description": ""},
        ])
        mapper = HeuristicEdgeMapper()
        edges = mapper.map(store)
        assert ("ep1", "br1", "IMPLEMENTS") in _edge_set(edges)

    def test_endpoint_implements_product_feature(self, tmp_path):
        """Endpoint with high overlap to feature → IMPLEMENTS edge."""
        store = _seed_store(tmp_path, [
            {"id": "ep1", "type": "api_endpoint",
             "name": "insurance quote submission", "description": ""},
            {"id": "ft1", "type": "product_feature",
             "name": "insurance quote submission processing",
             "description": ""},
        ])
        mapper = HeuristicEdgeMapper()
        edges = mapper.map(store)
        assert ("ep1", "ft1", "IMPLEMENTS") in _edge_set(edges)


# ---------------------------------------------------------------------------
# Applicable-type gating (Spec 006)
# ---------------------------------------------------------------------------

class TestApplicableTypeGating:
    def test_implements_blocked_between_test_scenario_and_rule(self, tmp_path):
        """IMPLEMENTS must not fire from test_scenario → business_rule."""
        store = _seed_store(tmp_path, [
            {"id": "sc1", "type": "test_scenario",
             "name": "youthful driver surcharge", "description": ""},
            {"id": "br1", "type": "business_rule",
             "name": "youthful driver surcharge calculation", "description": ""},
        ])
        mapper = HeuristicEdgeMapper()
        edges = mapper.map(store)
        assert ("sc1", "br1", "IMPLEMENTS") not in _edge_set(edges)

    def test_tests_edge_not_between_two_rules(self, tmp_path):
        """TESTS must not fire between two business_rule nodes."""
        store = _seed_store(tmp_path, [
            {"id": "br1", "type": "business_rule",
             "name": "youthful driver surcharge",
             "description": ""},
            {"id": "br2", "type": "business_rule",
             "name": "youthful driver surcharge calculation",
             "description": ""},
        ])
        mapper = HeuristicEdgeMapper()
        edges = mapper.map(store)
        assert ("br1", "br2", "TESTS") not in _edge_set(edges)
        assert ("br2", "br1", "TESTS") not in _edge_set(edges)


# ---------------------------------------------------------------------------
# Code Component → Endpoint (IMPLEMENTS via class match — unchanged)
# ---------------------------------------------------------------------------

class TestCodeComponentToEndpoint:
    def test_endpoint_class_matches_comp_name(self, tmp_path):
        store = _seed_store(tmp_path, [
            {"id": "ep1", "type": "api_endpoint",
             "name": "GET /quotes",
             "description": "",
             "metadata": {"class": "QuotesController", "path": "/quotes"}},
            {"id": "cc1", "type": "code_component",
             "name": "quotescontroller", "description": ""},
        ])
        mapper = HeuristicEdgeMapper()
        edges = mapper.map(store)
        assert ("ep1", "cc1", "IMPLEMENTS") in _edge_set(edges)

    def test_empty_class_metadata_no_match(self, tmp_path):
        store = _seed_store(tmp_path, [
            {"id": "ep1", "type": "api_endpoint",
             "name": "GET /quotes",
             "description": "",
             "metadata": {"class": "", "path": "/quotes"}},
            {"id": "cc1", "type": "code_component",
             "name": "quotescontroller", "description": ""},
        ])
        mapper = HeuristicEdgeMapper()
        edges = mapper.map(store)
        assert ("ep1", "cc1", "IMPLEMENTS") not in _edge_set(edges)


# ---------------------------------------------------------------------------
# Edge metadata shape (Spec 006)
# ---------------------------------------------------------------------------

class TestEdgeMetadataShape:
    def test_emitted_edges_have_confidence_field(self, tmp_path):
        """Every heuristic edge must carry metadata.confidence as a float."""
        store = _seed_store(tmp_path, [
            {"id": "sc1", "type": "test_scenario",
             "name": "youthful driver surcharge", "description": ""},
            {"id": "br1", "type": "business_rule",
             "name": "youthful driver surcharge calculation", "description": ""},
        ])
        mapper = HeuristicEdgeMapper()
        edges = mapper.map(store)
        for edge in edges:
            assert "metadata" in edge
            assert "confidence" in edge["metadata"], (
                f"Edge {edge['source_id']}→{edge['target_id']} missing confidence"
            )
            assert isinstance(edge["metadata"]["confidence"], float)
            assert 0.0 <= edge["metadata"]["confidence"] <= 1.0

    def test_emitted_edges_have_evidence_block(self, tmp_path):
        """Every heuristic edge must carry metadata.evidence with method + score."""
        store = _seed_store(tmp_path, [
            {"id": "sc1", "type": "test_scenario",
             "name": "youthful driver surcharge", "description": ""},
            {"id": "br1", "type": "business_rule",
             "name": "youthful driver surcharge calculation", "description": ""},
        ])
        mapper = HeuristicEdgeMapper()
        edges = mapper.map(store)
        for edge in edges:
            ev = edge.get("metadata", {}).get("evidence", {})
            assert "method" in ev, f"Edge missing evidence.method: {edge}"
            assert "score" in ev, f"Edge missing evidence.score: {edge}"

    def test_emitted_edges_have_required_keys(self, tmp_path):
        """Required keys: source_id, target_id, relationship, metadata."""
        store = _seed_store(tmp_path, [
            {"id": "sc1", "type": "test_scenario",
             "name": "youthful driver surcharge", "description": ""},
            {"id": "br1", "type": "business_rule",
             "name": "youthful driver surcharge calculation", "description": ""},
        ])
        mapper = HeuristicEdgeMapper()
        edges = mapper.map(store)
        assert len(edges) > 0
        for edge in edges:
            assert "source_id" in edge
            assert "target_id" in edge
            assert "relationship" in edge
            assert "metadata" in edge


# ---------------------------------------------------------------------------
# Skip-if-deterministic (Spec 006 §2.4)
# ---------------------------------------------------------------------------

class TestSkipDeterministicEdge:
    def test_heuristic_does_not_overwrite_parser_edge(self, tmp_path):
        """If a parser-emitted edge (confidence=1.0) already exists for a triple,
        the heuristic mapper must not add a duplicate lower-confidence edge."""
        store = _seed_store(tmp_path, [
            {"id": "sc1", "type": "test_scenario",
             "name": "youthful driver surcharge", "description": ""},
            {"id": "br1", "type": "business_rule",
             "name": "youthful driver surcharge calculation", "description": ""},
        ])
        # Pre-insert a parser-emitted TESTS edge
        store.upsert_edge(
            "sc1", "br1", "TESTS",
            {"source": "parser:feature", "confidence": 1.0,
             "evidence": {"method": "explicit_ref", "score": 1.0, "notes": "tag"}},
        )
        mapper = HeuristicEdgeMapper()
        edges = mapper.map(store)
        # The mapper should NOT propose this edge again
        assert ("sc1", "br1", "TESTS") not in _edge_set(edges), (
            "Heuristic mapper re-emitted an edge that was already parser-emitted"
        )


# ---------------------------------------------------------------------------
# Empty / No-op cases (unchanged)
# ---------------------------------------------------------------------------

class TestEdgeMapperNoOp:
    def test_empty_store_returns_empty_list(self, tmp_path):
        store = GraphStore(str(tmp_path))
        mapper = HeuristicEdgeMapper()
        assert mapper.map(store) == []

    def test_no_scenarios_returns_no_tests_edges(self, tmp_path):
        store = _seed_store(tmp_path, [
            {"id": "br1", "type": "business_rule",
             "name": "youthful driver surcharge", "description": ""},
        ])
        mapper = HeuristicEdgeMapper()
        edges = mapper.map(store)
        assert not any(e["relationship"] == "TESTS" for e in edges)

    def test_mapper_is_idempotent(self, tmp_path):
        """Running map() twice must return the same edge set."""
        store = _seed_store(tmp_path, [
            {"id": "sc1", "type": "test_scenario",
             "name": "youthful driver surcharge", "description": ""},
            {"id": "br1", "type": "business_rule",
             "name": "youthful driver surcharge calculation", "description": ""},
        ])
        mapper = HeuristicEdgeMapper()
        first_run = _edge_set(mapper.map(store))
        second_run = _edge_set(mapper.map(store))
        assert first_run == second_run

    def test_mapper_does_not_write_to_store(self, tmp_path):
        """map() must be pure — it must NOT call store.upsert_edge()."""
        store = _seed_store(tmp_path, [
            {"id": "sc1", "type": "test_scenario",
             "name": "youthful driver surcharge", "description": ""},
            {"id": "br1", "type": "business_rule",
             "name": "youthful driver surcharge calculation", "description": ""},
        ])
        before = _edge_set(store.get_edges())
        mapper = HeuristicEdgeMapper()
        mapper.map(store)
        after = _edge_set(store.get_edges())
        assert before == after, "HeuristicEdgeMapper.map() must not write to the store"


# ---------------------------------------------------------------------------
# SPEC-3 Wave 1: rule-family subtypes participate in TESTS scoring
# ---------------------------------------------------------------------------

class TestRuleFamilySubtypeBucket:

    @pytest.mark.parametrize(
        "subtype",
        ["validation_rule", "eligibility_rule",
         "ui_business_rule", "security_rule"],
    )
    def test_rule_family_subtypes_participate_in_scoring(self, tmp_path, subtype):
        """SPEC-3 Wave 1 §1.2: rule-family subtypes are now bucketed alongside
        business_rule so test_scenario nodes can target them with TESTS edges
        via the policy's applicable_types list."""
        store = _seed_store(tmp_path, [
            {"id": "sc1", "type": "test_scenario",
             "name": "youthful driver surcharge",
             "description": ""},
            {"id": f"rule_yds_{subtype}", "type": subtype,
             "name": "youthful driver surcharge calculation",
             "description": ""},
            # Padding so the corpus has enough docs for non-trivial IDF.
            {"id": "br_pad", "type": "business_rule",
             "name": "endorsement eligibility window", "description": ""},
            {"id": "sc_pad", "type": "test_scenario",
             "name": "renewal reinstatement", "description": ""},
        ])
        mapper = HeuristicEdgeMapper()
        edges = mapper.map(store)
        assert ("sc1", f"rule_yds_{subtype}", "TESTS") in _edge_set(edges), (
            f"Expected TESTS edge from sc1 → rule_yds_{subtype}, "
            f"got edges: {_edge_set(edges)}"
        )
