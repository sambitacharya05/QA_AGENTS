"""Per-file parse dispatch and governance stamping.

Extracted from ``engine/extractor.py`` (Spec 003 — Wave 2).
"""

import logging
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

from engine.ingestion.walker import DiscoveredFile

if TYPE_CHECKING:
    from db.graph_store import GraphStore

log = logging.getLogger(__name__)


@dataclass
class ParseOutcome:
    """Result of parsing a single :class:`DiscoveredFile`."""

    file: DiscoveredFile
    entities: list = field(default_factory=list)
    relationships: list = field(default_factory=list)
    raw_text: str = ""
    cached: bool = False
    error: Optional[Exception] = None


class ParseDispatcher:
    """Routes a file through the parser factory and stamps governance metadata.

    Pure — does NOT write to the store.  The orchestrator calls
    ``_apply_outcome()`` to persist entities and relationships returned here.

    Replicates the parse-dispatch block from ``ContextExtractor.ingest_workspace()``
    at lines 81-128 of ``engine/extractor.py`` (Spec 003 — Wave 2).
    """

    # SPEC-3 Wave 2: extend governance lock to .properties (authoritative
    # rule_constants like dob.min.age=18) and .loc (locator definitions that
    # are authoritative DOM bindings).
    DOC_ORIGIN_EXTS: frozenset = frozenset({
        ".docx", ".xlsx", ".pdf", ".md",
        ".properties",
        ".loc",
    })

    def __init__(
        self,
        workspace_path: str,
        store: "GraphStore",
        shared_context: dict | None = None,
    ):
        self.workspace_path = workspace_path
        self.store = store
        self.shared_context: dict = shared_context if shared_context is not None else {}

    def parse(self, df: DiscoveredFile) -> ParseOutcome:
        """Parse *df* and return a :class:`ParseOutcome` (no store writes).

        On any exception the outcome's ``error`` attribute is set and entities /
        relationships are left empty — callers should check for this.
        """
        from parsers import get_parser_for_file  # deferred to avoid circular import

        try:
            parser = get_parser_for_file(df.abs_path, self.shared_context)
            result = parser.parse(df.abs_path)

            entities = result.get("entities", [])
            for entity in entities:
                self.stamp_origin(entity, df)

            return ParseOutcome(
                file=df,
                entities=entities,
                relationships=result.get("relationships", []),
                raw_text=result.get("raw_text", ""),
                cached=False,
            )
        except Exception as exc:
            return ParseOutcome(file=df, error=exc)

    def stamp_origin(self, entity: dict, df: DiscoveredFile) -> dict:
        """Stamp ``sync_governance.origin = "documentation"`` on doc-type files.

        Mirrors the origin-stamping block from ``ingest_workspace()``
        (lines 92-98 of the original extractor).
        """
        if df.extension in self.DOC_ORIGIN_EXTS:
            if "metadata" not in entity or entity["metadata"] is None:
                entity["metadata"] = {}
            if (
                "sync_governance" not in entity["metadata"]
                or entity["metadata"]["sync_governance"] is None
            ):
                entity["metadata"]["sync_governance"] = {}
            entity["metadata"]["sync_governance"]["origin"] = "documentation"
        return entity
