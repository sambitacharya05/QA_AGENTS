"""Tests for db/graph_store.py upsert_node node-type handling.

SPEC-3 Wave 1: assert rule-family subtypes (validation_rule, eligibility_rule,
ui_business_rule, security_rule) survive upsert_node as first-class types and
gain metadata.rule_family for downstream uniform bucketing.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.graph_store import GraphStore


@pytest.mark.parametrize(
    "subtype",
    ["validation_rule", "eligibility_rule", "ui_business_rule", "security_rule"],
)
def test_rule_family_subtype_preserved_as_first_class(tmp_path, subtype):
    store = GraphStore(str(tmp_path))
    node_id = f"rule_{subtype}_sample"
    store.upsert_node(node_id, subtype, "Sample Rule", "desc", {})

    data = store.graph.nodes[node_id]
    assert data["type"] == subtype, (
        f"Expected node_type to remain {subtype}, got {data['type']}"
    )
    assert data["metadata"].get("rule_family") == "business_rule", (
        "Expected metadata.rule_family to be back-filled to 'business_rule' "
        "so downstream mappers can bucket uniformly."
    )


def test_validation_rule_preserved_as_first_class(tmp_path):
    """SPEC-3 Wave 1 §1.2 — the named example from the spec."""
    store = GraphStore(str(tmp_path))
    store.upsert_node("rule_dob_format", "validation_rule",
                      "DOB Format Rule", "Validates DOB", {})

    data = store.graph.nodes["rule_dob_format"]
    assert data["type"] == "validation_rule"
    assert data["metadata"]["rule_family"] == "business_rule"


def test_rich_custom_categories_still_collapse_to_business_rule(tmp_path):
    """Back-compat: existing claims/billing/etc still collapse to business_rule
    with rule_category set. Wave 1 must not regress this path."""
    store = GraphStore(str(tmp_path))
    store.upsert_node("rule_claims_sample", "claims", "Claims Rule", "desc", {})

    data = store.graph.nodes["rule_claims_sample"]
    assert data["type"] == "business_rule"
    assert data["metadata"]["rule_category"] == "claims"


def test_rule_family_does_not_overwrite_existing_rule_family_metadata(tmp_path):
    """If a caller pre-sets metadata.rule_family, setdefault must keep it."""
    store = GraphStore(str(tmp_path))
    store.upsert_node(
        "rule_explicit", "validation_rule",
        "Explicit Rule", "desc",
        {"rule_family": "custom_family"},
    )

    data = store.graph.nodes["rule_explicit"]
    assert data["metadata"]["rule_family"] == "custom_family"


# ---------------------------------------------------------------------------
# SPEC-3 Wave 2: ui_element / field_specification / rule_constant first-class
# ---------------------------------------------------------------------------

def test_ui_element_preserved_as_first_class(tmp_path):
    """Wave 2 type must NOT be silently coerced to business_rule."""
    store = GraphStore(str(tmp_path))
    store.upsert_node(
        "ui_element_btn_send_otp", "ui_element",
        "BTN_SEND_OTP", "Send OTP button",
        {"selector": "id=send-otp"},
    )
    data = store.graph.nodes["ui_element_btn_send_otp"]
    assert data["type"] == "ui_element"


def test_field_specification_preserved_as_first_class(tmp_path):
    """Wave 2 type must NOT be silently coerced to business_rule."""
    store = GraphStore(str(tmp_path))
    store.upsert_node(
        "field_spec_fld_dob", "field_specification",
        "Field Spec: Date of Birth", "DOB field",
        {"field_id": "FLD_DOB"},
    )
    data = store.graph.nodes["field_spec_fld_dob"]
    assert data["type"] == "field_specification"


def test_rule_constant_preserved_as_first_class(tmp_path):
    """Wave 2 type must NOT be silently coerced to business_rule."""
    store = GraphStore(str(tmp_path))
    store.upsert_node(
        "rule_constant_dob_min_age", "rule_constant",
        "Rule Constant: dob.min.age", "DOB min age",
        {"config_key": "dob.min.age", "value": 18},
    )
    data = store.graph.nodes["rule_constant_dob_min_age"]
    assert data["type"] == "rule_constant"
