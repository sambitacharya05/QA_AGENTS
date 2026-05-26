"""
Tests for Spec 001 — Local Storage Hardening.

Covers:
- Atomic write semantics (no half-written files)
- File locking (advisory, cross-process or in-process)
- Backup rotation (.bak) and corrupt-file quarantine
- Schema migration (legacy graph.json 'links' → 'edges')
- write amplification via GraphStore.transaction()
- No silent reset on load failure (raises, not swallows)
"""
import json
import os
import sys
import threading
import time

import pytest

# Ensure the project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.local_store import LocalJsonStore, LocalStoreCorruptError
from db.graph_store import GraphStore
from db.migrations import GRAPH_SCHEMA_VERSION, GRAPH_MIGRATORS


# ── LocalJsonStore unit tests ───────────────────────────────────────────────

class TestAtomicWrite:
    def test_writes_and_reads_payload(self, tmp_path):
        path = str(tmp_path / "data.json")
        store = LocalJsonStore(path, schema_version=1, migrators={0: lambda p: p})
        with store.lock():
            store.atomic_write({"hello": "world"})

        with store.lock():
            payload = store.safe_read()
        assert payload == {"hello": "world"}

    def test_creates_bak_on_second_write(self, tmp_path):
        path = str(tmp_path / "data.json")
        store = LocalJsonStore(path, schema_version=1, migrators={0: lambda p: p})
        with store.lock():
            store.atomic_write({"v": 1})
        with store.lock():
            store.atomic_write({"v": 2})

        assert os.path.exists(store.bak_path), ".bak file should exist after second write"
        with open(store.bak_path, "r", encoding="utf-8") as f:
            bak_data = json.load(f)
        assert bak_data["payload"]["v"] == 1, ".bak should contain the previous version"

    def test_write_includes_schema_envelope(self, tmp_path):
        path = str(tmp_path / "data.json")
        store = LocalJsonStore(path, schema_version=1, migrators={0: lambda p: p})
        with store.lock():
            store.atomic_write({"key": "value"})

        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)

        assert raw["schema_version"] == 1
        assert raw["generator"] == "context_builder"
        assert "written_at" in raw
        assert raw["payload"] == {"key": "value"}

    def test_raw_mode_no_envelope(self, tmp_path):
        path = str(tmp_path / "raw.json")
        store = LocalJsonStore(path, schema_version=0, migrators={}, use_envelope=False)
        with store.lock():
            store.atomic_write({"raw": True})

        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)

        # In raw mode the file should be the payload directly, no schema_version key
        assert raw == {"raw": True}
        assert "schema_version" not in raw

    def test_no_leftover_tmp_file_on_success(self, tmp_path):
        path = str(tmp_path / "data.json")
        store = LocalJsonStore(path, schema_version=1, migrators={0: lambda p: p})
        with store.lock():
            store.atomic_write({"ok": True})

        tmp_files = [f for f in os.listdir(tmp_path) if f.startswith(".write-")]
        assert tmp_files == [], "No .write-*.json temp files should remain after a successful write"


class TestBackupAndRescue:
    def test_safe_read_returns_none_when_no_file(self, tmp_path):
        path = str(tmp_path / "missing.json")
        store = LocalJsonStore(path, schema_version=1, migrators={0: lambda p: p})
        with store.lock():
            result = store.safe_read()
        assert result is None

    def test_rescue_from_bak_on_corrupt_primary(self, tmp_path):
        path = str(tmp_path / "data.json")
        store = LocalJsonStore(path, schema_version=1, migrators={0: lambda p: p})

        # Write good data → creates the primary file
        with store.lock():
            store.atomic_write({"good": True})
        # Write again so .bak contains the good data
        with store.lock():
            store.atomic_write({"also_good": True})

        # Now corrupt the primary file
        with open(path, "w", encoding="utf-8") as f:
            f.write("{")  # invalid JSON

        # safe_read should rescue from .bak and quarantine the corrupt file
        with store.lock():
            payload = store.safe_read()

        assert payload == {"good": True}, "Should have recovered from .bak"
        corrupt_files = [f for f in os.listdir(tmp_path) if ".corrupt." in f]
        assert corrupt_files, "Corrupt file should have been quarantined"

    def test_raises_when_both_files_corrupt(self, tmp_path):
        path = str(tmp_path / "data.json")
        bak_path = f"{path}.bak"
        store = LocalJsonStore(path, schema_version=1, migrators={0: lambda p: p})

        # Write something first so the files exist
        with store.lock():
            store.atomic_write({"v": 1})
        with store.lock():
            store.atomic_write({"v": 2})  # creates .bak

        # Corrupt both
        for p in (path, bak_path):
            with open(p, "w", encoding="utf-8") as f:
                f.write("{")

        with pytest.raises(LocalStoreCorruptError):
            with store.lock():
                store.safe_read()


class TestSchemaMigration:
    def test_legacy_graph_links_migrated_to_edges(self, tmp_path):
        """A graph.json written by NetworkX <3.4 uses 'links' not 'edges'."""
        path = str(tmp_path / "graph.json")
        store = LocalJsonStore(
            path,
            schema_version=GRAPH_SCHEMA_VERSION,
            migrators=GRAPH_MIGRATORS,
            use_envelope=True,
        )

        # Simulate a legacy v0 file (no envelope, uses 'links' key)
        legacy_payload = {
            "directed": True,
            "multigraph": False,
            "graph": {},
            "nodes": [{"id": "n1"}, {"id": "n2"}],
            "links": [{"source": "n1", "target": "n2", "relationship": "TESTS"}],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(legacy_payload, f)

        # safe_read should detect v0, migrate links→edges, return migrated payload
        with store.lock():
            payload = store.safe_read()

        assert "edges" in payload, "Migration should rename 'links' to 'edges'"
        assert "links" not in payload, "Old 'links' key should be gone after migration"

    def test_current_schema_file_not_remigrated(self, tmp_path):
        path = str(tmp_path / "data.json")
        store = LocalJsonStore(path, schema_version=1, migrators={0: lambda p: p})

        with store.lock():
            store.atomic_write({"v": "current"})
        with store.lock():
            payload = store.safe_read()

        assert payload == {"v": "current"}

    def test_missing_migrator_raises(self, tmp_path):
        path = str(tmp_path / "data.json")
        # schema_version=2 but no migrator for v0→v1 or v1→v2
        store = LocalJsonStore(path, schema_version=2, migrators={})

        # Write a legacy v0 file
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"old": True}, f)

        with pytest.raises(LocalStoreCorruptError, match="No migrator"):
            with store.lock():
                store.safe_read()


class TestFileLocking:
    def test_lock_serialises_threads(self, tmp_path):
        """Two threads writing to the same store must not interleave."""
        path = str(tmp_path / "shared.json")
        store = LocalJsonStore(path, schema_version=1, migrators={0: lambda p: p})
        results = []
        errors = []

        def writer(value: int):
            try:
                with store.lock():
                    store.atomic_write({"writer": value})
                    time.sleep(0.02)  # hold lock briefly
                    payload = store.safe_read()
                    results.append(payload["writer"])
            except Exception as exc:
                errors.append(str(exc))

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Unexpected errors: {errors}"
        # Each writer reads back its own value (no interleaving)
        assert set(results) == {0, 1, 2}


# ── GraphStore integration tests ────────────────────────────────────────────

class TestGraphStoreTransaction:
    def test_transaction_coalesces_writes(self, tmp_path):
        """Nodes written inside a transaction must produce exactly ONE graph write."""
        store = GraphStore(str(tmp_path))
        write_count = [0]

        original_save = store.save_graph

        def counting_save():
            write_count[0] += 1
            original_save()

        store.save_graph = counting_save

        with store.transaction():
            for i in range(10):
                store.upsert_node(
                    f"node_{i}", "business_rule", f"Rule {i}", f"Desc {i}", {}
                )

        # transaction() calls save_graph once at exit
        assert write_count[0] == 1, (
            f"Expected 1 save_graph call, got {write_count[0]}. "
            f"upsert_node must not save inline during a transaction."
        )

    def test_transaction_persists_all_nodes(self, tmp_path):
        store = GraphStore(str(tmp_path))
        with store.transaction():
            for i in range(5):
                store.upsert_node(
                    f"rule_{i}", "business_rule", f"Rule {i}", "", {}
                )

        # Reload from disk to verify durability
        store2 = GraphStore(str(tmp_path))
        nodes = store2.query_nodes(node_type="business_rule")
        assert len(nodes) == 5

    def test_graph_loads_schema_envelope_transparently(self, tmp_path):
        """GraphStore must load its own schema-versioned files without error."""
        store = GraphStore(str(tmp_path))
        store.upsert_node("n1", "code_component", "MyClass", "Desc", {})
        store.save_graph()

        # Reload — must see schema envelope but expose the graph cleanly
        store2 = GraphStore(str(tmp_path))
        assert store2.get_node("n1") is not None

    def test_graph_migrates_legacy_file(self, tmp_path):
        """GraphStore must transparently migrate a legacy NetworkX 'links' file."""
        import networkx as nx
        storage_dir = tmp_path / ".context_builder"
        storage_dir.mkdir()
        graph_path = storage_dir / "graph.json"

        # Build a small graph and write it in legacy format (no envelope, links key)
        g = nx.DiGraph()
        g.add_node("a", type="business_rule", name="A", description="", metadata={})
        g.add_node("b", type="api_endpoint", name="B", description="", metadata={})
        g.add_edge("a", "b", relationship="IMPLEMENTS")

        try:
            legacy_data = nx.node_link_data(g, edges="links")
        except TypeError:
            legacy_data = nx.node_link_data(g)
            # Simulate legacy format
            if "edges" in legacy_data:
                legacy_data["links"] = legacy_data.pop("edges")

        with open(graph_path, "w", encoding="utf-8") as f:
            json.dump(legacy_data, f)

        # GraphStore should migrate and load without error
        store = GraphStore(str(tmp_path))
        node_a = store.get_node("a")
        assert node_a is not None, "Node 'a' should load from migrated legacy file"
        assert store.graph.has_edge("a", "b"), "Edge should survive migration"

    def test_bak_rotation_on_save(self, tmp_path):
        store = GraphStore(str(tmp_path))
        store.upsert_node("x", "business_rule", "X", "", {})
        store.save_graph()
        store.upsert_node("y", "business_rule", "Y", "", {})
        store.save_graph()

        bak = os.path.join(str(tmp_path), ".context_builder", "graph.json.bak")
        assert os.path.exists(bak), "graph.json.bak should exist after second save"
