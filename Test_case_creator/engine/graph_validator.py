"""
Graph structure validator for the Test Case Creator engine.

Validates that a graph.json file produced by the Context Builder conforms to
the structural schema expected by the test-generation pipeline before it is
consumed by the ``extract_subgraph`` MCP tool.

Public API
----------
``validate_graph_structure(graph_path: str) -> ValidationResult``

    Performs a full structural audit and returns a ``ValidationResult`` that
    separates blocking *errors* (pipeline must not proceed) from advisory
    *warnings* (pipeline can continue, but traceability output may be sparse).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Data contract
# ---------------------------------------------------------------------------

@dataclass
class ValidationResult:
    """
    Result of a structural graph validation pass.

    Attributes
    ----------
    is_valid : bool
        ``True`` only when ``errors`` is empty.  Warnings do not affect
        validity.
    errors : list[str]
        Blocking issues — the pipeline **must** halt when this list is
        non-empty.  Each entry is a human-readable message with enough detail
        for the developer to act on it.
    warnings : list[str]
        Non-blocking advisories — the pipeline may continue, but the user
        should be informed.
    stats : dict
        Aggregate counts useful for surfacing in the VS Code chat panel
        (e.g. ``total_nodes``, ``business_rule_count``).
    """
    is_valid: bool
    errors:   list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats:    dict       = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Relationship types that the BFS traversal in ``extract_subgraph`` follows.
#: Must stay in sync with the constant of the same name in mcp_server.py.
#: The first group reflects the types actually produced by context_builder;
#: the second group keeps legacy / hand-authored graph types working.
TRAVERSABLE_EDGE_TYPES: frozenset[str] = frozenset({
    'TESTS', 'IMPLEMENTS', 'CALLS', 'REFERENCES',  # context_builder output
    'TRACES_TO', 'MAPS_TO', 'DECLARES',             # legacy / hand-authored
})

#: Canonical field names for edges, along with accepted aliases.
_EDGE_FIELD_ALIASES: dict[str, list[str]] = {
    'source':       ['source',       'source_id'],
    'target':       ['target',       'target_id'],
    'relationship': ['relationship', 'type'],
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_edge_field(edge: dict, canonical: str) -> str | None:
    """
    Return the value of *canonical* from *edge*, trying all known aliases.
    Returns ``None`` if no alias is present or the value is falsy.
    """
    for alias in _EDGE_FIELD_ALIASES.get(canonical, [canonical]):
        value = edge.get(alias)
        if value:
            return str(value)
    return None


def _normalise_nodes(raw_nodes: list | dict) -> list[dict]:
    """
    Accept nodes as either a *list* of dicts or a *dict* keyed by node-ID,
    and return a flat list of node dicts.
    """
    if isinstance(raw_nodes, list):
        return [n for n in raw_nodes if isinstance(n, dict)]
    if isinstance(raw_nodes, dict):
        return [n for n in raw_nodes.values() if isinstance(n, dict)]
    return []


# ---------------------------------------------------------------------------
# Public validator
# ---------------------------------------------------------------------------

def validate_graph_structure(graph_path: str) -> ValidationResult:
    """
    Perform a full structural audit of a ``graph.json`` file.

    Check order
    -----------
    1. File exists and is readable.
    2. File content is valid JSON.
    3. Top-level object contains the required ``nodes`` key.
    4. ``nodes`` value is a list or dict (not a scalar).
    5. At least one node of ``type == "business_rule"`` exists.
    6. Every node has a non-empty ``id`` or ``rule_id`` field.
    7. Every edge has ``source``/``source_id``, ``target``/``target_id``,
       and ``relationship``/``type`` fields.
    8. Every edge endpoint references a node that exists in ``nodes``.

    Warnings (non-blocking)
    -----------------------
    - No ``test_scenario`` nodes — BDD traceability column will be empty.
    - No ``api_endpoint`` nodes — Technical API column will be empty.
    - No traversable edges despite edges being present — subgraph will equal
      the seed rule nodes only.

    Parameters
    ----------
    graph_path:
        Absolute or workspace-relative path to ``graph.json``.

    Returns
    -------
    ValidationResult
        ``is_valid`` is ``True`` iff ``errors`` is empty.
    """
    result = ValidationResult(is_valid=False)

    # ------------------------------------------------------------------
    # Check 1: File existence
    # ------------------------------------------------------------------
    if not os.path.exists(graph_path):
        result.errors.append(
            f"File not found: '{graph_path}'. "
            "Please run the Context Builder agent to generate graph.json."
        )
        return result

    # ------------------------------------------------------------------
    # Check 2: Valid JSON
    # ------------------------------------------------------------------
    try:
        with open(graph_path, 'r', encoding='utf-8') as fh:
            graph = json.load(fh)
    except json.JSONDecodeError as exc:
        result.errors.append(
            f"Invalid JSON in '{graph_path}': {exc}. "
            "Re-run the Context Builder to regenerate a valid graph.json."
        )
        return result
    except OSError as exc:
        result.errors.append(f"Cannot read '{graph_path}': {exc}.")
        return result

    if not isinstance(graph, dict):
        result.errors.append(
            "graph.json must be a JSON object at the top level, "
            f"but got {type(graph).__name__}."
        )
        return result

    # ------------------------------------------------------------------
    # Envelope unwrap
    # LocalJsonStore (context_builder) wraps every write in:
    #   { "schema_version": N, "generator": "context_builder",
    #     "written_at": "<iso8601>", "payload": { nodes, edges, ... } }
    # Transparently peel it off so all subsequent checks operate on the
    # flat graph object regardless of which writer version produced the file.
    # ------------------------------------------------------------------
    if "schema_version" in graph and "payload" in graph:
        inner = graph["payload"]
        if isinstance(inner, dict):
            graph = inner

    # ------------------------------------------------------------------
    # Check 3: Required top-level 'nodes' key
    # ------------------------------------------------------------------
    if 'nodes' not in graph:
        result.errors.append(
            "Missing required top-level key 'nodes'. "
            "Expected structure: { \"nodes\": [...], \"links\": [...] }. "
            "Check that the Context Builder version is compatible with this agent."
        )
        return result

    raw_nodes = graph['nodes']

    # ------------------------------------------------------------------
    # Check 4: 'nodes' must be a list or dict
    # ------------------------------------------------------------------
    if not isinstance(raw_nodes, (list, dict)):
        result.errors.append(
            f"'nodes' must be a JSON array or object, got {type(raw_nodes).__name__}. "
            "Re-run the Context Builder."
        )
        return result

    node_list = _normalise_nodes(raw_nodes)
    # NetworkX node_link_data(graph, edges="edges") writes the key as "edges";
    # fall back to "links" for older-format graphs.
    edges: list = graph.get('edges', []) or graph.get('links', []) or []
    if not isinstance(edges, list):
        edges = []

    # Build lookup of valid node IDs and type counts
    known_ids: set[str] = set()
    type_counts: dict[str, int] = {}

    for node in node_list:
        node_id = node.get('id') or node.get('rule_id')
        if node_id:
            known_ids.add(str(node_id))
        node_type = str(node.get('type', 'unknown')).lower()
        type_counts[node_type] = type_counts.get(node_type, 0) + 1

    # ------------------------------------------------------------------
    # Check 5: At least one business_rule node
    # ------------------------------------------------------------------
    business_rule_count = type_counts.get('business_rule', 0)
    if business_rule_count == 0:
        result.errors.append(
            "No nodes with type 'business_rule' found in 'nodes'. "
            "The pipeline requires at least one business rule to generate test cases. "
            "Verify that the Context Builder completed successfully and that node types "
            "are spelled as 'business_rule' (snake_case)."
        )

    # ------------------------------------------------------------------
    # Check 6: Every node has a unique identifier
    # ------------------------------------------------------------------
    nodes_missing_id = sum(
        1 for n in node_list
        if isinstance(n, dict) and not (n.get('id') or n.get('rule_id'))
    )
    if nodes_missing_id > 0:
        result.errors.append(
            f"{nodes_missing_id} node(s) are missing both 'id' and 'rule_id' fields. "
            "Every node must have a unique identifier for subgraph extraction to work."
        )

    # ------------------------------------------------------------------
    # Checks 7 & 8: Edge structure and dangling references
    # ------------------------------------------------------------------
    dangling: list[str] = []
    traversable_edge_count = 0

    for i, edge in enumerate(edges):
        if not isinstance(edge, dict):
            continue

        src = _resolve_edge_field(edge, 'source')
        tgt = _resolve_edge_field(edge, 'target')
        rel = _resolve_edge_field(edge, 'relationship')

        if not src:
            result.errors.append(
                f"Edge[{i}] is missing a 'source' or 'source_id' field."
            )
        if not tgt:
            result.errors.append(
                f"Edge[{i}] is missing a 'target' or 'target_id' field."
            )
        if not rel:
            result.errors.append(
                f"Edge[{i}] is missing a 'relationship' or 'type' field."
            )

        # Dangling-reference check (only when both endpoints are present)
        if src and tgt and known_ids:
            if src not in known_ids:
                dangling.append(src)
            if tgt not in known_ids:
                dangling.append(tgt)

        if rel and rel.upper() in TRAVERSABLE_EDGE_TYPES:
            traversable_edge_count += 1

    if dangling:
        unique_dangling = sorted(set(dangling))
        preview = unique_dangling[:5]
        suffix = '…' if len(unique_dangling) > 5 else ''
        result.errors.append(
            f"{len(unique_dangling)} edge endpoint(s) reference node ID(s) that do not "
            f"exist in 'nodes': {preview}{suffix}. "
            "This indicates an inconsistent graph — re-run the Context Builder."
        )

    # ------------------------------------------------------------------
    # Warnings (non-blocking)
    # ------------------------------------------------------------------
    if type_counts.get('test_scenario', 0) == 0:
        result.warnings.append(
            "No 'test_scenario' nodes found. "
            "The BDD Scenario column in the traceability matrix will show 'None Linked'. "
            "Consider linking BDD scenarios to business rules in the Context Builder."
        )

    if type_counts.get('api_endpoint', 0) == 0:
        result.warnings.append(
            "No 'api_endpoint' nodes found. "
            "The Technical API column in the traceability matrix will show 'None Linked'."
        )

    if edges and traversable_edge_count == 0:
        traversable_names = ', '.join(sorted(TRAVERSABLE_EDGE_TYPES))
        result.warnings.append(
            f"None of the {len(edges)} edge(s) use a traversable relationship type "
            f"({traversable_names}). "
            "Subgraph extraction will return only the root rule nodes with no connected context."
        )

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------
    result.stats = {
        'total_nodes':            len(node_list),
        'business_rule_count':    business_rule_count,
        'edge_count':             len(edges),
        'traversable_edge_count': traversable_edge_count,
        'node_types':             type_counts,
    }

    # is_valid iff no blocking errors accumulated
    result.is_valid = len(result.errors) == 0
    return result
