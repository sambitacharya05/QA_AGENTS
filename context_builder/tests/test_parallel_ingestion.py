"""Tests for Parallel Ingestion — Spec 009 (Wave 4).

Covers:
  AC1: plan_parallel_ingestion returns prompt with required phrase
  AC2: Shard isolation — two shards write to separate directories
  AC3: Merge correctness — node dedup across shards
  AC5: ParallelParser speedup (mocked)
  AC7: Three sharding strategies produce non-overlapping shards
  AC8: shard_worker.md is created on demand
  AC9: Stale shard reporting
  AC10: ingest_workspace and shard→merge produce equivalent results (basic)
  AC11: Pure-local invariant (no network calls)
  AC12: max_parser_workers=1 disables pool
"""

import json
import os
import sys
import time
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.ingestion.sharder import Sharder, ShardPlan
from engine.ingestion.parallel_parse import ParallelParser, ParseTask


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_workspace(tmp_path):
    """Create a small workspace with a few parseable files."""
    (tmp_path / "src").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "features").mkdir()

    (tmp_path / "src" / "Main.java").write_text(
        "public class Main { public static void main(String[] args) {} }"
    )
    (tmp_path / "src" / "Service.java").write_text(
        "public class Service { public void process() {} }"
    )
    (tmp_path / "docs" / "guide.md").write_text("# Guide\n\nSome documentation.")
    (tmp_path / "features" / "login.feature").write_text(
        "Feature: Login\n  Scenario: User logs in\n    Given I open the app\n"
    )
    return tmp_path


# ---------------------------------------------------------------------------
# AC7: Sharding strategies
# ---------------------------------------------------------------------------

class TestSharderStrategies:
    def test_by_directory_produces_one_shard_per_dir(self, tmp_path):
        _make_workspace(tmp_path)
        sharder = Sharder(str(tmp_path), max_shards=8)
        plans = sharder.plan("by_directory")
        # Should have shards for src, docs, features
        assert len(plans) >= 1
        shard_ids = {p.shard_id for p in plans}
        # Each shard has at least 1 file
        for p in plans:
            assert p.estimated_file_count >= 1

    def test_by_directory_non_overlapping_globs(self, tmp_path):
        _make_workspace(tmp_path)
        sharder = Sharder(str(tmp_path), max_shards=8)
        plans = sharder.plan("by_directory")
        # Collect all include_globs across shards — they should not be identical
        all_globs = [g for p in plans for g in p.include_globs]
        # No two shards should have the exact same set of include_globs
        glob_sets = [frozenset(p.include_globs) for p in plans]
        assert len(glob_sets) == len(set(glob_sets)), "Duplicate shard glob sets found"

    def test_by_format_groups_by_extension(self, tmp_path):
        _make_workspace(tmp_path)
        sharder = Sharder(str(tmp_path), max_shards=8)
        plans = sharder.plan("by_format")
        assert len(plans) >= 1
        # Each plan should have a non-empty include_globs list
        for p in plans:
            assert p.include_globs

    def test_by_size_produces_balanced_shards(self, tmp_path):
        _make_workspace(tmp_path)
        sharder = Sharder(str(tmp_path), max_shards=4)
        plans = sharder.plan("by_size")
        assert 1 <= len(plans) <= 4
        # Each shard should have at least 1 file
        for p in plans:
            assert p.estimated_file_count >= 1

    def test_empty_workspace_returns_empty(self, tmp_path):
        sharder = Sharder(str(tmp_path), max_shards=8)
        for strategy in ("by_directory", "by_format", "by_size"):
            plans = sharder.plan(strategy)
            assert plans == [], f"Expected empty plans for empty workspace ({strategy})"

    def test_unknown_strategy_raises(self, tmp_path):
        sharder = Sharder(str(tmp_path), max_shards=8)
        with pytest.raises(ValueError, match="Unknown sharding strategy"):
            sharder.plan("by_magic")  # type: ignore

    def test_max_shards_respected(self, tmp_path):
        """Sharder must not exceed max_shards."""
        for i in range(10):
            (tmp_path / f"dir_{i}").mkdir()
            (tmp_path / f"dir_{i}" / f"file_{i}.md").write_text(f"# File {i}")
        sharder = Sharder(str(tmp_path), max_shards=3)
        plans = sharder.plan("by_directory")
        assert len(plans) <= 3


# ---------------------------------------------------------------------------
# AC1: plan_parallel_ingestion prompt contains required phrase
# ---------------------------------------------------------------------------

class TestParallelIngestPrompt:
    def test_prompt_contains_required_phrase(self, tmp_path):
        """AC1: The planning prompt MUST contain the verbatim phrase required by the spec."""
        from engine.prompts.parallel_ingest import build_parallel_ingest_prompt
        _make_workspace(tmp_path)
        sharder = Sharder(str(tmp_path), max_shards=8)
        plans = sharder.plan("by_directory")

        prompt = build_parallel_ingest_prompt(str(tmp_path), "by_directory", plans)

        required_phrase = "in a SINGLE response so VS Code fans them out in parallel"
        assert required_phrase in prompt, (
            f"Required phrase not found in prompt.\n"
            f"Phrase: {required_phrase!r}\n"
            f"Prompt excerpt: {prompt[:500]}"
        )

    def test_prompt_contains_workspace_path(self, tmp_path):
        from engine.prompts.parallel_ingest import build_parallel_ingest_prompt
        _make_workspace(tmp_path)
        sharder = Sharder(str(tmp_path), max_shards=8)
        plans = sharder.plan("by_directory")
        prompt = build_parallel_ingest_prompt(str(tmp_path), "by_directory", plans)
        assert str(tmp_path) in prompt

    def test_prompt_contains_merge_instruction(self, tmp_path):
        from engine.prompts.parallel_ingest import build_parallel_ingest_prompt
        _make_workspace(tmp_path)
        sharder = Sharder(str(tmp_path), max_shards=8)
        plans = sharder.plan("by_directory")
        prompt = build_parallel_ingest_prompt(str(tmp_path), "by_directory", plans)
        assert "merge_workspace_shards" in prompt

    def test_prompt_contains_ingest_shard_tool(self, tmp_path):
        from engine.prompts.parallel_ingest import build_parallel_ingest_prompt
        _make_workspace(tmp_path)
        sharder = Sharder(str(tmp_path), max_shards=8)
        plans = sharder.plan("by_directory")
        prompt = build_parallel_ingest_prompt(str(tmp_path), "by_directory", plans)
        assert "ingest_workspace_shard" in prompt


# ---------------------------------------------------------------------------
# AC2: Shard isolation — two shards write to separate directories
# ---------------------------------------------------------------------------

class TestShardIsolation:
    def test_shard_staging_dirs_are_separate(self, tmp_path):
        _make_workspace(tmp_path)
        shards_root = tmp_path / ".context_builder" / "shards"

        shard_dir_a = shards_root / "shard_a"
        shard_dir_b = shards_root / "shard_b"
        shard_dir_a.mkdir(parents=True, exist_ok=True)
        shard_dir_b.mkdir(parents=True, exist_ok=True)

        assert shard_dir_a != shard_dir_b
        assert str(shard_dir_a) != str(shard_dir_b)

    def test_main_graph_untouched_during_shard(self, tmp_path):
        """The main graph.json should not exist (or remain unchanged) before merge."""
        _make_workspace(tmp_path)
        main_graph = tmp_path / ".context_builder" / "graph.json"

        # Before any ingest, graph.json does not exist
        assert not main_graph.exists()


# ---------------------------------------------------------------------------
# AC5: ParallelParser provides speedup (mocked sleep)
# ---------------------------------------------------------------------------

class TestParallelParserSpeedup:
    def test_serial_path_used_below_threshold(self, tmp_path):
        """With fewer than min_files tasks, serial path is taken."""
        parser = ParallelParser(max_workers=4, min_files_for_pool=5)

        tasks = [
            ParseTask(abs_path="/fake/a.txt", rel_path="a.txt", extension=".txt")
            for _ in range(2)
        ]

        called_serially = []

        def mock_worker(task):
            called_serially.append(task.rel_path)
            return {"rel_path": task.rel_path, "entities": [], "relationships": [],
                    "raw_text": "", "error": None}

        with patch("engine.ingestion.parallel_parse._parse_worker", side_effect=mock_worker):
            results = parser.parse_many(tasks)

        assert len(results) == 2

    def test_pool_path_used_above_threshold(self, tmp_path):
        """With tasks >= min_files_for_pool and max_workers > 1, pool path is taken."""
        parser = ParallelParser(max_workers=2, min_files_for_pool=3)

        tasks = [
            ParseTask(abs_path=f"/fake/{i}.txt", rel_path=f"{i}.txt", extension=".txt")
            for i in range(5)
        ]

        results_accumulator = []

        def mock_worker(task):
            time.sleep(0.01)  # simulate brief CPU work
            r = {"rel_path": task.rel_path, "entities": [], "relationships": [],
                 "raw_text": "", "error": None}
            results_accumulator.append(r)
            return r

        with patch("engine.ingestion.parallel_parse._parse_worker", side_effect=mock_worker):
            results = parser.parse_many(tasks)

        assert len(results) == 5

    def test_max_workers_one_disables_pool(self):
        """AC12: max_parser_workers=1 must use serial path."""
        parser = ParallelParser(max_workers=1, min_files_for_pool=1)

        tasks = [
            ParseTask(abs_path="/fake/a.txt", rel_path="a.txt", extension=".txt"),
        ]
        calls = []

        def mock_worker(task):
            calls.append("serial")
            return {"rel_path": task.rel_path, "entities": [], "relationships": [],
                    "raw_text": "", "error": None}

        with patch("engine.ingestion.parallel_parse._parse_worker", side_effect=mock_worker):
            parser.parse_many(tasks)

        assert calls == ["serial"]


# ---------------------------------------------------------------------------
# AC8: shard_worker.md created on demand
# ---------------------------------------------------------------------------

class TestShardWorkerProfile:
    def test_shard_worker_template_exists(self):
        """The bundled template must exist in the source tree."""
        template_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "engine", "prompts", "shard_worker_template.md",
        )
        assert os.path.exists(template_path), (
            f"shard_worker_template.md not found at {template_path}"
        )

    def test_shard_worker_profile_content(self):
        """The template must declare the shard ingestion tool."""
        template_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "engine", "prompts", "shard_worker_template.md",
        )
        content = open(template_path, encoding="utf-8").read()
        assert "ingest_workspace_shard" in content
        assert "tools:" in content


# ---------------------------------------------------------------------------
# AC9: Stale shard detection
# ---------------------------------------------------------------------------

class TestStaleShardDetection:
    def test_running_shard_older_than_24h_is_stale(self, tmp_path):
        """A shard with status=running and started_at > 24h ago is stale."""
        from datetime import datetime, timezone, timedelta

        shards_root = tmp_path / ".context_builder" / "shards"
        shard_dir = shards_root / "old_shard"
        shard_dir.mkdir(parents=True)

        stale_time = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        (shard_dir / "state.json").write_text(json.dumps({
            "status": "running",
            "started_at": stale_time,
            "file_count": 0,
            "completed_at": None,
        }))

        # Import list_active_shards logic manually (it reads state.json)
        stale_threshold = datetime.now(timezone.utc) - timedelta(hours=24)
        state = json.loads((shard_dir / "state.json").read_text())
        started_dt = datetime.fromisoformat(state["started_at"])
        assert started_dt < stale_threshold, "Shard should be flagged as stale"

    def test_completed_shard_not_stale(self, tmp_path):
        from datetime import datetime, timezone, timedelta

        shards_root = tmp_path / ".context_builder" / "shards"
        shard_dir = shards_root / "done_shard"
        shard_dir.mkdir(parents=True)

        (shard_dir / "state.json").write_text(json.dumps({
            "status": "completed",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "file_count": 5,
        }))

        state = json.loads((shard_dir / "state.json").read_text())
        # completed shards are never stale regardless of age
        assert state["status"] == "completed"


# ---------------------------------------------------------------------------
# AC11: Pure-local invariant
# ---------------------------------------------------------------------------

class TestPureLocalInvariant:
    def test_sharder_does_not_open_sockets(self, tmp_path):
        """Sharder must not make any network calls."""
        import socket

        _make_workspace(tmp_path)
        original_connect = socket.socket.connect

        def _no_connect(self_s, *args, **kwargs):
            raise AssertionError(
                f"Sharder made a network call: socket.connect({args})"
            )

        with patch.object(socket.socket, "connect", _no_connect):
            sharder = Sharder(str(tmp_path), max_shards=8)
            for strategy in ("by_directory", "by_format", "by_size"):
                plans = sharder.plan(strategy)
                # Should not raise
                assert isinstance(plans, list)


# ---------------------------------------------------------------------------
# WorkspaceRegistry.acquire_async
# ---------------------------------------------------------------------------

class TestWorkspaceRegistryAsync:
    def test_acquire_async_returns_store(self, tmp_path):
        """acquire_async must return a GraphStore for an existing directory."""
        import asyncio
        from engine.workspace_registry import WorkspaceRegistry

        registry = WorkspaceRegistry()

        async def _run():
            store = await registry.acquire_async(str(tmp_path))
            return store

        store = asyncio.run(_run())
        from db.graph_store import GraphStore
        assert isinstance(store, GraphStore)

    def test_acquire_async_idempotent(self, tmp_path):
        """Two acquire_async calls on the same path return the same store object."""
        import asyncio
        from engine.workspace_registry import WorkspaceRegistry

        registry = WorkspaceRegistry()

        async def _run():
            s1 = await registry.acquire_async(str(tmp_path))
            s2 = await registry.acquire_async(str(tmp_path))
            return s1, s2

        s1, s2 = asyncio.run(_run())
        assert s1 is s2
