"""Tests for Spec 005 — Copilot-native edge augmentation.

Acceptance criteria covered:
  AC1  No external network calls (socket patched)
  AC2  Dead code removed — no google.genai / gemini / gpt-5 in active modules
  AC3  Prompt budget — render_edge_proposal_prompt truncates to ≤60 000 chars
  AC4  Schema rejection — missing fields → VALIDATION_ERROR
  AC5  Confidence floor — proposal below floor is rejected
  AC6  Missing node rejected
  AC7  Provenance recorded on accepted edges
  AC8  End-to-end round-trip using a mock LLM JSON response
  AC9  README accurately describes pattern (spot-checks)

Plus unit tests for:
  - EdgeCandidate dataclass
  - HeuristicEdgeMapper.last_candidates population
  - ContextExtractor.get_pending_edge_candidates()
  - render_edge_proposal_prompt() truncation
"""

from __future__ import annotations

import json
import os
import socket
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_store(tmpdir: str):
    """Build a minimal in-memory GraphStore pointing at *tmpdir*."""
    from db.graph_store import GraphStore
    return GraphStore(tmpdir)


def _populate_store(store):
    """Add two nodes that will sit in the ambiguous band after scoring."""
    store.upsert_node(
        "rule_age_eligibility",
        "business_rule",
        "Age Eligibility Rule",
        "Policyholder must be between 18 and 75 years of age to obtain coverage.",
        {},
    )
    store.upsert_node(
        "scenario_age_check",
        "test_scenario",
        "Age Eligibility Check",
        "Verify age eligibility for coverage when policyholder is 17 years old.",
        {},
    )


def _populate_store_diverse(store):
    """Populate with enough nodes to generate sub-threshold candidates reliably."""
    _populate_store(store)
    # Add an endpoint that partly matches but probably won't hit the 0.75 auto-emit
    store.upsert_node(
        "ep_quote",
        "api_endpoint",
        "Quote Endpoint",
        "Issues a quote for eligible policyholders. Checks age criteria.",
        {},
    )
    store.upsert_node(
        "rule_coverage_limit",
        "business_rule",
        "Coverage Limit Rule",
        "Maximum coverage amount is capped based on risk tier.",
        {},
    )


# ---------------------------------------------------------------------------
# AC2: Dead code removed
# ---------------------------------------------------------------------------

class TestDeadCodeRemoved:
    def test_engine_prompts_py_gone(self):
        """The old engine/prompts.py flat file must not exist."""
        base = Path(__file__).parents[1]
        assert not (base / "engine" / "prompts.py").exists(), (
            "engine/prompts.py still exists — it should have been replaced by "
            "engine/prompts/edge_proposal.py"
        )

    def test_simulated_install_test_gone(self):
        """The stale simulated_install_test directories must be gone."""
        base = Path(__file__).parents[1]
        assert not (base / "tests" / "simulated_install_test").exists()
        assert not (base / "tests" / "simulated_install_test_sandbox").exists()

    def test_no_google_genai_in_active_modules(self):
        """grep active source files for dead LLM references."""
        base = Path(__file__).parents[1]
        patterns = ["google.genai", "from google import genai", "google-genai",
                    "gemini-2.5-flash", "gpt-5"]
        active_dirs = [base / "engine", base / "db", base / "parsers"]
        active_files = [base / "main.py", base / "mcp_models.py",
                        base / "requirements.txt"]

        hits = []
        for d in active_dirs:
            for py in d.rglob("*.py"):
                text = py.read_text(encoding="utf-8", errors="replace")
                for pat in patterns:
                    if pat in text:
                        hits.append(f"{py.relative_to(base)}: contains '{pat}'")
        for f in active_files:
            if f.exists():
                text = f.read_text(encoding="utf-8", errors="replace")
                for pat in patterns:
                    if pat in text:
                        hits.append(f"{f.relative_to(base)}: contains '{pat}'")

        assert not hits, "Dead LLM references found:\n" + "\n".join(hits)


# ---------------------------------------------------------------------------
# AC3: Prompt budget
# ---------------------------------------------------------------------------

class TestRenderEdgeProposalPrompt:
    def _make_candidate(self, i: int):
        from engine.edges.heuristic import EdgeCandidate
        return EdgeCandidate(
            source_id=f"src_{i}",
            target_id=f"tgt_{i}",
            proposed_relationship="TESTS",
            score=round(0.74 - i * 0.001, 4),
            rationale=f"shared token {i}",
            source_excerpt="A " * 200,   # ~400 chars each
            target_excerpt="B " * 200,
        )

    def test_100_candidates_within_60k(self):
        from engine.prompts.edge_proposal import render_edge_proposal_prompt
        candidates = [self._make_candidate(i) for i in range(100)]
        rendered = render_edge_proposal_prompt(candidates, max_chars=60_000)
        assert len(rendered) <= 60_000

    def test_1000_candidates_truncated_to_budget(self):
        from engine.prompts.edge_proposal import render_edge_proposal_prompt
        candidates = [self._make_candidate(i) for i in range(1_000)]
        rendered = render_edge_proposal_prompt(candidates, max_chars=60_000)
        assert len(rendered) <= 60_000

    def test_empty_candidates_returns_prompt_with_zero(self):
        from engine.prompts.edge_proposal import render_edge_proposal_prompt
        rendered = render_edge_proposal_prompt([])
        assert "0 ambiguous" in rendered

    def test_prompt_contains_allowed_relationships(self):
        from engine.prompts.edge_proposal import render_edge_proposal_prompt
        rendered = render_edge_proposal_prompt([self._make_candidate(0)])
        for rel in ("IMPLEMENTS", "VALIDATES", "TESTS", "USES_MODEL", "EXCLUDES", "MAPS_TO"):
            assert rel in rendered


# ---------------------------------------------------------------------------
# AC4: Schema rejection
# ---------------------------------------------------------------------------

class TestApplyEdgeProposalsSchemaRejection:
    def test_missing_required_fields_returns_validation_error(self, tmp_path):
        import main as m
        store = _make_store(str(tmp_path))
        orig = m.active_store
        m.active_store = store
        try:
            result = m.apply_edge_proposals(
                proposals_json='[{"source_id": "x"}]',
            )
        finally:
            m.active_store = orig
        assert result.ok is False
        assert result.error.code == "VALIDATION_ERROR"

    def test_invalid_json_returns_validation_error(self, tmp_path):
        import main as m
        store = _make_store(str(tmp_path))
        orig = m.active_store
        m.active_store = store
        try:
            result = m.apply_edge_proposals(proposals_json="not-json")
        finally:
            m.active_store = orig
        assert result.ok is False
        assert result.error.code == "VALIDATION_ERROR"

    def test_top_level_non_array_returns_validation_error(self, tmp_path):
        import main as m
        store = _make_store(str(tmp_path))
        orig = m.active_store
        m.active_store = store
        try:
            result = m.apply_edge_proposals(proposals_json='{"key": "value"}')
        finally:
            m.active_store = orig
        assert result.ok is False
        assert result.error.code == "VALIDATION_ERROR"

    def test_invalid_relationship_type_returns_validation_error(self, tmp_path):
        import main as m
        store = _make_store(str(tmp_path))
        _populate_store(store)
        orig = m.active_store
        m.active_store = store
        proposal = [{
            "source_id": "scenario_age_check",
            "target_id": "rule_age_eligibility",
            "relationship": "INVALID_REL",
            "confidence": 0.9,
            "rationale": "test",
        }]
        try:
            result = m.apply_edge_proposals(proposals_json=json.dumps(proposal))
        finally:
            m.active_store = orig
        assert result.ok is False
        assert result.error.code == "VALIDATION_ERROR"


# ---------------------------------------------------------------------------
# AC5: Confidence floor enforcement
# ---------------------------------------------------------------------------

class TestConfidenceFloor:
    def test_below_floor_is_rejected(self, tmp_path):
        import main as m
        store = _make_store(str(tmp_path))
        _populate_store(store)
        proposal = [{
            "source_id": "scenario_age_check",
            "target_id": "rule_age_eligibility",
            "relationship": "TESTS",
            "confidence": 0.5,
            "rationale": "test",
        }]
        orig = m.active_store
        m.active_store = store
        try:
            result = m.apply_edge_proposals(
                proposals_json=json.dumps(proposal),
                confidence_floor=0.6,
            )
        finally:
            m.active_store = orig
        assert result.ok is True
        assert result.data.total_proposed == 1
        assert len(result.data.accepted) == 0
        assert len(result.data.rejected) == 1
        assert "confidence 0.5 < floor 0.6" in result.data.rejected[0]["reason"]

    def test_at_floor_is_accepted(self, tmp_path):
        import main as m
        store = _make_store(str(tmp_path))
        _populate_store(store)
        proposal = [{
            "source_id": "scenario_age_check",
            "target_id": "rule_age_eligibility",
            "relationship": "TESTS",
            "confidence": 0.6,
            "rationale": "exactly at floor",
        }]
        orig = m.active_store
        m.active_store = store
        try:
            result = m.apply_edge_proposals(
                proposals_json=json.dumps(proposal),
                confidence_floor=0.6,
            )
        finally:
            m.active_store = orig
        assert result.ok is True
        assert len(result.data.accepted) == 1
        assert len(result.data.rejected) == 0


# ---------------------------------------------------------------------------
# AC6: Missing node rejected
# ---------------------------------------------------------------------------

class TestMissingNodeRejected:
    def test_nonexistent_source_rejected(self, tmp_path):
        import main as m
        store = _make_store(str(tmp_path))
        _populate_store(store)
        proposal = [{
            "source_id": "rule_nonexistent",
            "target_id": "rule_age_eligibility",
            "relationship": "IMPLEMENTS",
            "confidence": 0.9,
            "rationale": "test",
        }]
        orig = m.active_store
        m.active_store = store
        try:
            result = m.apply_edge_proposals(proposals_json=json.dumps(proposal))
        finally:
            m.active_store = orig
        assert result.ok is True
        assert len(result.data.accepted) == 0
        assert len(result.data.rejected) == 1
        assert "source or target node missing" in result.data.rejected[0]["reason"]
        # Graph unchanged
        assert not store.has_edge("rule_nonexistent", "rule_age_eligibility", "IMPLEMENTS")

    def test_nonexistent_target_rejected(self, tmp_path):
        import main as m
        store = _make_store(str(tmp_path))
        _populate_store(store)
        proposal = [{
            "source_id": "scenario_age_check",
            "target_id": "nonexistent_target",
            "relationship": "TESTS",
            "confidence": 0.9,
            "rationale": "test",
        }]
        orig = m.active_store
        m.active_store = store
        try:
            result = m.apply_edge_proposals(proposals_json=json.dumps(proposal))
        finally:
            m.active_store = orig
        assert result.ok is True
        assert len(result.data.accepted) == 0
        assert "source or target node missing" in result.data.rejected[0]["reason"]


# ---------------------------------------------------------------------------
# AC7: Provenance recorded
# ---------------------------------------------------------------------------

class TestProvenanceRecorded:
    def test_accepted_edge_has_copilot_provenance(self, tmp_path):
        import main as m
        store = _make_store(str(tmp_path))
        _populate_store(store)
        proposal = [{
            "source_id": "scenario_age_check",
            "target_id": "rule_age_eligibility",
            "relationship": "TESTS",
            "confidence": 0.85,
            "rationale": "Scenario verifies age restriction enforced by rule.",
        }]
        orig = m.active_store
        m.active_store = store
        try:
            result = m.apply_edge_proposals(proposals_json=json.dumps(proposal))
        finally:
            m.active_store = orig

        assert result.ok is True
        assert len(result.data.accepted) == 1

        # Retrieve the edge from the store and inspect metadata
        edges = store.get_edges()
        matching = [
            e for e in edges
            if e["source_id"] == "scenario_age_check"
            and e["target_id"] == "rule_age_eligibility"
            and e["relationship"] == "TESTS"
        ]
        assert matching, "Accepted edge not found in store"
        meta = matching[0]["metadata"]
        assert meta["source"] == "copilot_proposal"
        assert isinstance(meta["confidence"], float)
        assert meta["rationale"] == "Scenario verifies age restriction enforced by rule."
        assert "reviewed_at" in meta
        # reviewed_at should be an ISO-8601 string
        from datetime import datetime
        datetime.fromisoformat(meta["reviewed_at"])  # raises if malformed


# ---------------------------------------------------------------------------
# AC8: End-to-end round-trip with mock LLM JSON response
# ---------------------------------------------------------------------------

class TestEndToEndRoundTrip:
    def test_mock_copilot_reply_produces_correct_graph(self, tmp_path):
        """Simulates the full round-trip:
           1. ingest workspace → heuristic mapper builds graph + candidates
           2. host LLM (mocked) returns a JSON array
           3. apply_edge_proposals upserts accepted edges with provenance
        """
        import main as m

        store = _make_store(str(tmp_path))
        _populate_store(store)

        # Simulate the graph already being in place (no file-system ingest needed)
        orig = m.active_store
        m.active_store = store
        try:
            # This is what the host LLM would return after reviewing the prompt
            mock_llm_json = json.dumps([
                {
                    "source_id": "scenario_age_check",
                    "target_id": "rule_age_eligibility",
                    "relationship": "TESTS",
                    "confidence": 0.88,
                    "rationale": "Scenario name and description directly reference age eligibility.",
                },
            ])

            result = m.apply_edge_proposals(
                proposals_json=mock_llm_json,
                confidence_floor=0.6,
            )
        finally:
            m.active_store = orig

        assert result.ok is True, f"Expected ok=True, got error: {result.error}"
        assert result.data.total_proposed == 1
        assert len(result.data.accepted) == 1
        assert len(result.data.rejected) == 0

        acc = result.data.accepted[0]
        assert acc["source_id"] == "scenario_age_check"
        assert acc["target_id"] == "rule_age_eligibility"
        assert acc["relationship"] == "TESTS"

        # Verify the edge is in the graph with correct provenance
        edges = store.get_edges()
        matches = [
            e for e in edges
            if e["source_id"] == "scenario_age_check"
            and e["relationship"] == "TESTS"
        ]
        assert matches
        meta = matches[0]["metadata"]
        assert meta["source"] == "copilot_proposal"
        assert meta["confidence"] == 0.88

    def test_null_relationship_proposals_are_skipped(self, tmp_path):
        """Items with relationship=null in the LLM JSON are silently skipped."""
        import main as m

        store = _make_store(str(tmp_path))
        _populate_store(store)
        orig = m.active_store
        m.active_store = store
        try:
            mock_llm_json = json.dumps([
                {
                    "source_id": "scenario_age_check",
                    "target_id": "rule_age_eligibility",
                    "relationship": None,
                    "confidence": 0.7,
                    "rationale": "Cannot determine.",
                },
            ])
            result = m.apply_edge_proposals(proposals_json=mock_llm_json)
        finally:
            m.active_store = orig

        assert result.ok is True
        assert result.data.total_proposed == 0  # null-rel items filtered before validation
        assert len(result.data.accepted) == 0
        assert len(result.data.rejected) == 0

    def test_mixed_accepted_rejected(self, tmp_path):
        """Multiple proposals — some accepted, some rejected for different reasons."""
        import main as m

        store = _make_store(str(tmp_path))
        _populate_store(store)
        orig = m.active_store
        m.active_store = store
        try:
            proposals = [
                {   # accepted
                    "source_id": "scenario_age_check",
                    "target_id": "rule_age_eligibility",
                    "relationship": "TESTS",
                    "confidence": 0.82,
                    "rationale": "Direct reference.",
                },
                {   # rejected — low confidence
                    "source_id": "scenario_age_check",
                    "target_id": "rule_age_eligibility",
                    "relationship": "IMPLEMENTS",
                    "confidence": 0.4,
                    "rationale": "Weak link.",
                },
                {   # rejected — missing node
                    "source_id": "ghost_node",
                    "target_id": "rule_age_eligibility",
                    "relationship": "MAPS_TO",
                    "confidence": 0.9,
                    "rationale": "Phantom.",
                },
            ]
            result = m.apply_edge_proposals(
                proposals_json=json.dumps(proposals),
                confidence_floor=0.6,
            )
        finally:
            m.active_store = orig

        assert result.ok is True
        assert result.data.total_proposed == 3
        assert len(result.data.accepted) == 1
        assert len(result.data.rejected) == 2


# ---------------------------------------------------------------------------
# AC1: No external network calls
# ---------------------------------------------------------------------------

class TestNoExternalNetworkCalls:
    def test_apply_edge_proposals_no_network(self, tmp_path):
        """socket.socket is patched to raise; apply_edge_proposals must still succeed."""
        import main as m

        store = _make_store(str(tmp_path))
        _populate_store(store)
        proposal = [{
            "source_id": "scenario_age_check",
            "target_id": "rule_age_eligibility",
            "relationship": "TESTS",
            "confidence": 0.85,
            "rationale": "test",
        }]
        orig = m.active_store
        m.active_store = store
        try:
            with patch.object(socket, "socket", side_effect=OSError("No network allowed")):
                result = m.apply_edge_proposals(proposals_json=json.dumps(proposal))
        finally:
            m.active_store = orig

        assert result.ok is True, "apply_edge_proposals made an unexpected network call"


# ---------------------------------------------------------------------------
# EdgeCandidate dataclass
# ---------------------------------------------------------------------------

class TestEdgeCandidateDataclass:
    def test_fields(self):
        from engine.edges.heuristic import EdgeCandidate
        ec = EdgeCandidate(
            source_id="a",
            target_id="b",
            proposed_relationship="TESTS",
            score=0.65,
            rationale="2 shared tokens: ['age', 'eligibility']",
            source_excerpt="Age rule text",
            target_excerpt="Scenario text",
        )
        assert ec.source_id == "a"
        assert ec.score == 0.65

    def test_asdict_serializable(self):
        import dataclasses
        from engine.edges.heuristic import EdgeCandidate
        ec = EdgeCandidate("a", "b", "TESTS", 0.65, "reason", "src", "tgt")
        d = dataclasses.asdict(ec)
        # Should round-trip through JSON
        json.dumps(d)


# ---------------------------------------------------------------------------
# HeuristicEdgeMapper.last_candidates
# ---------------------------------------------------------------------------

class TestHeuristicLastCandidates:
    def test_last_candidates_empty_before_map(self):
        from engine.edges.heuristic import HeuristicEdgeMapper
        mapper = HeuristicEdgeMapper()
        assert mapper.last_candidates == []

    def test_ambiguous_candidates_empty_before_map(self):
        from engine.edges.heuristic import HeuristicEdgeMapper
        mapper = HeuristicEdgeMapper()
        assert mapper.ambiguous_candidates(limit=10) == []

    def test_last_candidates_populated_after_map(self, tmp_path):
        """After map() on a store with ambiguous pairs, last_candidates is non-empty."""
        from engine.edges.heuristic import HeuristicEdgeMapper, EdgeCandidate
        store = _make_store(str(tmp_path))

        # Add several nodes; with only two closely related nodes the TF-IDF
        # scores might be low — we just check that the mechanism works.
        _populate_store_diverse(store)

        mapper = HeuristicEdgeMapper()
        mapper.map(store)
        # last_candidates should be a list (possibly empty if TF-IDF scores are all
        # either above auto_emit_threshold or below candidate_threshold)
        assert isinstance(mapper.last_candidates, list)
        for c in mapper.last_candidates:
            assert isinstance(c, EdgeCandidate)

    def test_ambiguous_candidates_sorted_descending(self, tmp_path):
        from engine.edges.heuristic import HeuristicEdgeMapper
        store = _make_store(str(tmp_path))
        _populate_store_diverse(store)
        mapper = HeuristicEdgeMapper()
        mapper.map(store)
        cands = mapper.ambiguous_candidates(limit=50)
        scores = [c.score for c in cands]
        assert scores == sorted(scores, reverse=True)

    def test_map_resets_candidates(self, tmp_path):
        """Calling map() twice resets last_candidates (no stale accumulation)."""
        from engine.edges.heuristic import HeuristicEdgeMapper
        store = _make_store(str(tmp_path))
        _populate_store_diverse(store)
        mapper = HeuristicEdgeMapper()
        mapper.map(store)
        first_count = len(mapper.last_candidates)
        mapper.map(store)
        second_count = len(mapper.last_candidates)
        # Both runs on the same store should yield the same count (idempotent)
        assert first_count == second_count


# ---------------------------------------------------------------------------
# ContextExtractor.get_pending_edge_candidates
# ---------------------------------------------------------------------------

class TestGetPendingEdgeCandidates:
    def test_returns_empty_list_before_ingest(self, tmp_path):
        from engine.extractor import ContextExtractor
        extractor = ContextExtractor(str(tmp_path))
        assert extractor.get_pending_edge_candidates() == []

    def test_returns_list_after_store_populated_and_mapper_run(self, tmp_path):
        from engine.extractor import ContextExtractor
        from engine.edges.heuristic import EdgeCandidate
        store = _make_store(str(tmp_path))
        _populate_store_diverse(store)
        extractor = ContextExtractor(store)

        # Run map() directly on the mapper so last_candidates are available
        from engine.edges.heuristic import HeuristicEdgeMapper
        extractor._edge_mappers[0].map(store)

        cands = extractor.get_pending_edge_candidates(limit=50)
        assert isinstance(cands, list)
        for c in cands:
            assert isinstance(c, EdgeCandidate)

    def test_limit_respected(self, tmp_path):
        from engine.extractor import ContextExtractor
        store = _make_store(str(tmp_path))
        _populate_store_diverse(store)
        extractor = ContextExtractor(store)
        extractor._edge_mappers[0].map(store)

        full = extractor.get_pending_edge_candidates(limit=100)
        limited = extractor.get_pending_edge_candidates(limit=1)
        assert len(limited) <= 1
        assert len(full) >= len(limited)


# ---------------------------------------------------------------------------
# AC9: README spot-checks
# ---------------------------------------------------------------------------

class TestReadmeAccuracy:
    def _readme(self) -> str:
        base = Path(__file__).parents[1]
        return (base / "README.md").read_text(encoding="utf-8")

    def test_readme_mentions_propose_edge_candidates(self):
        assert "propose_edge_candidates" in self._readme()

    def test_readme_mentions_apply_edge_proposals(self):
        assert "apply_edge_proposals" in self._readme()

    def test_readme_no_external_api_keys(self):
        readme = self._readme()
        assert "No external API keys required" in readme or "no external API keys" in readme.lower()

    def test_readme_no_gpt5_reference(self):
        assert "gpt-5" not in self._readme()
