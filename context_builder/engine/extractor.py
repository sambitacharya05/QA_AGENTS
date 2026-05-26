"""Context extraction orchestrator.

Spec 003 (Wave 2): This module is now a thin orchestrator.  The seven
responsibilities previously crammed into a 1,020-LOC god-class have been
extracted to focused sub-modules:

  engine/ingestion/walker.py          — file discovery + priority sort
  engine/ingestion/parse_dispatcher.py — parser factory + governance stamp
  engine/edges/heuristic.py           — token-overlap edge mapper
  engine/edges/test_framework.py      — import-graph edge mapper
  engine/edges/endpoint_deduper.py    — OpenAPI ↔ RestAssured merge
  engine/blueprint/utility_compiler.py — method-signature mining
  engine/blueprint/synthesizer.py     — framework + template inference

Spec 008 (Wave 4): Incremental indexing v2 — provenance table, orphan cleanup
on file delete, and stage-aware caching for edge mappers and blueprint synth.

  db/provenance.py                    — per-file → {nodes, edges} index
  engine/edges/base.py                — EdgeMapper.map_incremental() contract

``BlueprintStore`` is constructed exactly once per ``ContextExtractor``
instance and injected into every sub-module that needs it.
"""

import logging
import os
from typing import Dict, Any, List, Set, Union

from db.graph_store import GraphStore
from db.blueprint_store import BlueprintStore
from db.provenance import ProvenanceStore
from engine.hooks import lifecycle_hooks
from engine.ingestion.walker import IngestionWalker
from engine.ingestion.parse_dispatcher import ParseDispatcher, ParseOutcome
from engine.edges.heuristic import HeuristicEdgeMapper
from engine.edges.test_framework import TestEdgeMapper
from engine.edges.endpoint_deduper import EndpointDeduper
from engine.blueprint.utility_compiler import UtilityCompiler
from engine.blueprint.synthesizer import BlueprintSynthesizer

log = logging.getLogger(__name__)

# Sentinel filenames that trigger blueprint re-synthesis when changed
_BLUEPRINT_SENTINELS: frozenset = frozenset({
    "package.json", "pom.xml", "requirements.txt", "build.gradle",
    "pyproject.toml", "Cargo.toml", "go.mod",
})


class ContextExtractor:
    """Thin orchestrator — wires sub-modules and drives the ingest pipeline."""

    def __init__(self, workspace_path_or_store: Union[str, GraphStore]):
        """Accept either a workspace path (str) or a pre-built GraphStore.

        Passing a ``GraphStore`` is preferred from ``main.py`` so the MCP tool
        and the extractor share the same store instance (and the same
        ``transaction()`` context).  Passing a string is retained for backward
        compatibility with existing tests.
        """
        if isinstance(workspace_path_or_store, GraphStore):
            self.store = workspace_path_or_store
            self.workspace_path = workspace_path_or_store.workspace_path
        else:
            self.workspace_path = workspace_path_or_store
            self.store = GraphStore(workspace_path_or_store)

        self.shared_context: Dict[str, Any] = {}

        # Sub-modules — wired once per extractor instance
        self._walker = IngestionWalker(self.workspace_path)
        self._dispatcher = ParseDispatcher(
            self.workspace_path, self.store, self.shared_context
        )

        # BlueprintStore constructed ONCE and shared with all blueprint sub-modules
        self._blueprint = BlueprintStore(self.workspace_path)

        self._edge_mappers = [
            HeuristicEdgeMapper(),
            TestEdgeMapper(),
        ]
        self._endpoint_deduper = EndpointDeduper()
        self._utility_compiler = UtilityCompiler(self.store, self._blueprint)
        self._blueprint_synth = BlueprintSynthesizer(
            self.store, self._blueprint, self.workspace_path
        )

        # Spec 008 — provenance store (lazy init so old-format workspaces work)
        self._provenance = ProvenanceStore(self.workspace_path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ingest_workspace(self) -> Dict[str, Any]:
        """Scan workspace, parse supported files, and build the graph.

        Spec 008 changes vs. Wave 2:
        1. **Deletion sweep** — files removed from disk have their orphan
           nodes/edges cleaned up before parsing begins.
        2. **Provenance recording** — every successfully parsed file's nodes
           and edges are catalogued so future runs can compute the dirty set.
        3. **Stage-aware edge mapping** — heuristic mappers use
           ``map_incremental()`` when a dirty set exists, turning O(N²) into
           O(|dirty| · N).
        4. **Blueprint synth gating** — ``BlueprintSynthesizer`` only runs
           when a config sentinel file (package.json, pom.xml, …) changed.

        All graph mutations are wrapped in a single ``store.transaction()`` so
        the graph and documents are written to disk at most twice during a full
        ingest.  Blueprint compilation runs outside the transaction (writes to
        blueprint.json, not graph.json).
        """
        lifecycle_hooks.trigger("on_parse_start", self.workspace_path)

        self.shared_context = {}  # reset for each run

        # Bootstrap provenance from graph metadata if this is a fresh upgrade
        if self._provenance.tracked_count() == 0 and self.store.graph.number_of_nodes() > 0:
            bootstrapped = self._provenance.bootstrap_from_graph(self.store.graph)
            if bootstrapped:
                log.info(
                    "Bootstrapped provenance for %d files on upgrade run", bootstrapped
                )

        current_files: Set[str] = {
            df.rel_path for df in self._walker.discover()
        }
        previous_files: Set[str] = self._provenance.all_files()
        deleted: Set[str] = previous_files - current_files

        parsed_count = 0
        skipped_count = 0
        n_nodes_removed = 0
        n_edges_removed = 0
        dirty_files: List = []  # DiscoveredFile objects for changed files

        with self.store.transaction():
            # ---- 1. Orphan cleanup for deleted files ----
            for rel in deleted:
                orphan_nodes, orphan_edges = self._provenance.forget(rel)
                for nid in orphan_nodes:
                    if self.store.remove_node(nid):
                        n_nodes_removed += 1
                        log.debug("Removed orphan node %s (source file deleted)", nid)
                for ek in orphan_edges:
                    parts = ek.split("|", 2)
                    if len(parts) == 3:
                        src, rel_type, tgt = parts
                        if self.store.remove_edge(src, tgt, rel_type):
                            n_edges_removed += 1

            if deleted:
                log.info(
                    "Deletion sweep: %d file(s) removed → %d orphan nodes, "
                    "%d orphan edges cleaned up",
                    len(deleted), n_nodes_removed, n_edges_removed,
                )

            # ---- 2. Parse only dirty (changed or new) files ----
            for df in self._walker.discover():
                try:
                    if self.store.should_reparse(df.abs_path):
                        outcome = self._dispatcher.parse(df)
                        if outcome.error:
                            log.warning(
                                "Failed to ingest %s: %s",
                                os.path.basename(df.abs_path),
                                outcome.error,
                            )
                        else:
                            self._apply_outcome(outcome)
                            # Record provenance for this file
                            sha256 = self.store.checksum_of(df.abs_path)
                            node_ids = {e["id"] for e in outcome.entities}
                            edge_keys = {
                                f"{r['source_id']}|{r['relationship']}|{r['target_id']}"
                                for r in outcome.relationships
                            }
                            self._provenance.record_parse(
                                df.rel_path, sha256, node_ids, edge_keys
                            )
                            dirty_files.append(df)
                            parsed_count += 1
                    else:
                        skipped_count += 1
                except Exception as exc:
                    log.warning(
                        "Failed to ingest %s: %s",
                        os.path.basename(df.abs_path), exc
                    )

            # ---- 3. Stage-aware edge mapping ----
            if dirty_files or deleted:
                dirty_node_ids = self._compute_dirty_node_ids(dirty_files, deleted)
                for mapper in self._edge_mappers:
                    if hasattr(mapper, "map_incremental") and dirty_files:
                        edges = mapper.map_incremental(self.store, dirty_node_ids)
                    else:
                        edges = mapper.map(self.store)
                    for e in edges:
                        self.store.upsert_edge(
                            e["source_id"],
                            e["target_id"],
                            e["relationship"],
                            e.get("metadata", {}),
                        )
                self._endpoint_deduper.rewrite(self.store)
            else:
                log.info(
                    "No-op ingest: no files changed or deleted — "
                    "skipping edge mappers and endpoint deduplication."
                )

        # transaction() exits here → at most two disk writes

        # ---- 4. Blueprint compilation (gated by config-file changes) ----
        try:
            from engine.standards_scanner import StandardsScanner

            scanner = StandardsScanner(self.workspace_path)
            detected = scanner.detect_standards()

            if detected:
                standards_global = (
                    self._blueprint.blueprint_data
                    .get("coding_standards", {})
                    .get("global", {})
                )
                if not standards_global.get("is_locked", False):
                    self._blueprint.blueprint_data["coding_standards"]["global"][
                        "naming_convention"
                    ] = detected["naming_convention"]
                    self._blueprint.blueprint_data["coding_standards"]["global"][
                        "indentation"
                    ] = detected["indentation"]
                    self._blueprint.save_blueprint()
                    log.info("Dynamically updated coding standards: %s", detected)

            # Only run blueprint synth if a config sentinel file changed
            if self._blueprint_inputs_changed(dirty_files):
                self._utility_compiler.compile()
                self._blueprint_synth.synthesize()
            elif dirty_files or deleted:
                # Still run utility compiler for code-component changes
                self._utility_compiler.compile()
            else:
                log.info("Skipping blueprint synthesis — no config sentinel files changed.")

        except Exception as exc:
            log.warning(
                "Failed to compile blueprint capabilities or detect standards: %s",
                exc,
                exc_info=True,
            )

        # Persist updated provenance
        self._provenance.save()

        lifecycle_hooks.trigger("on_extraction_complete", self.workspace_path)

        return {
            "status": "success",
            "parsed_files": parsed_count,
            "cached_files": skipped_count,
            "deleted_files": len(deleted),
            "nodes_removed": n_nodes_removed,
            "edges_removed": n_edges_removed,
            "total_nodes": len(self.store.query_nodes()),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _apply_outcome(self, outcome: ParseOutcome) -> None:
        """Persist a successfully parsed file's entities and relationships."""
        self.store.upsert_raw_document(outcome.file.abs_path, outcome.raw_text)
        for entity in outcome.entities:
            self.store.upsert_node(
                entity["id"],
                entity["type"],
                entity["name"],
                entity.get("description", ""),
                entity.get("metadata", {}),
            )
            lifecycle_hooks.trigger("on_entity_discovered", entity)
        for rel in outcome.relationships:
            self.store.upsert_edge(
                rel["source_id"],
                rel["target_id"],
                rel["relationship"],
                rel.get("metadata", {}),
            )
        lifecycle_hooks.trigger("on_document_parsed", outcome.file.abs_path, {
            "entities": outcome.entities,
            "relationships": outcome.relationships,
            "raw_text": outcome.raw_text,
        })

    def _compute_dirty_node_ids(
        self,
        dirty_files: List,
        deleted: Set[str],
    ) -> Set[str]:
        """Collect node IDs for dirty files plus their first-degree graph neighbours.

        Heuristic mappers often connect a newly-parsed node to existing ones —
        including first-degree neighbours ensures those existing connections are
        re-evaluated too.
        """
        seeds: Set[str] = set()
        for df in dirty_files:
            seeds |= self._provenance.nodes_from(df.rel_path)

        neighbors: Set[str] = set()
        for nid in seeds:
            if self.store.graph.has_node(nid):
                for nb in self.store.graph.successors(nid):
                    neighbors.add(nb)
                for nb in self.store.graph.predecessors(nid):
                    neighbors.add(nb)

        return seeds | neighbors

    def _blueprint_inputs_changed(self, dirty_files: List) -> bool:
        """Return True if any dirty file is a blueprint config sentinel."""
        for df in dirty_files:
            basename = os.path.basename(df.rel_path)
            if basename in _BLUEPRINT_SENTINELS:
                return True
            # Any *.config.ts / *.config.js also triggers re-synth
            if df.rel_path.endswith((".config.ts", ".config.js")):
                return True
        return False

    # ------------------------------------------------------------------
    # Spec 005: Copilot edge-augmentation
    # ------------------------------------------------------------------

    def get_pending_edge_candidates(self, limit: int = 50):
        """Return sub-threshold edge candidates from the last ingest run.

        These are pairs that scored between ``candidate_threshold`` and
        ``auto_emit_threshold`` in the :class:`~engine.edges.heuristic.HeuristicEdgeMapper`.
        They are surfaced to the host LLM (Copilot Chat) via the
        ``propose_edge_candidates`` MCP prompt for human-in-the-loop review.

        Returns an empty list when ``ingest_workspace()`` has not been called yet
        or when no pairs landed in the ambiguous band.
        """
        from engine.edges.heuristic import HeuristicEdgeMapper
        for mapper in self._edge_mappers:
            if isinstance(mapper, HeuristicEdgeMapper):
                return mapper.ambiguous_candidates(limit=limit)
        return []

    # ------------------------------------------------------------------
    # Backward-compatibility shims
    # ------------------------------------------------------------------
    # Tests in test_upsert_governance.py and test_utility_compilation.py
    # call these private methods directly.  They delegate to the extracted
    # sub-modules and will be removed once those tests are migrated.

    def map_relationships(self, nodes=None) -> None:
        """Deprecated shim — delegates to the edge mapper pipeline."""
        for mapper in self._edge_mappers:
            edges = mapper.map(self.store)
            for e in edges:
                self.store.upsert_edge(
                    e["source_id"],
                    e["target_id"],
                    e["relationship"],
                    e.get("metadata", {}),
                )

    def _map_test_relationships(self, nodes=None) -> None:
        """Deprecated shim — delegates to TestEdgeMapper.

        The ``nodes`` parameter is accepted for call-site compatibility but
        ignored: ``TestEdgeMapper.map()`` queries the store directly.
        """
        edges = self._edge_mappers[1].map(self.store)  # TestEdgeMapper is index 1
        for e in edges:
            self.store.upsert_edge(
                e["source_id"],
                e["target_id"],
                e["relationship"],
                e.get("metadata", {}),
            )

    def _compile_utility_blueprints(self) -> None:
        """Deprecated shim — delegates to UtilityCompiler."""
        self._utility_compiler.compile()
