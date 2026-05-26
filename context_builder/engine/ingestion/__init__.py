"""Workspace ingestion — file discovery and per-file parse dispatch."""

from engine.ingestion.walker import DiscoveredFile, IngestionWalker
from engine.ingestion.parse_dispatcher import ParseOutcome, ParseDispatcher

__all__ = [
    "DiscoveredFile",
    "IngestionWalker",
    "ParseOutcome",
    "ParseDispatcher",
]
