"""Tests for ProvenanceStore — Spec 008 (Wave 4).

Covers:
  1. Provenance round-trips (save → reload → data intact)
  2. record_parse / forget mechanics — orphan detection
  3. Shared-source preservation (node from 2 files; one deleted → node survives)
  4. Bootstrap from graph node metadata (legacy upgrade path)
  5. Thread safety (concurrent record_parse calls with RLock)
  6. Orphan candidate count helper
"""

import json
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.provenance import ProvenanceStore


# ---------------------------------------------------------------------------
# AC1: Round-trip persistence
# ---------------------------------------------------------------------------

class TestProvenanceRoundTrip:
    def test_save_and_reload(self, tmp_path):
        prov = ProvenanceStore(str(tmp_path))
        prov.record_parse(
            "docs/policy.md", "abc123",
            node_ids={"node_1", "node_2"},
            edge_keys={"node_1|IMPLEMENTS|node_2"},
        )
        prov.save()

        # Reload fresh instance
        prov2 = ProvenanceStore(str(tmp_path))
        assert "docs/policy.md" in prov2.all_files()
        assert prov2.nodes_from("docs/policy.md") == {"node_1", "node_2"}
        assert prov2.edges_from("docs/policy.md") == {"node_1|IMPLEMENTS|node_2"}

    def test_empty_workspace_no_crash(self, tmp_path):
        prov = ProvenanceStore(str(tmp_path))
        assert prov.all_files() == set()
        prov.save()
        prov2 = ProvenanceStore(str(tmp_path))
        assert prov2.all_files() == set()

    def test_multiple_files_tracked(self, tmp_path):
        prov = ProvenanceStore(str(tmp_path))
        prov.record_parse("a.md", "h1", {"n1"}, set())
        prov.record_parse("b.java", "h2", {"n2", "n3"}, {"n2|TESTS|n3"})
        prov.save()

        prov2 = ProvenanceStore(str(tmp_path))
        assert prov2.all_files() == {"a.md", "b.java"}
        assert prov2.nodes_from("b.java") == {"n2", "n3"}


# ---------------------------------------------------------------------------
# AC2 / AC1 (spec): forget returns correct orphan sets
# ---------------------------------------------------------------------------

class TestForgetOrphans:
    def test_forget_single_source_node(self, tmp_path):
        prov = ProvenanceStore(str(tmp_path))
        prov.record_parse("a.md", "h1", {"rule_x", "rule_y"}, set())
        prov.save()

        orphan_nodes, orphan_edges = prov.forget("a.md")
        assert "rule_x" in orphan_nodes
        assert "rule_y" in orphan_nodes
        assert "a.md" not in prov.all_files()

    def test_forget_nonexistent_file_no_crash(self, tmp_path):
        prov = ProvenanceStore(str(tmp_path))
        nodes, edges = prov.forget("does_not_exist.md")
        assert nodes == set()
        assert edges == set()

    def test_forget_returns_only_orphan_edges(self, tmp_path):
        """Edge exclusively from the deleted file becomes an orphan."""
        prov = ProvenanceStore(str(tmp_path))
        prov.record_parse(
            "a.md", "h1",
            node_ids={"n1"},
            edge_keys={"n1|IMPLEMENTS|n2"},
        )
        prov.record_parse(
            "b.md", "h2",
            node_ids={"n2"},
            edge_keys=set(),
        )
        prov.save()

        orphan_nodes, orphan_edges = prov.forget("a.md")
        assert "n1" in orphan_nodes
        assert "n1|IMPLEMENTS|n2" in orphan_edges
        # n2 still from b.md — not an orphan
        assert "n2" not in orphan_nodes


# ---------------------------------------------------------------------------
# AC2 (spec): Shared-source preservation
# ---------------------------------------------------------------------------

class TestSharedSourcePreservation:
    def test_shared_node_survives_one_file_deletion(self, tmp_path):
        """rule_x from both a.md and b.md — deleting a.md must NOT orphan rule_x."""
        prov = ProvenanceStore(str(tmp_path))
        prov.record_parse("a.md", "h1", {"rule_x"}, set())
        prov.record_parse("b.md", "h2", {"rule_x", "rule_y"}, set())
        prov.save()

        orphan_nodes, _ = prov.forget("a.md")
        assert "rule_x" not in orphan_nodes, "rule_x still sourced from b.md"
        assert "rule_y" not in orphan_nodes, "rule_y still sourced from b.md"

    def test_node_orphaned_only_when_all_sources_deleted(self, tmp_path):
        prov = ProvenanceStore(str(tmp_path))
        prov.record_parse("a.md", "h1", {"n1"}, set())
        prov.record_parse("b.md", "h2", {"n1"}, set())
        prov.save()

        orphans1, _ = prov.forget("a.md")
        assert "n1" not in orphans1

        orphans2, _ = prov.forget("b.md")
        assert "n1" in orphans2


# ---------------------------------------------------------------------------
# AC8 (spec): Bootstrap from graph metadata (legacy upgrade)
# ---------------------------------------------------------------------------

class TestBootstrapFromGraph:
    def test_bootstrap_populates_provenance(self, tmp_path):
        import networkx as nx
        G = nx.DiGraph()
        G.add_node("n1", type="business_rule", name="R1", description="",
                   metadata={"source_file": "policies/a.md"})
        G.add_node("n2", type="business_rule", name="R2", description="",
                   metadata={"source_file": "policies/b.md"})

        prov = ProvenanceStore(str(tmp_path))
        n = prov.bootstrap_from_graph(G)
        assert n == 2
        assert "policies/a.md" in prov.all_files()
        assert "n1" in prov.nodes_from("policies/a.md")

    def test_bootstrap_skipped_if_already_populated(self, tmp_path):
        import networkx as nx
        prov = ProvenanceStore(str(tmp_path))
        prov.record_parse("existing.md", "hash", {"n0"}, set())

        G = nx.DiGraph()
        G.add_node("n1", type="business_rule", name="R1", description="",
                   metadata={"source_file": "new.md"})

        n = prov.bootstrap_from_graph(G)
        assert n == 0
        assert "new.md" not in prov.all_files()  # bootstrap was a no-op

    def test_bootstrap_nodes_without_source_file_skipped(self, tmp_path):
        import networkx as nx
        G = nx.DiGraph()
        G.add_node("n1", type="business_rule", name="R1", description="",
                   metadata={})  # no source_file

        prov = ProvenanceStore(str(tmp_path))
        n = prov.bootstrap_from_graph(G)
        assert n == 0


# ---------------------------------------------------------------------------
# Spec 009 thread safety: concurrent record_parse calls
# ---------------------------------------------------------------------------

class TestThreadSafety:
    def test_concurrent_record_parse(self, tmp_path):
        """100 threads calling record_parse concurrently must not raise or lose data."""
        prov = ProvenanceStore(str(tmp_path))
        errors = []

        def _write(i):
            try:
                prov.record_parse(
                    f"file_{i}.md", f"hash{i}",
                    node_ids={f"node_{i}"},
                    edge_keys={f"node_{i}|TESTS|node_target"},
                )
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_write, args=(i,)) for i in range(100)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread errors: {errors}"
        # All 100 files should be tracked
        assert len(prov.all_files()) == 100


# ---------------------------------------------------------------------------
# Orphan candidate count
# ---------------------------------------------------------------------------

class TestOrphanCandidateCount:
    def test_zero_orphans_when_all_tracked(self, tmp_path):
        prov = ProvenanceStore(str(tmp_path))
        prov.record_parse("a.md", "h1", {"n1", "n2"}, set())
        assert prov.orphan_candidate_count({"n1", "n2"}) == 0

    def test_counts_untracked_nodes(self, tmp_path):
        prov = ProvenanceStore(str(tmp_path))
        prov.record_parse("a.md", "h1", {"n1"}, set())
        # n2 and n3 are in graph but not in provenance
        assert prov.orphan_candidate_count({"n1", "n2", "n3"}) == 2


# ---------------------------------------------------------------------------
# files_for_node and files_for_edge
# ---------------------------------------------------------------------------

class TestQueryHelpers:
    def test_files_for_node(self, tmp_path):
        prov = ProvenanceStore(str(tmp_path))
        prov.record_parse("a.md", "h1", {"shared"}, set())
        prov.record_parse("b.md", "h2", {"shared"}, set())
        prov.record_parse("c.md", "h3", {"other"}, set())
        assert prov.files_for_node("shared") == {"a.md", "b.md"}
        assert prov.files_for_node("other") == {"c.md"}
        assert prov.files_for_node("unknown") == set()

    def test_files_for_edge(self, tmp_path):
        prov = ProvenanceStore(str(tmp_path))
        prov.record_parse("a.md", "h1", {"n1"}, {"n1|TESTS|n2"})
        prov.record_parse("b.md", "h2", {"n2"}, set())
        assert prov.files_for_edge("n1|TESTS|n2") == {"a.md"}
        assert prov.files_for_edge("n1|IMPLEMENTS|n99") == set()
