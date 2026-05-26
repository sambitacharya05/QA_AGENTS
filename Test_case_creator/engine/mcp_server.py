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


@mcp.tool()
def extract_subgraph(
    graph_path: str,
    rule_ids: list[str],
    bidirectional: bool = True,
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
    # 1. Structural file exists pre-flight
    if not os.path.exists(graph_path):
        raise FileNotFoundError(f"Context graph not found at: {graph_path}")

    with open(graph_path, 'r', encoding='utf-8') as f:
        graph = json.load(f)

    # Unwrap the LocalJsonStore envelope if present.
    # Context Builder writes: { schema_version, generator, written_at, payload: {nodes, edges} }
    if isinstance(graph, dict) and "schema_version" in graph and "payload" in graph:
        inner = graph.get("payload", {})
        if isinstance(inner, dict):
            graph = inner

    # Safeguard: Default empty objects if nodes/edges keys are missing
    raw_nodes = graph.get('nodes', [])
    # NetworkX node_link_data(graph, edges="edges") writes the key as "edges";
    # fall back to "links" for graphs serialised by older NetworkX versions.
    edges = graph.get('edges', []) or graph.get('links', [])

    # Normalize nodes to a dictionary for fast lookup
    nodes_dict = {}
    if isinstance(raw_nodes, list):
        for node in raw_nodes:
            if isinstance(node, dict):
                node_id = node.get('id') or node.get('rule_id')
                if node_id:
                    nodes_dict[node_id] = node
    elif isinstance(raw_nodes, dict):
        nodes_dict = raw_nodes

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

    return {
        'matched_rule_ids': rule_ids,
        'subgraph_nodes':   result_nodes,
        'subgraph_edges':   subgraph_edges,
    }


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
