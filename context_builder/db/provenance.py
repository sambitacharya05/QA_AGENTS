"""Provenance store — Spec 008 (Wave 4).

Maps each source file to the set of nodes and edges it produced, enabling:

1. **Orphan cleanup on file deletion** — when a file disappears, the nodes/edges
   it exclusively contributed can be removed from the graph.
2. **Stage-aware caching** — only nodes touched by changed or deleted files need
   to flow through the heuristic edge mappers; unaffected nodes are skipped.
3. **Backward-compatibility bootstrap** — a workspace that has graph.json but no
   provenance.json is detected on first read; a best-effort attribution is built
   from node ``metadata.source_file`` fields rather than crashing.

Thread safety
-------------
All mutations (``record_parse``, ``forget``) are guarded by a ``threading.RLock``
so that Spec 009's ``merge_workspace_shards`` — which calls ``record_parse`` for
every node in every shard in a tight loop — does not race against concurrent
tool calls.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Set, Tuple

from db.local_store import LocalJsonStore

log = logging.getLogger(__name__)

PROVENANCE_SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------


@dataclass
class FileProvenance:
    """Provenance record for a single source file."""

    file_rel_path: str
    sha256: str
    last_parsed_iso: str
    node_ids: Set[str] = field(default_factory=set)
    edge_keys: Set[str] = field(default_factory=set)
    # edge_keys are stable strings: f"{source_id}|{relationship}|{target_id}"

    def to_dict(self) -> dict:
        return {
            "file_rel_path": self.file_rel_path,
            "sha256": self.sha256,
            "last_parsed_iso": self.last_parsed_iso,
            "node_ids": sorted(self.node_ids),
            "edge_keys": sorted(self.edge_keys),
        }


# ---------------------------------------------------------------------------
# ProvenanceStore
# ---------------------------------------------------------------------------


class ProvenanceStore:
    """Stores and queries file → {nodes, edges} provenance mappings.

    Persisted to ``<workspace>/.context_builder/provenance.json``.

    Usage::

        prov = ProvenanceStore(workspace_path)

        # After parsing a file:
        prov.record_parse(rel_path, sha256, node_ids={"n1", "n2"},
                          edge_keys={"n1|IMPLEMENTS|n2"})
        prov.save()

        # At the start of the next ingest:
        deleted = prov.all_files() - current_file_set
        for rel in deleted:
            orphan_nodes, orphan_edges = prov.forget(rel)
            # ... remove them from the graph ...
        prov.save()
    """

    def __init__(self, workspace_path: str) -> None:
        self.workspace_path = workspace_path
        storage_dir = os.path.join(workspace_path, ".context_builder")
        os.makedirs(storage_dir, exist_ok=True)
        path = os.path.join(storage_dir, "provenance.json")
        self._store = LocalJsonStore(
            path,
            schema_version=PROVENANCE_SCHEMA_VERSION,
            migrators={},
            use_envelope=False,  # raw JSON — tests read it directly
        )
        self._lock = threading.RLock()
        self._by_file: Dict[str, FileProvenance] = self._load()

    # ------------------------------------------------------------------
    # Internal load
    # ------------------------------------------------------------------

    def _load(self) -> Dict[str, FileProvenance]:
        """Deserialise provenance.json (or return empty dict if absent)."""
        try:
            payload = self._store.safe_read() or {}
        except Exception as exc:
            log.warning("Could not read provenance.json: %s — starting fresh", exc)
            return {}

        out: Dict[str, FileProvenance] = {}
        for rel, raw in (payload.get("files") or {}).items():
            out[rel] = FileProvenance(
                file_rel_path=rel,
                sha256=raw.get("sha256", ""),
                last_parsed_iso=raw.get("last_parsed_iso", ""),
                node_ids=set(raw.get("node_ids", [])),
                edge_keys=set(raw.get("edge_keys", [])),
            )
        return out

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self) -> None:
        """Atomically persist current provenance to disk."""
        with self._lock:
            payload = {
                "schema_version": PROVENANCE_SCHEMA_VERSION,
                "files": {rel: fp.to_dict() for rel, fp in self._by_file.items()},
            }
        with self._store.lock():
            self._store.atomic_write(payload)

    # ------------------------------------------------------------------
    # Queries  (thread-safe reads)
    # ------------------------------------------------------------------

    def all_files(self) -> Set[str]:
        """Return the set of all relative file paths tracked by this store."""
        with self._lock:
            return set(self._by_file.keys())

    def nodes_from(self, file_rel: str) -> Set[str]:
        """Return the node IDs attributed to *file_rel*."""
        with self._lock:
            fp = self._by_file.get(file_rel)
            return set(fp.node_ids) if fp else set()

    def edges_from(self, file_rel: str) -> Set[str]:
        """Return the edge keys attributed to *file_rel*."""
        with self._lock:
            fp = self._by_file.get(file_rel)
            return set(fp.edge_keys) if fp else set()

    def files_for_node(self, node_id: str) -> Set[str]:
        """Return the set of files that contributed *node_id*."""
        with self._lock:
            return {rel for rel, fp in self._by_file.items() if node_id in fp.node_ids}

    def files_for_edge(self, edge_key: str) -> Set[str]:
        """Return the set of files that contributed *edge_key*."""
        with self._lock:
            return {rel for rel, fp in self._by_file.items() if edge_key in fp.edge_keys}

    def tracked_count(self) -> int:
        """Number of files currently tracked."""
        with self._lock:
            return len(self._by_file)

    def orphan_candidate_count(self, graph_node_ids: Set[str]) -> int:
        """Count nodes present in the graph whose source_file is not in provenance.

        Used by ``get_graph_summary`` for an integrity check.  Not perfect — a
        node that was genuinely merged from multiple files may have a single
        ``source_file`` that IS tracked, so this is a conservative lower bound.
        """
        with self._lock:
            tracked_files = set(self._by_file.keys())
        # Caller supplies the graph node IDs; we count those with no provenance
        # We can't check source_file from here without the graph, so just return
        # how many graph nodes are NOT in any provenance record's node_ids.
        tracked_nodes: Set[str] = set()
        with self._lock:
            for fp in self._by_file.values():
                tracked_nodes |= fp.node_ids
        return len(graph_node_ids - tracked_nodes)

    # ------------------------------------------------------------------
    # Mutations  (guarded by RLock for Spec 009 thread-safety)
    # ------------------------------------------------------------------

    def record_parse(
        self,
        file_rel: str,
        sha256: str,
        node_ids: Set[str],
        edge_keys: Set[str],
    ) -> None:
        """Record (or overwrite) the provenance entry for *file_rel*.

        Called after each successful parse.  Any previous node_ids / edge_keys
        for this file are replaced with the new sets (delete-then-insert
        semantics per the dirty-subgraph re-map in Spec 008).
        """
        with self._lock:
            self._by_file[file_rel] = FileProvenance(
                file_rel_path=file_rel,
                sha256=sha256,
                last_parsed_iso=datetime.now(timezone.utc).isoformat(),
                node_ids=set(node_ids),
                edge_keys=set(edge_keys),
            )

    def forget(self, file_rel: str) -> Tuple[Set[str], Set[str]]:
        """Remove provenance for *file_rel* and return orphaned items.

        Returns a ``(orphan_node_ids, orphan_edge_keys)`` tuple.  An item is
        "orphaned" if it is no longer attributed to any remaining file after
        *file_rel* is removed.  Nodes/edges contributed by multiple files
        (deep-merged per Spec 001) survive if any other contributing file
        remains tracked.
        """
        with self._lock:
            fp = self._by_file.pop(file_rel, None)
            if not fp:
                return set(), set()

            # Orphan nodes: node IDs not present in any remaining file's record
            orphan_nodes: Set[str] = set()
            for nid in fp.node_ids:
                still_sourced = any(
                    nid in other.node_ids
                    for other in self._by_file.values()
                )
                if not still_sourced:
                    orphan_nodes.add(nid)

            # Orphan edges: edge keys not present in any remaining file's record
            orphan_edges: Set[str] = set()
            for ek in fp.edge_keys:
                still_sourced = any(
                    ek in other.edge_keys
                    for other in self._by_file.values()
                )
                if not still_sourced:
                    orphan_edges.add(ek)

        return orphan_nodes, orphan_edges

    # ------------------------------------------------------------------
    # Bootstrap from legacy graph (Spec 008 AC8)
    # ------------------------------------------------------------------

    def bootstrap_from_graph(self, graph) -> int:  # graph: nx.DiGraph
        """Populate provenance from existing graph node metadata.

        Called on first ingest after upgrade when ``provenance.json`` is absent.
        Each node's ``metadata.source_file`` is used as the attributing file.
        Edges are not bootstrapped (edge provenance requires re-parse).

        Returns the number of files bootstrapped.
        """
        if self._by_file:
            # Already populated — skip; don't clobber a real provenance file.
            return 0

        file_to_nodes: Dict[str, Set[str]] = {}
        for node_id, data in graph.nodes(data=True):
            src = (data.get("metadata") or {}).get("source_file", "")
            if src:
                file_to_nodes.setdefault(src, set()).add(node_id)

        with self._lock:
            for rel, nids in file_to_nodes.items():
                self._by_file[rel] = FileProvenance(
                    file_rel_path=rel,
                    sha256="",  # unknown — will be refreshed on next parse
                    last_parsed_iso=datetime.now(timezone.utc).isoformat(),
                    node_ids=nids,
                    edge_keys=set(),  # no edge provenance in legacy graphs
                )

        log.info(
            "Bootstrapped provenance from graph: %d files → %d nodes",
            len(file_to_nodes),
            sum(len(v) for v in file_to_nodes.values()),
        )
        return len(file_to_nodes)
