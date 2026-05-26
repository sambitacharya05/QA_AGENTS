"""Tests for engine/edges/heuristic.py _load_policy back-fill behaviour.

SPEC-3 Wave 1: a workspace policy file that pre-dates the new MAPS_TO
relationship must gain it on next load without losing the user's edits to
existing thresholds.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.edges.heuristic import _DEFAULT_POLICY, _load_policy


def _write_policy(storage_dir: str, body: dict) -> str:
    os.makedirs(storage_dir, exist_ok=True)
    path = os.path.join(storage_dir, "edge_policy.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(body, f)
    return path


def test_existing_policy_keeps_user_thresholds_and_gains_new_relations(tmp_path):
    """A pre-Wave 1 policy with a tightened TESTS min_score must KEEP that
    value AND gain the new MAPS_TO relationship on load."""
    storage_dir = str(tmp_path / ".context_builder")
    user_policy = {
        "schema_version": 1,
        "auto_emit_threshold": 0.75,
        "candidate_threshold": 0.35,
        "relationship_rules": {
            "TESTS": {
                "min_score": 0.80,   # user tightened from default 0.65
                "applicable_types": [["test_scenario", "business_rule"]],
            },
        },
    }
    _write_policy(storage_dir, user_policy)

    loaded = _load_policy(storage_dir)

    # User edit preserved
    assert loaded["relationship_rules"]["TESTS"]["min_score"] == 0.80, (
        "Back-fill must not overwrite a user-tightened threshold"
    )
    assert loaded["relationship_rules"]["TESTS"]["applicable_types"] == [
        ["test_scenario", "business_rule"]
    ], "User-narrowed applicable_types must not be replaced"

    # MAPS_TO back-filled from defaults
    assert "MAPS_TO" in loaded["relationship_rules"], (
        "MAPS_TO must be back-filled into pre-existing user policies"
    )
    maps_to = loaded["relationship_rules"]["MAPS_TO"]
    assert maps_to["min_score"] == 0.60
    assert ["field_specification", "validation_rule"] in maps_to["applicable_types"]


def test_fresh_workspace_gets_full_default_policy_including_maps_to(tmp_path):
    """A workspace with no edge_policy.json must get the full default policy
    including MAPS_TO."""
    storage_dir = str(tmp_path / ".context_builder")
    # No file written → _load_policy creates one from _DEFAULT_POLICY.

    loaded = _load_policy(storage_dir)

    # Policy file was created
    assert os.path.exists(os.path.join(storage_dir, "edge_policy.json"))

    # All Wave 1 relationships are present
    rels = loaded["relationship_rules"]
    for rel in ("TESTS", "IMPLEMENTS", "VALIDATES", "USES_MODEL", "MAPS_TO"):
        assert rel in rels, f"Fresh workspace missing relationship rule {rel}"

    # Rule-family subtypes appear in TESTS applicable_types
    tests_pairs = rels["TESTS"]["applicable_types"]
    for subtype in (
        "validation_rule", "eligibility_rule",
        "ui_business_rule", "security_rule",
    ):
        assert ["test_scenario", subtype] in tests_pairs, (
            f"Default TESTS rule should include test_scenario→{subtype} pair"
        )


def test_wave3_new_relationships_backfill_correctly(tmp_path):
    """SPEC-3 Wave 3: an existing policy that pre-dates CALLS / USES_DATA /
    REFERENCES must gain all three on next load, while user thresholds on
    existing relationships are preserved."""
    storage_dir = str(tmp_path / ".context_builder")
    pre_wave3_policy = {
        "schema_version": 1,
        "auto_emit_threshold": 0.75,
        "candidate_threshold": 0.35,
        "relationship_rules": {
            "TESTS": {
                "min_score": 0.85,   # user tightened
                "applicable_types": [["test_scenario", "business_rule"]],
            },
            "IMPLEMENTS": {
                "min_score": 0.70,
                "applicable_types": [["code_component", "business_rule"]],
            },
        },
    }
    _write_policy(storage_dir, pre_wave3_policy)

    loaded = _load_policy(storage_dir)
    rels = loaded["relationship_rules"]

    # User edits preserved
    assert rels["TESTS"]["min_score"] == 0.85

    # All Wave 1 + Wave 2 + Wave 3 relationships back-filled
    for rel in (
        "TESTS", "IMPLEMENTS", "VALIDATES", "USES_MODEL",
        "MAPS_TO", "REFERENCES",
        "CALLS", "USES_DATA",
    ):
        assert rel in rels, f"Back-fill missed {rel}"

    # USES_DATA shape from Wave 3
    uses_data = rels["USES_DATA"]
    assert uses_data["min_score"] == 0.60
    assert ["test_scenario", "data_model"] in uses_data["applicable_types"]

    # CALLS shape from Wave 3
    calls = rels["CALLS"]
    assert ["code_component", "test_utility"] in calls["applicable_types"]
