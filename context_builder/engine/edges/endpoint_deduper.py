"""Endpoint deduplication — merges duplicate api_endpoint nodes.

Extracted verbatim from ``ContextExtractor._deduplicate_endpoints()``
in ``engine/extractor.py`` (Spec 003 — Wave 2).

No behavior change from the original.  ``EndpointDeduper`` is a *graph
rewriter*, not an edge proposer, so it does not implement the
:class:`~engine.edges.base.EdgeMapper` protocol — it writes directly to the
store via ``rewrite()``.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from db.graph_store import GraphStore

log = logging.getLogger(__name__)


class EndpointDeduper:
    """Consolidates duplicate ``api_endpoint`` nodes extracted from OpenAPI
    specs and RestAssured code.

    Merges duplicate nodes by normalized ``(path, method)`` key, preferring
    the node with ``responses``/``parameters`` metadata (i.e. the OpenAPI
    source) as the canonical node.  All edges pointing to / from the dropped
    duplicates are re-wired to the canonical node.
    """

    @staticmethod
    def _normalized_path(path_str: str) -> str:
        clean = path_str.strip("/").lower()
        return re.sub(r'[^a-z0-9]', '', clean)

    def rewrite(self, store: "GraphStore") -> int:
        """Merge duplicate api_endpoint nodes in *store*.

        Returns the number of nodes removed.
        """
        nodes = store.query_nodes(node_type="api_endpoint")
        if not nodes:
            return 0

        groups: dict[tuple, list[dict]] = {}
        for node in nodes:
            metadata = node.get("metadata") or {}
            path = metadata.get("path") or metadata.get("target_route") or ""
            method = (metadata.get("http_method") or "").upper()
            if not path or not method:
                continue

            group_key = (self._normalized_path(path), method)
            groups.setdefault(group_key, []).append(node)

        removed = 0
        for group_key, group_nodes in groups.items():
            if len(group_nodes) <= 1:
                continue

            # Prefer the node with OpenAPI metadata (responses/parameters) as primary
            primary_node = None
            for node in group_nodes:
                metadata = node.get("metadata") or {}
                if "responses" in metadata or "parameters" in metadata:
                    primary_node = node
                    break

            if not primary_node:
                primary_node = min(group_nodes, key=lambda n: len(n["id"]))

            duplicates = [n for n in group_nodes if n["id"] != primary_node["id"]]

            primary_meta = primary_node.get("metadata") or {}
            primary_sync = primary_meta.get("sync_governance") or {}
            primary_origins = primary_sync.get("source_origins") or []
            if not isinstance(primary_origins, list):
                primary_origins = [primary_origins] if primary_origins else []

            primary_desc = primary_node.get("description") or ""

            for dup in duplicates:
                dup_meta = dup.get("metadata") or {}
                dup_sync = dup_meta.get("sync_governance") or {}
                dup_origins = dup_sync.get("source_origins") or []
                if not isinstance(dup_origins, list):
                    dup_origins = [dup_origins] if dup_origins else []

                primary_origins.extend(dup_origins)

                dup_desc = dup.get("description") or ""
                if len(dup_desc) > len(primary_desc):
                    primary_desc = dup_desc

                for k, v in dup_meta.items():
                    if k == "sync_governance":
                        continue
                    if k not in primary_meta:
                        primary_meta[k] = v
                    elif isinstance(v, dict) and isinstance(primary_meta[k], dict):
                        primary_meta[k] = {**primary_meta[k], **v}

            primary_origins = list(set(primary_origins))
            primary_meta["sync_governance"] = {
                "is_merged": True,
                "origin": primary_sync.get("origin", "unknown"),
                "source_origins": primary_origins,
            }

            store.graph.nodes[primary_node["id"]]["description"] = primary_desc
            store.graph.nodes[primary_node["id"]]["metadata"] = primary_meta

            primary_id = primary_node["id"]
            for dup in duplicates:
                dup_id = dup["id"]
                edges_to_dup = store.get_edges(target_id=dup_id)
                edges_from_dup = store.get_edges(source_id=dup_id)

                for e in edges_to_dup:
                    store.upsert_edge(
                        e["source_id"],
                        primary_id,
                        e["relationship"],
                        e.get("metadata", {}),
                    )

                for e in edges_from_dup:
                    store.upsert_edge(
                        primary_id,
                        e["target_id"],
                        e["relationship"],
                        e.get("metadata", {}),
                    )

                if store.graph.has_node(dup_id):
                    store.graph.remove_node(dup_id)
                    removed += 1

        return removed
