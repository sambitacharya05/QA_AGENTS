"""Tests for parsers/properties_parser.py — per-key rule_constant emission.

SPEC-3 Wave 2: replaces the misrouted SchemaParser fallback that emitted
zero entities for application.properties / bootstrap.properties / pom.properties.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parsers.properties_parser import PropertiesParser


def _write_properties(tmp_path, body: str) -> str:
    path = tmp_path / "application.properties"
    path.write_text(body, encoding="utf-8")
    return str(path)


def test_per_key_node_emission(tmp_path):
    """SPEC-3 Wave 2 §2.2: 8 keys → 8 rule_constant nodes."""
    body = """
dob.min.age=18
dob.max.age=65
otp.length=6
otp.timeout.seconds=120
otp.max.attempts=3
mobile.country.code=+91
file.upload.size.mb=5
session.idle.minutes=15
""".strip()
    path = _write_properties(tmp_path, body)
    result = PropertiesParser().parse(path)
    rc = [e for e in result["entities"] if e["type"] == "rule_constant"]
    assert len(rc) == 8
    ids = {n["id"] for n in rc}
    assert "rule_constant_dob_min_age" in ids
    assert "rule_constant_otp_max_attempts" in ids
    assert "rule_constant_session_idle_minutes" in ids


def test_value_type_inference(tmp_path):
    """SPEC-3 Wave 2 §2.2: boolean / int / float / list / string detection."""
    body = """
feature.flag.enabled=true
dob.min.age=18
risk.multiplier=1.5
supported.versions=6,7,8,9
country.code=IN
""".strip()
    path = _write_properties(tmp_path, body)
    result = PropertiesParser().parse(path)
    by_key = {
        n["metadata"]["config_key"]: n["metadata"]
        for n in result["entities"]
    }
    assert by_key["feature.flag.enabled"]["value_type"] == "boolean"
    assert by_key["feature.flag.enabled"]["value"] is True
    assert by_key["dob.min.age"]["value_type"] == "integer"
    assert by_key["dob.min.age"]["value"] == 18
    assert by_key["risk.multiplier"]["value_type"] == "float"
    assert by_key["risk.multiplier"]["value"] == 1.5
    assert by_key["supported.versions"]["value_type"] == "list"
    assert by_key["supported.versions"]["value"] == ["6", "7", "8", "9"]
    assert by_key["country.code"]["value_type"] == "string"


def test_comments_and_blank_lines_ignored(tmp_path):
    """SPEC-3 Wave 2 §2.2: # and ! comment lines plus blank lines emit nothing."""
    body = """
# This is a comment
! This is also a comment per the .properties spec

dob.min.age=18

# Another comment
otp.length=6
""".strip()
    path = _write_properties(tmp_path, body)
    result = PropertiesParser().parse(path)
    rc = [e for e in result["entities"] if e["type"] == "rule_constant"]
    assert len(rc) == 2
    ids = {n["id"] for n in rc}
    assert ids == {"rule_constant_dob_min_age", "rule_constant_otp_length"}


def test_dotted_key_becomes_underscore_id(tmp_path):
    """SPEC-3 Wave 2 §2.2: dob.min.age=18 → id=rule_constant_dob_min_age."""
    body = "dob.min.age=18\n"
    path = _write_properties(tmp_path, body)
    result = PropertiesParser().parse(path)
    rc = result["entities"][0]
    assert rc["id"] == "rule_constant_dob_min_age"
    assert rc["metadata"]["config_key"] == "dob.min.age"
    assert rc["metadata"]["namespace"] == "dob"
