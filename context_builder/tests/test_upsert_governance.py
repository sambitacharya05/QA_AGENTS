"""
Tests for the Conflict Resolution and Upsert Governance specification.

Validates that upsert_node merges attributes correctly when the same node_id
is inserted from multiple heterogeneous sources, and that sync_governance
tracking is maintained.
"""
import os
import sys

import pytest

# Ensure parent directory is in python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.graph_store import GraphStore


@pytest.fixture
def store(tmp_path):
    """Provides a fresh GraphStore in a temp directory."""
    return GraphStore(str(tmp_path))


class TestUpsertNodeMerge:
    """Tests for the smart upsert merge logic in GraphStore."""

    def test_new_node_gets_sync_governance(self, store):
        """Brand-new nodes must have sync_governance.is_merged=False."""
        store.upsert_node(
            "rule_age_limit", "business_rule", "Age Limit",
            "Must be >= 18", {"source_file": "spec.xlsx"}
        )
        node = store.get_node("rule_age_limit")
        assert node is not None
        gov = node["metadata"]["sync_governance"]
        assert gov["is_merged"] is False
        assert "spec.xlsx" in gov["source_origins"]

    def test_merged_node_preserves_rich_description(self, store):
        """When a doc-sourced node has a longer description, code-sourced updates must not overwrite it."""
        rich_desc = (
            "Detailed explanation of youthful driver surcharge: drivers under 25 "
            "are charged +15% on base premium. Applies to all standard auto policies. "
            "This surcharge is calculated after base premium determination and before "
            "any loyalty discounts are applied."
        )
        short_desc = "age < 25 check"

        # First insert from documentation (rich)
        store.upsert_node(
            "rule_youthful_driver_surcharge", "business_rule",
            "Youthful Driver Premium Surcharge",
            rich_desc, {"source_file": "policy_bounds.xlsx"}
        )

        # Second insert from code (short)
        store.upsert_node(
            "rule_youthful_driver_surcharge", "business_rule",
            "YDS",
            short_desc, {"source_file": "PolicyService.java"}
        )

        node = store.get_node("rule_youthful_driver_surcharge")
        assert node["description"] == rich_desc  # Rich description preserved
        assert node["name"] == "Youthful Driver Premium Surcharge"  # Longer name kept
        gov = node["metadata"]["sync_governance"]
        assert gov["is_merged"] is True
        assert set(gov["source_origins"]) == {"policy_bounds.xlsx", "PolicyService.java"}

    def test_merged_node_deep_merges_metadata(self, store):
        """Metadata dictionaries should be deep-merged, not replaced."""
        store.upsert_node(
            "rule_test", "business_rule", "Test Rule", "Desc",
            {"source_file": "a.xlsx", "variables": {"age": "int"}}
        )
        store.upsert_node(
            "rule_test", "business_rule", "Test Rule", "Desc",
            {
                "source_file": "b.java",
                "variables": {"risk": "float"},
                "code_expression_profile": {"method": "checkAge"}
            }
        )

        node = store.get_node("rule_test")
        # Nested dict "variables" should be deep-merged
        assert node["metadata"]["variables"] == {"age": "int", "risk": "float"}
        # New nested dict should be added
        assert node["metadata"]["code_expression_profile"]["method"] == "checkAge"

    def test_single_node_count_for_duplicate_rule(self, store):
        """Acceptance criterion: same ID from 2 sources → single node count."""
        store.upsert_node(
            "rule_youthful_driver_surcharge", "business_rule",
            "Youthful Driver", "From Excel",
            {"source_file": "policy_bounds.xlsx"}
        )
        store.upsert_node(
            "rule_youthful_driver_surcharge", "business_rule",
            "YDS Code", "From Java",
            {"source_file": "PolicyService.java"}
        )

        all_nodes = store.query_nodes(node_type="business_rule")
        yds_nodes = [n for n in all_nodes if n["id"] == "rule_youthful_driver_surcharge"]
        assert len(yds_nodes) == 1

    def test_non_business_rule_always_overwrites_description(self, store):
        """For non-business_rule types, the latest description always wins regardless of length."""
        store.upsert_node(
            "endpoint_post_quotes", "api_endpoint",
            "POST /quotes", "Original long description with many details about the endpoint",
            {"source_file": "spec.json"}
        )
        store.upsert_node(
            "endpoint_post_quotes", "api_endpoint",
            "POST /quotes", "Updated from code",
            {"source_file": "Controller.java"}
        )
        node = store.get_node("endpoint_post_quotes")
        assert node["description"] == "Updated from code"

    def test_name_keeps_longer_variant(self, store):
        """The longer (more descriptive) name should be preserved across merges."""
        store.upsert_node(
            "rule_min_premium", "business_rule",
            "Minimum Premium Threshold Rule",
            "All quotes must have a minimum premium of $50.00",
            {"source_file": "spec.docx"}
        )
        store.upsert_node(
            "rule_min_premium", "business_rule",
            "Min Prem",
            "min check",
            {"source_file": "Calculator.java"}
        )
        node = store.get_node("rule_min_premium")
        assert node["name"] == "Minimum Premium Threshold Rule"

    def test_sync_governance_tracks_all_sources(self, store):
        """sync_governance.source_origins must accumulate across 3+ merges."""
        for source in ["spec.docx", "rules.xlsx", "PolicyService.java"]:
            store.upsert_node(
                "rule_multi_source", "business_rule",
                "Multi Source Rule", f"From {source}",
                {"source_file": source}
            )

        node = store.get_node("rule_multi_source")
        gov = node["metadata"]["sync_governance"]
        assert gov["is_merged"] is True
        assert set(gov["source_origins"]) == {"spec.docx", "rules.xlsx", "PolicyService.java"}

    def test_metadata_flat_values_overwritten(self, store):
        """Non-dict metadata values should be overwritten by the latest insert."""
        store.upsert_node(
            "rule_flat", "business_rule", "Flat Test", "Desc",
            {"source_file": "a.xlsx", "priority": "low"}
        )
        store.upsert_node(
            "rule_flat", "business_rule", "Flat Test", "Desc",
            {"source_file": "b.java", "priority": "high", "implementation_status": "verified_in_code"}
        )

        node = store.get_node("rule_flat")
        assert node["metadata"]["priority"] == "high"
        assert node["metadata"]["implementation_status"] == "verified_in_code"


class TestDocLockOnExtendedExtensions:
    """SPEC-3 Wave 2 (A3): .properties / .loc now in DOC_ORIGIN_EXTS so nodes
    those parsers emit are governance-locked. Agent overwrite attempts must
    be blocked and recorded as behavioral_anomalies (not silently merged)."""

    def _seed_doc_locked(self, store, node_id, node_type, source_path):
        """Seed a node as if a doc-origin parser had just stamped it."""
        store.upsert_node(
            node_id, node_type, f"Initial {node_id}",
            "Parser-emitted authoritative value.",
            {
                "source_file": source_path,
                "sync_governance": {
                    "is_merged": False,
                    "origin": "documentation",
                    "source_origins": [source_path],
                },
            },
        )

    def test_agent_overwrite_blocked_on_properties_rule_constant(self, store):
        self._seed_doc_locked(
            store, "rule_constant_dob_min_age", "rule_constant",
            "ingest/config/application.properties",
        )

        # Agent tries to overwrite with a different description.
        store.upsert_node(
            "rule_constant_dob_min_age", "rule_constant",
            "Initial rule_constant_dob_min_age",
            "Agent-rewritten description (should be REJECTED).",
            {
                "source_file": "agent:rule-extractor",
                "sync_governance": {
                    "caller": "agent",
                    "agent_name": "rule-extractor",
                    "timestamp": "2026-05-26T00:00:00Z",
                },
            },
        )

        node = store.get_node("rule_constant_dob_min_age")
        assert node["description"] == "Parser-emitted authoritative value.", (
            "Doc-locked rule_constant must NOT be overwritten by agent."
        )
        anomalies = node["metadata"].get("behavioral_anomalies", [])
        assert len(anomalies) >= 1, (
            "Blocked overwrite must be recorded as a behavioral_anomaly."
        )

    def test_agent_overwrite_blocked_on_loc_ui_element(self, store):
        self._seed_doc_locked(
            store, "ui_element_btn_send_otp", "ui_element",
            "ingest/pages/login.loc",
        )

        store.upsert_node(
            "ui_element_btn_send_otp", "ui_element",
            "Initial ui_element_btn_send_otp",
            "Agent-rewritten ui_element description (should be REJECTED).",
            {
                "source_file": "agent:test-infra-mapper",
                "sync_governance": {
                    "caller": "agent",
                    "agent_name": "test-infra-mapper",
                    "timestamp": "2026-05-26T00:00:00Z",
                },
            },
        )

        node = store.get_node("ui_element_btn_send_otp")
        assert node["description"] == "Parser-emitted authoritative value."
        anomalies = node["metadata"].get("behavioral_anomalies", [])
        assert len(anomalies) >= 1


class TestIdUtils:
    """Tests for the SUEI ID generation utility."""

    def test_basic_rule_id(self):
        from parsers.id_utils import generate_node_id
        assert generate_node_id("business_rule", "Youthful Driver Surcharge") == "rule_youthful_driver_surcharge"

    def test_strips_noise_prefix(self):
        from parsers.id_utils import generate_node_id
        assert generate_node_id("business_rule", "Rule: Age Limit Eligibility") == "rule_age_limit_eligibility"

    def test_feature_prefix(self):
        from parsers.id_utils import generate_node_id
        assert generate_node_id("product_feature", "Quote Calculator") == "feature_quote_calculator"

    def test_endpoint_prefix(self):
        from parsers.id_utils import generate_node_id
        assert generate_node_id("api_endpoint", "POST /api/v1/calculator/calculate") == "endpoint_post_api_v1_calculator_calculate"

    def test_scenario_prefix(self):
        from parsers.id_utils import generate_node_id
        assert generate_node_id("test_scenario", "Applicant is too young") == "scenario_applicant_is_too_young"

    def test_special_chars_removed(self):
        from parsers.id_utils import generate_node_id
        result = generate_node_id("business_rule", "Age >= 18 && < 75 (years)")
        assert result == "rule_age_18_75_years"

    def test_deterministic(self):
        """Same input must always produce the same output."""
        from parsers.id_utils import generate_node_id
        id1 = generate_node_id("business_rule", "Youthful Driver Surcharge")
        id2 = generate_node_id("business_rule", "Youthful Driver Surcharge")
        assert id1 == id2


def test_language_isolation_in_relationships(tmp_path):
    from engine.extractor import ContextExtractor
    workspace = str(tmp_path)
    extractor = ContextExtractor(workspace)
    
    # Create directories
    os.makedirs(os.path.join(workspace, "src", "steps"), exist_ok=True)
    os.makedirs(os.path.join(workspace, "src", "pages"), exist_ok=True)
    os.makedirs(os.path.join(workspace, "src", "models"), exist_ok=True)
    
    # 1. Java Step Definition
    java_step_file = os.path.join(workspace, "src", "steps", "JavaStep.java")
    java_step_code = """
    package com.test.steps;
    import com.test.pages.MemberDashboardPage;
    public class JavaStep {
        public void step() {
            MemberDashboardPage page = new MemberDashboardPage();
            ClaimRequest req = new ClaimRequest();
        }
    }
    """
    with open(java_step_file, "w") as f: f.write(java_step_code)
    extractor.store.upsert_raw_document(java_step_file, java_step_code)
    
    # 2. TypeScript Step Definition
    ts_step_file = os.path.join(workspace, "src", "steps", "TsStep.ts")
    ts_step_code = """
    import { MemberDashboardPage } from '../pages/MemberDashboardPage';
    import { ClaimRequest } from '../models/ClaimRequest';
    async function step(req: ClaimRequest) {
        const page = new MemberDashboardPage();
    }
    """
    with open(ts_step_file, "w") as f: f.write(ts_step_code)
    extractor.store.upsert_raw_document(ts_step_file, ts_step_code)
    
    # 3. Java POM
    java_page_file = os.path.join(workspace, "src", "pages", "MemberDashboardPage.java")
    with open(java_page_file, "w") as f: f.write("public class MemberDashboardPage {}")
    extractor.store.upsert_raw_document(java_page_file, "public class MemberDashboardPage {}")
    
    # 4. TypeScript POM
    ts_page_file = os.path.join(workspace, "src", "pages", "MemberDashboardPage.ts")
    with open(ts_page_file, "w") as f: f.write("export class MemberDashboardPage {}")
    extractor.store.upsert_raw_document(ts_page_file, "export class MemberDashboardPage {}")
    
    # 5. Java DTO Model
    java_model_file = os.path.join(workspace, "src", "models", "ClaimRequest.java")
    with open(java_model_file, "w") as f: f.write("public record ClaimRequest() {}")
    extractor.store.upsert_raw_document(java_model_file, "public record ClaimRequest() {}")
    
    # 6. TS Interface Model
    ts_model_file = os.path.join(workspace, "src", "models", "ClaimRequest.ts")
    with open(ts_model_file, "w") as f: f.write("export interface ClaimRequest {}")
    extractor.store.upsert_raw_document(ts_model_file, "export interface ClaimRequest {}")
    extractor.store.upsert_raw_document(ts_model_file, "export interface ClaimRequest {}")
    
    # Create the nodes
    step_java_node = {
        "id": "step_javastep", "type": "test_step_definition", "name": "JavaStep", "description": "",
        "metadata": {"source_file": java_step_file}
    }
    step_ts_node = {
        "id": "step_tsstep", "type": "test_step_definition", "name": "TsStep", "description": "",
        "metadata": {"source_file": ts_step_file}
    }
    page_java_node = {
        "id": "page_memberdashboardpage_java", "type": "ui_page_object", "name": "MemberDashboardPage", "description": "",
        "metadata": {"source_file": java_page_file, "class_name": "MemberDashboardPage"}
    }
    page_ts_node = {
        "id": "page_memberdashboardpage_ts", "type": "ui_page_object", "name": "MemberDashboardPage", "description": "",
        "metadata": {"source_file": ts_page_file, "class_name": "MemberDashboardPage"}
    }
    model_java_node = {
        "id": "schema_claimrequest_java", "type": "data_model", "name": "ClaimRequest", "description": "",
        "metadata": {"source_file": java_model_file, "language": "java", "class_name": "ClaimRequest"}
    }
    model_ts_node = {
        "id": "schema_claimrequest_ts", "type": "data_model", "name": "ClaimRequest", "description": "",
        "metadata": {"source_file": ts_model_file, "language": "typescript", "class_name": "ClaimRequest"}
    }
    
    nodes = [step_java_node, step_ts_node, page_java_node, page_ts_node, model_java_node, model_ts_node]
    for n in nodes:
        extractor.store.upsert_node(n["id"], n["type"], n["name"], n["description"], n["metadata"])
        
    stored_nodes = extractor.store.query_nodes()
    extractor._map_test_relationships(stored_nodes)
    
    edges = extractor.store.get_edges()
    
    # Assert REFERENCES edges are isolated
    java_refs = [e["target_id"] for e in edges if e["source_id"] == "step_javastep" and e["relationship"] == "REFERENCES"]
    ts_refs = [e["target_id"] for e in edges if e["source_id"] == "step_tsstep" and e["relationship"] == "REFERENCES"]
    
    assert "page_memberdashboardpage_java" in java_refs
    assert "page_memberdashboardpage_ts" not in java_refs
    
    assert "page_memberdashboardpage_ts" in ts_refs
    assert "page_memberdashboardpage_java" not in ts_refs
    
    # Assert USES edges are isolated
    java_uses = [e["target_id"] for e in edges if e["source_id"] == "step_javastep" and e["relationship"] == "USES"]
    ts_uses = [e["target_id"] for e in edges if e["source_id"] == "step_tsstep" and e["relationship"] == "USES"]
    
    assert "schema_claimrequest_java" in java_uses
    assert "schema_claimrequest_ts" not in java_uses
    
    assert "schema_claimrequest_ts" in ts_uses
    assert "schema_claimrequest_java" not in ts_uses
