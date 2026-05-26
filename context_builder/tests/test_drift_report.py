"""Tests for main.get_behavioral_drift_report — enriched Wave 3 shape (A5).

The new shape carries top-level `by_kind` / `by_severity` buckets and
per-record `anomaly_kind`/`severity`/`detected_by`/`detected_at`/`evidence`
keys. Legacy on-disk records still parse via the shim in main.py.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main
from db.graph_store import GraphStore


@pytest.fixture
def store_with_anomalies(tmp_path, monkeypatch):
    s = GraphStore(str(tmp_path))
    s.upsert_node("ui_element_btn", "ui_element", "BTN_X", "btn", {})
    s.upsert_node("rule_payment_limit", "business_rule", "Payment Limit", "Max $5000", {})

    # New structured record (Wave 3+)
    s.record_anomaly(
        "ui_element_btn", "ui_without_requirement", "warning",
        {"summary": "Element has no rule", "source_file": "x.java"},
    )
    s.record_anomaly(
        "rule_payment_limit", "rule_without_implementation", "critical",
        {"summary": "No code component implements this rule"},
    )
    # Legacy-shaped record (pre-Wave 3) — drift shim should still surface it
    legacy_meta = s.graph.nodes["ui_element_btn"]["metadata"]
    legacy_meta.setdefault("behavioral_anomalies", []).append({
        "observed_deviation_profile": "Legacy text from pre-Wave 3 graph",
        "reported_by": "playwright_explorer",
        "timestamp": "2025-01-01T00:00:00Z",
    })

    # Make sure main._get_store returns our test store regardless of registry.
    monkeypatch.setattr(main, "active_store", s, raising=False)
    return s


def test_by_kind_and_by_severity_buckets(store_with_anomalies):
    report = json.loads(main.get_behavioral_drift_report())
    assert "by_kind" in report
    assert "by_severity" in report
    # Two structured + one legacy = 3 anomaly records total
    assert report["by_kind"]["ui_without_requirement"] == 1
    assert report["by_kind"]["rule_without_implementation"] == 1
    assert report["by_severity"]["warning"] == 1
    assert report["by_severity"]["critical"] == 1
    # Legacy record gets the fallback kind/severity.
    assert report["by_kind"]["legacy_requirement_drift"] == 1
    assert report["by_severity"]["info"] == 1


def test_legacy_anomalies_have_legacy_kind(store_with_anomalies):
    report = json.loads(main.get_behavioral_drift_report())
    legacy_records = [
        r for r in report["node_anomalies"]
        if r["anomaly_kind"] == "legacy_requirement_drift"
    ]
    assert len(legacy_records) == 1
    record = legacy_records[0]
    assert record["severity"] == "info"
    assert record["detected_by"] == "unknown"
    # Shim populates evidence.summary from observed_deviation_profile.
    assert "Legacy text" in record["evidence"]["summary"]
    assert record["evidence"]["source_file"] == "playwright_explorer"
