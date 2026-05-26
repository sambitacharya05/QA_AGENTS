import os
import json
import logging
import hashlib
import networkx as nx
from contextlib import contextmanager
from typing import List, Dict, Any, Optional

from db.local_store import LocalJsonStore
from db.migrations import (
    GRAPH_SCHEMA_VERSION, GRAPH_MIGRATORS,
    DOCS_SCHEMA_VERSION, DOCS_MIGRATORS,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Custom exceptions (Spec 006)
# ---------------------------------------------------------------------------

class EdgeEndpointMissing(ValueError):
    """Raised by ``upsert_edge(..., missing_endpoint='raise')`` when either
    endpoint node does not exist in the graph.

    Use this mode in new mapper/test code so missing-endpoint bugs surface
    immediately rather than being silently swallowed.
    """


class GraphStore:
    """Manages the NetworkX graph and document metadata, persisting to JSON.

    Storage is backed by :class:`db.local_store.LocalJsonStore` which provides:
    - Atomic writes (tempfile + os.replace)
    - Advisory file locking (fcntl / msvcrt)
    - Automatic .bak rotation and .corrupt quarantine on load failure
    - Schema-version envelope with automatic migration

    Use :meth:`transaction` to batch a full ingest into at most two writes
    (one optional mid-pipeline checkpoint + one final commit) instead of one
    write per mutation.
    """

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def to_rel_path(self, path: str) -> str:
        if not path:
            return path
        if not os.path.isabs(path):
            return path.replace('\\', '/')
        rel = os.path.relpath(path, self.workspace_path).replace('\\', '/')
        return rel

    def to_abs_path(self, path: str) -> str:
        if not path:
            return path
        if os.path.isabs(path):
            return path
        return os.path.normpath(os.path.join(self.workspace_path, path))

    # ------------------------------------------------------------------
    # Constructor
    # ------------------------------------------------------------------

    def __init__(self, workspace_path: str):
        self.workspace_path = workspace_path
        self.storage_dir = os.path.join(workspace_path, ".context_builder")
        os.makedirs(self.storage_dir, exist_ok=True)

        self._graph_store = LocalJsonStore(
            os.path.join(self.storage_dir, "graph.json"),
            schema_version=GRAPH_SCHEMA_VERSION,
            migrators=GRAPH_MIGRATORS,
            use_envelope=True,
        )
        self._docs_store = LocalJsonStore(
            os.path.join(self.storage_dir, "documents.json"),
            schema_version=DOCS_SCHEMA_VERSION,
            migrators=DOCS_MIGRATORS,
            use_envelope=True,
        )

        # Dirty flags — set by mutation methods; cleared by save_* methods
        self._graph_dirty: bool = False
        self._docs_dirty: bool = False

        self.graph = self._load_graph()
        self.documents = self._load_documents()

        # Stateful Path Alignment Sweep to convert legacy absolute paths to relative paths
        changes_detected = False

        migrated_documents = {}
        for k, v in self.documents.items():
            idx = -1
            if "ingest/" in k:
                idx = k.find("ingest/")
            elif "ingest\\" in k:
                idx = k.find("ingest\\")

            if idx != -1:
                suffix = k[idx:]
                new_k = suffix.replace('\\', '/')
                if k != new_k:
                    migrated_documents[new_k] = v
                    changes_detected = True
                else:
                    migrated_documents[k] = v
            else:
                rel_k = self.to_rel_path(k)
                if k != rel_k:
                    migrated_documents[rel_k] = v
                    changes_detected = True
                else:
                    migrated_documents[k] = v
        self.documents = migrated_documents

        for node_id, node_data in self.graph.nodes(data=True):
            metadata = node_data.get("metadata") or {}
            source_file = metadata.get("source_file")
            if source_file and isinstance(source_file, str):
                idx = -1
                if "ingest/" in source_file:
                    idx = source_file.find("ingest/")
                elif "ingest\\" in source_file:
                    idx = source_file.find("ingest\\")
                if idx != -1:
                    new_source_file = source_file[idx:].replace('\\', '/')
                    if source_file != new_source_file:
                        metadata["source_file"] = new_source_file
                        changes_detected = True
                else:
                    rel_source_file = self.to_rel_path(source_file)
                    if source_file != rel_source_file:
                        metadata["source_file"] = rel_source_file
                        changes_detected = True

            sync_gov = metadata.get("sync_governance") or {}
            source_origins = sync_gov.get("source_origins") or []
            if source_origins and isinstance(source_origins, list):
                new_origins = []
                origin_changed = False
                for origin in source_origins:
                    if isinstance(origin, str):
                        idx = -1
                        if "ingest/" in origin:
                            idx = origin.find("ingest/")
                        elif "ingest\\" in origin:
                            idx = origin.find("ingest\\")
                        if idx != -1:
                            new_origin = origin[idx:].replace('\\', '/')
                            if origin != new_origin:
                                new_origins.append(new_origin)
                                origin_changed = True
                                continue
                        else:
                            rel_origin = self.to_rel_path(origin)
                            if origin != rel_origin:
                                new_origins.append(rel_origin)
                                origin_changed = True
                                continue
                    new_origins.append(origin)
                if origin_changed:
                    sync_gov["source_origins"] = new_origins
                    changes_detected = True

        if changes_detected:
            self.save_graph()
            self.save_documents()

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------

    def _load_graph(self) -> nx.DiGraph:
        """Load the graph from disk; return an empty DiGraph if not yet created."""
        with self._graph_store.lock():
            try:
                payload = self._graph_store.safe_read()
            except Exception as exc:
                log.warning(
                    "Could not load graph at %s: %s — starting with empty graph",
                    self._graph_store.file_path, exc,
                )
                return nx.DiGraph()
        if payload is None:
            return nx.DiGraph()
        try:
            # NetworkX >=3.4: pass edges= explicitly to avoid deprecation warning
            return nx.node_link_graph(payload, directed=True, edges="edges")
        except TypeError:
            # Fallback for older NetworkX that doesn't understand edges=
            return nx.node_link_graph(payload, directed=True)

    def _load_documents(self) -> Dict[str, Dict[str, Any]]:
        """Load document metadata and content from disk."""
        with self._docs_store.lock():
            try:
                payload = self._docs_store.safe_read()
            except Exception as exc:
                log.warning(
                    "Could not load documents at %s: %s — starting fresh",
                    self._docs_store.file_path, exc,
                )
                return {}
        return payload if payload is not None else {}

    # ------------------------------------------------------------------
    # Save (public)
    # ------------------------------------------------------------------

    def save_graph(self) -> None:
        """Force-write the in-memory graph to disk atomically.

        Prefer :meth:`transaction` for batched writes during ingestion.
        """
        with self._graph_store.lock():
            try:
                payload = nx.node_link_data(self.graph, edges="edges")
            except TypeError:
                # Older NetworkX without edges= kwarg
                payload = nx.node_link_data(self.graph)
            self._graph_store.atomic_write(payload)
        self._graph_dirty = False

    def save_documents(self) -> None:
        """Force-write the documents dict to disk atomically."""
        with self._docs_store.lock():
            self._docs_store.atomic_write(self.documents)
        self._docs_dirty = False

    # ------------------------------------------------------------------
    # Transaction context manager
    # ------------------------------------------------------------------

    @contextmanager
    def transaction(self):
        """Coalesce all writes within the block into at most one flush each.

        All upsert_node / upsert_edge / upsert_raw_document calls inside the
        block only set dirty flags; the actual I/O happens once on exit.

        Example::

            with store.transaction():
                for fp in files:
                    store.upsert_node(...)
            # → exactly one save_graph() + one save_documents() on exit
        """
        try:
            yield self
        finally:
            if self._graph_dirty:
                self.save_graph()
            if self._docs_dirty:
                self.save_documents()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def compute_checksum(self, file_path: str) -> str:
        """Compute SHA-256 checksum of *file_path*."""
        sha256 = hashlib.sha256()
        with open(file_path, 'rb') as f:
            while chunk := f.read(8192):
                sha256.update(chunk)
        return sha256.hexdigest()

    def should_reparse(self, file_path: str) -> bool:
        """Return True if *file_path* has changed since the last ingestion."""
        if not os.path.exists(file_path):
            return False
        current_checksum = self.compute_checksum(file_path)
        doc_info = self.documents.get(self.to_rel_path(file_path))
        if not doc_info:
            return True
        return doc_info.get("checksum") != current_checksum

    # ------------------------------------------------------------------
    # Mutation methods  (mark dirty; do NOT save inline)
    # ------------------------------------------------------------------

    def upsert_raw_document(self, file_path: str, content: str) -> None:
        """Store or update the parsed raw text content of *file_path*."""
        checksum = self.compute_checksum(file_path)
        rel_path = self.to_rel_path(file_path)
        self.documents[rel_path] = {
            "checksum": checksum,
            "content": content
        }
        self._docs_dirty = True
        # Callers that are NOT inside a transaction() get immediate persistence
        # via save_documents() only if they call it explicitly (e.g. transaction exit).
        # For backward compatibility with code that expects save after every call,
        # we persist here.  Inside a transaction() the flag will be cleared once
        # at the end; outside, it is cleared immediately below.
        self.save_documents()

    def upsert_node(
        self,
        node_id: str,
        node_type: str,
        name: str,
        description: str,
        metadata: Dict[str, Any],
    ) -> None:
        """Insert or statefully merge attributes for a node.

        Enforces SSoT governance: agent calls cannot overwrite documentation-
        locked nodes; instead the deviation is recorded in behavioral_anomalies.
        """
        # Ontology Harmonization and strict type coercion
        rich_custom_categories = {
            "claims", "billing", "access_control", "enrollment", "non_functional"
        }
        if node_type in rich_custom_categories:
            if metadata is None:
                metadata = {}
            metadata["rule_category"] = node_type
            node_type = "business_rule"

        if metadata is None:
            metadata = {}

        # Enforce clean workspace-relative paths
        if "source_file" in metadata and metadata["source_file"]:
            metadata["source_file"] = self.to_rel_path(metadata["source_file"])

        incoming_sync = metadata.get("sync_governance") or {}
        if "source_origins" in incoming_sync and incoming_sync["source_origins"]:
            incoming_sync["source_origins"] = [
                self.to_rel_path(o) for o in incoming_sync["source_origins"] if o
            ]
            metadata["sync_governance"] = incoming_sync

        if self.graph.has_node(node_id):
            existing_data = self.graph.nodes[node_id]
            existing_meta = existing_data.get("metadata") or {}

            # Governance guardrail
            existing_sync = existing_meta.get("sync_governance") or {}
            is_locked = existing_sync.get("origin") == "documentation"
            incoming_sync = metadata.get("sync_governance") or {}
            caller_is_agent = incoming_sync.get("caller") == "agent"

            if is_locked and caller_is_agent:
                log.warning(
                    "Governance Guardrail: blocked agent modification of locked node %s",
                    node_id,
                )
                merged_metadata = dict(existing_meta)
                if "behavioral_anomalies" not in merged_metadata:
                    merged_metadata["behavioral_anomalies"] = []
                merged_metadata["behavioral_anomalies"].append({
                    "timestamp": incoming_sync.get("timestamp", "unknown"),
                    "observed_deviation_profile": description,
                    "reported_by": metadata.get("source_file", "playwright_mcp_explorer"),
                })
                self.graph.nodes[node_id]["metadata"] = merged_metadata
                self._graph_dirty = True
                return

            # Priority Rule: preserve richer descriptions
            if (
                len(description) < len(existing_data.get("description", ""))
                and node_type == "business_rule"
            ):
                final_description = existing_data.get("description")
            else:
                final_description = description

            # Deep merge metadata
            merged_metadata = dict(existing_meta)
            for key, value in metadata.items():
                if isinstance(value, dict) and isinstance(merged_metadata.get(key), dict):
                    merged_metadata[key] = {**merged_metadata[key], **value}
                else:
                    merged_metadata[key] = value

            merged_metadata["sync_governance"] = {
                "is_merged": True,
                "origin": incoming_sync.get("origin", existing_sync.get("origin", "unknown")),
                "source_origins": list(set(
                    [self.to_rel_path(o) for o in existing_sync.get("source_origins", []) if o]
                    + [self.to_rel_path(metadata.get("source_file", "unknown"))]
                )),
            }

            self.graph.add_node(
                node_id,
                type=node_type,
                name=name if len(name) > len(existing_data.get("name", "")) else existing_data.get("name"),
                description=final_description,
                metadata=merged_metadata,
            )
        else:
            incoming_sync = metadata.get("sync_governance") or {}
            if "sync_governance" not in metadata:
                metadata["sync_governance"] = {
                    "is_merged": False,
                    "origin": incoming_sync.get("origin", "unknown"),
                    "source_origins": [self.to_rel_path(metadata.get("source_file", "unknown"))],
                }
            else:
                metadata["sync_governance"]["source_origins"] = [
                    self.to_rel_path(o) for o in incoming_sync.get("source_origins", []) if o
                ]

            self.graph.add_node(
                node_id,
                type=node_type,
                name=name,
                description=description,
                metadata=metadata,
            )

        self._graph_dirty = True

    def has_edge(self, source_id: str, target_id: str, relationship: str) -> bool:
        """Return True if a directed edge of *relationship* type exists.

        Used by heuristic mappers (Spec 006) and Copilot proposal paths to
        avoid overwriting higher-confidence parser-emitted edges with lower-
        confidence inferred ones.
        """
        if not self.graph.has_edge(source_id, target_id):
            return False
        data = self.graph.edges[source_id, target_id]
        return data.get("relationship", "").upper() == relationship.upper()

    def upsert_edge(
        self,
        source_id: str,
        target_id: str,
        relationship: str,
        metadata: Dict[str, Any] = None,
        missing_endpoint: str = "warn",
    ) -> bool:
        """Insert or update a directed edge in the graph.

        Parameters
        ----------
        missing_endpoint:
            Controls behaviour when either endpoint node does not exist:

            ``"warn"`` (default)
                Log a WARNING and return ``False``.  Preserves the contract
                for all existing call-sites.
            ``"raise"``
                Raise :class:`EdgeEndpointMissing`.  Preferred for new mapper
                code so bugs surface immediately in tests.
            ``"silent"``
                Pre-existing silent-drop behaviour, retained for compatibility
                but strongly discouraged.

        Returns
        -------
        bool
            ``True`` if the edge was inserted/updated; ``False`` if rejected
            due to missing endpoints.
        """
        if metadata is None:
            metadata = {}
        if not self.graph.has_node(source_id) or not self.graph.has_node(target_id):
            msg = (
                f"upsert_edge skipped — missing endpoint(s): "
                f"source={source_id!r} target={target_id!r} rel={relationship!r}"
            )
            if missing_endpoint == "raise":
                raise EdgeEndpointMissing(msg)
            if missing_endpoint == "warn":
                log.warning(msg)
            # "silent" → fall through and return False
            return False

        self.graph.add_edge(
            source_id, target_id,
            relationship=relationship.upper(),
            metadata=metadata,
        )
        self._graph_dirty = True
        return True

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def get_node(self, node_id: str) -> Optional[Dict[str, Any]]:
        """Return a single node by ID, or None."""
        if self.graph.has_node(node_id):
            node = dict(self.graph.nodes[node_id])
            node["id"] = node_id
            return node
        return None

    def query_nodes(
        self,
        query_str: Optional[str] = None,
        node_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return nodes matching an optional type filter and/or keyword search."""
        results = []
        for node_id, data in self.graph.nodes(data=True):
            if node_type and data.get("type") != node_type:
                continue
            if query_str:
                term = query_str.lower()
                name = data.get("name", "").lower()
                desc = data.get("description", "").lower()
                meta_str = str(data.get("metadata", {})).lower()
                if term not in name and term not in desc and term not in meta_str:
                    continue
            node_dict = dict(data)
            node_dict["id"] = node_id
            results.append(node_dict)
        return results

    def get_edges(
        self,
        source_id: Optional[str] = None,
        target_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return edges, optionally filtered by source or target ID."""
        results = []
        for u, v, data in self.graph.edges(data=True):
            if source_id and u != source_id:
                continue
            if target_id and v != target_id:
                continue
            results.append({
                "source_id": u,
                "target_id": v,
                "relationship": data.get("relationship", ""),
                "metadata": data.get("metadata", {}),
            })
        return results

    def get_traceability_graph(
        self,
        rule_id: str,
        depth: int = 2,
        max_nodes: int = 80,
        min_edge_confidence: float = 0.0,
    ) -> Dict[str, Any]:
        """Return the local neighbourhood of *rule_id*, BFS-bounded.

        Replaces the previous ``nx.node_connected_component`` call which
        returned the *entire* weakly-connected component — potentially hundreds
        of nodes on a mature graph (Spec 006).

        Parameters
        ----------
        depth:
            Maximum hops from *rule_id* (default 2: rule → direct → one-hop).
        max_nodes:
            Hard cap on returned nodes.  When truncation is needed the nodes
            reachable via the highest-confidence edges are retained first.
        min_edge_confidence:
            Drop edges whose ``metadata.confidence`` is below this threshold
            from the BFS traversal entirely.  Useful to exclude very weak
            heuristic edges from traceability views.

        Returns
        -------
        dict
            ``{"nodes": [...], "edges": [...], "metadata": {...}}``
        """
        if not self.graph.has_node(rule_id):
            return {"nodes": [], "edges": []}

        # Build a confidence-filtered undirected view for BFS traversal
        undirected = nx.Graph()
        for u, v, data in self.graph.edges(data=True):
            conf = data.get("metadata", {}).get("confidence", 1.0)
            if conf >= min_edge_confidence:
                undirected.add_edge(u, v, **data)

        if not undirected.has_node(rule_id):
            # The rule itself has no qualifying edges; return just the rule node
            node_data = dict(self.graph.nodes[rule_id])
            node_data["id"] = rule_id
            return {"nodes": [node_data], "edges": [],
                    "metadata": {"depth": depth, "truncated": False}}

        # BFS up to *depth* hops
        visited: set[str] = {rule_id}
        frontier: set[str] = {rule_id}
        for _ in range(depth):
            next_frontier: set[str] = set()
            for n in frontier:
                if undirected.has_node(n):
                    for nb in undirected.neighbors(n):
                        if nb not in visited:
                            next_frontier.add(nb)
            visited |= next_frontier
            frontier = next_frontier
            if not frontier:
                break

        truncated = False
        if len(visited) > max_nodes:
            # Rank nodes by the maximum confidence of their best incident edge.
            # rule_id is ALWAYS kept and counts toward the budget so the
            # returned node count is exactly max_nodes (never max_nodes+1).
            def _max_conf(node: str) -> float:
                best = 0.0
                if undirected.has_node(node):
                    for nb in undirected.neighbors(node):
                        if nb in visited:
                            conf = undirected[node][nb].get(
                                "metadata", {}
                            ).get("confidence", 1.0)
                            if conf > best:
                                best = conf
                return best

            ranked_rest = sorted(
                (n for n in visited if n != rule_id),
                key=_max_conf, reverse=True,
            )
            visited = {rule_id} | set(ranked_rest[:max_nodes - 1])
            truncated = True

        nodes = []
        for n in visited:
            if self.graph.has_node(n):
                node_dict = dict(self.graph.nodes[n])
                node_dict["id"] = n
                nodes.append(node_dict)

        edges = []
        for u, v, data in self.graph.edges(data=True):
            if u in visited and v in visited:
                edges.append({
                    "source_id": u,
                    "target_id": v,
                    "relationship": data.get("relationship", ""),
                    "metadata": data.get("metadata", {}),
                })

        return {
            "nodes": nodes,
            "edges": edges,
            "metadata": {"depth": depth, "truncated": truncated},
        }

    def get_raw_documents(self) -> List[Dict[str, str]]:
        """Return all raw documents as a list of {path, content} dicts."""
        return [
            {"path": path, "content": info.get("content", "")}
            for path, info in self.documents.items()
        ]

    def has_node(self, node_id: str) -> bool:
        """Return True if *node_id* exists in the graph."""
        return self.graph.has_node(node_id)

    def remove_node(self, node_id: str) -> bool:
        """Delete a node and all its incident edges from the graph.

        NetworkX cascades incident edges automatically when a node is removed,
        so callers do not need to manually delete edges first.

        Returns
        -------
        bool
            ``True`` if the node existed and was removed; ``False`` if absent.
        """
        if not self.graph.has_node(node_id):
            return False
        self.graph.remove_node(node_id)  # NetworkX cascades incident edges
        self._graph_dirty = True
        return True

    def remove_edge(
        self, source_id: str, target_id: str, relationship: str
    ) -> bool:
        """Remove the directed edge from *source_id* to *target_id*.

        The *relationship* parameter is accepted for API clarity and forward-
        compatibility with multi-edge graphs, but is not currently used as a
        discriminator (NetworkX DiGraph stores at most one edge per (u, v) pair).

        Returns
        -------
        bool
            ``True`` if the edge existed and was removed; ``False`` if absent.
        """
        if not self.graph.has_edge(source_id, target_id):
            return False
        self.graph.remove_edge(source_id, target_id)
        self._graph_dirty = True
        return True

    def checksum_of(self, abs_path: str) -> str:
        """Return the SHA-256 hash of *abs_path*.

        Public wrapper around :meth:`compute_checksum` so the orchestrator can
        record file provenance without re-hashing a file that was just parsed.
        """
        return self.compute_checksum(abs_path)

    def clear_graph(self) -> None:
        """Delete all nodes, edges, and document references."""
        self.graph.clear()
        self.documents = {}
        self.save_graph()
        self.save_documents()
