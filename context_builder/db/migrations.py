"""
Schema migration registry for all local JSON stores.

Each store tracks an integer ``schema_version`` stamped in the on-disk envelope.
When a file is read, LocalJsonStore walks the migrators from the file's version
up to the current version, calling each in sequence.

Adding a new migration
----------------------
1. Increment the corresponding ``*_SCHEMA_VERSION`` constant.
2. Add a ``{old_version: migrator_fn}`` entry to the matching ``*_MIGRATORS`` dict.
   The migrator receives the *payload* dict (not the envelope) and must return
   the upgraded payload dict.

Current versions
----------------
  graph.json      → GRAPH_SCHEMA_VERSION    = 1
  documents.json  → DOCS_SCHEMA_VERSION     = 1
  blueprint.json  → BLUEPRINT_SCHEMA_VERSION = 1  (written raw, no envelope)
"""

from typing import Callable, Dict

# ---------------------------------------------------------------------------
# graph.json
# ---------------------------------------------------------------------------

GRAPH_SCHEMA_VERSION = 1


def _graph_v0_to_v1(payload: dict) -> dict:
    """NetworkX <3.4 used 'links' as the edge-list key; >=3.4 uses 'edges'.
    Normalize legacy files so node_link_graph can load them with edges='edges'."""
    if "links" in payload and "edges" not in payload:
        payload["edges"] = payload.pop("links")
    return payload


GRAPH_MIGRATORS: Dict[int, Callable[[dict], dict]] = {
    0: _graph_v0_to_v1,
}

# ---------------------------------------------------------------------------
# documents.json
# ---------------------------------------------------------------------------

DOCS_SCHEMA_VERSION = 1

# No structural change between v0 and v1 for the documents store — v0 files
# already have the right shape ({rel_path: {checksum, content}}).  A no-op
# migrator is sufficient to advance the version stamp.
DOCS_MIGRATORS: Dict[int, Callable[[dict], dict]] = {
    0: lambda p: p,
}

# ---------------------------------------------------------------------------
# blueprint.json
# ---------------------------------------------------------------------------

# BlueprintStore uses LocalJsonStore in *raw* mode (use_envelope=False) so the
# schema_version constant below is declared for documentation purposes only —
# it is NOT passed to LocalJsonStore.  The file is always written as plain JSON.
BLUEPRINT_SCHEMA_VERSION = 0  # raw mode — no envelope written
BLUEPRINT_MIGRATORS: Dict[int, Callable[[dict], dict]] = {}
