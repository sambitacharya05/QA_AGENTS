"""Tests for engine/edges/endpoint_deduper.py — EndpointDeduper.

Spec 003 (Wave 2) — pins the endpoint merge behaviour so Spec 006 can refine
it safely.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.graph_store import GraphStore
from engine.edges.endpoint_deduper import EndpointDeduper


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed(store: GraphStore, nodes: list[dict]) -> None:
    for n in nodes:
        store.upsert_node(
            n["id"], n["type"], n["name"],
            n.get("description", ""),
            n.get("metadata", {}),
        )


def _node_ids(store: GraphStore) -> set[str]:
    return {n["id"] for n in store.query_nodes()}


# ---------------------------------------------------------------------------
# Basic deduplication
# ---------------------------------------------------------------------------

class TestEndpointDeduper:
    def test_no_duplicates_returns_zero(self, tmp_path):
        store = GraphStore(str(tmp_path))
        _seed(store, [
            {"id": "ep1", "type": "api_endpoint",
             "name": "GET /quotes",
             "metadata": {"path": "/quotes", "http_method": "GET"}},
        ])
        deduper = EndpointDeduper()
        assert deduper.rewrite(store) == 0

    def test_empty_store_returns_zero(self, tmp_path):
        store = GraphStore(str(tmp_path))
        deduper = EndpointDeduper()
        assert deduper.rewrite(store) == 0

    def test_two_duplicate_endpoints_merged_to_one(self, tmp_path):
        store = GraphStore(str(tmp_path))
        _seed(store, [
            # OpenAPI node — has 'responses' → should be preferred as primary
            {"id": "ep_openapi", "type": "api_endpoint",
             "name": "POST /quotes (OpenAPI)",
             "metadata": {
                 "path": "/quotes",
                 "http_method": "POST",
                 "responses": {"200": {"description": "OK"}},
             }},
            # RestAssured node — no 'responses'
            {"id": "ep_ra", "type": "api_endpoint",
             "name": "POST /quotes (RestAssured)",
             "metadata": {
                 "target_route": "/quotes",
                 "http_method": "POST",
             }},
        ])
        deduper = EndpointDeduper()
        removed = deduper.rewrite(store)

        ids = _node_ids(store)
        assert removed == 1
        assert "ep_openapi" in ids, "OpenAPI node should be the primary (has responses)"
        assert "ep_ra" not in ids, "RestAssured duplicate should be removed"

    def test_preferred_node_has_responses_metadata(self, tmp_path):
        store = GraphStore(str(tmp_path))
        _seed(store, [
            {"id": "ep_plain", "type": "api_endpoint",
             "name": "POST /orders plain",
             "metadata": {"path": "/orders", "http_method": "POST"}},
            {"id": "ep_rich", "type": "api_endpoint",
             "name": "POST /orders rich",
             "metadata": {
                 "path": "/orders",
                 "http_method": "POST",
                 "responses": {"201": {"description": "Created"}},
             }},
        ])
        deduper = EndpointDeduper()
        deduper.rewrite(store)
        ids = _node_ids(store)
        assert "ep_rich" in ids
        assert "ep_plain" not in ids

    def test_duplicate_edges_rewired_to_primary(self, tmp_path):
        store = GraphStore(str(tmp_path))
        _seed(store, [
            {"id": "ep_primary", "type": "api_endpoint",
             "name": "GET /members primary",
             "metadata": {
                 "path": "/members",
                 "http_method": "GET",
                 "parameters": [],
             }},
            {"id": "ep_dup", "type": "api_endpoint",
             "name": "GET /members dup",
             "metadata": {"path": "/members", "http_method": "GET"}},
            {"id": "sc1", "type": "test_scenario",
             "name": "Fetch members scenario", "description": ""},
        ])
        # Edge points to the duplicate
        store.upsert_edge("sc1", "ep_dup", "TESTS", {"reason": "test"})

        deduper = EndpointDeduper()
        deduper.rewrite(store)

        # Edge must now point to the primary
        edges = store.get_edges(source_id="sc1")
        targets = [e["target_id"] for e in edges if e["relationship"] == "TESTS"]
        assert "ep_primary" in targets
        assert "ep_dup" not in targets

    def test_path_normalisation_is_case_insensitive(self, tmp_path):
        """/Quotes and /quotes are treated as the same endpoint."""
        store = GraphStore(str(tmp_path))
        _seed(store, [
            {"id": "ep1", "type": "api_endpoint",
             "name": "GET /Quotes",
             "metadata": {"path": "/Quotes", "http_method": "GET",
                          "responses": {}}},
            {"id": "ep2", "type": "api_endpoint",
             "name": "GET /quotes",
             "metadata": {"path": "/quotes", "http_method": "GET"}},
        ])
        deduper = EndpointDeduper()
        removed = deduper.rewrite(store)
        assert removed == 1

    def test_different_methods_not_merged(self, tmp_path):
        """GET /quotes and POST /quotes must NOT be merged."""
        store = GraphStore(str(tmp_path))
        _seed(store, [
            {"id": "ep_get", "type": "api_endpoint",
             "name": "GET /quotes",
             "metadata": {"path": "/quotes", "http_method": "GET"}},
            {"id": "ep_post", "type": "api_endpoint",
             "name": "POST /quotes",
             "metadata": {"path": "/quotes", "http_method": "POST"}},
        ])
        deduper = EndpointDeduper()
        removed = deduper.rewrite(store)
        assert removed == 0
        ids = _node_ids(store)
        assert "ep_get" in ids and "ep_post" in ids

    def test_endpoints_without_path_or_method_skipped(self, tmp_path):
        """Nodes missing path or method are not candidates for deduplication."""
        store = GraphStore(str(tmp_path))
        _seed(store, [
            {"id": "ep_incomplete", "type": "api_endpoint",
             "name": "Unknown endpoint",
             "metadata": {"path": "/something"}},  # no http_method
            {"id": "ep_complete", "type": "api_endpoint",
             "name": "GET /something",
             "metadata": {"path": "/something", "http_method": "GET"}},
        ])
        deduper = EndpointDeduper()
        removed = deduper.rewrite(store)
        assert removed == 0  # incomplete node not paired

    def test_sync_governance_is_merged_flag_set_on_primary(self, tmp_path):
        store = GraphStore(str(tmp_path))
        _seed(store, [
            {"id": "ep_primary", "type": "api_endpoint",
             "name": "POST /policies primary",
             "metadata": {
                 "path": "/policies",
                 "http_method": "POST",
                 "responses": {},
                 "sync_governance": {"origin": "openapi"},
             }},
            {"id": "ep_dup", "type": "api_endpoint",
             "name": "POST /policies dup",
             "metadata": {
                 "path": "/policies",
                 "http_method": "POST",
                 "sync_governance": {"origin": "restassured"},
             }},
        ])
        deduper = EndpointDeduper()
        deduper.rewrite(store)
        primary = store.get_node("ep_primary")
        sg = primary.get("metadata", {}).get("sync_governance", {})
        assert sg.get("is_merged") is True

    def test_longer_description_wins(self, tmp_path):
        """The longest description among duplicates should survive on the primary."""
        store = GraphStore(str(tmp_path))
        _seed(store, [
            {"id": "ep_primary", "type": "api_endpoint",
             "name": "GET /items primary",
             "description": "Short",
             "metadata": {"path": "/items", "http_method": "GET",
                          "responses": {}}},
            {"id": "ep_dup", "type": "api_endpoint",
             "name": "GET /items dup",
             "description": "This is a longer and more detailed description",
             "metadata": {"path": "/items", "http_method": "GET"}},
        ])
        deduper = EndpointDeduper()
        deduper.rewrite(store)
        primary = store.get_node("ep_primary")
        assert "longer" in primary.get("description", "")
