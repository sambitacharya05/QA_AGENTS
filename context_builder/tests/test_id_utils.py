"""Tests for parsers/id_utils.py — SUEI node-ID generation.

SPEC-3 Wave 1: keeps TYPE_PREFIX honest as new rule-family subtypes and
field_specification join the table. SPEC-3 Wave 2 adds the ui_page_object /
ui_element / rule_constant rows; their tests live here too once that wave lands.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parsers.id_utils import TYPE_PREFIX, generate_node_id


# SPEC-3 Wave 1 — 5 new types, expected ID for the concept "DOB Format".
@pytest.mark.parametrize(
    "node_type, expected_id",
    [
        ("validation_rule", "rule_dob_format"),
        ("eligibility_rule", "rule_dob_format"),
        ("ui_business_rule", "rule_dob_format"),
        ("security_rule", "rule_dob_format"),
        ("field_specification", "field_spec_dob_format"),
    ],
)
def test_wave1_new_types_generate_expected_ids(node_type, expected_id):
    assert generate_node_id(node_type, "DOB Format") == expected_id


def test_wave1_types_are_registered_in_type_prefix():
    expected = {
        "validation_rule": "rule",
        "eligibility_rule": "rule",
        "ui_business_rule": "rule",
        "security_rule": "rule",
        "field_specification": "field_spec",
    }
    for node_type, prefix in expected.items():
        assert TYPE_PREFIX.get(node_type) == prefix


# SPEC-3 Wave 2 additions
@pytest.mark.parametrize(
    "node_type, expected_id",
    [
        ("ui_page_object", "ui_page_login_page"),
        ("ui_element", "ui_element_login_page"),
        ("rule_constant", "rule_constant_login_page"),
    ],
)
def test_wave2_new_types_generate_expected_ids(node_type, expected_id):
    assert generate_node_id(node_type, "Login Page") == expected_id


def test_wave2_types_are_registered_in_type_prefix():
    expected = {
        "ui_page_object": "ui_page",
        "ui_element": "ui_element",
        "rule_constant": "rule_constant",
    }
    for node_type, prefix in expected.items():
        assert TYPE_PREFIX.get(node_type) == prefix
