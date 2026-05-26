"""
Context Builder MCP Server — main entry point.

Must be the FIRST module executed; configure_logging() redirects stdout→stderr
before any import that might emit output.
"""

# ── Logging must be configured before any other import ──────────────────────
from engine.logging_setup import configure_logging
configure_logging()

import logging
import json
import os
import time

from mcp.server.fastmcp import FastMCP

from db.graph_store import GraphStore
from db.blueprint_store import BlueprintStore
from engine.workspace_registry import WorkspaceRegistry
from engine.paths import ensure_within_workspace, PathTraversalError
from mcp_models import (
    ToolResponse,
    ErrorDetail,
    IngestResult,
    NodeSummary,
    QueryResult,
    EdgeSummary,
    TraceabilityGraph,
    GraphSummary,
    WorkspaceSyncReport,
    DriftReport,
    EdgeProposal,
    EdgeProposalApplyResult,
    # Spec 009
    ShardPlanEntry,
    ShardIngestResult,
    ShardMergeResult,
    ShardStatus,
    ActiveShardsResult,
    # SPEC-3 Wave 3
    RecordAnomalyResult,
    ok as _ok,
    err as _err,
)

log = logging.getLogger(__name__)

mcp = FastMCP("Context Builder Server")

# Primary per-workspace store registry
registry = WorkspaceRegistry()

# ── Backward-compat shim ─────────────────────────────────────────────────────
# Tests that monkeypatch ``main.active_store`` (e.g. test_mcp_app_features.py)
# still work: _get_store() checks this variable before falling back to registry.
active_store: GraphStore = None


def _get_store(workspace_path: str = None) -> GraphStore:
    """Return the active GraphStore for *workspace_path*.

    Resolution order:
    1. *workspace_path* → registry.get(workspace_path)
    2. ``active_store`` monkeypatched by tests
    3. registry default workspace
    """
    if workspace_path:
        try:
            return registry.get(workspace_path)
        except LookupError:
            # Not in registry yet — create it
            return registry.acquire(workspace_path)

    global active_store
    if active_store is not None:
        return active_store

    try:
        return registry.get()
    except LookupError:
        # Last resort: fall back to cwd (preserves old behaviour)
        return registry.acquire(os.getcwd())


# ── MCP Tools ────────────────────────────────────────────────────────────────

@mcp.tool()
def ingest_workspace(workspace_path: str) -> ToolResponse:
    """Scan a workspace directory and (re)build the Semantic Context Graph.

    Args:
        workspace_path: Absolute path to the directory containing context
                        documents and code files.
    """
    try:
        store = registry.acquire(workspace_path, make_default=True)
    except FileNotFoundError as exc:
        return _err("INVALID_PATH", str(exc),
                    hint="Provide an absolute path to an existing directory.")

    from engine.extractor import ContextExtractor
    t0 = time.monotonic()
    try:
        extractor = ContextExtractor(store)
        result = extractor.ingest_workspace()
    except Exception as exc:
        log.exception("Ingest failed for %s", workspace_path)
        return _err("INTERNAL_ERROR", f"Ingest failed: {exc}")

    return _ok(IngestResult(
        workspace_path=store.workspace_path,
        parsed_files=result["parsed_files"],
        cached_files=result["cached_files"],
        deleted_files=result.get("deleted_files", 0),
        nodes_removed=result.get("nodes_removed", 0),
        edges_removed=result.get("edges_removed", 0),
        total_nodes=result["total_nodes"],
        total_edges=len(store.get_edges()),
        duration_seconds=round(time.monotonic() - t0, 3),
    ))


@mcp.tool()
def query_semantic_graph(
    query: str,
    node_type: str = None,
    workspace_path: str = None,
) -> ToolResponse:
    """Query the semantic graph for nodes matching *query* (BM25-ranked).

    Args:
        query: Search term.
        node_type: Optional type filter (e.g. 'business_rule', 'api_endpoint').
        workspace_path: Target workspace; defaults to the most-recently ingested one.
    """
    try:
        store = _get_store(workspace_path)
    except (LookupError, FileNotFoundError) as exc:
        return _err("WORKSPACE_NOT_FOUND", str(exc),
                    hint="Call ingest_workspace(workspace_path) first.")

    all_nodes = store.query_nodes(node_type=node_type)

    if not query:
        results = all_nodes[:50]
    else:
        from engine.search import SemanticSearch
        results = SemanticSearch(all_nodes).search(query, top_n=20)

    node_summaries = [
        NodeSummary(
            id=n["id"],
            type=n.get("type", ""),
            name=n.get("name", ""),
            description=n.get("description", ""),
            metadata=n.get("metadata", {}),
        )
        for n in results
    ]

    return _ok(QueryResult(
        query=query,
        node_type_filter=node_type,
        results=node_summaries,
        total_matches=len(node_summaries),
    ))


@mcp.tool()
def list_workspaces() -> ToolResponse:
    """List all workspaces this server has ingested in the current session."""
    return _ok(registry.list_workspaces())


@mcp.tool()
def get_rule_traceability(
    rule_id: str,
    depth: int = 2,
    min_confidence: float = 0.0,
    workspace_path: str = None,
) -> ToolResponse:
    """Return traceability details for *rule_id*: BFS-bounded connected nodes and edges.

    Spec 006 (Wave 3): the old full-component dump is replaced with a BFS-
    bounded traversal so low-confidence heuristic edges do not overwhelm the
    result.

    Args:
        rule_id: The unique ID of the business rule node.
        depth: Maximum BFS hops from the rule node (default 2).
        min_confidence: Exclude edges with confidence below this threshold
                        (default 0.0 = include all).
        workspace_path: Target workspace; defaults to the active one.
    """
    try:
        store = _get_store(workspace_path)
    except LookupError as exc:
        return _err("WORKSPACE_NOT_FOUND", str(exc))

    subgraph = store.get_traceability_graph(
        rule_id,
        depth=depth,
        max_nodes=80,
        min_edge_confidence=min_confidence,
    )
    if not subgraph["nodes"]:
        return _err("NODE_NOT_FOUND", f"No rule found with ID '{rule_id}'.")

    return _ok(TraceabilityGraph(
        rule_id=rule_id,
        nodes=[NodeSummary(id=n["id"], type=n.get("type", ""),
                           name=n.get("name", ""), description=n.get("description", ""),
                           metadata=n.get("metadata", {})) for n in subgraph["nodes"]],
        edges=[EdgeSummary(source_id=e["source_id"], target_id=e["target_id"],
                           relationship=e["relationship"], metadata=e.get("metadata", {}))
               for e in subgraph["edges"]],
    ))


@mcp.tool()
def list_edges_by_source(
    source: str,
    workspace_path: str = None,
    limit: int = 100,
) -> ToolResponse:
    """List edges filtered by their provenance source.

    Spec 006 (Wave 3): exposes edge provenance for audit — useful for
    reviewing Copilot-proposed edges or weak heuristic edges before accepting
    them.

    Args:
        source: Provenance tag to filter on.  Recognised values:
                ``"heuristic"`` — edges emitted by the TF-IDF mapper.
                ``"parser"``    — edges emitted by any document parser
                                  (includes ``"parser:markdown"``, etc.).
                ``"copilot_proposal"`` — edges accepted from Copilot Chat
                                          (Spec 005).
                ``"manual"``    — edges added via the ``add_edge`` tool.
        limit: Maximum number of edges returned (default 100).
        workspace_path: Target workspace; defaults to the active one.
    """
    try:
        store = _get_store(workspace_path)
    except LookupError as exc:
        return _err("WORKSPACE_NOT_FOUND", str(exc))

    matching = []
    for edge in store.get_edges():
        meta_source = edge.get("metadata", {}).get("source", "")
        # "parser" matches "parser", "parser:markdown", "parser:feature", …
        if meta_source == source or meta_source.startswith(f"{source}:"):
            matching.append(edge)
        if len(matching) >= limit:
            break

    edges = [
        EdgeSummary(
            source_id=e["source_id"],
            target_id=e["target_id"],
            relationship=e["relationship"],
            metadata=e.get("metadata", {}),
        )
        for e in matching
    ]
    return _ok({"source_filter": source, "count": len(edges), "edges": [e.model_dump() for e in edges]})


@mcp.tool()
def get_graph_summary(workspace_path: str = None) -> str:
    """Return a textual summary of the active Semantic Context Graph."""
    try:
        store = _get_store(workspace_path)
        nodes = store.query_nodes()
        edges = store.get_edges()

        types_breakdown = {}
        for n in nodes:
            types_breakdown[n["type"]] = types_breakdown.get(n["type"], 0) + 1

        rels_breakdown = {}
        for e in edges:
            rels_breakdown[e["relationship"]] = rels_breakdown.get(e["relationship"], 0) + 1

        return json.dumps({
            "workspace_path": store.workspace_path,
            "total_nodes": len(nodes),
            "node_types_breakdown": types_breakdown,
            "total_edges": len(edges),
            "relationships_breakdown": rels_breakdown,
        }, indent=2)
    except Exception as exc:
        return f"Error retrieving graph summary: {exc}"


@mcp.tool()
def get_raw_documents(workspace_path: str = None) -> str:
    """Retrieve all raw document paths and their extracted text content."""
    try:
        store = _get_store(workspace_path)
        return json.dumps(store.get_raw_documents(), indent=2)
    except Exception as exc:
        return f"Error retrieving raw documents: {exc}"


@mcp.tool()
def add_node(
    node_id: str,
    node_type: str,
    name: str,
    description: str,
    metadata: str = "{}",
    workspace_path: str = None,
) -> ToolResponse:
    """Manually insert or update a node in the Semantic Context Graph."""
    try:
        store = _get_store(workspace_path)
    except LookupError as exc:
        return _err("WORKSPACE_NOT_FOUND", str(exc))

    try:
        meta_dict = json.loads(metadata) if metadata else {}
    except json.JSONDecodeError as exc:
        return _err("VALIDATION_ERROR", f"metadata is not valid JSON: {exc}")

    # Validate source_file if present (path-traversal guard)
    source_file = meta_dict.get("source_file")
    if source_file:
        try:
            ensure_within_workspace(source_file, store.workspace_path)
        except PathTraversalError as exc:
            return _err("PATH_TRAVERSAL", str(exc))

    try:
        with store.transaction():
            store.upsert_node(node_id, node_type, name, description, meta_dict)
    except Exception as exc:
        log.exception("add_node failed for %s", node_id)
        return _err("INTERNAL_ERROR", f"Failed to add node: {exc}")

    return _ok({"node_id": node_id, "status": "upserted"})


@mcp.tool()
def add_edge(
    source_id: str,
    target_id: str,
    relationship: str,
    metadata: str = "{}",
    workspace_path: str = None,
) -> ToolResponse:
    """Manually insert or update a directed edge in the Semantic Context Graph."""
    try:
        store = _get_store(workspace_path)
    except LookupError as exc:
        return _err("WORKSPACE_NOT_FOUND", str(exc))

    try:
        meta_dict = json.loads(metadata) if metadata else {}
    except json.JSONDecodeError as exc:
        return _err("VALIDATION_ERROR", f"metadata is not valid JSON: {exc}")

    try:
        with store.transaction():
            store.upsert_edge(source_id, target_id, relationship.upper(), meta_dict)
    except Exception as exc:
        log.exception("add_edge failed for %s→%s", source_id, target_id)
        return _err("INTERNAL_ERROR", f"Failed to add edge: {exc}")

    return _ok({"source_id": source_id, "target_id": target_id,
                "relationship": relationship.upper(), "status": "upserted"})


@mcp.tool()
def get_all_edges(workspace_path: str = None) -> str:
    """Retrieve all directed edges (relationships) currently stored."""
    try:
        store = _get_store(workspace_path)
        return json.dumps(store.get_edges(), indent=2)
    except Exception as exc:
        return f"Error retrieving all edges: {exc}"


@mcp.tool()
def check_workspace_sync(workspace_path: str) -> ToolResponse:
    """Compare files in *workspace_path* against the indexed document store.

    Reports new (unindexed), modified (checksum-changed), removed, and
    up-to-date files.
    """
    try:
        store = _get_store(workspace_path)
    except Exception as exc:
        return _err("INVALID_PATH", str(exc))

    from parsers import PARSER_MAP
    exclude_dirs = {
        ".git", "node_modules", "target", "build", "dist",
        ".gradle", ".idea", ".vscode", ".context_builder",
    }

    new_files, modified_files, up_to_date_files = [], [], []
    indexed_paths = set(store.documents.keys())
    found_paths: set = set()

    for root, dirs, files in os.walk(workspace_path):
        dirs[:] = [d for d in dirs if d not in exclude_dirs]
        for file in files:
            file_path = os.path.join(root, file)
            _, ext = os.path.splitext(file.lower())
            if ext not in PARSER_MAP:
                continue
            found_paths.add(file_path)
            rel = store.to_rel_path(file_path)
            # Register both the absolute path and its relative form so the
            # removed-files check below (which iterates over relative index
            # keys) can match correctly regardless of which form was stored.
            found_paths.add(rel)
            if rel not in indexed_paths and file_path not in indexed_paths:
                new_files.append(file_path)
            elif store.should_reparse(file_path):
                modified_files.append(file_path)
            else:
                up_to_date_files.append(file_path)

    # indexed_paths keys are relative; found_paths now contains both forms.
    # Return absolute paths so callers can display them unambiguously.
    removed_files = [
        store.to_abs_path(p)
        for p in indexed_paths
        if p not in found_paths and store.to_abs_path(p) not in found_paths
    ]

    return _ok(WorkspaceSyncReport(
        workspace_path=workspace_path,
        is_in_sync=(not new_files and not modified_files and not removed_files),
        new_files=new_files,
        modified_files=modified_files,
        removed_files=removed_files,
        up_to_date_files=up_to_date_files,
        total_indexed=len(indexed_paths),
    ))


@mcp.tool()
def query_framework_blueprint(
    target_file_path: str,
    workspace_path: str = None,
) -> str:
    """Query the framework blueprint for standards applicable to *target_file_path*.

    Args:
        target_file_path: Absolute path of the file the agent intends to modify.
        workspace_path: Target workspace; defaults to the active one.
    """
    try:
        store = _get_store(workspace_path)
        blueprint = BlueprintStore(store.workspace_path)
        applicable_standards = blueprint.get_standards_for_path(target_file_path)
        reusable_pool = blueprint.get_all_reusable_methods()
        return json.dumps({
            "target_path": target_file_path,
            "applicable_architectural_rules": applicable_standards,
            "available_reusable_methods": reusable_pool,
        }, indent=2)
    except Exception as exc:
        return f"Error querying framework blueprint: {exc}"


@mcp.tool()
def get_framework_generation_blueprint(workspace_path: str = None) -> str:
    """Retrieve the green-field scaffolding rules, templates, and MCP exploration guidelines."""
    try:
        store = _get_store(workspace_path)
        blueprint = BlueprintStore(store.workspace_path)
        return json.dumps(blueprint.get_generation_blueprint(), indent=2)
    except Exception as exc:
        return f"Error retrieving generation blueprint: {exc}"


@mcp.tool()
def get_behavioral_drift_report(workspace_path: str = None) -> str:
    """Scan the context graph for detected conflicts and anomalies.

    SPEC-3 Wave 3 (A5 breaking change): the per-record outer keys changed shape.
    OLD: {node_id, node_name, anomaly_type, details}.
    NEW: {node_id, node_name, anomaly_kind, severity, detected_by, detected_at,
          evidence, suggested_action}.
    Plus top-level by_kind and by_severity counters. Legacy on-disk records that
    pre-date Wave 3 still parse because the new code falls back to legacy fields
    when new ones are absent.
    """
    try:
        store = _get_store(workspace_path)
        drift_edges = [
            e for e in store.get_edges() if e["relationship"] == "DRIFTED_FROM"
        ]

        flat_records: list[dict] = []
        by_kind: dict[str, int] = {}
        by_severity: dict[str, int] = {}

        for node_id, data in store.graph.nodes(data=True):
            anomalies = data.get("metadata", {}).get("behavioral_anomalies", [])
            for record in anomalies:
                # New structured records carry anomaly_kind/severity; legacy
                # records (pre-Wave 3 graphs) don't — shim them in.
                kind = record.get("anomaly_kind", "legacy_requirement_drift")
                severity = record.get("severity", "info")
                flat_records.append({
                    "node_id": node_id,
                    "node_name": data.get("name"),
                    "anomaly_kind": kind,
                    "severity": severity,
                    "detected_by": record.get("detected_by", "unknown"),
                    "detected_at": record.get("detected_at"),
                    "evidence": record.get("evidence", {
                        "summary": str(
                            record.get("observed_deviation_profile", "(legacy record)")
                        )[:200],
                        "source_file": record.get("reported_by"),
                    }),
                    "suggested_action": record.get("suggested_action"),
                })
                by_kind[kind] = by_kind.get(kind, 0) + 1
                by_severity[severity] = by_severity.get(severity, 0) + 1

        return json.dumps({
            "total_anomalies_detected": len(flat_records) + len(drift_edges),
            "by_kind": by_kind,
            "by_severity": by_severity,
            "drift_edges": drift_edges,
            "node_anomalies": flat_records,
        }, indent=2)
    except Exception as exc:
        return f"Error building drift report: {exc}"


# SPEC-3 Wave 3: explicit anomaly recording by the relationship-linker.
@mcp.tool()
def record_anomaly(
    node_id: str,
    anomaly_kind: str,
    severity: str,
    evidence_json: str,
    suggested_action: str = None,
    workspace_path: str = None,
):
    """Persist a structured behavioral anomaly on a node.

    Called by the relationship-linker agent when it detects one of the four
    divergence categories. Idempotent — re-recording the same (kind, summary)
    on the same node is a no-op.

    Args:
        node_id: Target node ID.
        anomaly_kind: One of ui_without_requirement, rule_without_implementation,
                      constant_spec_divergence, endpoint_without_test.
        severity: One of critical, warning, info.
        evidence_json: JSON string conforming to AnomalyEvidence schema.
        suggested_action: Optional remediation hint for human reviewers.
        workspace_path: Target workspace; defaults to the active one.
    """
    try:
        store = _get_store(workspace_path)
    except LookupError as exc:
        return _err("WORKSPACE_NOT_FOUND", str(exc))

    try:
        evidence = json.loads(evidence_json)
    except json.JSONDecodeError as exc:
        return _err("VALIDATION_ERROR", f"evidence_json is not valid JSON: {exc}")

    valid_kinds = {
        "ui_without_requirement", "rule_without_implementation",
        "constant_spec_divergence", "endpoint_without_test",
    }
    if anomaly_kind not in valid_kinds:
        return _err("VALIDATION_ERROR", f"Unknown anomaly_kind: {anomaly_kind}")
    if severity not in {"critical", "warning", "info"}:
        return _err("VALIDATION_ERROR", f"Unknown severity: {severity}")

    try:
        with store.transaction():
            persisted = store.record_anomaly(
                node_id=node_id,
                anomaly_kind=anomaly_kind,
                severity=severity,
                evidence=evidence,
                suggested_action=suggested_action,
            )
    except Exception as exc:
        log.exception("record_anomaly failed for %s", node_id)
        return _err("INTERNAL_ERROR", f"record_anomaly failed: {exc}")

    return _ok(RecordAnomalyResult(
        node_id=node_id, anomaly_kind=anomaly_kind,
        severity=severity, persisted=persisted,
    ))


@mcp.tool()
def resolve_behavioral_drift(
    node_id: str,
    resolution_action: str,
    target_anomaly_index: int = -1,
    workspace_path: str = None,
) -> str:
    """Formally resolve a recorded behavioral drift anomaly on a requirement node.

    Args:
        node_id: The unique ID of the target requirement node.
        resolution_action: 'update_spec' or 'keep_expected'.
        target_anomaly_index: 0-based index of the anomaly to apply (default: -1 = latest).
        workspace_path: Target workspace; defaults to the active one.
    """
    if resolution_action not in ["update_spec", "keep_expected"]:
        return (f"Error: Invalid resolution action '{resolution_action}'. "
                f"Must be 'update_spec' or 'keep_expected'.")
    try:
        store = _get_store(workspace_path)
        if not store.graph.has_node(node_id):
            return f"Error: Node '{node_id}' does not exist in context graph."

        node_data = store.graph.nodes[node_id]
        anomalies = node_data.get("metadata", {}).get("behavioral_anomalies", [])
        if not anomalies:
            return f"Node '{node_id}' has no active behavioral anomalies to resolve."

        if resolution_action == "update_spec":
            try:
                latest_observation = anomalies[target_anomaly_index]["observed_deviation_profile"]
            except IndexError:
                return (f"Error: Invalid anomaly index '{target_anomaly_index}' for node "
                        f"'{node_id}' with {len(anomalies)} anomalies.")
            store.graph.nodes[node_id]["description"] = latest_observation

        store.graph.nodes[node_id]["metadata"]["behavioral_anomalies"] = []

        edges_to_remove = [
            (u, v) for u, v, data in store.graph.edges(data=True)
            if v == node_id and data.get("relationship") == "DRIFTED_FROM"
        ]
        for u, v in edges_to_remove:
            store.graph.remove_edge(u, v)

        store.save_graph()
        return f"Successfully resolved behavioral drift for node: {node_id} using action: {resolution_action}"
    except Exception as exc:
        return f"Error resolving behavioral drift: {exc}"


# ── MCP Resources ─────────────────────────────────────────────────────────────

@mcp.resource("context://graph/summary")
def get_graph_summary_resource() -> str:
    """Return a textual summary of the active Semantic Context Graph."""
    return get_graph_summary()


@mcp.resource("context://rules/{rule_id}")
def get_rule_detail(rule_id: str) -> str:
    """Expose detailed fields and raw documentation for a specific business rule."""
    try:
        store = _get_store()
        node = store.get_node(rule_id)
        if not node or node["type"] != "business_rule":
            return f"Business rule with ID '{rule_id}' not found."
        return json.dumps(node, indent=2)
    except Exception as exc:
        return f"Error fetching rule: {exc}"


# ── MCP Prompts ───────────────────────────────────────────────────────────────

@mcp.prompt()
def generate_bdd_tests(rule_id: str) -> str:
    """Provide a system prompt and semantic context for writing Gherkin BDD tests."""
    try:
        store = _get_store()
        node = store.get_node(rule_id)
        if not node:
            return f"Business rule with ID '{rule_id}' not found."

        trace_data = store.get_traceability_graph(rule_id)
        related_elements = [
            f"- {n['type'].upper()} ({n['id']}): {n['name']} - {n.get('description', '')}"
            for n in trace_data["nodes"]
            if n["id"] != rule_id
        ]
        related_str = "\n".join(related_elements) if related_elements else "None detected."

        return f"""You are a senior Quality Assurance Engineer specializing in Behavior-Driven Development (BDD).
Your task is to write a comprehensive Gherkin BDD `.feature` file to test the following business rule:

### TARGET BUSINESS RULE:
- **ID**: {node['id']}
- **Name**: {node['name']}
- **Description**: {node.get('description', '')}
- **Metadata**: {json.dumps(node.get('metadata', {}), indent=2)}

### RELATED TECHNICAL COMPONENTS & ENDPOINTS:
{related_str}

### INSTRUCTIONS:
1. Write a high-quality Gherkin BDD `.feature` file.
2. Include both positive verification scenarios and negative edge-cases.
3. Utilize Scenario Outlines for boundary value testing if applicable.
"""
    except Exception as exc:
        return f"Error building BDD prompt: {exc}"


@mcp.prompt()
def generate_api_tests(endpoint_id: str) -> str:
    """Provide a system prompt and endpoint context for generating API integration tests."""
    try:
        store = _get_store()
        node = store.get_node(endpoint_id)
        if not node or node["type"] != "api_endpoint":
            return f"API Endpoint with ID '{endpoint_id}' not found."

        return f"""You are a backend test engineer.
Your task is to write automated functional integration tests for the following API endpoint:

### TARGET ENDPOINT:
- **Name**: {node['name']}
- **Description**: {node.get('description', '')}
- **Metadata**: {json.dumps(node.get('metadata', {}), indent=2)}

### INSTRUCTIONS:
1. Write integration test cases.
2. Ensure you mock downstream services appropriately.
3. Test for success conditions and failure paths.
"""
    except Exception as exc:
        return f"Error building API prompt: {exc}"


# ── Spec 005: Copilot-native edge augmentation ────────────────────────────────

@mcp.prompt()
def propose_edge_candidates(
    workspace_path: str = None,
    limit: int = 50,
) -> str:
    """Return a prompt for the host LLM (e.g. Copilot Chat) to review ambiguous
    edge candidates that the heuristic mapper did not emit automatically.

    The host LLM executes this prompt with its own model and credentials —
    the MCP server does NOT call any external LLM.  After Copilot returns a
    JSON array, pass it to ``apply_edge_proposals``.

    Args:
        workspace_path: Target workspace; defaults to the most-recently ingested one.
        limit: Maximum number of candidates to include in the prompt (default 50).
    """
    from engine.prompts.edge_proposal import render_edge_proposal_prompt

    try:
        store = _get_store(workspace_path)
    except (LookupError, FileNotFoundError) as exc:
        return (f"# Error: workspace not found\n{exc}\n\n"
                "Run `ingest_workspace(workspace_path)` first.")

    # The extractor is the authoritative holder of last_candidates.
    # Re-use the cached extractor if available (WorkspaceRegistry stores it);
    # fall back to constructing one (candidates will be empty until an ingest).
    from engine.extractor import ContextExtractor
    extractor = ContextExtractor(store)
    candidates = extractor.get_pending_edge_candidates(limit=limit)

    if not candidates:
        return (
            "# No ambiguous edge candidates pending.\n\n"
            "Either `ingest_workspace` has not been run yet, or all candidate "
            "pairs were already above the auto-emit threshold.\n\n"
            "Run `ingest_workspace(workspace_path)` and then call this prompt again."
        )

    return render_edge_proposal_prompt(candidates)


@mcp.tool()
def apply_edge_proposals(
    proposals_json: str,
    workspace_path: str = None,
    confidence_floor: float = 0.6,
) -> ToolResponse:
    """Apply edge proposals returned by a host LLM (e.g. Copilot Chat).

    Validates the JSON against the EdgeProposal schema, enforces the confidence
    floor, verifies that both endpoints exist, and upserts accepted edges with
    full provenance metadata (``source="copilot_proposal"``).

    Args:
        proposals_json: A JSON array conforming to the schema in the
            ``propose_edge_candidates`` prompt output format.
        workspace_path: Target workspace; defaults to the active one.
        confidence_floor: Proposals with confidence below this value are
            rejected (default 0.6).
    """
    import pydantic

    try:
        store = _get_store(workspace_path)
    except (LookupError, FileNotFoundError) as exc:
        return _err("WORKSPACE_NOT_FOUND", str(exc),
                    hint="Call ingest_workspace(workspace_path) first.")

    # --- Parse and validate input ---
    # Validate ALL items first so malformed payloads (missing required fields)
    # return VALIDATION_ERROR.  After validation, silently skip items where
    # relationship is null — those are the LLM signalling "I cannot determine".
    try:
        raw = json.loads(proposals_json)
        if not isinstance(raw, list):
            raise ValueError("Expected a JSON array at the top level.")
        all_parsed = [EdgeProposal(**p) for p in raw]
        proposals = [p for p in all_parsed if p.relationship is not None]
    except (json.JSONDecodeError, pydantic.ValidationError, ValueError) as exc:
        return _err(
            "VALIDATION_ERROR",
            f"Could not parse proposals: {exc}",
            hint="The host LLM should return strict JSON matching the prompt schema.",
        )

    accepted: list[dict] = []
    rejected: list[dict] = []

    try:
        from datetime import datetime, timezone
        with store.transaction():
            for p in proposals:
                pair_id = f"{p.source_id}->{p.target_id}"

                if p.confidence < confidence_floor:
                    rejected.append({
                        "id": pair_id,
                        "reason": (
                            f"confidence {p.confidence} < floor {confidence_floor}"
                        ),
                    })
                    continue

                if (not store.graph.has_node(p.source_id)
                        or not store.graph.has_node(p.target_id)):
                    rejected.append({
                        "id": pair_id,
                        "reason": "source or target node missing",
                    })
                    continue

                store.upsert_edge(
                    p.source_id,
                    p.target_id,
                    p.relationship,
                    {
                        "source": "copilot_proposal",
                        "mapper": "copilot",
                        "confidence": p.confidence,
                        "rationale": p.rationale,
                        "reviewed_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
                accepted.append({
                    "source_id": p.source_id,
                    "target_id": p.target_id,
                    "relationship": p.relationship,
                })

    except Exception as exc:
        log.exception("apply_edge_proposals failed")
        return _err("INTERNAL_ERROR", f"Failed to apply proposals: {exc}")

    return _ok(EdgeProposalApplyResult(
        accepted=accepted,
        rejected=rejected,
        total_proposed=len(proposals),
    ))


@mcp.tool()
def request_edge_review(
    workspace_path: str = None,
    limit: int = 30,
) -> str:
    """Convenience helper: return the edge-review prompt for the chat host to execute.

    Equivalent to invoking the ``propose_edge_candidates`` prompt manually.
    Some MCP clients (Cursor, Roo-Code) re-inject long tool responses into the
    chat context, making this a single-call shortcut for the two-step workflow.

    After the host LLM responds with a JSON array, pass it to
    ``apply_edge_proposals``.
    """
    return propose_edge_candidates(workspace_path=workspace_path, limit=limit)


# ── Spec 009: Parallel ingestion tools ───────────────────────────────────────

def _ensure_shard_worker_profile(workspace_path: str) -> None:
    """Write the shard-worker agent profile if it doesn't exist yet."""
    agents_dir = os.path.join(workspace_path, ".context_builder", "agents")
    profile_path = os.path.join(agents_dir, "shard_worker.md")
    if not os.path.exists(profile_path):
        try:
            os.makedirs(agents_dir, exist_ok=True)
            template_path = os.path.join(
                os.path.dirname(__file__),
                "engine", "prompts", "shard_worker_template.md",
            )
            if os.path.exists(template_path):
                import shutil
                shutil.copy2(template_path, profile_path)
                log.info("Created shard_worker.md at %s", profile_path)
        except OSError as exc:
            log.warning("Could not create shard_worker.md: %s", exc)


def _load_parallelism_config(workspace_path: str) -> dict:
    """Load .context_builder/parallelism.json, creating defaults if absent."""
    cfg_path = os.path.join(workspace_path, ".context_builder", "parallelism.json")
    defaults = {
        "schema_version": 1,
        "max_parser_workers": 8,
        "max_shards": 8,
        "min_files_for_parallel_parse": 3,
        "shard_lock_timeout_seconds": 30,
    }
    if not os.path.exists(cfg_path):
        try:
            os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(defaults, f, indent=2)
        except OSError:
            pass
        return defaults
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        for k, v in defaults.items():
            cfg.setdefault(k, v)
        return cfg
    except (OSError, json.JSONDecodeError):
        return defaults


@mcp.prompt()
def plan_parallel_ingestion(
    workspace_path: str,
    strategy: str = "by_directory",
) -> str:
    """Return a planning prompt that tells the host LLM how to dispatch parallel shard ingestion.

    The host LLM (Copilot Chat) reads this prompt and dispatches one subagent per
    shard IN A SINGLE RESPONSE so VS Code fans them out in parallel.

    Args:
        workspace_path: Absolute path to the workspace root.
        strategy: Sharding strategy — one of ``by_directory``, ``by_format``,
                  or ``by_size`` (default: ``by_directory``).
    """
    from engine.ingestion.sharder import Sharder, ShardStrategy
    from engine.prompts.parallel_ingest import build_parallel_ingest_prompt

    try:
        if not os.path.isdir(workspace_path):
            return (
                f"# Error: workspace not found\n"
                f"`{workspace_path}` is not a directory.\n"
                f"Call `ingest_workspace(workspace_path)` first."
            )
        if strategy not in ("by_directory", "by_format", "by_size"):
            return (
                f"# Error: unknown strategy `{strategy}`\n"
                "Valid strategies: `by_directory`, `by_format`, `by_size`."
            )

        cfg = _load_parallelism_config(workspace_path)
        sharder = Sharder(workspace_path, max_shards=cfg.get("max_shards", 8))
        plans = sharder.plan(strategy)  # type: ignore[arg-type]

        if not plans:
            return (
                "# No parseable files found\n\n"
                f"The workspace at `{workspace_path}` contains no parseable "
                "files. Nothing to shard."
            )
        if len(plans) == 1:
            return (
                "# Workspace too small to benefit from parallel ingestion\n\n"
                f"Only 1 shard was produced ({plans[0].estimated_file_count} files). "
                "Call `ingest_workspace(workspace_path)` directly instead — "
                "subagent overhead would exceed the parsing time."
            )

        _ensure_shard_worker_profile(workspace_path)
        return build_parallel_ingest_prompt(workspace_path, strategy, plans)

    except Exception as exc:
        log.exception("plan_parallel_ingestion failed")
        return f"# Error computing shard plan\n{exc}"


@mcp.tool()
def ingest_workspace_shard(
    workspace_path: str,
    shard_id: str,
    include_globs: list,
    exclude_globs: list = None,
) -> ToolResponse:
    """Ingest a single shard of workspace files into a per-shard staging area.

    Called by subagents dispatched by the main agent after ``plan_parallel_ingestion``.
    Writes to ``.context_builder/shards/<shard_id>/`` — the main graph is untouched
    until ``merge_workspace_shards`` is called.

    Args:
        workspace_path: Absolute path to the workspace root.
        shard_id: Stable identifier from the shard plan (used as staging dir name).
        include_globs: List of glob patterns (relative to workspace root) to ingest.
        exclude_globs: Glob patterns to exclude (default: empty).
    """
    import fnmatch
    import shutil as _shutil

    if exclude_globs is None:
        exclude_globs = []

    try:
        if not os.path.isdir(workspace_path):
            return _err("INVALID_PATH", f"Workspace not found: {workspace_path!r}")

        # Validate shard_id (filesystem safety)
        import re as _re
        if not _re.match(r"^[a-zA-Z0-9_\-]{1,64}$", shard_id):
            return _err(
                "VALIDATION_ERROR",
                f"Invalid shard_id {shard_id!r}: must be alphanumeric/dash/underscore, ≤64 chars.",
            )

        cfg = _load_parallelism_config(workspace_path)
        shard_dir = os.path.join(workspace_path, ".context_builder", "shards", shard_id)
        os.makedirs(shard_dir, exist_ok=True)

        # Write state.json — "running"
        state_path = os.path.join(shard_dir, "state.json")
        from datetime import datetime, timezone
        started_at = datetime.now(timezone.utc).isoformat()
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump({"status": "running", "started_at": started_at,
                       "file_count": 0, "completed_at": None}, f)

        t0 = time.monotonic()

        # Discover files matching include_globs (excluding exclude_globs)
        from engine.ingestion.sharder import ALL_PARSEABLE
        matched_files = []
        for dirpath, dirnames, filenames in os.walk(workspace_path):
            dirnames[:] = [
                d for d in dirnames
                if d not in {".context_builder", ".git", "__pycache__", "node_modules"}
                and not d.startswith(".")
            ]
            for fname in filenames:
                abs_path = os.path.join(dirpath, fname)
                rel_path = os.path.relpath(abs_path, workspace_path).replace("\\", "/")
                ext = os.path.splitext(fname)[1].lower()
                if ext not in ALL_PARSEABLE:
                    continue
                # Check include globs
                included = any(
                    fnmatch.fnmatch(rel_path, g.lstrip("/"))
                    for g in include_globs
                ) if include_globs else True
                if not included:
                    continue
                # Check exclude globs
                excluded = any(
                    fnmatch.fnmatch(rel_path, g.lstrip("/"))
                    for g in exclude_globs
                )
                if excluded:
                    continue
                matched_files.append((abs_path, rel_path, ext))

        # Build a shard-local GraphStore
        from db.graph_store import GraphStore as _GraphStore
        shard_store = _GraphStore(shard_dir)

        # Parse files (using parallel pool if warranted)
        from engine.ingestion.parallel_parse import ParallelParser, ParseTask
        max_workers = cfg.get("max_parser_workers", 8)
        min_files = cfg.get("min_files_for_parallel_parse", 3)

        # Build tasks
        tasks = [
            ParseTask(
                abs_path=abs_p,
                rel_path=rel_p,
                extension=ext,
                shared_context_snapshot={},
            )
            for abs_p, rel_p, ext in matched_files
        ]

        parsed_count = 0
        error_count = 0

        if tasks:
            # Try parallel parse; fall back gracefully if parsers aren't importable
            # via the worker path (worker needs the same Python env)
            try:
                parser = ParallelParser(
                    max_workers=max_workers if len(tasks) >= min_files else 1,
                    min_files_for_pool=min_files,
                )
                results = parser.parse_many(tasks)
            except Exception as exc:
                log.warning("Parallel parser failed (%s) — serial fallback", exc)
                # Serial fallback using ParseDispatcher
                from engine.ingestion.parse_dispatcher import ParseDispatcher
                from engine.ingestion.walker import DiscoveredFile
                dispatcher = ParseDispatcher(workspace_path, shard_store, {})
                results = []
                for abs_p, rel_p, ext in matched_files:
                    df = DiscoveredFile(abs_path=abs_p, rel_path=rel_p,
                                       extension=ext, priority=5)
                    outcome = dispatcher.parse(df)
                    if outcome.error:
                        results.append({"rel_path": rel_p, "error": outcome.error,
                                        "entities": [], "relationships": [], "raw_text": ""})
                    else:
                        results.append({
                            "rel_path": rel_p, "error": None,
                            "entities": outcome.entities,
                            "relationships": outcome.relationships,
                            "raw_text": outcome.raw_text,
                        })

            with shard_store.transaction():
                for res in results:
                    if res.get("error"):
                        error_count += 1
                        log.warning("Shard %s: parse error for %s: %s",
                                    shard_id, res["rel_path"], res["error"])
                        continue
                    for entity in res.get("entities", []):
                        shard_store.upsert_node(
                            entity["id"], entity["type"], entity["name"],
                            entity.get("description", ""), entity.get("metadata", {}),
                        )
                    for rel in res.get("relationships", []):
                        shard_store.upsert_edge(
                            rel["source_id"], rel["target_id"],
                            rel["relationship"], rel.get("metadata", {}),
                        )
                    parsed_count += 1

        duration = round(time.monotonic() - t0, 3)

        # Update state.json — "completed"
        completed_at = datetime.now(timezone.utc).isoformat()
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump({
                "status": "completed",
                "started_at": started_at,
                "completed_at": completed_at,
                "file_count": parsed_count,
            }, f)

        return _ok(ShardIngestResult(
            workspace_path=workspace_path,
            shard_id=shard_id,
            parsed_files=parsed_count,
            error_files=error_count,
            total_nodes=len(shard_store.query_nodes()),
            total_edges=len(shard_store.get_edges()),
            duration_seconds=duration,
        ))

    except Exception as exc:
        log.exception("ingest_workspace_shard failed for %s", shard_id)
        # Mark shard as failed
        try:
            state_path = os.path.join(
                workspace_path, ".context_builder", "shards", shard_id, "state.json"
            )
            if os.path.exists(state_path):
                with open(state_path, "r+", encoding="utf-8") as f:
                    state = json.load(f)
                    state["status"] = "failed"
                    f.seek(0); f.truncate()
                    json.dump(state, f)
        except Exception:
            pass
        return _err("INTERNAL_ERROR", f"Shard ingest failed: {exc}")


@mcp.tool()
def merge_workspace_shards(
    workspace_path: str,
    shard_ids: list,
) -> ToolResponse:
    """Merge completed shard staging areas into the main workspace graph.

    Called by the main agent after all subagents have completed.  Runs edge
    mappers, endpoint deduplication, and (conditionally) blueprint synthesis
    once on the merged graph.  Deletes shard staging directories on success.

    Args:
        workspace_path: Absolute path to the workspace root.
        shard_ids: List of shard IDs to merge (from ``plan_parallel_ingestion``).
    """
    import shutil as _shutil

    try:
        if not os.path.isdir(workspace_path):
            return _err("INVALID_PATH", f"Workspace not found: {workspace_path!r}")

        t0 = time.monotonic()
        store = registry.acquire(workspace_path, make_default=True)
        shards_root = os.path.join(workspace_path, ".context_builder", "shards")

        failed_shards = []
        merged_shards = 0

        with store.transaction():
            for shard_id in shard_ids:
                shard_dir = os.path.join(shards_root, shard_id)
                state_path = os.path.join(shard_dir, "state.json")

                # Check shard status
                if not os.path.exists(state_path):
                    log.warning("Shard %s: state.json missing — skipping", shard_id)
                    failed_shards.append(shard_id)
                    continue

                try:
                    with open(state_path, "r", encoding="utf-8") as f:
                        state = json.load(f)
                except (OSError, json.JSONDecodeError) as exc:
                    log.warning("Shard %s: cannot read state.json (%s) — skipping",
                                shard_id, exc)
                    failed_shards.append(shard_id)
                    continue

                if state.get("status") == "failed":
                    log.warning("Shard %s: status=failed — skipping", shard_id)
                    failed_shards.append(shard_id)
                    continue

                # Load shard graph and merge into main store
                from db.graph_store import GraphStore as _GraphStore
                shard_store = _GraphStore(shard_dir)

                for node_id, data in shard_store.graph.nodes(data=True):
                    store.upsert_node(
                        node_id,
                        data.get("type", "unknown"),
                        data.get("name", ""),
                        data.get("description", ""),
                        data.get("metadata", {}),
                    )
                for u, v, data in shard_store.graph.edges(data=True):
                    store.upsert_edge(
                        u, v,
                        data.get("relationship", "RELATED_TO"),
                        data.get("metadata", {}),
                    )
                merged_shards += 1

            # Post-merge edge mapping (needs cross-shard visibility)
            from engine.extractor import ContextExtractor
            extractor = ContextExtractor(store)
            for mapper in extractor._edge_mappers:
                edges = mapper.map(store)
                for e in edges:
                    store.upsert_edge(
                        e["source_id"], e["target_id"],
                        e["relationship"], e.get("metadata", {}),
                    )
            extractor._endpoint_deduper.rewrite(store)

        # Blueprint synth outside transaction
        try:
            from engine.extractor import ContextExtractor as CE
            _ex = CE(store)
            _ex._utility_compiler.compile()
            _ex._blueprint_synth.synthesize()
        except Exception as exc:
            log.warning("Post-merge blueprint synth failed: %s", exc)

        # Clean up shard staging directories on success
        for shard_id in shard_ids:
            if shard_id not in failed_shards:
                shard_dir = os.path.join(shards_root, shard_id)
                try:
                    _shutil.rmtree(shard_dir, ignore_errors=True)
                except Exception as exc:
                    log.warning("Could not remove shard dir %s: %s", shard_dir, exc)

        duration = round(time.monotonic() - t0, 3)
        return _ok(ShardMergeResult(
            workspace_path=workspace_path,
            shards_merged=merged_shards,
            shards_failed=failed_shards,
            total_nodes=len(store.query_nodes()),
            total_edges=len(store.get_edges()),
            duration_seconds=duration,
        ))

    except Exception as exc:
        log.exception("merge_workspace_shards failed")
        return _err("INTERNAL_ERROR", f"Shard merge failed: {exc}")


@mcp.tool()
def list_active_shards(workspace_path: str) -> ToolResponse:
    """List all shard staging areas for a workspace, including their status.

    Shards older than 24 hours with status 'running' are reported as 'stale'.

    Args:
        workspace_path: Absolute path to the workspace root.
    """
    from datetime import datetime, timezone, timedelta

    try:
        if not os.path.isdir(workspace_path):
            return _err("INVALID_PATH", f"Workspace not found: {workspace_path!r}")

        shards_root = os.path.join(workspace_path, ".context_builder", "shards")
        if not os.path.isdir(shards_root):
            return _ok(ActiveShardsResult(workspace_path=workspace_path, shards=[]))

        shards = []
        stale_threshold = datetime.now(timezone.utc) - timedelta(hours=24)

        for shard_id in sorted(os.listdir(shards_root)):
            shard_dir = os.path.join(shards_root, shard_id)
            if not os.path.isdir(shard_dir):
                continue
            state_path = os.path.join(shard_dir, "state.json")
            if not os.path.exists(state_path):
                shards.append(ShardStatus(shard_id=shard_id, status="unknown"))
                continue
            try:
                with open(state_path, "r", encoding="utf-8") as f:
                    state = json.load(f)
                status = state.get("status", "unknown")
                started_at = state.get("started_at")
                is_stale = False
                if status == "running" and started_at:
                    try:
                        started_dt = datetime.fromisoformat(started_at)
                        if started_dt < stale_threshold:
                            status = "stale"
                            is_stale = True
                    except ValueError:
                        pass
                shards.append(ShardStatus(
                    shard_id=shard_id,
                    status=status,
                    started_at=started_at,
                    completed_at=state.get("completed_at"),
                    file_count=state.get("file_count", 0),
                    is_stale=is_stale,
                ))
            except (OSError, json.JSONDecodeError):
                shards.append(ShardStatus(shard_id=shard_id, status="unknown"))

        return _ok(ActiveShardsResult(workspace_path=workspace_path, shards=shards))

    except Exception as exc:
        log.exception("list_active_shards failed")
        return _err("INTERNAL_ERROR", f"Failed to list shards: {exc}")


@mcp.tool()
def clear_stale_shards(workspace_path: str) -> ToolResponse:
    """Remove shard staging directories that are stale (started > 24h ago with no completion).

    Args:
        workspace_path: Absolute path to the workspace root.
    """
    import shutil as _shutil
    from datetime import datetime, timezone, timedelta

    try:
        if not os.path.isdir(workspace_path):
            return _err("INVALID_PATH", f"Workspace not found: {workspace_path!r}")

        shards_root = os.path.join(workspace_path, ".context_builder", "shards")
        if not os.path.isdir(shards_root):
            return _ok({"cleared": 0, "message": "No shard directory found."})

        stale_threshold = datetime.now(timezone.utc) - timedelta(hours=24)
        cleared = 0

        for shard_id in sorted(os.listdir(shards_root)):
            shard_dir = os.path.join(shards_root, shard_id)
            if not os.path.isdir(shard_dir):
                continue
            state_path = os.path.join(shard_dir, "state.json")
            is_stale = False
            try:
                if os.path.exists(state_path):
                    with open(state_path, "r", encoding="utf-8") as f:
                        state = json.load(f)
                    status = state.get("status", "unknown")
                    started_at = state.get("started_at", "")
                    if status in ("running", "unknown") and started_at:
                        try:
                            started_dt = datetime.fromisoformat(started_at)
                            if started_dt < stale_threshold:
                                is_stale = True
                        except ValueError:
                            is_stale = True
                    elif status == "unknown":
                        is_stale = True
                else:
                    # No state file → always stale
                    is_stale = True
            except (OSError, json.JSONDecodeError):
                is_stale = True

            if is_stale:
                try:
                    _shutil.rmtree(shard_dir, ignore_errors=True)
                    cleared += 1
                    log.info("Cleared stale shard: %s", shard_id)
                except Exception as exc:
                    log.warning("Could not remove stale shard %s: %s", shard_id, exc)

        return _ok({"cleared": cleared,
                    "message": f"Removed {cleared} stale shard(s)."})

    except Exception as exc:
        log.exception("clear_stale_shards failed")
        return _err("INTERNAL_ERROR", f"Failed to clear stale shards: {exc}")


if __name__ == "__main__":
    log.info("Starting Context Builder MCP server (stdio transport)")
    mcp.run()
