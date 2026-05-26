import json
import os
from mcp.server.fastmcp import FastMCP
from engine.csv_writer import serialize
from engine.graph_validator import validate_graph_structure

# Expose FastMCP server
mcp = FastMCP("test-case-creator-engine")

@mcp.tool()
def validate_graph(graph_path: str) -> dict:
    """
    Validates the structural integrity of a graph.json file before it is
    used by the test-generation pipeline.

    Checks performed:
    - File exists and is readable.
    - Content is valid JSON.
    - Top-level 'nodes' key is present and is a list or dict.
    - At least one node of type 'business_rule' exists.
    - Every node has an 'id' or 'rule_id' field.
    - Every edge has source, target, and relationship fields.
    - Edge endpoints reference node IDs that exist in 'nodes'.

    Returns a result dict with:
      is_valid   (bool)       – True iff no blocking errors.
      errors     (list[str])  – Blocking issues; pipeline must halt.
      warnings   (list[str])  – Non-blocking advisories.
      stats      (dict)       – Node/edge counts for display.
    """
    result = validate_graph_structure(graph_path)
    return {
        'is_valid': result.is_valid,
        'errors':   result.errors,
        'warnings': result.warnings,
        'stats':    result.stats,
    }


def _load_graph(graph_path: str) -> tuple[dict, list]:
    """Load a context_builder graph.json, unwrap the LocalJsonStore envelope,
    and return (nodes_dict_by_id, edges_list)."""
    if not os.path.exists(graph_path):
        raise FileNotFoundError(f"Context graph not found at: {graph_path}")
    with open(graph_path, "r", encoding="utf-8") as f:
        graph = json.load(f)
    if isinstance(graph, dict) and "schema_version" in graph and "payload" in graph:
        inner = graph.get("payload", {})
        if isinstance(inner, dict):
            graph = inner
    raw_nodes = graph.get("nodes", [])
    edges = graph.get("edges", []) or graph.get("links", [])
    nodes_dict: dict = {}
    if isinstance(raw_nodes, list):
        for node in raw_nodes:
            if isinstance(node, dict):
                node_id = node.get("id") or node.get("rule_id")
                if node_id:
                    nodes_dict[node_id] = node
    elif isinstance(raw_nodes, dict):
        nodes_dict = raw_nodes
    return nodes_dict, edges


# SPEC-5 Wave 1: rule-family types the analyzer surfaces.
_RULE_FAMILY = {
    "business_rule", "validation_rule", "eligibility_rule",
    "ui_business_rule", "security_rule",
}


@mcp.tool()
def list_features_with_area_paths(graph_path: str) -> dict:
    """SPEC-5 Wave 1: return all product_feature nodes with their derived
    Azure DevOps area paths.

    area_path is derived from metadata.parent_product_name (set by
    rule_extractor) concatenated with the feature name via backslash.
    Returns counts of associated rules and TESTS edges.
    """
    nodes, edges = _load_graph(graph_path)

    # Count rules per feature_id via metadata.parent_feature_id
    rules_by_feature: dict[str, int] = {}
    for node in nodes.values():
        if node.get("type") in _RULE_FAMILY:
            parent = node.get("metadata", {}).get("parent_feature_id")
            if parent:
                rules_by_feature[parent] = rules_by_feature.get(parent, 0) + 1

    # Count TESTS edges per feature_id (edges pointing into any rule whose
    # parent_feature_id matches this feature).
    rule_to_feature: dict[str, str] = {}
    for nid, node in nodes.items():
        if node.get("type") in _RULE_FAMILY:
            parent = node.get("metadata", {}).get("parent_feature_id")
            if parent:
                rule_to_feature[nid] = parent
    tests_by_feature: dict[str, int] = {}
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        rel = (edge.get("relationship") or edge.get("type") or "").upper()
        if rel != "TESTS":
            continue
        tgt = edge.get("target") or edge.get("target_id")
        feature = rule_to_feature.get(tgt)
        if feature:
            tests_by_feature[feature] = tests_by_feature.get(feature, 0) + 1

    features = []
    for nid, node in nodes.items():
        if node.get("type") != "product_feature":
            continue
        meta = node.get("metadata", {}) or {}
        parent_product = meta.get("parent_product_name") or ""
        name = node.get("name") or nid
        if parent_product:
            area_path = f"{parent_product}\\{name}"
        else:
            area_path = name
        features.append({
            "id": nid,
            "name": name,
            "area_path": area_path,
            "rule_count": rules_by_feature.get(nid, 0),
            "tests_edge_count": tests_by_feature.get(nid, 0),
        })

    return {"features": features}


@mcp.tool()
def list_rules_with_test_coverage(graph_path: str) -> dict:
    """SPEC-5 Wave 1: return all rule-family nodes with their TESTS/IMPLEMENTS/
    VALIDATES edge counts plus any behavioral_anomaly kinds recorded on the node.
    """
    nodes, edges = _load_graph(graph_path)

    in_counts: dict[str, dict[str, int]] = {}
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        rel = (edge.get("relationship") or edge.get("type") or "").upper()
        if rel not in {"TESTS", "IMPLEMENTS", "VALIDATES"}:
            continue
        tgt = edge.get("target") or edge.get("target_id")
        if not tgt:
            continue
        in_counts.setdefault(tgt, {"TESTS": 0, "IMPLEMENTS": 0, "VALIDATES": 0})
        in_counts[tgt][rel] = in_counts[tgt].get(rel, 0) + 1

    rules = []
    for nid, node in nodes.items():
        if node.get("type") not in _RULE_FAMILY:
            continue
        meta = node.get("metadata", {}) or {}
        counts = in_counts.get(nid, {"TESTS": 0, "IMPLEMENTS": 0, "VALIDATES": 0})
        anomalies = meta.get("behavioral_anomalies", []) or []
        anomaly_flags = sorted({
            a.get("anomaly_kind") for a in anomalies
            if isinstance(a, dict) and a.get("anomaly_kind")
        })
        rules.append({
            "id": nid,
            "name": node.get("name") or nid,
            "description": node.get("description") or "",
            "type": node.get("type"),
            "feature_id": meta.get("parent_feature_id"),
            "tests_edge_count": counts.get("TESTS", 0),
            "implements_edge_count": counts.get("IMPLEMENTS", 0),
            "validates_edge_count": counts.get("VALIDATES", 0),
            "anomaly_flags": list(anomaly_flags),
            "rule_origin": meta.get("rule_origin", "functional"),
        })

    return {"rules": rules}


@mcp.tool()
def extract_subgraph(
    graph_path: str,
    rule_ids: list[str],
    bidirectional: bool = True,
    include_extended_context: bool = False,
) -> dict:
    """
    Extracts the minimal connected subgraph reachable from the given rule_ids.

    By default (bidirectional=True) the BFS follows edges in **both** directions
    for traversable relationship types (TESTS, IMPLEMENTS, CALLS, REFERENCES,
    TRACES_TO, MAPS_TO, DECLARES):
    - Forward:  source_id == current_node  →  enqueue target_id
    - Reverse:  target_id == current_node  →  enqueue source_id

    This ensures that nodes which *point to* a seed rule (e.g. a BDD scenario
    tracing TO a business rule) are discovered along with nodes the rule points
    to.  Set bidirectional=False to restore the original outgoing-only behavior.

    Returns only the nodes and edges relevant to the requested rules.
    """
    nodes_dict, edges = _load_graph(graph_path)

    visited_ids = set()
    queue = list(rule_ids)
    result_nodes = {}
    # Include the relationship types actually produced by context_builder, plus the
    # legacy set kept for forward-compatibility with hand-authored graphs.
    TRAVERSABLE_EDGE_TYPES = {
        'TESTS', 'IMPLEMENTS', 'CALLS', 'REFERENCES',  # context_builder output
        'TRACES_TO', 'MAPS_TO', 'DECLARES',             # legacy / hand-authored
    }

    # 2. Graph Traversal (BFS — bidirectional by default)
    while queue:
        node_id = queue.pop(0)
        if node_id in visited_ids:
            continue

        visited_ids.add(node_id)
        node = nodes_dict.get(node_id)

        # Behavior: Silent skip on non-existent rule_id in the graph
        if not node:
            continue

        result_nodes[node_id] = node

        for edge in edges:
            if not isinstance(edge, dict):
                continue

            source_id    = edge.get('source')    or edge.get('source_id')
            target_id    = edge.get('target')    or edge.get('target_id')
            relationship = edge.get('relationship') or edge.get('type')

            if not (relationship and relationship.upper() in TRAVERSABLE_EDGE_TYPES):
                continue

            # Forward traversal: current node is the source → follow to target
            if source_id == node_id and target_id:
                queue.append(target_id)

            # Reverse traversal (bidirectional only): current node is the target
            # → follow back to source so nodes that point *to* this rule are found
            if bidirectional and target_id == node_id and source_id:
                queue.append(source_id)

    # 3. Compile connected edge slice
    subgraph_edges = []
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        source_id = edge.get('source') or edge.get('source_id')
        target_id = edge.get('target') or edge.get('target_id')
        if source_id in visited_ids and target_id in visited_ids:
            subgraph_edges.append(edge)

    base = {
        'matched_rule_ids': rule_ids,
        'subgraph_nodes':   result_nodes,
        'subgraph_edges':   subgraph_edges,
    }

    if not include_extended_context:
        return base

    # SPEC-5 Wave 1: extended context for the rewritten test_case_analyst
    # (Phase -1). Walks rule-family seed nodes outward and collects:
    #   - linked_rule_constants   (MAPS_TO from rule_constant → seed)
    #   - linked_field_specs      (MAPS_TO from field_specification → seed)
    #   - linked_test_scenarios   (TESTS from test_scenario → seed)
    #   - linked_ui_elements      (VALIDATES/MAPS_TO/REFERENCES from ui_element → seed)
    #   - behavioral_anomalies    (anomaly records on seed nodes)
    #   - target_feature_id       (dominant product_feature_id across seeds)
    seed_ids = set(rule_ids)
    linked_rule_constants: list[dict] = []
    linked_field_specs: list[dict] = []
    linked_test_scenarios: list[dict] = []
    linked_ui_elements: list[dict] = []
    seen_added: set[str] = set()

    def _add_node(target_list: list, node: dict) -> None:
        nid = node.get("id") or node.get("rule_id")
        key = (id(target_list), nid)
        if key in seen_added:
            return
        seen_added.add(key)
        target_list.append(node)

    for edge in edges:
        if not isinstance(edge, dict):
            continue
        rel = (edge.get("relationship") or edge.get("type") or "").upper()
        src = edge.get("source") or edge.get("source_id")
        tgt = edge.get("target") or edge.get("target_id")
        if tgt not in seed_ids and src not in seed_ids:
            continue
        # The interesting direction is <other_node> → seed.
        other_id = src if tgt in seed_ids else tgt
        other = nodes_dict.get(other_id)
        if not other:
            continue
        other_type = other.get("type")
        if rel == "MAPS_TO" and other_type == "rule_constant":
            _add_node(linked_rule_constants, other)
        elif rel == "MAPS_TO" and other_type == "field_specification":
            _add_node(linked_field_specs, other)
        elif rel == "TESTS" and other_type == "test_scenario":
            _add_node(linked_test_scenarios, other)
        elif rel in {"VALIDATES", "MAPS_TO", "REFERENCES"} and other_type == "ui_element":
            _add_node(linked_ui_elements, other)

    behavioral_anomalies: list[dict] = []
    for rid in rule_ids:
        node = nodes_dict.get(rid)
        if not node:
            continue
        for record in node.get("metadata", {}).get("behavioral_anomalies", []) or []:
            behavioral_anomalies.append({"node_id": rid, **record})

    # Dominant parent_feature_id across the seeds (mode).
    feature_counts: dict[str, int] = {}
    for rid in rule_ids:
        node = nodes_dict.get(rid)
        if not node:
            continue
        pf = node.get("metadata", {}).get("parent_feature_id")
        if pf:
            feature_counts[pf] = feature_counts.get(pf, 0) + 1
    target_feature_id = (
        max(feature_counts, key=feature_counts.get) if feature_counts else None
    )

    base.update({
        "linked_rule_constants": linked_rule_constants,
        "linked_field_specs": linked_field_specs,
        "linked_test_scenarios": linked_test_scenarios,
        "linked_ui_elements": linked_ui_elements,
        "behavioral_anomalies": behavioral_anomalies,
        "target_feature_id": target_feature_id,
    })
    return base


# SPEC-5 Wave 2: structured constraints for verifier Point 8 (input ambiguity).
@mcp.tool()
def get_rule_constraints_for_test_validation(
    graph_path: str,
    rule_ids: list[str],
) -> dict:
    """Return structured constraints for each rule + its MAPS_TO peers.

    The test_verifier uses this to check whether a single test input violates
    multiple rule predicates simultaneously (H7: 2030-01-01 violates both
    format and future-date rules).
    """
    nodes, edges = _load_graph(graph_path)

    # Index MAPS_TO peers: rule_id → list of peer rule_ids reachable via MAPS_TO
    # edges (in either direction) whose other endpoint is also a rule.
    peers_by_rule: dict[str, set[str]] = {rid: set() for rid in rule_ids}
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        if (edge.get("relationship") or edge.get("type") or "").upper() != "MAPS_TO":
            continue
        src = edge.get("source") or edge.get("source_id")
        tgt = edge.get("target") or edge.get("target_id")
        for endpoint, other in ((src, tgt), (tgt, src)):
            if endpoint in peers_by_rule:
                other_node = nodes.get(other)
                if other_node and other_node.get("type") in _RULE_FAMILY:
                    if other != endpoint:
                        peers_by_rule[endpoint].add(other)

    def _rule_entry(rule_id: str) -> dict:
        node = nodes.get(rule_id, {})
        meta = node.get("metadata", {}) or {}
        return {
            "rule_id": rule_id,
            "constraints": meta.get("constraints", {}) or {},
            "verbatim_error_message": meta.get("verbatim_error_message"),
        }

    result = []
    for rid in rule_ids:
        entry = _rule_entry(rid)
        entry["maps_to_peers"] = [
            _rule_entry(peer) for peer in sorted(peers_by_rule.get(rid, set()))
        ]
        result.append(entry)

    return {"rules": result}


# SPEC-5 Wave 2: per-feature anomaly aggregation for the coverage_reporter.
@mcp.tool()
def get_anomalies_for_features(
    graph_path: str,
    feature_ids: list[str],
) -> dict:
    """Return behavioral_anomalies for rules belonging to the given features.

    Aggregates anomaly records carried on rule-family nodes whose
    metadata.parent_feature_id matches one of the supplied feature_ids.
    Wraps the same shape get_behavioral_drift_report exposes, filtered.
    """
    nodes, _ = _load_graph(graph_path)
    target = set(feature_ids)

    by_feature: dict[str, dict[str, list]] = {fid: {} for fid in target}
    by_severity: dict[str, int] = {"critical": 0, "warning": 0, "info": 0}
    total = 0

    for nid, node in nodes.items():
        meta = node.get("metadata", {}) or {}
        pf = meta.get("parent_feature_id")
        if pf not in target:
            continue
        for record in meta.get("behavioral_anomalies", []) or []:
            if not isinstance(record, dict):
                continue
            kind = record.get("anomaly_kind", "legacy_requirement_drift")
            severity = record.get("severity", "info")
            entry = {
                "node_id": nid,
                "node_name": node.get("name"),
                "anomaly_kind": kind,
                "severity": severity,
                "detected_by": record.get("detected_by", "unknown"),
                "detected_at": record.get("detected_at"),
                "evidence": record.get("evidence", {}),
                "suggested_action": record.get("suggested_action"),
            }
            by_feature.setdefault(pf, {}).setdefault(kind, []).append(entry)
            if severity in by_severity:
                by_severity[severity] += 1
            else:
                by_severity[severity] = by_severity.get(severity, 0) + 1
            total += 1

    return {"by_feature": by_feature, "by_severity": by_severity, "total": total}


@mcp.tool()
def write_azure_csv(test_cases_json: str, output_path: str, area_path: str) -> dict:
    """
    Deserializes test_cases_json, calls csv_writer.py serialize to produce Azure DevOps CSV rows,
    and writes the result to output_path.
    Returns: { "success": True, "rows_written": int, "output_path": str }
    Raises: ValueError on malformed JSON; IOError on write failure.
    """
    try:
        test_cases = json.loads(test_cases_json)
    except Exception as e:
        raise ValueError(f"Malformed test cases JSON: {str(e)}")

    csv_content = serialize(test_cases, area_path)

    # Ensure directory exists
    if os.path.dirname(output_path):
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(csv_content)

    rows_count = len(csv_content.splitlines()) - 1  # Subtract header row
    return {
        "success":      True,
        "rows_written": rows_count,
        "output_path":  output_path,
    }


@mcp.tool()
def write_coverage_report(markdown_content: str, output_path: str) -> dict:
    """
    Writes markdown_content string to output_path as UTF-8.
    Returns: { "success": True, "output_path": str }
    Raises: IOError on write failure.
    """
    # Ensure directory exists
    if os.path.dirname(output_path):
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(markdown_content)

    return {
        "success":     True,
        "output_path": output_path,
    }


if __name__ == "__main__":
    mcp.run()
