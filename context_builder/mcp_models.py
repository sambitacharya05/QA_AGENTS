"""
Typed Pydantic response envelopes for all MCP tools.

FastMCP serialises Pydantic models to ``structuredContent`` automatically when
typed return annotations are present, allowing Copilot Chat to validate tool
output against a schema and branch on ``code`` without substring matching.

All tools return ``ToolResponse[T]``:
  - ``ok=True``  → ``data`` is populated, ``error`` is None
  - ``ok=False`` → ``error`` is populated, ``data`` is None
"""

from typing import Dict, Generic, List, Literal, Optional, TypeVar

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Spec 006: Heuristic edge evidence block
# ---------------------------------------------------------------------------

T = TypeVar("T")


class HeuristicEvidence(BaseModel):
    """Structured evidence block attached to every heuristic-mapper edge.

    Stored in ``edge.metadata.evidence`` so downstream tooling (Copilot Chat,
    Spec 005's proposal reviewer) can rank and filter edges without parsing
    free-text reason strings.
    """

    method: Literal["tfidf_overlap", "import_graph", "path_match", "explicit_ref"]
    score: float = Field(ge=0.0, le=1.0)
    discriminating_tokens: List[str] = Field(default_factory=list)
    notes: Optional[str] = None


# ---------------------------------------------------------------------------
# Error detail
# ---------------------------------------------------------------------------

class ErrorDetail(BaseModel):
    code: Literal[
        "WORKSPACE_NOT_FOUND",
        "NODE_NOT_FOUND",
        "INVALID_PATH",
        "PATH_TRAVERSAL",
        "VALIDATION_ERROR",
        "INTERNAL_ERROR",
        "NO_CANDIDATES",
        "LOCK_TIMEOUT",       # Spec 009: shard merge timed out waiting for lock
        "SHARD_NOT_FOUND",    # Spec 009: named shard does not exist
        "INVALID_STRATEGY",   # Spec 009: unknown sharding strategy
    ]
    message: str
    hint: Optional[str] = None


# ---------------------------------------------------------------------------
# Generic response envelope
# ---------------------------------------------------------------------------

class ToolResponse(BaseModel, Generic[T]):
    ok: bool
    data: Optional[T] = None
    error: Optional[ErrorDetail] = None


# ---------------------------------------------------------------------------
# Tool-specific data models
# ---------------------------------------------------------------------------

class IngestResult(BaseModel):
    workspace_path: str
    parsed_files: int
    cached_files: int
    deleted_files: int = 0       # Spec 008: files removed since last ingest
    nodes_removed: int = 0       # Spec 008: orphan nodes removed
    edges_removed: int = 0       # Spec 008: orphan edges removed
    total_nodes: int
    total_edges: int
    duration_seconds: float


class NodeSummary(BaseModel):
    id: str
    type: str
    name: str
    description: str = ""
    metadata: dict = Field(default_factory=dict)


class QueryResult(BaseModel):
    query: str
    node_type_filter: Optional[str] = None
    results: List[NodeSummary]
    total_matches: int


class EdgeSummary(BaseModel):
    source_id: str
    target_id: str
    relationship: str
    metadata: dict = Field(default_factory=dict)


class TraceabilityGraph(BaseModel):
    rule_id: str
    nodes: List[NodeSummary]
    edges: List[EdgeSummary]


class GraphSummary(BaseModel):
    workspace_path: str
    total_nodes: int
    node_types_breakdown: dict
    total_edges: int
    relationships_breakdown: dict


class WorkspaceSyncReport(BaseModel):
    workspace_path: str
    is_in_sync: bool
    new_files: List[str]
    modified_files: List[str]
    removed_files: List[str]
    up_to_date_files: List[str]
    total_indexed: int


class DriftReport(BaseModel):
    total_anomalies_detected: int
    drift_edges: List[EdgeSummary]
    node_anomalies: List[dict]


# ---------------------------------------------------------------------------
# Spec 005: Copilot edge-augmentation models
# ---------------------------------------------------------------------------

#: The set of relationship types the host LLM is allowed to propose.
EdgeRel = Literal[
    "IMPLEMENTS",
    "VALIDATES",
    "TESTS",
    "USES_MODEL",
    "EXCLUDES",
    "MAPS_TO",
]


class EdgeProposal(BaseModel):
    """A single edge proposal returned by the host LLM (e.g. Copilot Chat).

    ``relationship`` may be ``null`` (``None``) — the LLM signals "I cannot
    determine" — in which case the proposal is silently skipped.  A missing
    ``source_id``, ``target_id``, or ``confidence`` field is a schema violation
    and causes the entire ``apply_edge_proposals`` call to return
    ``VALIDATION_ERROR``.
    """

    source_id: str
    target_id: str
    relationship: Optional[EdgeRel] = None
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = ""


class EdgeProposalApplyResult(BaseModel):
    """Summary of ``apply_edge_proposals`` outcome."""

    accepted: List[Dict]
    rejected: List[Dict]
    total_proposed: int


# ---------------------------------------------------------------------------
# Convenience factory helpers (used in main.py)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Spec 009: Parallel ingestion models
# ---------------------------------------------------------------------------


class ShardPlanEntry(BaseModel):
    """A single shard returned by ``plan_parallel_ingestion``."""

    shard_id: str
    include_globs: List[str]
    exclude_globs: List[str]
    estimated_file_count: int
    estimated_byte_size: int
    description: str


class ShardIngestResult(BaseModel):
    """Summary returned by ``ingest_workspace_shard``."""

    workspace_path: str
    shard_id: str
    parsed_files: int
    error_files: int
    total_nodes: int
    total_edges: int
    duration_seconds: float


class ShardMergeResult(BaseModel):
    """Summary returned by ``merge_workspace_shards``."""

    workspace_path: str
    shards_merged: int
    shards_failed: List[str]
    total_nodes: int
    total_edges: int
    duration_seconds: float


class ShardStatus(BaseModel):
    """Single shard state entry returned by ``list_active_shards``."""

    shard_id: str
    status: str  # "pending" | "running" | "completed" | "failed" | "stale"
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    file_count: int = 0
    is_stale: bool = False


class ActiveShardsResult(BaseModel):
    """Result of ``list_active_shards``."""

    workspace_path: str
    shards: List[ShardStatus]


# ---------------------------------------------------------------------------
# SPEC-3 Wave 3: anomaly recording schemas
# ---------------------------------------------------------------------------


class AnomalyEvidence(BaseModel):
    """Structured evidence block on every behavioral_anomaly record."""

    summary: str = Field(..., description="One-sentence explanation of the divergence")
    source_file: Optional[str] = Field(None, description="Workspace-relative path of the source file")
    related_node_ids: List[str] = Field(default_factory=list)
    related_edge_keys: List[str] = Field(default_factory=list)


class BehavioralAnomalyRecord(BaseModel):
    """Single anomaly entry persisted in a node's metadata.behavioral_anomalies."""

    node_id: str
    anomaly_kind: Literal[
        "ui_without_requirement",
        "rule_without_implementation",
        "constant_spec_divergence",
        "endpoint_without_test",
    ]
    severity: Literal["critical", "warning", "info"]
    detected_by: str = Field("relationship-linker", description="Agent that recorded the anomaly")
    detected_at: str = Field(..., description="ISO-8601 UTC timestamp")
    evidence: AnomalyEvidence
    suggested_action: Optional[str] = None


class LinkerSummary(BaseModel):
    """Summary returned by the relationship-linker after a run."""

    workspace_path: str
    edges_emitted: int
    edges_skipped_off_policy: int
    anomalies_recorded: int
    anomalies_by_kind: Dict[str, int]
    duration_seconds: float


class RecordAnomalyResult(BaseModel):
    """Return shape from the record_anomaly MCP tool."""

    node_id: str
    anomaly_kind: str
    severity: str
    persisted: bool


# ---------------------------------------------------------------------------
# Convenience factory helpers (used in main.py)
# ---------------------------------------------------------------------------


def ok(data: T) -> ToolResponse:  # type: ignore[type-arg]
    """Shorthand for a successful ToolResponse."""
    return ToolResponse(ok=True, data=data)


def err(
    code: str,
    message: str,
    hint: Optional[str] = None,
) -> ToolResponse:  # type: ignore[type-arg]
    """Shorthand for a failed ToolResponse."""
    return ToolResponse(
        ok=False,
        error=ErrorDetail(code=code, message=message, hint=hint),  # type: ignore[arg-type]
    )
