import os
import sys
import json
import pytest

# Ensure parent directory is in python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main
from db.graph_store import GraphStore
from db.blueprint_store import BlueprintStore


@pytest.fixture
def active_store_and_blueprint(tmp_path, monkeypatch):
    """Provides an active GraphStore and BlueprintStore pointing to a temp directory, mapped to main.py."""
    store = GraphStore(str(tmp_path))
    blueprint = BlueprintStore(str(tmp_path))
    
    # Pre-populate blueprint standards
    blueprint.register_directory_standard("**/pages/**", "Page Object Model (POM)", ["Rule 1"])
    blueprint.register_utility_signature("util_date", "DateUtils", "src/DateUtils.java", [
        {"method_name": "today", "signature": "public static String today()", "parameters": {}, "return_type": "String", "description": "Returns today"}
    ])
    
    # Pre-populate some graph nodes
    store.upsert_node(
        "rule_mcp_verification", "business_rule", "MCP Verification", "Locked specification rule text",
        {"sync_governance": {"origin": "documentation"}}
    )
    store.upsert_node(
        "endpoint_mcp_api", "api_endpoint", "GET /api/mcp", "API info",
        {"source_file": "ApiController.java"}
    )
    store.upsert_edge("endpoint_mcp_api", "rule_mcp_verification", "IMPLEMENTS")
    
    # Add dummy rules to make BM25 corpus larger so that positive score checks pass during semantic query
    for i in range(1, 10):
        store.upsert_node(
            f"rule_dummy_{i}", "business_rule", f"Dummy Rule {i}", f"This is placeholder requirement description number {i}",
            {"sync_governance": {"origin": "documentation"}}
        )
    
    store.save_graph()
    
    monkeypatch.setattr(main, "active_store", store)
    
    return store, blueprint


def test_query_framework_blueprint(active_store_and_blueprint):
    store, blueprint = active_store_and_blueprint
    
    # Test query framework blueprint tool
    response_json = main.query_framework_blueprint("src/pages/LoginPage.page.ts")
    response = json.loads(response_json)
    
    assert "src/pages/LoginPage.page.ts" in response["target_path"]
    assert response["applicable_architectural_rules"]["pattern"] == "Page Object Model (POM)"
    assert response["applicable_architectural_rules"]["rules"] == ["Rule 1"]
    
    # Verify utility signature retrieval
    assert len(response["available_reusable_methods"]) >= 1
    assert any(m["class_name"] == "DateUtils" for m in response["available_reusable_methods"])


def test_get_framework_generation_blueprint(active_store_and_blueprint):
    store, blueprint = active_store_and_blueprint
    
    # Test get generation blueprint tool
    response_json = main.get_framework_generation_blueprint()
    response = json.loads(response_json)
    
    assert "target_meta_framework" in response
    assert "directory_scaffolding" in response
    assert "scaffolding_templates" in response


def test_behavioral_drift_flow(active_store_and_blueprint):
    store, blueprint = active_store_and_blueprint
    
    # 1. Trigger write lock protection and log behavioral anomaly
    agent_meta = {
        "sync_governance": {"caller": "agent", "timestamp": "2026-05-24T17:15:00Z"},
        "source_file": "playwright_suite"
    }
    # Attempting to overwrite a locked node "rule_mcp_verification"
    store.upsert_node(
        "rule_mcp_verification", "business_rule", "MCP Verification",
        "Observed deviation profile description", agent_meta
    )
    
    # Add a topological DRIFTED_FROM edge
    store.upsert_node("ui_page_drift", "ui_page_object", "Drifting Page", "Desc", {})
    store.upsert_edge("ui_page_drift", "rule_mcp_verification", "DRIFTED_FROM")
    
    store.save_graph()
    
    # 2. Get drift report
    report_json = main.get_behavioral_drift_report()
    report = json.loads(report_json)
    
    assert report["total_anomalies_detected"] == 2
    assert len(report["drift_edges"]) == 1
    assert report["drift_edges"][0]["source_id"] == "ui_page_drift"
    assert report["drift_edges"][0]["target_id"] == "rule_mcp_verification"
    
    # SPEC-3 Wave 3 (A5): records are flattened (one entry per anomaly) and
    # the per-record outer keys are anomaly_kind / severity / evidence / ...
    assert len(report["node_anomalies"]) == 1
    record = report["node_anomalies"][0]
    assert record["node_id"] == "rule_mcp_verification"
    assert record["anomaly_kind"] == "legacy_requirement_drift"
    assert record["evidence"]["summary"] == "Observed deviation profile description"
    
    # 3. Resolve behavioral drift via update_spec
    resolve_res = main.resolve_behavioral_drift("rule_mcp_verification", "update_spec", target_anomaly_index=0)
    assert "Successfully resolved" in resolve_res
    
    # Verify description is updated and anomalies + drift edges are cleared
    resolved_node = store.get_node("rule_mcp_verification")
    assert resolved_node["description"] == "Observed deviation profile description"
    assert resolved_node["metadata"]["behavioral_anomalies"] == []
    assert len(store.get_edges(source_id="ui_page_drift", target_id="rule_mcp_verification")) == 0


def test_mcp_prompts(active_store_and_blueprint):
    # Test generation prompts
    bdd_prompt = main.generate_bdd_tests("rule_mcp_verification")
    assert "TARGET BUSINESS RULE" in bdd_prompt
    assert "MCP Verification" in bdd_prompt
    assert "RELATED TECHNICAL COMPONENTS" in bdd_prompt
    assert "endpoint_mcp_api" in bdd_prompt
    
    api_prompt = main.generate_api_tests("endpoint_mcp_api")
    assert "TARGET ENDPOINT" in api_prompt
    assert "ApiController.java" in api_prompt


def test_mcp_resources(active_store_and_blueprint):
    # Test resource summary
    summary_json = main.get_graph_summary_resource()
    summary = json.loads(summary_json)
    assert summary["total_nodes"] == 11
    assert "business_rule" in summary["node_types_breakdown"]
    
    # Test detailed rule resource
    detail_json = main.get_rule_detail("rule_mcp_verification")
    detail = json.loads(detail_json)
    assert detail["name"] == "MCP Verification"
    assert detail["metadata"]["sync_governance"]["origin"] == "documentation"


def test_query_semantic_graph(active_store_and_blueprint):
    # Test semantic query search — query_semantic_graph now returns ToolResponse[QueryResult]
    response = main.query_semantic_graph("Verification")
    assert response.ok, f"Expected ok=True but got error: {response.error}"
    results = response.data.results
    assert len(results) > 0
    assert any("rule_mcp_verification" in r.id for r in results)


def test_check_workspace_sync(active_store_and_blueprint, tmp_path):
    store, blueprint = active_store_and_blueprint

    # Create files in mock workspace to verify sync functionality
    features_dir = tmp_path / "features"
    os.makedirs(features_dir, exist_ok=True)

    feature_file = features_dir / "sample.feature"
    feature_file.write_text("Feature: Sample Feature")

    # Store should initially see it as a new file since it has not been ingested
    # check_workspace_sync now returns ToolResponse[WorkspaceSyncReport]
    response = main.check_workspace_sync(str(tmp_path))
    assert response.ok, f"Expected ok=True but got error: {response.error}"
    sync_res = response.data

    assert sync_res.is_in_sync is False
    assert any("sample.feature" in p for p in sync_res.new_files)
