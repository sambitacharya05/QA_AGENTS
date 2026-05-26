"""Edge inference — heuristic mapping, test-framework mapping, endpoint deduplication."""

from engine.edges.base import EdgeMapper
from engine.edges.heuristic import HeuristicEdgeMapper
from engine.edges.test_framework import TestEdgeMapper
from engine.edges.endpoint_deduper import EndpointDeduper

__all__ = [
    "EdgeMapper",
    "HeuristicEdgeMapper",
    "TestEdgeMapper",
    "EndpointDeduper",
]
