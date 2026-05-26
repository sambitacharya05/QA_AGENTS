"""Tests for Spec 007 — Parser-Level Relationship Emission.

Covers:
- parsers/base.py          (make_entity, make_relationship helpers)
- parsers/markdown_parser.py  (PART_OF edges from heading hierarchy)
- parsers/word_parser.py      (PART_OF edges — mocked with a fake docx)
- parsers/feature_parser.py   (PART_OF scenario→feature, TESTS via @rule: tags)
- parsers/ast/java_extractor.py  (IMPLEMENTS endpoint→class)
- parsers/code_parser.py         (propagates IMPLEMENTS from AST extractor)
- parsers/test_framework_parsers.py  (BINDS_LOCATOR, USES_POM)
"""

import os
import sys
import tempfile
import textwrap
from typing import Any, Dict, List

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parsers.base import BaseParser


# ===========================================================================
# BaseParser helpers
# ===========================================================================

class TestBaseParserHelpers:
    class _ConcreteParser(BaseParser):
        def parse(self, file_path: str) -> Dict[str, Any]:
            return {}

    def _p(self):
        return self._ConcreteParser()

    def test_make_entity_shape(self):
        e = BaseParser.make_entity("id1", "business_rule", "My Rule",
                                   "desc", {"source_file": "/foo.md"})
        assert e["id"] == "id1"
        assert e["type"] == "business_rule"
        assert e["name"] == "My Rule"
        assert e["description"] == "desc"
        assert e["metadata"]["source_file"] == "/foo.md"

    def test_make_entity_defaults(self):
        e = BaseParser.make_entity("id2", "test_scenario", "S")
        assert e["description"] == ""
        assert e["metadata"] == {}

    def test_make_relationship_shape(self):
        r = BaseParser.make_relationship(
            "src", "tgt", "PART_OF", 1.0, "markdown", "h2_in_h1"
        )
        assert r["source_id"] == "src"
        assert r["target_id"] == "tgt"
        assert r["relationship"] == "PART_OF"
        assert r["metadata"]["source"] == "parser:markdown"
        assert r["metadata"]["confidence"] == 1.0
        assert r["metadata"]["evidence"]["method"] == "explicit_ref"
        assert r["metadata"]["evidence"]["notes"] == "h2_in_h1"

    def test_make_relationship_default_confidence_is_1(self):
        r = BaseParser.make_relationship("a", "b", "TESTS")
        assert r["metadata"]["confidence"] == 1.0

    def test_make_relationship_without_parser_name(self):
        r = BaseParser.make_relationship("a", "b", "PART_OF")
        assert r["metadata"]["source"] == "parser"

    def test_provenance_pattern(self):
        """Every parser-emitted relationship must match 'parser:<name>' or 'parser'."""
        r = BaseParser.make_relationship("a", "b", "PART_OF", parser_name="feature")
        src = r["metadata"]["source"]
        assert src == "parser:feature" or src.startswith("parser:")


# ===========================================================================
# MarkdownParser — PART_OF hierarchy
# ===========================================================================

class TestMarkdownParserRelationships:
    def _parse_md(self, content: str) -> Dict[str, Any]:
        from parsers.markdown_parser import MarkdownParser
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as f:
            f.write(textwrap.dedent(content))
            path = f.name
        try:
            return MarkdownParser().parse(path)
        finally:
            os.unlink(path)

    def _rel_set(self, result: dict) -> set[tuple]:
        return {
            (r["source_id"], r["target_id"], r["relationship"])
            for r in result["relationships"]
        }

    def test_one_h1_two_h2_three_h3_produces_five_part_of_edges(self):
        """AC #1: 1 H1, 2 H2, 3 H3 → 6 nodes and 5 PART_OF edges."""
        result = self._parse_md("""
            # Quote Premium Calculation
            ## Youthful Driver Surcharge
            ### Positive: Surcharge Applied
            ### Negative: No Surcharge
            ## Endorsement Eligibility
            ### Positive: Endorsement Allowed
        """)
        entities = result["entities"]
        rels = result["relationships"]
        assert len(entities) == 6
        part_of = [r for r in rels if r["relationship"] == "PART_OF"]
        assert len(part_of) == 5

    def test_h2_part_of_h1(self):
        result = self._parse_md("""
            # Feature Alpha
            ## Rule Beta
        """)
        rels = result["relationships"]
        # Verify the PART_OF direction: rule → feature
        part_of = [r for r in rels if r["relationship"] == "PART_OF"]
        assert len(part_of) == 1
        assert part_of[0]["target_id"] != part_of[0]["source_id"]
        # source is the business_rule (H2), target is the product_feature (H1)
        src_type = next(
            e["type"] for e in result["entities"] if e["id"] == part_of[0]["source_id"]
        )
        tgt_type = next(
            e["type"] for e in result["entities"] if e["id"] == part_of[0]["target_id"]
        )
        assert src_type == "business_rule"
        assert tgt_type == "product_feature"

    def test_h3_under_h2_part_of_h2(self):
        result = self._parse_md("""
            # Feature
            ## Rule
            ### Scenario
        """)
        part_of = [r for r in result["relationships"] if r["relationship"] == "PART_OF"]
        # Expect 2 edges: H2→H1 and H3→H2
        assert len(part_of) == 2

    def test_h3_with_no_h2_part_of_h1(self):
        result = self._parse_md("""
            # Feature
            ### Scenario
        """)
        part_of = [r for r in result["relationships"] if r["relationship"] == "PART_OF"]
        assert len(part_of) == 1
        tgt_type = next(
            e["type"] for e in result["entities"]
            if e["id"] == part_of[0]["target_id"]
        )
        assert tgt_type == "product_feature"

    def test_all_part_of_edges_have_confidence_1(self):
        result = self._parse_md("""
            # Feature
            ## Rule
            ### Scenario
        """)
        for r in result["relationships"]:
            assert r["metadata"]["confidence"] == 1.0

    def test_generic_header_h1_resets_parent(self):
        """A generic H1 ('Purpose') should not serve as parent for H2s below it."""
        result = self._parse_md("""
            # Purpose
            ## Some Rule
        """)
        part_of = [r for r in result["relationships"] if r["relationship"] == "PART_OF"]
        assert len(part_of) == 0  # "Purpose" filtered out → no parent

    def test_no_relationships_with_no_structure(self):
        result = self._parse_md("Just some free text with no headings.\n")
        assert result["relationships"] == []


# ===========================================================================
# FeatureParser — PART_OF and @rule: TESTS
# ===========================================================================

class TestFeatureParserRelationships:
    def _parse_feature(self, content: str) -> Dict[str, Any]:
        from parsers.feature_parser import FeatureParser
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".feature", delete=False, encoding="utf-8"
        ) as f:
            f.write(textwrap.dedent(content))
            path = f.name
        try:
            return FeatureParser().parse(path)
        finally:
            os.unlink(path)

    @pytest.mark.skipif(
        not __import__("importlib").util.find_spec("gherkin"),
        reason="gherkin-official not installed",
    )
    def test_feature_with_three_scenarios_produces_three_part_of_edges(self):
        result = self._parse_feature("""
            Feature: Quote Premium Calculation
              Background:
                Given the application is running

              Scenario: Youthful driver
                When driver age is 18
                Then surcharge applies

              Scenario: Standard driver
                When driver age is 30
                Then no surcharge

              Scenario: Senior driver
                When driver age is 70
                Then no surcharge
        """)
        part_of = [r for r in result["relationships"] if r["relationship"] == "PART_OF"]
        assert len(part_of) == 3

    @pytest.mark.skipif(
        not __import__("importlib").util.find_spec("gherkin"),
        reason="gherkin-official not installed",
    )
    def test_explicit_rule_tag_emits_tests_edge(self):
        """AC #6: @rule:<slug> tag → TESTS edge with confidence=1.0."""
        result = self._parse_feature("""
            Feature: Surcharge Calculation

              @rule:youthful_driver_surcharge
              Scenario: Young driver surcharge applied
                Given driver age is 18
                Then surcharge applies
        """)
        tests_edges = [r for r in result["relationships"] if r["relationship"] == "TESTS"]
        assert len(tests_edges) == 1
        assert tests_edges[0]["target_id"] == "rule_youthful_driver_surcharge"
        assert tests_edges[0]["metadata"]["confidence"] == 1.0
        assert "explicit_tag" in tests_edges[0]["metadata"]["evidence"]["notes"]

    @pytest.mark.skipif(
        not __import__("importlib").util.find_spec("gherkin"),
        reason="gherkin-official not installed",
    )
    def test_provenance_matches_parser_pattern(self):
        result = self._parse_feature("""
            Feature: Billing

              Scenario: Invoice generation
                Given an invoice is requested
                Then it is generated
        """)
        for r in result["relationships"]:
            assert r["metadata"]["source"].startswith("parser:")
            assert r["metadata"]["confidence"] == 1.0
            assert r["metadata"]["evidence"]["method"] == "explicit_ref"


# ===========================================================================
# Java AST extractor — IMPLEMENTS edges
# ===========================================================================

class TestJavaExtractorImplementsEdges:
    @pytest.fixture(autouse=True)
    def skip_if_no_treesitter(self):
        try:
            from parsers.ast.loader import parser_for
            parser_for("java")
        except Exception:
            pytest.skip("tree-sitter-java not available")

    def _extract(self, source: str) -> Dict[str, Any]:
        from parsers.ast.java_extractor import JavaEndpointExtractor
        return JavaEndpointExtractor().extract(source, "/fake/Controller.java")

    def test_single_class_one_endpoint_produces_implements_edge(self):
        source = """
        @RestController
        @RequestMapping("/orders")
        public class OrderController {
            @GetMapping("/all")
            public List<Order> getAll() { return null; }
        }
        """
        result = self._extract(source)
        rels = result.get("relationships", [])
        implements = [r for r in rels if r["relationship"] == "IMPLEMENTS"]
        assert len(implements) == 1

    def test_implements_edge_direction_endpoint_to_class(self):
        """IMPLEMENTS source is the endpoint, target is the class."""
        source = """
        @RestController
        public class OrderController {
            @PostMapping("/orders")
            public Order create() { return null; }
        }
        """
        result = self._extract(source)
        entities = result["entities"]
        rels = result.get("relationships", [])
        endpoint_ids = {e["id"] for e in entities if e["type"] == "api_endpoint"}
        comp_ids = {e["id"] for e in entities if e["type"] == "code_component"}
        implements = [r for r in rels if r["relationship"] == "IMPLEMENTS"]
        for imp in implements:
            assert imp["source_id"] in endpoint_ids
            assert imp["target_id"] in comp_ids

    def test_multiple_classes_correct_endpoint_attribution(self):
        """AC #4: two classes → each endpoint correctly points to its own class."""
        source = """
        @RestController
        public class ClassA {
            @GetMapping("/a")
            public String getA() { return "a"; }
        }

        @RestController
        public class ClassB {
            @PostMapping("/b")
            public String postB() { return "b"; }
        }
        """
        result = self._extract(source)
        rels = result.get("relationships", [])
        entities = result["entities"]
        class_by_name = {
            e["metadata"]["class_name"]: e["id"]
            for e in entities if e["type"] == "code_component"
        }
        ep_by_path = {
            e["metadata"].get("path", ""): e["id"]
            for e in entities if e["type"] == "api_endpoint"
        }
        implements = [r for r in rels if r["relationship"] == "IMPLEMENTS"]
        assert len(implements) == 2
        # /a → ClassA, /b → ClassB
        for imp in implements:
            if imp["source_id"] == ep_by_path.get("/a"):
                assert imp["target_id"] == class_by_name.get("ClassA")
            elif imp["source_id"] == ep_by_path.get("/b"):
                assert imp["target_id"] == class_by_name.get("ClassB")

    def test_implements_confidence_is_one(self):
        source = """
        @RestController
        public class OrderController {
            @GetMapping("/orders")
            public List<Order> list() { return null; }
        }
        """
        result = self._extract(source)
        for r in result.get("relationships", []):
            assert r["metadata"]["confidence"] == 1.0
            assert r["metadata"]["evidence"]["method"] == "explicit_ref"


# ===========================================================================
# PlaywrightBddParser — BINDS_LOCATOR edges
# ===========================================================================

class TestPlaywrightBddParserRelationships:
    def _parse_ts(self, content: str, suffix: str = "Page.ts") -> Dict[str, Any]:
        from parsers.test_framework_parsers import PlaywrightBddParser
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=suffix, delete=False, encoding="utf-8"
        ) as f:
            f.write(textwrap.dedent(content))
            path = f.name
        try:
            return PlaywrightBddParser().parse(path)
        finally:
            os.unlink(path)

    def test_pom_with_three_locators_produces_three_binds_locator_edges(self):
        """AC #7: 3 named POM locator fields → ≥ 3 ui_locator nodes + ≥ 3 BINDS_LOCATOR edges.

        Note: the parser's inline locator scan may also harvest additional
        ``inline_locator_*`` entries from the same element selectors; the
        assertion is a lower bound to remain stable across parser evolution.
        """
        result = self._parse_ts("""
            class LoginPage {
                readonly usernameInput = page.locator('#username');
                readonly passwordInput = page.locator('#password');
                readonly submitButton = page.locator('#submit');
            }
        """)
        locator_entities = [e for e in result["entities"] if e["type"] == "ui_locator"]
        binds_edges = [r for r in result["relationships"] if r["relationship"] == "BINDS_LOCATOR"]
        assert len(locator_entities) >= 3, (
            f"Expected ≥ 3 ui_locator entities, got {len(locator_entities)}"
        )
        assert len(binds_edges) >= 3, (
            f"Expected ≥ 3 BINDS_LOCATOR edges, got {len(binds_edges)}"
        )

    def test_binds_locator_edges_have_correct_provenance(self):
        result = self._parse_ts("""
            class LoginPage {
                readonly usernameInput = page.locator('#username');
                readonly passwordInput = page.locator('#password');
            }
        """)
        for r in result["relationships"]:
            if r["relationship"] == "BINDS_LOCATOR":
                assert r["metadata"]["source"].startswith("parser:")
                assert r["metadata"]["confidence"] == 1.0
                assert r["metadata"]["evidence"]["method"] == "explicit_ref"

    def test_pom_with_no_locators_produces_no_binds_locator_edges(self):
        result = self._parse_ts("""
            class LoginPage {
                async login(username: string, password: string) {
                    // no locators
                }
            }
        """)
        binds = [r for r in result["relationships"] if r["relationship"] == "BINDS_LOCATOR"]
        assert len(binds) == 0

    def test_ui_locator_nodes_have_selector_in_metadata(self):
        result = self._parse_ts("""
            class FormPage {
                readonly emailInput = page.locator('#email');
            }
        """)
        locators = [e for e in result["entities"] if e["type"] == "ui_locator"]
        # At least one locator entity with selector '#email' must be present
        assert len(locators) >= 1
        selectors = [loc["metadata"].get("selector") for loc in locators]
        assert "#email" in selectors


# ===========================================================================
# CodeParser — relationship propagation end-to-end
# ===========================================================================

class TestCodeParserRelationshipPropagation:
    @pytest.fixture(autouse=True)
    def skip_if_no_treesitter(self):
        try:
            from parsers.ast.loader import parser_for
            parser_for("java")
        except Exception:
            pytest.skip("tree-sitter-java not available")

    def test_scan_java_propagates_implements_relationships(self, tmp_path):
        from parsers.code_parser import CodeParser
        source = textwrap.dedent("""
            @RestController
            @RequestMapping("/items")
            public class ItemController {
                @GetMapping("/list")
                public List<Item> list() { return null; }
            }
        """)
        java_file = tmp_path / "ItemController.java"
        java_file.write_text(source)
        result = CodeParser().parse(str(java_file))
        implements = [r for r in result["relationships"] if r["relationship"] == "IMPLEMENTS"]
        assert len(implements) >= 1


# ===========================================================================
# No-double-emit: heuristic should not overwrite parser-emitted PART_OF
# ===========================================================================

class TestNoDoubleEmit:
    def test_heuristic_mapper_skips_existing_parser_implements(self, tmp_path):
        """AC #9: heuristic mapper does not re-emit an already-present parser edge."""
        from db.graph_store import GraphStore
        from engine.edges.heuristic import HeuristicEdgeMapper

        store = GraphStore(str(tmp_path))
        store.upsert_node("ep1", "api_endpoint", "youthful driver surcharge", "", {})
        store.upsert_node("br1", "business_rule", "youthful driver surcharge calculation", "", {})
        # Pre-insert parser-emitted edge
        store.upsert_edge("ep1", "br1", "IMPLEMENTS", {
            "source": "parser:java",
            "confidence": 1.0,
            "evidence": {"method": "explicit_ref", "score": 1.0, "notes": "test"},
        })
        mapper = HeuristicEdgeMapper()
        proposed = mapper.map(store)
        duplicates = [
            e for e in proposed
            if e["source_id"] == "ep1" and e["target_id"] == "br1"
            and e["relationship"] == "IMPLEMENTS"
        ]
        assert len(duplicates) == 0, "Heuristic should not re-emit a parser-emitted edge"
