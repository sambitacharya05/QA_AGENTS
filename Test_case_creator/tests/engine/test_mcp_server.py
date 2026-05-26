import os
import json
import pytest
from engine.mcp_server import (
    extract_subgraph,
    write_azure_csv,
    write_coverage_report,
    validate_graph,
    list_features_with_area_paths,
    list_rules_with_test_coverage,
    get_rule_constraints_for_test_validation,
    get_anomalies_for_features,
)

def test_extract_subgraph_bfs_traversal(tmp_path):
    # Create a mock graph.json representing rich node-link structure
    graph_data = {
        "nodes": [
            {"id": "rule_01", "type": "business_rule", "name": "Claims Rule 1"},
            {"id": "rule_02", "type": "business_rule", "name": "Claims Rule 2"},
            {"id": "scenario_01", "type": "test_scenario", "name": "BDD Scenario 1"},
            {"id": "api_01", "type": "api_endpoint", "name": "POST /api/claims"},
            {"id": "unrelated_rule", "type": "business_rule", "name": "Billing Rule"}
        ],
        "links": [
            {"source": "rule_01", "target": "scenario_01", "relationship": "TRACES_TO"},
            {"source": "scenario_01", "target": "api_01", "relationship": "MAPS_TO"},
            {"source": "rule_02", "target": "rule_01", "relationship": "DECLARES"},
            {"source": "rule_01", "target": "unrelated_rule", "relationship": "OTHER_RELATIONSHIP"}  # Non-traversable
        ]
    }
    
    graph_file = tmp_path / "graph.json"
    with open(graph_file, "w", encoding="utf-8") as f:
        json.dump(graph_data, f)
        
    # Act: Extract subgraph starting from rule_02
    result = extract_subgraph(str(graph_file), ["rule_02"])
    
    # Assert
    assert result["matched_rule_ids"] == ["rule_02"]
    nodes = result["subgraph_nodes"]
    
    # rule_02 -> rule_01 (DECLARES) -> scenario_01 (TRACES_TO) -> api_01 (MAPS_TO)
    # unrelated_rule should not be traversed because relationship is OTHER_RELATIONSHIP
    assert "rule_02" in nodes
    assert "rule_01" in nodes
    assert "scenario_01" in nodes
    assert "api_01" in nodes
    assert "unrelated_rule" not in nodes
    
    edges = result["subgraph_edges"]
    assert len(edges) == 3
    relationships = {e.get("relationship") for e in edges}
    assert "DECLARES" in relationships
    assert "TRACES_TO" in relationships
    assert "MAPS_TO" in relationships

def test_extract_subgraph_silent_skip_nonexistent(tmp_path):
    graph_data = {
        "nodes": [{"id": "rule_01", "type": "business_rule"}],
        "links": []
    }
    graph_file = tmp_path / "graph.json"
    with open(graph_file, "w", encoding="utf-8") as f:
        json.dump(graph_data, f)
        
    result = extract_subgraph(str(graph_file), ["non_existent_rule"])
    assert result["matched_rule_ids"] == ["non_existent_rule"]
    assert result["subgraph_nodes"] == {}
    assert result["subgraph_edges"] == []

def test_extract_subgraph_empty_graph_safeguard(tmp_path):
    graph_file = tmp_path / "graph.json"
    with open(graph_file, "w", encoding="utf-8") as f:
        json.dump({}, f)  # Completely empty graph
        
    result = extract_subgraph(str(graph_file), ["rule_01"])
    assert result["subgraph_nodes"] == {}
    assert result["subgraph_edges"] == []

def test_write_azure_csv_persistence(tmp_path):
    test_cases = [
        {
            "title": "Validate Deduction Check",
            "category_id": "api_functional",
            "target_rule_id": "rule_01",
            "steps": [
                {"step_number": 1, "action": "Setup test data", "expected": "Data is prepared"},
                {"step_number": 2, "action": "Submit claim request", "expected": "Verify rejection"}
            ]
        }
    ]
    test_cases_json = json.dumps(test_cases)
    output_file = tmp_path / "sub_dir" / "test_cases.csv"
    
    # Act
    result = write_azure_csv(test_cases_json, str(output_file), "TechInsurance\\Claims")
    
    # Assert
    assert result["success"] is True
    assert result["rows_written"] == 2  # 2 steps
    assert os.path.exists(output_file)
    
    with open(output_file, "r", encoding="utf-8") as f:
        content = f.read()
    assert "Validate Deduction Check" in content
    assert "Setup test data" in content
    assert "TechInsurance\\Claims" in content

def test_write_azure_csv_malformed_json(tmp_path):
    output_file = tmp_path / "test_cases.csv"
    with pytest.raises(ValueError, match="Malformed test cases JSON"):
        write_azure_csv("invalid_json_string{", str(output_file), "TechInsurance")

def test_write_coverage_report_persistence(tmp_path):
    markdown_content = "# Test Coverage & Traceability Report\n- Coverage: 100%"
    output_file = tmp_path / "sub_dir" / "coverage_report.md"

    # Act
    result = write_coverage_report(markdown_content, str(output_file))

    # Assert
    assert result["success"] is True
    assert os.path.exists(output_file)

    with open(output_file, "r", encoding="utf-8") as f:
        content = f.read()
    assert markdown_content in content


# ---------------------------------------------------------------------------
# Bidirectional BFS traversal (Issue 5 fix)
# ---------------------------------------------------------------------------

def test_extract_subgraph_bidirectional_traversal(tmp_path):
    """
    A scenario node that has an edge pointing *to* a seed rule (reverse direction)
    must be discovered when bidirectional=True (the default).

    Graph:   scenario_01  --TRACES_TO-->  rule_01
    Seed:    rule_01
    Expected: scenario_01 is returned because it traces to rule_01 (reverse edge).
    """
    graph_data = {
        "nodes": [
            {"id": "rule_01",    "type": "business_rule", "name": "Claims Rule"},
            {"id": "scenario_01","type": "test_scenario",  "name": "BDD: Deny inactive"},
            {"id": "api_01",     "type": "api_endpoint",   "name": "POST /claims"},
        ],
        "links": [
            # scenario points TO rule (reverse direction relative to seed)
            {"source": "scenario_01", "target": "rule_01", "relationship": "TRACES_TO"},
            # rule points TO api (forward direction)
            {"source": "rule_01",     "target": "api_01",  "relationship": "MAPS_TO"},
        ]
    }
    graph_file = tmp_path / "graph.json"
    with open(graph_file, "w", encoding="utf-8") as f:
        json.dump(graph_data, f)

    # Act: seed from rule_01 with default bidirectional=True
    result = extract_subgraph(str(graph_file), ["rule_01"])

    nodes = result["subgraph_nodes"]
    # scenario_01 must be discovered via reverse TRACES_TO edge
    assert "scenario_01" in nodes, "Reverse-edge node not found — bidirectional BFS broken"
    # api_01 must be discovered via forward MAPS_TO edge
    assert "api_01" in nodes,      "Forward-edge node not found"
    assert "rule_01" in nodes,     "Seed rule itself not returned"


def test_extract_subgraph_unidirectional_excludes_reverse(tmp_path):
    """
    With bidirectional=False (legacy mode), a scenario that points TO the seed
    rule must NOT be returned — only nodes reachable via outgoing edges are found.
    """
    graph_data = {
        "nodes": [
            {"id": "rule_01",    "type": "business_rule"},
            {"id": "scenario_01","type": "test_scenario"},
        ],
        "links": [
            {"source": "scenario_01", "target": "rule_01", "relationship": "TRACES_TO"}
        ]
    }
    graph_file = tmp_path / "graph.json"
    with open(graph_file, "w", encoding="utf-8") as f:
        json.dump(graph_data, f)

    result = extract_subgraph(str(graph_file), ["rule_01"], bidirectional=False)

    nodes = result["subgraph_nodes"]
    assert "rule_01"     in nodes
    assert "scenario_01" not in nodes, "Reverse node should be absent in unidirectional mode"


# ---------------------------------------------------------------------------
# validate_graph MCP tool (Issue 1 fix)
# ---------------------------------------------------------------------------

def test_validate_graph_valid_graph(tmp_path):
    """validate_graph returns is_valid=True for a well-formed graph."""
    graph_data = {
        "nodes": [
            {"id": "rule_001", "type": "business_rule", "name": "Claims Rule"},
            {"id": "scen_001", "type": "test_scenario",  "name": "BDD scenario"},
            {"id": "api_001",  "type": "api_endpoint",   "name": "POST /claims"},
        ],
        "links": [
            {"source": "rule_001", "target": "scen_001", "relationship": "TRACES_TO"},
            {"source": "rule_001", "target": "api_001",  "relationship": "MAPS_TO"},
        ]
    }
    graph_file = tmp_path / "graph.json"
    with open(graph_file, "w", encoding="utf-8") as f:
        json.dump(graph_data, f)

    result = validate_graph(str(graph_file))

    assert result["is_valid"] is True
    assert result["errors"] == []
    assert result["stats"]["business_rule_count"] == 1
    assert result["stats"]["traversable_edge_count"] == 2


def test_validate_graph_missing_file():
    """validate_graph returns is_valid=False when the file does not exist."""
    result = validate_graph("/does/not/exist/graph.json")

    assert result["is_valid"] is False
    assert len(result["errors"]) >= 1
    assert any("not found" in e.lower() or "file" in e.lower() for e in result["errors"])


def test_validate_graph_no_business_rules(tmp_path):
    """validate_graph returns is_valid=False when no business_rule nodes exist."""
    graph_data = {
        "nodes": [
            {"id": "scen_001", "type": "test_scenario", "name": "Orphan scenario"}
        ],
        "links": []
    }
    graph_file = tmp_path / "graph.json"
    with open(graph_file, "w", encoding="utf-8") as f:
        json.dump(graph_data, f)

    result = validate_graph(str(graph_file))

    assert result["is_valid"] is False
    assert any("business_rule" in e for e in result["errors"])


# ---------------------------------------------------------------------------
# SPEC-5 Wave 1 tests
# ---------------------------------------------------------------------------

def _write_graph(tmp_path, body):
    path = tmp_path / "graph.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(body, f)
    return str(path)


def test_list_features_with_area_paths_derives_area_path(tmp_path):
    """SPEC-5 Wave 1 §1.1: feature.area_path = parent_product_name\\feature_name."""
    body = {
        "nodes": [
            {"id": "feature_resume_app", "type": "product_feature",
             "name": "Resume Application",
             "metadata": {"parent_product_name": "Click 2 Protect Supreme Plus"}},
            {"id": "rule_dob", "type": "eligibility_rule", "name": "DOB Age",
             "metadata": {"parent_feature_id": "feature_resume_app"}},
            {"id": "scen_01", "type": "test_scenario", "name": "DOB scenario"},
        ],
        "edges": [
            {"source_id": "scen_01", "target_id": "rule_dob", "relationship": "TESTS"},
        ],
    }
    path = _write_graph(tmp_path, body)
    out = list_features_with_area_paths(path)
    feats = out["features"]
    assert len(feats) == 1
    f = feats[0]
    assert f["id"] == "feature_resume_app"
    assert f["area_path"] == "Click 2 Protect Supreme Plus\\Resume Application"
    assert f["rule_count"] == 1
    assert f["tests_edge_count"] == 1


def test_list_rules_with_test_coverage_includes_counts_and_anomalies(tmp_path):
    """SPEC-5 Wave 1 §1.1: rule index carries TESTS/IMPLEMENTS/VALIDATES counts
    plus anomaly_flags from metadata.behavioral_anomalies."""
    body = {
        "nodes": [
            {"id": "rule_a", "type": "business_rule", "name": "Rule A",
             "description": "Some rule",
             "metadata": {
                 "parent_feature_id": "feature_x",
                 "rule_origin": "functional",
                 "behavioral_anomalies": [
                     {"anomaly_kind": "rule_without_implementation", "severity": "warning"},
                 ],
             }},
            {"id": "rule_b", "type": "validation_rule", "name": "Rule B",
             "description": "", "metadata": {}},
            {"id": "ep_x", "type": "api_endpoint", "name": "POST /x", "metadata": {}},
            {"id": "scen_x", "type": "test_scenario", "name": "Scenario X", "metadata": {}},
        ],
        "edges": [
            {"source_id": "scen_x", "target_id": "rule_a", "relationship": "TESTS"},
            {"source_id": "ep_x", "target_id": "rule_b", "relationship": "IMPLEMENTS"},
        ],
    }
    path = _write_graph(tmp_path, body)
    out = list_rules_with_test_coverage(path)
    rules_by_id = {r["id"]: r for r in out["rules"]}
    assert rules_by_id["rule_a"]["tests_edge_count"] == 1
    assert rules_by_id["rule_a"]["implements_edge_count"] == 0
    assert "rule_without_implementation" in rules_by_id["rule_a"]["anomaly_flags"]
    assert rules_by_id["rule_b"]["implements_edge_count"] == 1
    assert rules_by_id["rule_b"]["tests_edge_count"] == 0


def test_extract_subgraph_extended_context_returns_linked_payloads(tmp_path):
    """SPEC-5 Wave 1 §1.6: include_extended_context=True surfaces linked
    rule_constants, field_specs, test_scenarios, ui_elements, and anomalies."""
    body = {
        "nodes": [
            {"id": "rule_dob", "type": "eligibility_rule", "name": "DOB",
             "metadata": {
                 "parent_feature_id": "feature_x",
                 "behavioral_anomalies": [
                     {"anomaly_kind": "constant_spec_divergence", "severity": "critical"},
                 ],
             }},
            {"id": "rule_constant_dob_min_age", "type": "rule_constant",
             "name": "dob.min.age", "metadata": {"value": 18}},
            {"id": "field_spec_dob", "type": "field_specification",
             "name": "DOB Field", "metadata": {}},
            {"id": "scen_dob", "type": "test_scenario",
             "name": "DOB scenario", "metadata": {}},
            {"id": "ui_element_dob_input", "type": "ui_element",
             "name": "DOB input", "metadata": {}},
        ],
        "edges": [
            {"source_id": "rule_constant_dob_min_age", "target_id": "rule_dob",
             "relationship": "MAPS_TO"},
            {"source_id": "field_spec_dob", "target_id": "rule_dob",
             "relationship": "MAPS_TO"},
            {"source_id": "scen_dob", "target_id": "rule_dob",
             "relationship": "TESTS"},
            {"source_id": "ui_element_dob_input", "target_id": "rule_dob",
             "relationship": "VALIDATES"},
        ],
    }
    path = _write_graph(tmp_path, body)
    out = extract_subgraph(path, ["rule_dob"], include_extended_context=True)
    assert any(n["id"] == "rule_constant_dob_min_age" for n in out["linked_rule_constants"])
    assert any(n["id"] == "field_spec_dob" for n in out["linked_field_specs"])
    assert any(n["id"] == "scen_dob" for n in out["linked_test_scenarios"])
    assert any(n["id"] == "ui_element_dob_input" for n in out["linked_ui_elements"])
    assert out["target_feature_id"] == "feature_x"
    assert len(out["behavioral_anomalies"]) == 1
    assert out["behavioral_anomalies"][0]["anomaly_kind"] == "constant_spec_divergence"


def test_extract_subgraph_default_omits_extended_context(tmp_path):
    """Back-compat: include_extended_context defaults to False — output is the
    original shape with no linked_* / behavioral_anomalies / target_feature_id."""
    body = {
        "nodes": [{"id": "rule_a", "type": "business_rule", "name": "A"}],
        "edges": [],
    }
    path = _write_graph(tmp_path, body)
    out = extract_subgraph(path, ["rule_a"])
    assert set(out.keys()) == {
        "matched_rule_ids", "subgraph_nodes", "subgraph_edges",
    }


# ---------------------------------------------------------------------------
# SPEC-5 Wave 2 tests
# ---------------------------------------------------------------------------

def test_get_rule_constraints_returns_constraints_and_maps_to_peers(tmp_path):
    """SPEC-5 Wave 2 §2.1: per-rule constraints + MAPS_TO peer rules."""
    body = {
        "nodes": [
            {"id": "rule_dob_format", "type": "validation_rule", "name": "DOB format",
             "metadata": {
                 "constraints": {"operator": "matches", "pattern": "DD/MM/YYYY",
                                 "subject_field": "date_of_birth"},
                 "verbatim_error_message": "Please enter a valid date of birth",
             }},
            {"id": "rule_dob_not_future", "type": "validation_rule", "name": "DOB not future",
             "metadata": {
                 "constraints": {"operator": "lt", "value": "<today>",
                                 "subject_field": "date_of_birth"},
                 "verbatim_error_message": "DOB cannot be a future date",
             }},
            {"id": "rule_unrelated", "type": "eligibility_rule", "name": "Unrelated rule",
             "metadata": {}},
        ],
        "edges": [
            {"source_id": "rule_dob_format", "target_id": "rule_dob_not_future",
             "relationship": "MAPS_TO"},
        ],
    }
    path = _write_graph(tmp_path, body)
    out = get_rule_constraints_for_test_validation(path, ["rule_dob_format"])
    rules = out["rules"]
    assert len(rules) == 1
    entry = rules[0]
    assert entry["rule_id"] == "rule_dob_format"
    assert entry["constraints"]["pattern"] == "DD/MM/YYYY"
    assert entry["verbatim_error_message"] == "Please enter a valid date of birth"
    peer_ids = {p["rule_id"] for p in entry["maps_to_peers"]}
    assert peer_ids == {"rule_dob_not_future"}


def test_get_anomalies_for_features_aggregates_by_feature_and_severity(tmp_path):
    """SPEC-5 Wave 2 §2.2: per-feature filter + by_severity counters."""
    body = {
        "nodes": [
            {"id": "rule_a", "type": "business_rule", "name": "Rule A",
             "metadata": {
                 "parent_feature_id": "feature_x",
                 "behavioral_anomalies": [
                     {"anomaly_kind": "rule_without_implementation", "severity": "warning",
                      "evidence": {"summary": "no impl edges"}},
                     {"anomaly_kind": "constant_spec_divergence", "severity": "critical",
                      "evidence": {"summary": "constant differs"}},
                 ],
             }},
            {"id": "rule_b", "type": "business_rule", "name": "Rule B",
             "metadata": {
                 "parent_feature_id": "feature_other",
                 "behavioral_anomalies": [
                     {"anomaly_kind": "rule_without_implementation", "severity": "info",
                      "evidence": {"summary": "in other feature"}},
                 ],
             }},
        ],
        "edges": [],
    }
    path = _write_graph(tmp_path, body)
    out = get_anomalies_for_features(path, ["feature_x"])
    assert out["total"] == 2
    assert out["by_severity"]["warning"] == 1
    assert out["by_severity"]["critical"] == 1
    assert out["by_severity"].get("info", 0) == 0
    assert "feature_x" in out["by_feature"]
    assert "feature_other" not in out["by_feature"]
    kinds = out["by_feature"]["feature_x"]
    assert len(kinds["rule_without_implementation"]) == 1
    assert len(kinds["constant_spec_divergence"]) == 1


def test_get_anomalies_for_features_empty_when_no_anomalies(tmp_path):
    body = {
        "nodes": [
            {"id": "rule_clean", "type": "business_rule", "name": "Clean",
             "metadata": {"parent_feature_id": "feature_x"}},
        ],
        "edges": [],
    }
    path = _write_graph(tmp_path, body)
    out = get_anomalies_for_features(path, ["feature_x"])
    assert out["total"] == 0
    assert out["by_severity"]["critical"] == 0
    assert out["by_feature"] == {"feature_x": {}}
