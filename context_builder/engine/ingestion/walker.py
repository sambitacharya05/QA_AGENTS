"""Workspace file discovery and priority ordering.

Extracted from ``engine/extractor.py`` (Spec 003 — Wave 2).
"""

import os
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class DiscoveredFile:
    """Represents a single file eligible for parsing."""

    abs_path: str
    rel_path: str
    extension: str
    priority: int  # lower = process first


class IngestionWalker:
    """Walks a workspace directory tree and yields files eligible for parsing.

    Replicates the directory-walk and priority-sort logic that previously lived
    in ``ContextExtractor.ingest_workspace()`` at lines 47-77 of
    ``engine/extractor.py``.
    """

    PRUNE_DIRS: frozenset = frozenset({
        ".git", "node_modules", "target", "build", "dist",
        ".gradle", ".idea", ".vscode", ".context_builder",
    })

    def __init__(self, workspace_path: str, extra_prune: set | None = None):
        self.workspace_path = workspace_path
        self._prune = self.PRUNE_DIRS | (extra_prune or set())

    @staticmethod
    def _priority_for(extension: str, rel_path: str) -> int:
        """Return an ingestion-priority integer (lower = processed first).

        Mirrors the ``get_ingestion_priority`` helper in the original extractor.
        """
        name = os.path.basename(rel_path).lower()
        if extension in (".properties", ".loc") or "locator" in name or "selector" in name:
            return 0  # Seed locator cache first
        if extension in (".bdd", ".feature", ".md"):
            return 1  # Scenarios and requirements specifications
        if "page" in name or "pom" in name:
            return 2  # POM page classes (requires locator cache)
        return 3  # Standard steps, models, and utility classes

    def discover(self) -> Iterable[DiscoveredFile]:
        """Yield :class:`DiscoveredFile` entries in priority order.

        Only files whose extension appears in ``parsers.PARSER_MAP`` (or
        ``.properties``) are yielded.
        """
        from parsers import PARSER_MAP  # deferred to avoid circular import

        collected: list[DiscoveredFile] = []
        for root, dirs, files in os.walk(self.workspace_path):
            dirs[:] = [d for d in dirs if d not in self._prune]
            for filename in files:
                abs_path = os.path.join(root, filename)
                _, ext = os.path.splitext(filename.lower())
                if ext in PARSER_MAP or ext == ".properties":
                    rel_path = os.path.relpath(abs_path, self.workspace_path).replace("\\", "/")
                    collected.append(
                        DiscoveredFile(
                            abs_path=abs_path,
                            rel_path=rel_path,
                            extension=ext,
                            priority=self._priority_for(ext, rel_path),
                        )
                    )

        collected.sort(key=lambda f: f.priority)
        return collected
