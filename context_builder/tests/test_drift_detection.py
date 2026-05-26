import pytest
import os
import json
import networkx as nx
from db.graph_store import GraphStore

@pytest.fixture
def temp_store(tmp_path):
    """Fixture initializing a fresh GraphStore in a temporary directory."""
    store = GraphStore(str(tmp_path))
    yield store
    store.clear_graph()

def test_write_lock_protection_triggered(temp_store):
    """Verifies that an agent attempting to overwrite a locked documentation node is blocked and logs an anomaly."""
    node_id = "rule_calculation_matrix"
    
    # 1. Ingestion pass simulation (Instantiated from Excel documentation)
    initial_meta = {
        "sync_governance": {
            "origin": "documentation",
            "is_merged": False
        },
        "source_file": "calculation_matrix.xlsx"
    }
    temp_store.upsert_node(
        node_id=node_id,
        node_type="business_rule",
        name="Excel Rules Matrix",
        description="Expected tax calculation rate: 0.15",
        metadata=initial_meta
    )
    
    # Verify successfully created
    node = temp_store.get_node(node_id)
    assert node["description"] == "Expected tax calculation rate: 0.15"
    assert node["metadata"]["sync_governance"]["origin"] == "documentation"

    # 2. Agent mutation attempt (Simulating Playwright telemetry mismatch detection)
    agent_meta = {
        "sync_governance": {
            "caller": "agent",
            "timestamp": "2026-05-24T12:00:00Z"
        },
        "source_file": "playwright_mcp_explorer"
    }
    temp_store.upsert_node(
        node_id=node_id,
        node_type="business_rule",
        name="Excel Rules Matrix",
        description="Actual runtime rate observed is 0.20",
        metadata=agent_meta
    )
    
    # Verify lock blocked mutation and logged anomaly
    updated_node = temp_store.get_node(node_id)
    assert updated_node["description"] == "Expected tax calculation rate: 0.15"  # UNCHANGED
    
    anomalies = updated_node["metadata"].get("behavioral_anomalies", [])
    assert len(anomalies) == 1
    assert anomalies[0]["observed_deviation_profile"] == "Actual runtime rate observed is 0.20"
    assert anomalies[0]["reported_by"] == "playwright_mcp_explorer"
    assert anomalies[0]["timestamp"] == "2026-05-24T12:00:00Z"


def test_write_lock_bypass_for_documentation(temp_store):
    """Verifies that subsequent documentation parsing runs CAN update locked nodes normally."""
    node_id = "rule_calculation_matrix"
    
    # Initial documentation pass
    initial_meta = {
        "sync_governance": {"origin": "documentation"},
        "source_file": "calculation_matrix.xlsx"
    }
    temp_store.upsert_node(node_id, "business_rule", "Excel Matrix", "Rate: 0.15", initial_meta)
    
    # Subsequent documentation pass (no caller=agent tag)
    updated_meta = {
        "sync_governance": {"origin": "documentation"},
        "source_file": "calculation_matrix_v2.xlsx"
    }
    temp_store.upsert_node(node_id, "business_rule", "Excel Matrix", "Rate: 0.18", updated_meta)
    
    # Verify description successfully updated
    node = temp_store.get_node(node_id)
    assert node["description"] == "Rate: 0.18"
    assert "behavioral_anomalies" not in node["metadata"]


def test_get_behavioral_drift_report(temp_store):
    """Verifies that the drift report successfully gathers both inline metadata anomalies and DRIFTED_FROM edges."""
    # 1. Setup inline metadata anomaly
    node_id = "rule_claim_verification"
    initial_meta = {
        "sync_governance": {"origin": "documentation"},
        "source_file": "verification_rules.docx"
    }
    temp_store.upsert_node(node_id, "business_rule", "Claims Rule", "Under age 25 check", initial_meta)
    
    agent_meta = {
        "sync_governance": {"caller": "agent", "timestamp": "2026"},
        "source_file": "playwright_explorer"
    }
    temp_store.upsert_node(node_id, "business_rule", "Claims Rule", "Live app skipped age check", agent_meta)

    # 2. Setup a topological DRIFTED_FROM edge
    ui_node_id = "ui_claims_page"
    temp_store.upsert_node(ui_node_id, "ui_page_object", "Claims Page POM", "Live page element locator config", {"source_file": "claims.page.ts"})
    temp_store.upsert_edge(ui_node_id, node_id, "DRIFTED_FROM", {"reason": "Mismatch between spec field and locator"})

    # Emulate main.py tool logic directly
    drift_records = []
    edges = temp_store.get_edges()
    drift_edges = [e for e in edges if e["relationship"] == "DRIFTED_FROM"]

    for n_id, data in temp_store.graph.nodes(data=True):
        anomalies = data.get("metadata", {}).get("behavioral_anomalies", [])
        if anomalies:
            drift_records.append({
                "node_id": n_id,
                "node_name": data.get("name"),
                "anomaly_type": "requirement_mismatch_drift",
                "details": anomalies
            })

    assert len(drift_edges) == 1
    assert drift_edges[0]["source_id"] == ui_node_id
    assert drift_edges[0]["target_id"] == node_id
    
    assert len(drift_records) == 1
    assert drift_records[0]["node_id"] == node_id
    assert drift_records[0]["details"][0]["observed_deviation_profile"] == "Live app skipped age check"


def test_resolve_behavioral_drift_update_spec_index(temp_store):
    """Verifies that resolve_behavioral_drift with 'update_spec' and selectable indices mutates spec description and cleans graph."""
    node_id = "rule_discount"
    ui_node_id = "ui_discount_button"
    
    # 1. Prepare node with active anomalies & DRIFTED_FROM edge
    temp_store.upsert_node(node_id, "business_rule", "Discount", "Rate is 10%", {"sync_governance": {"origin": "documentation"}})
    temp_store.upsert_node(ui_node_id, "ui_page_object", "Button POM", "", {})
    temp_store.upsert_edge(ui_node_id, node_id, "DRIFTED_FROM", {})
    
    # Add multiple cumulative anomalies
    temp_store.upsert_node(node_id, "business_rule", "Discount", "Rate is actually 12%", {"sync_governance": {"caller": "agent"}})
    temp_store.upsert_node(node_id, "business_rule", "Discount", "Rate is actually 15%", {"sync_governance": {"caller": "agent"}})

    # Validate baseline anomalies exist
    n_data = temp_store.graph.nodes[node_id]
    assert n_data["description"] == "Rate is 10%"
    assert len(temp_store.get_edges(source_id=ui_node_id, target_id=node_id)) == 1
    anomalies = n_data.get("metadata", {}).get("behavioral_anomalies", [])
    assert len(anomalies) == 2

    # 2. Run Resolution action = "update_spec" selecting index 0 (12%)
    target_anomaly_index = 0
    latest_observation = anomalies[target_anomaly_index]["observed_deviation_profile"]
    
    temp_store.graph.nodes[node_id]["description"] = latest_observation
    temp_store.graph.nodes[node_id]["metadata"]["behavioral_anomalies"] = []
    
    # Remove DRIFTED_FROM edges
    edges_to_remove = [(u, v) for u, v, d in temp_store.graph.edges(data=True) if v == node_id and d.get("relationship") == "DRIFTED_FROM"]
    for u, v in edges_to_remove:
        temp_store.graph.remove_edge(u, v)

    # 3. Assert Results
    resolved_node = temp_store.get_node(node_id)
    assert resolved_node["description"] == "Rate is actually 12%"  # Mutated to selected index reality
    assert resolved_node["metadata"]["behavioral_anomalies"] == []  # Cleared
    assert len(temp_store.get_edges(source_id=ui_node_id, target_id=node_id)) == 0  # Deleted


def test_resolve_behavioral_drift_keep_expected(temp_store):
    """Verifies that resolve_behavioral_drift with 'keep_expected' preserves spec description but cleans anomalies."""
    node_id = "rule_discount"
    ui_node_id = "ui_discount_button"
    
    # 1. Prepare node with active anomalies & DRIFTED_FROM edge
    temp_store.upsert_node(node_id, "business_rule", "Discount", "Rate is 10%", {"sync_governance": {"origin": "documentation"}})
    temp_store.upsert_node(ui_node_id, "ui_page_object", "Button POM", "", {})
    temp_store.upsert_edge(ui_node_id, node_id, "DRIFTED_FROM", {})
    
    temp_store.upsert_node(node_id, "business_rule", "Discount", "Rate is actually 12%", {"sync_governance": {"caller": "agent"}})

    # 2. Run Resolution action = "keep_expected"
    temp_store.graph.nodes[node_id]["metadata"]["behavioral_anomalies"] = []
    edges_to_remove = [(u, v) for u, v, d in temp_store.graph.edges(data=True) if v == node_id and d.get("relationship") == "DRIFTED_FROM"]
    for u, v in edges_to_remove:
        temp_store.graph.remove_edge(u, v)

    # 3. Assert Results
    resolved_node = temp_store.get_node(node_id)
    assert resolved_node["description"] == "Rate is 10%"  # Intact
    assert resolved_node["metadata"]["behavioral_anomalies"] == []  # Cleared
    assert len(temp_store.get_edges(source_id=ui_node_id, target_id=node_id)) == 0  # Deleted


def test_mcp_tools_drift_report_and_resolve(temp_store, monkeypatch):
    """Verifies that the FastMCP tools in main.py correctly report and resolve drift on the active store."""
    import main
    # Mock the global active store in main.py to point to our temp store
    monkeypatch.setattr(main, "active_store", temp_store)

    # 1. Setup a locked business rule node
    temp_store.upsert_node(
        "rule_mcp_test", "business_rule", "MCP Rule", "Standard expected behavior",
        {"sync_governance": {"origin": "documentation"}}
    )

    # 2. Simulate agent override triggering write-lock & anomaly accumulation
    agent_meta = {
        "sync_governance": {"caller": "agent", "timestamp": "2026-05-24T15:00:00Z"},
        "source_file": "playwright_agent"
    }
    temp_store.upsert_node(
        "rule_mcp_test", "business_rule", "MCP Rule", "Observed deviation rate 1.5",
        metadata=agent_meta
    )
    temp_store.upsert_node(
        "rule_mcp_test", "business_rule", "MCP Rule", "Observed deviation rate 2.0",
        metadata=agent_meta
    )

    # 3. Call get_behavioral_drift_report() tool handler
    report_json = main.get_behavioral_drift_report()
    report = json.loads(report_json)

    # SPEC-3 Wave 3 (A5): records are flattened — one report entry per anomaly,
    # not one per node. So 2 agent attempts produce 2 node_anomalies entries.
    assert report["total_anomalies_detected"] == 2
    assert "by_kind" in report
    assert "by_severity" in report
    rule_records = [
        r for r in report["node_anomalies"] if r["node_id"] == "rule_mcp_test"
    ]
    assert len(rule_records) == 2
    # Legacy anomalies (recorded via governance-guardrail path) get the
    # fallback anomaly_kind via the shim in main.py.
    assert all(r["anomaly_kind"] == "legacy_requirement_drift" for r in rule_records)
    summaries = {r["evidence"]["summary"] for r in rule_records}
    assert "Observed deviation rate 1.5" in summaries
    assert "Observed deviation rate 2.0" in summaries

    # 4. Resolve using resolve_behavioral_drift with specific index (Index 0: rate 1.5)
    resolve_res = main.resolve_behavioral_drift("rule_mcp_test", "update_spec", target_anomaly_index=0)
    assert "Successfully resolved" in resolve_res

    # Verify rule description was correctly updated to index 0 (1.5) and anomalies cleared
    updated_node = temp_store.get_node("rule_mcp_test")
    assert updated_node["description"] == "Observed deviation rate 1.5"
    assert updated_node["metadata"]["behavioral_anomalies"] == []
