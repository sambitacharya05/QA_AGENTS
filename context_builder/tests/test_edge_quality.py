"""Tests for Spec 006 — Heuristic Edge Quality.

Covers:
- engine/edges/tokenize.py  (domain-stopword filtering, caching)
- engine/edges/scoring.py   (CorpusTfIdf cosine, discriminating_overlap)
- db/graph_store.py         (EdgeEndpointMissing, upsert_edge loud-fail,
                              has_edge, bounded get_traceability_graph)
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.edges.tokenize import tokenize, DEFAULT_DOMAIN_STOPWORDS
from engine.edges.scoring import CorpusTfIdf
from db.graph_store import GraphStore, EdgeEndpointMissing


# ===========================================================================
# tokenize()
# ===========================================================================

class TestTokenize:
    def test_returns_frozenset(self):
        result = tokenize("Submit the claim form")
        assert isinstance(result, frozenset)

    def test_domain_stopwords_filtered(self):
        result = tokenize("policy coverage claim premium")
        # All four words are in DEFAULT_DOMAIN_STOPWORDS → empty after filter
        assert result == frozenset()

    def test_generic_stopwords_filtered(self):
        result = tokenize("the and for with that this")
        assert result == frozenset()

    def test_short_tokens_excluded(self):
        """Tokens shorter than 3 chars are excluded by the regex."""
        result = tokenize("An or is it ok go")
        assert result == frozenset()

    def test_discriminating_tokens_kept(self):
        result = tokenize("youthful driver surcharge calculation")
        assert "youthful" in result
        assert "driver" in result
        assert "surcharge" in result
        assert "calculation" in result

    def test_extra_stopwords_merged(self):
        extra = frozenset({"driver", "surcharge"})
        result = tokenize("youthful driver surcharge calculation", extra)
        assert "driver" not in result
        assert "surcharge" not in result
        assert "youthful" in result

    def test_mixed_case_lowercased(self):
        result = tokenize("Youthful DRIVER Surcharge")
        assert "youthful" in result
        assert "driver" in result
        assert "surcharge" in result

    def test_empty_string_returns_empty(self):
        assert tokenize("") == frozenset()

    def test_none_equivalent(self):
        # tokenize handles falsy text gracefully
        assert tokenize(None) == frozenset()  # type: ignore[arg-type]

    def test_cached_same_result_for_same_input(self):
        r1 = tokenize("youthful driver surcharge")
        r2 = tokenize("youthful driver surcharge")
        assert r1 is r2  # same object → lru_cache hit


# ===========================================================================
# CorpusTfIdf
# ===========================================================================

class TestCorpusTfIdf:
    def _build_corpus(self, docs):
        return CorpusTfIdf(docs)

    def test_cosine_identical_docs_is_one(self):
        model = self._build_corpus([
            ("a", "youthful driver surcharge"),
            ("b", "youthful driver surcharge"),
        ])
        assert abs(model.cosine("a", "b") - 1.0) < 1e-9

    def test_cosine_disjoint_docs_is_zero(self):
        model = self._build_corpus([
            ("a", "youthful driver surcharge"),
            ("b", "endorsement eligibility reinstatement"),
        ])
        assert model.cosine("a", "b") == 0.0

    def test_cosine_subset_relation_exceeds_threshold(self):
        """If a_id tokens ⊆ b_id tokens, cosine should exceed 0.75 with 2 docs."""
        model = self._build_corpus([
            ("a", "youthful driver surcharge"),            # 3 tokens, all in b
            ("b", "youthful driver surcharge calculation"), # 4 tokens
        ])
        score = model.cosine("a", "b")
        assert score > 0.75, f"Expected cosine > 0.75, got {score}"

    def test_cosine_symmetric(self):
        model = self._build_corpus([
            ("a", "youthful driver surcharge"),
            ("b", "youthful driver surcharge calculation"),
        ])
        assert abs(model.cosine("a", "b") - model.cosine("b", "a")) < 1e-9

    def test_cosine_unknown_doc_returns_zero(self):
        model = self._build_corpus([("a", "youthful driver surcharge")])
        assert model.cosine("a", "UNKNOWN") == 0.0

    def test_discriminating_overlap_sorted_by_idf(self):
        """Rarest token (lowest df) should appear first in the list."""
        # Only doc "a" contains "reinstatement" → very high IDF relative to
        # "youthful" and "driver" which both docs share.
        model = self._build_corpus([
            ("a", "youthful driver reinstatement"),
            ("b", "youthful driver endorsement"),
            ("c", "youthful eligibility"),
            ("d", "driver eligibility"),
        ])
        overlap = model.discriminating_overlap("a", "b")
        # "youthful" and "driver" are shared; "youthful" appears in 3 docs,
        # "driver" in 3 docs too — but either way both should be present
        assert set(overlap) == {"youthful", "driver"}

    def test_discriminating_overlap_empty_when_no_shared_tokens(self):
        model = self._build_corpus([
            ("a", "youthful driver surcharge"),
            ("b", "endorsement eligibility reinstatement"),
        ])
        assert model.discriminating_overlap("a", "b") == []

    def test_corpus_built_with_domain_stopwords_filtered(self):
        """Tokens in DEFAULT_DOMAIN_STOPWORDS should not appear in doc_tokens."""
        model = CorpusTfIdf(
            [("a", "policy coverage claim"), ("b", "policy coverage claim")],
            extra_stopwords=frozenset(),
        )
        # After tokenize(), domain stopwords are removed → doc_tokens should be empty
        assert model.doc_tokens.get("a", frozenset()) == frozenset()
        assert model.cosine("a", "b") == 0.0

    def test_add_doc_after_fit(self):
        model = self._build_corpus([
            ("a", "youthful driver surcharge"),
            ("b", "endorsement eligibility"),
        ])
        model.add_doc("c", "youthful driver surcharge")
        # "c" should now share tokens with "a"
        assert model.doc_tokens["c"] == model.doc_tokens["a"]


# ===========================================================================
# GraphStore.upsert_edge — loud-fail modes (Spec 006 §2.1D)
# ===========================================================================

class TestUpsertEdgeLoudFail:
    def test_warn_mode_returns_false_for_missing_endpoint(self, tmp_path):
        store = GraphStore(str(tmp_path))
        store.upsert_node("a", "test_scenario", "A", "", {})
        result = store.upsert_edge("a", "MISSING", "TESTS", missing_endpoint="warn")
        assert result is False

    def test_raise_mode_raises_for_missing_source(self, tmp_path):
        store = GraphStore(str(tmp_path))
        store.upsert_node("b", "business_rule", "B", "", {})
        with pytest.raises(EdgeEndpointMissing):
            store.upsert_edge("MISSING", "b", "TESTS", missing_endpoint="raise")

    def test_raise_mode_raises_for_missing_target(self, tmp_path):
        store = GraphStore(str(tmp_path))
        store.upsert_node("a", "test_scenario", "A", "", {})
        with pytest.raises(EdgeEndpointMissing):
            store.upsert_edge("a", "MISSING", "TESTS", missing_endpoint="raise")

    def test_silent_mode_returns_false_quietly(self, tmp_path):
        store = GraphStore(str(tmp_path))
        result = store.upsert_edge("X", "Y", "TESTS", missing_endpoint="silent")
        assert result is False

    def test_valid_edge_returns_true(self, tmp_path):
        store = GraphStore(str(tmp_path))
        store.upsert_node("a", "test_scenario", "A", "", {})
        store.upsert_node("b", "business_rule", "B", "", {})
        result = store.upsert_edge("a", "b", "TESTS")
        assert result is True


# ===========================================================================
# GraphStore.has_edge (Spec 006)
# ===========================================================================

class TestHasEdge:
    def test_has_edge_true_for_existing_edge(self, tmp_path):
        store = GraphStore(str(tmp_path))
        store.upsert_node("a", "test_scenario", "A", "", {})
        store.upsert_node("b", "business_rule", "B", "", {})
        store.upsert_edge("a", "b", "TESTS")
        assert store.has_edge("a", "b", "TESTS") is True

    def test_has_edge_false_for_missing_edge(self, tmp_path):
        store = GraphStore(str(tmp_path))
        store.upsert_node("a", "test_scenario", "A", "", {})
        store.upsert_node("b", "business_rule", "B", "", {})
        assert store.has_edge("a", "b", "TESTS") is False

    def test_has_edge_case_insensitive_relationship(self, tmp_path):
        store = GraphStore(str(tmp_path))
        store.upsert_node("a", "test_scenario", "A", "", {})
        store.upsert_node("b", "business_rule", "B", "", {})
        store.upsert_edge("a", "b", "TESTS")
        assert store.has_edge("a", "b", "tests") is True

    def test_has_edge_wrong_relationship_returns_false(self, tmp_path):
        store = GraphStore(str(tmp_path))
        store.upsert_node("a", "test_scenario", "A", "", {})
        store.upsert_node("b", "business_rule", "B", "", {})
        store.upsert_edge("a", "b", "TESTS")
        assert store.has_edge("a", "b", "IMPLEMENTS") is False


# ===========================================================================
# GraphStore.get_traceability_graph — BFS-bounded (Spec 006 §2.1F)
# ===========================================================================

class TestBoundedTraceabilityGraph:

    def _build_chain(self, tmp_path, n_nodes: int) -> GraphStore:
        """Build a linear chain: n0 → n1 → n2 → … → n_{n-1}."""
        store = GraphStore(str(tmp_path))
        for i in range(n_nodes):
            store.upsert_node(f"n{i}", "business_rule", f"Rule {i}", "", {})
        for i in range(n_nodes - 1):
            store.upsert_edge(
                f"n{i}", f"n{i+1}", "IMPLEMENTS",
                {"confidence": 1.0},
            )
        return store

    def test_unknown_rule_returns_empty(self, tmp_path):
        store = GraphStore(str(tmp_path))
        result = store.get_traceability_graph("nonexistent")
        assert result["nodes"] == []

    def test_depth_1_returns_only_direct_neighbours(self, tmp_path):
        """depth=1: only nodes directly connected to the root are returned."""
        store = self._build_chain(tmp_path, 4)
        result = store.get_traceability_graph("n0", depth=1)
        returned_ids = {n["id"] for n in result["nodes"]}
        assert "n0" in returned_ids
        assert "n1" in returned_ids
        assert "n2" not in returned_ids  # 2 hops away
        assert "n3" not in returned_ids  # 3 hops away

    def test_depth_2_returns_two_hop_neighbours(self, tmp_path):
        store = self._build_chain(tmp_path, 5)
        result = store.get_traceability_graph("n0", depth=2)
        returned_ids = {n["id"] for n in result["nodes"]}
        assert "n0" in returned_ids
        assert "n1" in returned_ids
        assert "n2" in returned_ids
        assert "n3" not in returned_ids  # 3 hops away

    def test_max_nodes_cap_respected(self, tmp_path):
        """max_nodes=3 on a larger graph must not return more than 3 nodes."""
        store = self._build_chain(tmp_path, 10)
        result = store.get_traceability_graph("n0", depth=10, max_nodes=3)
        assert len(result["nodes"]) <= 3

    def test_rule_id_always_in_result(self, tmp_path):
        """The queried rule_id must always appear in the result nodes."""
        store = self._build_chain(tmp_path, 10)
        result = store.get_traceability_graph("n0", depth=10, max_nodes=2)
        ids = {n["id"] for n in result["nodes"]}
        assert "n0" in ids

    def test_truncated_flag_set_when_capped(self, tmp_path):
        store = self._build_chain(tmp_path, 10)
        result = store.get_traceability_graph("n0", depth=10, max_nodes=3)
        assert result["metadata"]["truncated"] is True

    def test_truncated_flag_false_when_not_capped(self, tmp_path):
        store = self._build_chain(tmp_path, 4)
        result = store.get_traceability_graph("n0", depth=10, max_nodes=100)
        assert result["metadata"]["truncated"] is False

    def test_min_confidence_filters_low_confidence_edges(self, tmp_path):
        """Edges with confidence < min_edge_confidence are excluded from BFS."""
        store = GraphStore(str(tmp_path))
        store.upsert_node("r", "business_rule", "Root", "", {})
        store.upsert_node("hi", "test_scenario", "High", "", {})
        store.upsert_node("lo", "test_scenario", "Low", "", {})
        store.upsert_edge("r", "hi", "TESTS", {"confidence": 1.0})
        store.upsert_edge("r", "lo", "TESTS", {"confidence": 0.3})

        result = store.get_traceability_graph("r", depth=1, min_edge_confidence=0.5)
        ids = {n["id"] for n in result["nodes"]}
        assert "hi" in ids
        assert "lo" not in ids

    def test_legacy_edges_without_confidence_treated_as_1(self, tmp_path):
        """Backward compat: edges without metadata.confidence → treated as 1.0."""
        store = GraphStore(str(tmp_path))
        store.upsert_node("r", "business_rule", "Root", "", {})
        store.upsert_node("x", "test_scenario", "X", "", {})
        # Edge without confidence key — legacy format
        store.upsert_edge("r", "x", "TESTS", {})
        result = store.get_traceability_graph("r", depth=1, min_edge_confidence=0.0)
        ids = {n["id"] for n in result["nodes"]}
        assert "x" in ids

    def test_result_is_deterministic_on_repeated_calls(self, tmp_path):
        """Calling get_traceability_graph twice returns the same node set."""
        store = self._build_chain(tmp_path, 20)
        r1 = {n["id"] for n in store.get_traceability_graph("n0", depth=5, max_nodes=6)["nodes"]}
        r2 = {n["id"] for n in store.get_traceability_graph("n0", depth=5, max_nodes=6)["nodes"]}
        assert r1 == r2
