"""
Unit tests for engine.graph_validator.validate_graph_structure().

Coverage map
------------
test_valid_graph_passes                     – happy path, full valid graph
test_valid_graph_dict_nodes_format          – nodes as a dict (alternative format)
test_valid_graph_edge_alias_fields          – source_id / target_id / type aliases
test_missing_file_returns_error             – file not found
test_invalid_json_returns_error             – malformed JSON
test_missing_nodes_key_returns_error        – no 'nodes' key
test_nodes_wrong_type_returns_error         – nodes is a scalar
test_no_business_rules_returns_error        – no business_rule type nodes
test_nodes_missing_id_returns_error         – nodes with no id/rule_id
test_dangling_edge_reference_returns_error  – edge points to unknown node ID
test_edge_missing_source_returns_error      – edge has no source
test_edge_missing_target_returns_error      – edge has no target
test_edge_missing_relationship_returns_error – edge has no relationship/type
test_warning_no_scenario_nodes              – warning when no test_scenario nodes
test_warning_no_api_endpoint_nodes          – warning when no api_endpoint nodes
test_warning_no_traversable_edges           – warning when edges present but none traversable
test_valid_graph_with_all_warnings_suppressed – no warnings on a fully specified graph
test_stats_are_correct                      – stats dict reflects actual counts
test_empty_nodes_list_returns_error         – empty nodes list → no business rules
"""

import json
import pytest
from engine.graph_validator import validate_graph_structure, ValidationResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def write_graph(tmp_path):
    """Returns a helper that serialises *data* to graph.json and returns the path."""
    def _write(data: dict) -> str:
        p = tmp_path / "graph.json"
        p.write_text(json.dumps(data), encoding='utf-8')
        return str(p)
    return _write


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------

def test_valid_graph_passes(write_graph):
    path = write_graph({
        "nodes": [
            {"id": "rule_001", "type": "business_rule", "name": "Claim Denial Rule"},
            {"id": "scen_001", "type": "test_scenario",  "name": "Deny inactive coverage"},
            {"id": "api_001",  "type": "api_endpoint",   "name": "POST /claims"},
            {"id": "dm_001",   "type": "data_model",     "name": "ClaimPayload"},
        ],
        "links": [
            {"source": "rule_001", "target": "scen_001", "relationship": "TRACES_TO"},
            {"source": "rule_001", "target": "api_001",  "relationship": "MAPS_TO"},
            {"source": "rule_001", "target": "dm_001",   "relationship": "DECLARES"},
        ]
    })
    result = validate_graph_structure(path)

    assert result.is_valid is True
    assert result.errors == []
    assert result.warnings == []
    assert result.stats["business_rule_count"] == 1
    assert result.stats["traversable_edge_count"] == 3
    assert result.stats["total_nodes"] == 4


def test_valid_graph_with_context_builder_envelope_passes(write_graph):
    """graph.json written by LocalJsonStore (schema envelope) must unwrap and validate.

    Context Builder wraps every write in:
        { schema_version, generator, written_at, payload: { directed, nodes, edges, ... } }
    NetworkX node_link_data() uses the key 'edges' (not 'links') when called with
    edges='edges'.  This test exercises both the envelope unwrap and the edges-key alias.
    """
    path = write_graph({
        "schema_version": 1,
        "generator": "context_builder",
        "written_at": "2025-01-01T00:00:00+00:00",
        "payload": {
            "directed": True,
            "multigraph": False,
            "graph": {},
            "nodes": [
                {"id": "rule_001", "type": "business_rule", "name": "Age Limit Rule"},
                {"id": "scen_001", "type": "test_scenario",  "name": "Under-age scenario"},
                {"id": "api_001",  "type": "api_endpoint",   "name": "POST /policy"},
            ],
            "edges": [
                {"source": "rule_001", "target": "scen_001", "relationship": "TRACES_TO"},
                {"source": "rule_001", "target": "api_001",  "relationship": "MAPS_TO"},
            ],
        },
    })
    result = validate_graph_structure(path)

    assert result.is_valid is True
    assert result.errors == []
    assert result.warnings == []
    assert result.stats["total_nodes"] == 3
    assert result.stats["business_rule_count"] == 1
    assert result.stats["traversable_edge_count"] == 2


def test_valid_graph_dict_nodes_format(write_graph):
    """Nodes expressed as a dict keyed by node-ID (alternative Context Builder format)."""
    path = write_graph({
        "nodes": {
            "rule_001": {"id": "rule_001", "type": "business_rule", "name": "Rule A"},
            "scen_001": {"id": "scen_001", "type": "test_scenario",  "name": "Scen A"},
        },
        "links": [
            {"source": "rule_001", "target": "scen_001", "relationship": "TRACES_TO"}
        ]
    })
    result = validate_graph_structure(path)

    assert result.is_valid is True
    assert result.stats["business_rule_count"] == 1
    assert result.stats["total_nodes"] == 2


def test_valid_graph_edge_alias_fields(write_graph):
    """Edges using source_id / target_id / type (alias field names)."""
    path = write_graph({
        "nodes": [
            {"id": "rule_001", "type": "business_rule"},
            {"id": "api_001",  "type": "api_endpoint"},
        ],
        "links": [
            {"source_id": "rule_001", "target_id": "api_001", "type": "MAPS_TO"}
        ]
    })
    result = validate_graph_structure(path)

    assert result.is_valid is True
    assert result.stats["traversable_edge_count"] == 1


# ---------------------------------------------------------------------------
# Error: file-system
# ---------------------------------------------------------------------------

def test_missing_file_returns_error():
    result = validate_graph_structure("/nonexistent/path/to/graph.json")

    assert result.is_valid is False
    assert len(result.errors) >= 1
    assert any("not found" in e.lower() or "file" in e.lower() for e in result.errors)


def test_invalid_json_returns_error(tmp_path):
    bad_file = tmp_path / "graph.json"
    bad_file.write_text("{not valid json", encoding='utf-8')
    result = validate_graph_structure(str(bad_file))

    assert result.is_valid is False
    assert any("json" in e.lower() for e in result.errors)


# ---------------------------------------------------------------------------
# Error: structural / schema
# ---------------------------------------------------------------------------

def test_missing_nodes_key_returns_error(write_graph):
    path = write_graph({"entities": [], "links": []})
    result = validate_graph_structure(path)

    assert result.is_valid is False
    assert any("'nodes'" in e for e in result.errors)


def test_nodes_wrong_type_returns_error(write_graph):
    path = write_graph({"nodes": "not_a_list_or_dict", "links": []})
    result = validate_graph_structure(path)

    assert result.is_valid is False
    assert any("nodes" in e.lower() for e in result.errors)


def test_no_business_rules_returns_error(write_graph):
    path = write_graph({
        "nodes": [
            {"id": "scen_001", "type": "test_scenario", "name": "Some scenario"}
        ],
        "links": []
    })
    result = validate_graph_structure(path)

    assert result.is_valid is False
    assert any("business_rule" in e for e in result.errors)


def test_empty_nodes_list_returns_error(write_graph):
    """An empty nodes array has no business rules → should fail."""
    path = write_graph({"nodes": [], "links": []})
    result = validate_graph_structure(path)

    assert result.is_valid is False
    assert any("business_rule" in e for e in result.errors)


def test_nodes_missing_id_returns_error(write_graph):
    """Nodes that lack both 'id' and 'rule_id' cannot be looked up by BFS."""
    path = write_graph({
        "nodes": [
            {"type": "business_rule", "name": "No ID node"},  # missing id
            {"id": "rule_ok", "type": "business_rule", "name": "Good node"},
        ],
        "links": []
    })
    result = validate_graph_structure(path)

    assert result.is_valid is False
    assert any("missing" in e.lower() and "id" in e.lower() for e in result.errors)


def test_dangling_edge_reference_returns_error(write_graph):
    """An edge whose target does not exist in nodes → dangling reference."""
    path = write_graph({
        "nodes": [
            {"id": "rule_001", "type": "business_rule"}
        ],
        "links": [
            {"source": "rule_001", "target": "ghost_node", "relationship": "TRACES_TO"}
        ]
    })
    result = validate_graph_structure(path)

    assert result.is_valid is False
    assert any("ghost_node" in e for e in result.errors)


def test_edge_missing_source_returns_error(write_graph):
    path = write_graph({
        "nodes": [
            {"id": "rule_001", "type": "business_rule"},
            {"id": "scen_001", "type": "test_scenario"},
        ],
        "links": [
            {"target": "scen_001", "relationship": "TRACES_TO"}  # no source
        ]
    })
    result = validate_graph_structure(path)

    assert result.is_valid is False
    assert any("source" in e.lower() for e in result.errors)


def test_edge_missing_target_returns_error(write_graph):
    path = write_graph({
        "nodes": [
            {"id": "rule_001", "type": "business_rule"},
        ],
        "links": [
            {"source": "rule_001", "relationship": "MAPS_TO"}  # no target
        ]
    })
    result = validate_graph_structure(path)

    assert result.is_valid is False
    assert any("target" in e.lower() for e in result.errors)


def test_edge_missing_relationship_returns_error(write_graph):
    path = write_graph({
        "nodes": [
            {"id": "rule_001", "type": "business_rule"},
            {"id": "scen_001", "type": "test_scenario"},
        ],
        "links": [
            {"source": "rule_001", "target": "scen_001"}  # no relationship/type
        ]
    })
    result = validate_graph_structure(path)

    assert result.is_valid is False
    assert any("relationship" in e.lower() or "type" in e.lower() for e in result.errors)


# ---------------------------------------------------------------------------
# Warning tests (is_valid=True but warnings present)
# ---------------------------------------------------------------------------

def test_warning_no_scenario_nodes(write_graph):
    path = write_graph({
        "nodes": [
            {"id": "rule_001", "type": "business_rule"}
        ],
        "links": []
    })
    result = validate_graph_structure(path)

    assert result.is_valid is True
    assert any("test_scenario" in w for w in result.warnings)


def test_warning_no_api_endpoint_nodes(write_graph):
    path = write_graph({
        "nodes": [
            {"id": "rule_001", "type": "business_rule"},
            {"id": "scen_001", "type": "test_scenario"},
        ],
        "links": []
    })
    result = validate_graph_structure(path)

    assert result.is_valid is True
    assert any("api_endpoint" in w for w in result.warnings)


def test_warning_no_traversable_edges(write_graph):
    """Edges exist but none use TRACES_TO / MAPS_TO / DECLARES."""
    path = write_graph({
        "nodes": [
            {"id": "rule_001", "type": "business_rule"},
            {"id": "scen_001", "type": "test_scenario"},
        ],
        "links": [
            {"source": "rule_001", "target": "scen_001", "relationship": "UNKNOWN_REL"}
        ]
    })
    result = validate_graph_structure(path)

    assert result.is_valid is True
    assert any("traversable" in w.lower() for w in result.warnings)


def test_valid_graph_with_all_warnings_suppressed(write_graph):
    """A graph with business_rule, test_scenario, api_endpoint and traversable edges
    should pass with zero warnings."""
    path = write_graph({
        "nodes": [
            {"id": "rule_001", "type": "business_rule"},
            {"id": "scen_001", "type": "test_scenario"},
            {"id": "api_001",  "type": "api_endpoint"},
        ],
        "links": [
            {"source": "rule_001", "target": "scen_001", "relationship": "TRACES_TO"},
            {"source": "rule_001", "target": "api_001",  "relationship": "MAPS_TO"},
        ]
    })
    result = validate_graph_structure(path)

    assert result.is_valid is True
    assert result.warnings == []


# ---------------------------------------------------------------------------
# Stats accuracy
# ---------------------------------------------------------------------------

def test_stats_are_correct(write_graph):
    path = write_graph({
        "nodes": [
            {"id": "rule_001", "type": "business_rule"},
            {"id": "rule_002", "type": "business_rule"},
            {"id": "scen_001", "type": "test_scenario"},
            {"id": "api_001",  "type": "api_endpoint"},
        ],
        "links": [
            {"source": "rule_001", "target": "scen_001", "relationship": "TRACES_TO"},
            {"source": "rule_002", "target": "api_001",  "relationship": "MAPS_TO"},
            {"source": "rule_001", "target": "api_001",  "relationship": "UNKNOWN"},  # non-traversable
        ]
    })
    result = validate_graph_structure(path)

    assert result.stats["total_nodes"] == 4
    assert result.stats["business_rule_count"] == 2
    assert result.stats["edge_count"] == 3
    assert result.stats["traversable_edge_count"] == 2
    assert result.stats["node_types"]["business_rule"] == 2
    assert result.stats["node_types"]["test_scenario"] == 1
    assert result.stats["node_types"]["api_endpoint"] == 1
