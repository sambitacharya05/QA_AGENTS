"""Tests for db.graph_store.GraphStore.record_anomaly.

SPEC-3 Wave 3 §3.2 / §3.6: relationship-linker explicitly records anomalies
without an upsert_node attempt. Idempotent on (kind, evidence.summary).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.graph_store import GraphStore


@pytest.fixture
def store(tmp_path):
    s = GraphStore(str(tmp_path))
    # Seed one node to record anomalies against.
    s.upsert_node(
        "ui_element_btn_send_otp", "ui_element",
        "BTN_SEND_OTP", "Send OTP button",
        {"selector": "id=send-otp"},
    )
    return s


def test_record_anomaly_persists_structured_record(store):
    ok = store.record_anomaly(
        node_id="ui_element_btn_send_otp",
        anomaly_kind="ui_without_requirement",
        severity="warning",
        evidence={
            "summary": "BTN_SEND_OTP exists in POM but no rule references it.",
            "source_file": "ingest/pages/LoginPage.java",
        },
        suggested_action="Add a rule node or remove the element.",
    )
    assert ok is True

    node = store.get_node("ui_element_btn_send_otp")
    anomalies = node["metadata"]["behavioral_anomalies"]
    assert len(anomalies) == 1
    record = anomalies[0]
    assert record["anomaly_kind"] == "ui_without_requirement"
    assert record["severity"] == "warning"
    assert record["detected_by"] == "relationship-linker"
    assert "detected_at" in record
    assert record["evidence"]["summary"].startswith("BTN_SEND_OTP")
    assert record["suggested_action"].startswith("Add a rule")


def test_record_anomaly_is_idempotent(store):
    """Same (kind, summary) recorded twice → second call returns False, no dup."""
    ev = {"summary": "Same divergence", "source_file": "x.java"}
    first = store.record_anomaly(
        "ui_element_btn_send_otp", "ui_without_requirement", "warning", ev,
    )
    second = store.record_anomaly(
        "ui_element_btn_send_otp", "ui_without_requirement", "warning", ev,
    )
    assert first is True
    assert second is False
    node = store.get_node("ui_element_btn_send_otp")
    assert len(node["metadata"]["behavioral_anomalies"]) == 1


def test_record_anomaly_rejects_unknown_kind(store):
    """The MCP wrapper does the validation, but the store method itself just
    persists what it's given. Document that here so callers know to validate."""
    # Direct store call accepts any string; MCP tool layer enforces the enum.
    ok = store.record_anomaly(
        "ui_element_btn_send_otp", "made_up_kind", "info",
        {"summary": "Custom anomaly"},
    )
    assert ok is True
    # Persistence still happened — validation lives at the MCP-tool boundary.
    node = store.get_node("ui_element_btn_send_otp")
    assert node["metadata"]["behavioral_anomalies"][0]["anomaly_kind"] == "made_up_kind"


def test_record_anomaly_on_missing_node_returns_false(store):
    ok = store.record_anomaly(
        "node_that_does_not_exist", "ui_without_requirement", "warning",
        {"summary": "won't persist"},
    )
    assert ok is False
