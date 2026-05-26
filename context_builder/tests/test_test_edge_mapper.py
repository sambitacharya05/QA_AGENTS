"""Tests for engine/edges/test_framework.py — TestEdgeMapper.

Spec 003 (Wave 2) — pins the import-graph, POM-reference, model-usage, and
page-validation edge rules.  Focuses on the language-isolation contracts so
Spec 006 can refine thresholds without breaking cross-framework isolation.
"""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.graph_store import GraphStore
from engine.edges.test_framework import TestEdgeMapper


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


def _add_doc(store: GraphStore, file_path: str, content: str) -> None:
    store.upsert_raw_document(file_path, content)


def _edges_of(store_or_list, rel: str, src: str) -> list[str]:
    """Return target_ids of edges with given rel and src from a list or store."""
    if isinstance(store_or_list, GraphStore):
        edges = store_or_list.get_edges()
    else:
        edges = store_or_list
    return [
        e["target_id"] for e in edges
        if e["source_id"] == src and e["relationship"] == rel
    ]


def _edge_set(edges: list[dict]) -> set[tuple]:
    return {(e["source_id"], e["target_id"], e["relationship"]) for e in edges}


# ---------------------------------------------------------------------------
# Language-isolation: REFERENCES (POM mentions)
# ---------------------------------------------------------------------------

class TestPomReferenceLanguageIsolation:
    """Java step → Java POM only; TS step → TS POM only."""

    def _build_store(self, tmp_path, java_step_file, ts_step_file,
                     java_page_file, ts_page_file) -> GraphStore:
        # Create the source files on disk so documents exist
        for fp, content in [
            (java_step_file, "import LoginPage;\nLoginPage loginPage;\n"),
            (ts_step_file, "import { LoginPage } from './LoginPage';\nloginPage.login();\n"),
        ]:
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            with open(fp, "w") as f:
                f.write(content)

        store = GraphStore(str(tmp_path))
        _add_doc(store, java_step_file,
                 "import LoginPage;\nLoginPage loginPage;\n")
        _add_doc(store, ts_step_file,
                 "import { LoginPage } from './LoginPage';\nloginPage.login();\n")

        _seed(store, [
            {"id": "step_java", "type": "test_step_definition",
             "name": "Java login steps",
             "metadata": {"source_file": java_step_file, "language": "java"}},
            {"id": "step_ts", "type": "test_step_definition",
             "name": "TS login steps",
             "metadata": {"source_file": ts_step_file, "language": "typescript"}},
            {"id": "page_java", "type": "ui_page_object",
             "name": "LoginPage",
             "metadata": {"source_file": java_page_file, "class_name": "LoginPage",
                          "framework": "selenium"}},
            {"id": "page_ts", "type": "ui_page_object",
             "name": "LoginPage",
             "metadata": {"source_file": ts_page_file, "class_name": "LoginPage",
                          "framework": "playwright"}},
        ])
        return store

    def test_java_step_references_java_pom_only(self, tmp_path):
        ws = str(tmp_path)
        store = self._build_store(
            tmp_path,
            java_step_file=os.path.join(ws, "steps", "LoginSteps.java"),
            ts_step_file=os.path.join(ws, "steps", "LoginSteps.ts"),
            java_page_file=os.path.join(ws, "pages", "LoginPage.java"),
            ts_page_file=os.path.join(ws, "pages", "LoginPage.ts"),
        )
        mapper = TestEdgeMapper()
        edges = mapper.map(store)
        java_refs = _edges_of(edges, "REFERENCES", "step_java")
        ts_refs = _edges_of(edges, "REFERENCES", "step_ts")

        assert "page_java" in java_refs, "Java step should REFERENCE Java POM"
        assert "page_ts" not in java_refs, "Java step must NOT REFERENCE TS POM"
        assert "page_ts" in ts_refs, "TS step should REFERENCE TS POM"
        assert "page_java" not in ts_refs, "TS step must NOT REFERENCE Java POM"


# ---------------------------------------------------------------------------
# Language-isolation: USES (data model)
# ---------------------------------------------------------------------------

class TestDataModelLanguageIsolation:
    def test_java_step_uses_java_model_only(self, tmp_path):
        ws = str(tmp_path)
        java_step = os.path.join(ws, "steps", "ClaimSteps.java")
        ts_step = os.path.join(ws, "steps", "ClaimSteps.ts")

        java_content = "ClaimRequest.builder().build();\n"
        ts_content = "const req: ClaimRequest = new ClaimRequest();\n"

        for fp, c in [(java_step, java_content), (ts_step, ts_content)]:
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            with open(fp, "w") as f:
                f.write(c)

        store = GraphStore(ws)
        _add_doc(store, java_step, java_content)
        _add_doc(store, ts_step, ts_content)

        _seed(store, [
            {"id": "step_java", "type": "test_step_definition",
             "name": "Java claim steps",
             "metadata": {"source_file": java_step}},
            {"id": "step_ts", "type": "test_step_definition",
             "name": "TS claim steps",
             "metadata": {"source_file": ts_step}},
            {"id": "model_java", "type": "data_model",
             "name": "ClaimRequest",
             "metadata": {"class_name": "ClaimRequest", "language": "java"}},
            {"id": "model_ts", "type": "data_model",
             "name": "ClaimRequest",
             "metadata": {"class_name": "ClaimRequest", "language": "typescript"}},
        ])

        mapper = TestEdgeMapper()
        edges = mapper.map(store)

        java_uses = _edges_of(edges, "USES", "step_java")
        ts_uses = _edges_of(edges, "USES", "step_ts")

        assert "model_java" in java_uses
        assert "model_ts" not in java_uses
        assert "model_ts" in ts_uses
        assert "model_java" not in ts_uses


# ---------------------------------------------------------------------------
# VALIDATES (POM → Business Rule)
# ---------------------------------------------------------------------------

class TestPageValidatesBusinessRule:
    def test_pom_validates_rule_on_token_overlap(self, tmp_path):
        store = GraphStore(str(tmp_path))
        _seed(store, [
            {"id": "page1", "type": "ui_page_object",
             "name": "Insurance claim submission form",
             "description": ""},
            {"id": "br1", "type": "business_rule",
             "name": "Insurance claim validation rules",
             "description": ""},
        ])
        mapper = TestEdgeMapper()
        edges = mapper.map(store)
        assert ("page1", "br1", "VALIDATES") in _edge_set(edges)

    def test_pom_with_empty_name_produces_no_validates_edge(self, tmp_path):
        store = GraphStore(str(tmp_path))
        _seed(store, [
            {"id": "page1", "type": "ui_page_object",
             "name": "", "description": ""},
            {"id": "br1", "type": "business_rule",
             "name": "Insurance claim validation rules", "description": ""},
        ])
        mapper = TestEdgeMapper()
        edges = mapper.map(store)
        assert ("page1", "br1", "VALIDATES") not in _edge_set(edges)


# ---------------------------------------------------------------------------
# Chained Validation (step → ep via locally proposed CALLS)
# ---------------------------------------------------------------------------

class TestChainedValidationTracing:
    def test_step_tests_endpoint_via_calls_chain(self, tmp_path):
        """Step → utility (CALLS) → endpoint (same source file) → TESTS edge."""
        ws = str(tmp_path)
        step_file = os.path.join(ws, "steps", "OrderSteps.java")
        util_file = os.path.join(ws, "utils", "OrderClient.java")

        os.makedirs(os.path.dirname(step_file), exist_ok=True)
        os.makedirs(os.path.dirname(util_file), exist_ok=True)

        step_content = "import com.proj.utils.OrderClient;\nOrderClient client;\n"
        with open(step_file, "w") as f:
            f.write(step_content)

        store = GraphStore(ws)
        _add_doc(store, step_file, step_content)

        _seed(store, [
            {"id": "step1", "type": "test_step_definition",
             "name": "Place order step",
             "metadata": {"source_file": step_file}},
            {"id": "util1", "type": "test_utility",
             "name": "OrderClient",
             "metadata": {
                 "source_file": util_file,
                 "class_name": "OrderClient",
                 "language": "java",
             }},
            {"id": "ep1", "type": "api_endpoint",
             "name": "POST /orders",
             "metadata": {"source_file": util_file, "path": "/orders"}},
        ])

        mapper = TestEdgeMapper()
        edges = mapper.map(store)

        # The CALLS edge (step1 → util1) is proposed in step 1
        # The TESTS edge (step1 → ep1) should be emitted in step 3
        assert ("step1", "util1", "CALLS") in _edge_set(edges)
        assert ("step1", "ep1", "TESTS") in _edge_set(edges)


# ---------------------------------------------------------------------------
# Idempotency and purity
# ---------------------------------------------------------------------------

def test_mapper_is_idempotent(tmp_path):
    """Running map() twice on the same store returns equivalent edges."""
    store = GraphStore(str(tmp_path))
    _seed(store, [
        {"id": "page1", "type": "ui_page_object",
         "name": "Insurance claim submission form", "description": ""},
        {"id": "br1", "type": "business_rule",
         "name": "Insurance claim validation rules", "description": ""},
    ])
    mapper = TestEdgeMapper()
    first = _edge_set(mapper.map(store))
    second = _edge_set(mapper.map(store))
    assert first == second


def test_mapper_does_not_write_to_store(tmp_path):
    """map() must be pure — must NOT call store.upsert_edge()."""
    store = GraphStore(str(tmp_path))
    _seed(store, [
        {"id": "page1", "type": "ui_page_object",
         "name": "Insurance claim submission form", "description": ""},
        {"id": "br1", "type": "business_rule",
         "name": "Insurance claim validation rules", "description": ""},
    ])
    before = _edge_set(store.get_edges())
    mapper = TestEdgeMapper()
    mapper.map(store)
    after = _edge_set(store.get_edges())
    assert before == after, "TestEdgeMapper.map() must not write to the store"
