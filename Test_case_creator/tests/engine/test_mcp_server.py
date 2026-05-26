import os
import json
import pytest
from engine.mcp_server import extract_subgraph, write_azure_csv, write_coverage_report, validate_graph

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
