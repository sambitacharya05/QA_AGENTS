"""
Shared Unique Entity Identifier (SUEI) generation utilities.

All parsers must use these functions to generate node IDs that are
decoupled from source filenames and based on domain concepts.

Formula: {type_prefix}_{lowercase_concept_slug}
Example: "business_rule", "Youthful Driver Surcharge" → "rule_youthful_driver_surcharge"
"""
import re

# Prefix mapping by node type
TYPE_PREFIX = {
    "business_rule": "rule",
    "product_feature": "feature",
    "api_endpoint": "endpoint",
    "code_component": "component",
    "data_model": "schema",
    "test_scenario": "scenario",
    "test_utility": "util",
    "claims": "rule",
    "billing": "rule",
    "access_control": "rule",
    "enrollment": "rule",
    "non_functional": "rule",

    # SPEC-3 Wave 1 — rule-family subtypes (kept in the rule_ family for ID uniformity)
    "validation_rule": "rule",
    "eligibility_rule": "rule",
    "ui_business_rule": "rule",
    "security_rule": "rule",

    # SPEC-3 Wave 1 — field specification nodes (own prefix)
    "field_specification": "field_spec",

    # SPEC-3 Wave 2 — UI / config baseline node types
    "ui_page_object": "ui_page",
    "ui_element": "ui_element",
    "rule_constant": "rule_constant",
}


def generate_node_id(node_type: str, concept_name: str) -> str:
    """
    Generates a SUEI-compliant node ID from a node type and concept name.

    The generated ID is:
    - Decoupled from source filenames or format-specific markers
    - Lowercase alphanumeric snake_case
    - Prefixed by the node type category

    Args:
        node_type: The semantic type of the node (e.g. "business_rule", "api_endpoint").
        concept_name: The human-readable concept name to slugify.

    Returns:
        A deterministic, SUEI-compliant node ID string.
    """
    prefix = TYPE_PREFIX.get(node_type, node_type)
    slug = _to_concept_slug(concept_name)
    return f"{prefix}_{slug}"


def _to_concept_slug(text: str) -> str:
    """
    Converts human-readable text to a clean snake_case concept slug.

    Strips common noise prefixes (Rule:, Feature:, Scenario:, etc.),
    replaces non-alphanumeric characters with underscores, and collapses
    multiple underscores into one.

    Args:
        text: The raw concept name text.

    Returns:
        A clean, lowercase, snake_case slug string.
    """
    text = text.lower().strip()
    # Remove common noise prefixes parsers may pass through
    text = re.sub(
        r"^(rule|feature|scenario|scenario outline)[:\s]+",
        "",
        text,
        flags=re.IGNORECASE,
    )
    # Replace non-alphanumeric chars with underscores
    text = re.sub(r"[^a-z0-9]+", "_", text)
    # Collapse multiple underscores and strip edges
    text = re.sub(r"_+", "_", text).strip("_")
    return text
