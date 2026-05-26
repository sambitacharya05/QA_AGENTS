"""EdgeMapper Protocol — the seam all edge-inference modules implement.

Extracted as a new contract in Spec 003 (Wave 2).
Updated in Spec 008 (Wave 4) to add the optional ``map_incremental`` method.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from db.graph_store import GraphStore


@runtime_checkable
class EdgeMapper(Protocol):
    """Each mapper inspects the current graph and proposes edges.

    Implementations MUST be idempotent — running twice produces the same edges.

    Purity requirement (Spec 009): ``map()`` MUST be a pure function of the
    graph it receives.  Mappers may read ``store`` freely but MUST NOT retain
    references to parser-level mutable state (e.g. a locator cache held by
    ``PlaywrightBddParser`` / ``QafAutomationParser``).  Spec 009 runs shard
    ingests in separate worker processes; edge mapping then runs once on the
    merged graph in the parent process.  If mappers depended on shard-local
    parser state that state would be gone by merge time.
    """

    name: str  # used in edge metadata 'mapper' field for provenance

    def map(self, store: "GraphStore") -> list[dict]:
        """Return proposed edges as::

            [
              {
                "source_id": ...,
                "target_id": ...,
                "relationship": ...,
                "metadata": {"reason": ..., ...},
              },
              ...
            ]

        The orchestrator calls ``store.upsert_edge()`` — mappers never write.
        Full re-map — always available as a fallback.
        """
        ...

    def map_incremental(
        self, store: "GraphStore", dirty_node_ids: set
    ) -> list[dict]:
        """Re-map edges touching only the *dirty_node_ids* subgraph.

        Optional optimisation introduced in Spec 008 (Wave 4).  When a mapper
        provides this method, the orchestrator calls it instead of :meth:`map`
        whenever the dirty set is smaller than the full node count.

        Contract
        --------
        1. A pair is only scored when at least one endpoint is in
           *dirty_node_ids* — turning the O(N²) full scan into O(|dirty| · N).
        2. Existing edges **sourced by this mapper** for the dirty subgraph MUST
           be removed before re-emitting (delete-then-insert).  The orchestrator
           handles the delete; mappers only need to return the fresh proposals.
        3. The return format is identical to :meth:`map`.

        Default implementation falls back to the full ``map(store)`` so mappers
        that do not override this still work correctly in all code paths.
        """
        # Default: full map (safe, correct, just not optimal)
        return self.map(store)
