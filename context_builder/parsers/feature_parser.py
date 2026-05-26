"""
Gherkin .feature file parser backed by gherkin-official.

Replaces the hand-rolled line-scanner with the official Cucumber/Gherkin parser
so that the following constructs work correctly:

  - Background: steps are merged (in order) into every scenario's step list.
  - Scenario Outline + Examples rows are expanded to N concrete test_scenario
    nodes (one per row), with placeholder substitution applied to name and steps.
  - Rule: blocks are walked and produce their own set of scenarios.
  - @tags on features and scenarios are surfaced in metadata.
  - Malformed .feature files are caught and logged — they never blow up the
    whole ingestion pipeline.
  - # language: headers are passed through to metadata.

Return shape is unchanged: {"raw_text", "entities", "relationships"}.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List

from parsers.base import BaseParser
from parsers.id_utils import generate_node_id

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# gherkin-official import — fail gracefully if not installed
# ---------------------------------------------------------------------------
try:
    from gherkin.parser import Parser as GherkinParser
    from gherkin.token_scanner import TokenScanner
    from gherkin.errors import CompositeParserException, ParserError
    _GHERKIN_AVAILABLE = True
except ImportError:
    _GHERKIN_AVAILABLE = False
    log.warning(
        "gherkin-official is not installed; feature_parser will return empty entities. "
        "Run: pip install gherkin-official"
    )


def _step_text(step: dict) -> str:
    """Format a single Gherkin step as 'Keyword text'."""
    keyword = step.get("keyword", "").rstrip()
    text = step.get("text", "")
    return f"{keyword} {text}".strip()


def _substitute(template: str, row_values: dict[str, str]) -> str:
    """Replace <placeholder> tokens in *template* with values from *row_values*."""
    for key, val in row_values.items():
        template = template.replace(f"<{key}>", val)
    return template


def _tag_names(tags: list) -> list[str]:
    """Extract bare tag names (strip leading @) from a list of tag dicts."""
    result = []
    for t in tags or []:
        name = t.get("name", "")
        result.append(name.lstrip("@"))
    return result


class FeatureParser(BaseParser):
    """Parses Gherkin BDD (.feature) files using gherkin-official."""

    def parse(self, file_path: str) -> Dict[str, Any]:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        with open(file_path, "r", encoding="utf-8", errors="ignore") as fh:
            source = fh.read()

        if not _GHERKIN_AVAILABLE:
            return {"raw_text": source, "entities": [], "relationships": []}

        try:
            doc = GherkinParser().parse(TokenScanner(source))
        except (CompositeParserException, Exception) as exc:  # noqa: BLE001
            log.warning("Gherkin parse error in %s: %s", file_path, exc)
            return {"raw_text": source, "entities": [], "relationships": []}

        feature = doc.get("feature")
        if not feature:
            return {"raw_text": source, "entities": [], "relationships": []}

        entities: List[Dict[str, Any]] = []
        relationships: List[Dict[str, Any]] = []

        feature_name = feature.get("name", "Unknown Feature")
        feature_language = feature.get("language", "en")
        feature_tags = _tag_names(feature.get("tags", []))

        feature_id = generate_node_id("product_feature", feature_name)
        entities.append({
            "id": feature_id,
            "type": "product_feature",
            "name": f"Feature: {feature_name}",
            "description": feature.get("description", "").strip(),
            "metadata": {
                "source_file": file_path,
                "language": feature_language,
                "tags": feature_tags,
            },
        })

        # Background steps accumulate and are prepended to every scenario
        background_steps: List[str] = []

        for child in feature.get("children", []):
            if "background" in child:
                background_steps = [_step_text(s) for s in child["background"].get("steps", [])]

            elif "scenario" in child:
                self._emit_scenario(
                    child["scenario"], feature_id, feature_name,
                    background_steps, file_path, entities, relationships,
                )

            elif "rule" in child:
                self._emit_rule(
                    child["rule"], feature_id, feature_name,
                    background_steps, file_path, entities, relationships,
                )

        return {"raw_text": source, "entities": entities, "relationships": relationships}

    # ------------------------------------------------------------------
    # Internal emission helpers
    # ------------------------------------------------------------------

    def _emit_scenario(
        self,
        scenario: dict,
        feature_id: str,
        feature_name: str,
        background_steps: List[str],
        file_path: str,
        entities: List[Dict[str, Any]],
        relationships: List[Dict[str, Any]],
    ) -> None:
        """Emit one or more test_scenario nodes from a Gherkin scenario dict."""
        examples = scenario.get("examples", [])
        keyword = scenario.get("keyword", "Scenario").strip()

        if examples and "Outline" in keyword:
            # Scenario Outline — expand each row to a concrete scenario.
            # SPEC-004 (Wave 5): enumerate rows so _emit_one can suffix the node ID
            # when the title contains no <placeholder> tokens and all rows would
            # otherwise produce the same ID.
            for example_block in examples:
                header = example_block.get("tableHeader", {})
                header_cells = [c.get("value", "") for c in header.get("cells", [])]
                for row_idx, row in enumerate(example_block.get("tableBody", [])):
                    row_vals = {
                        h: c.get("value", "")
                        for h, c in zip(header_cells, row.get("cells", []))
                    }
                    expanded_name = _substitute(scenario.get("name", ""), row_vals)
                    expanded_steps = [
                        _substitute(_step_text(s), row_vals)
                        for s in scenario.get("steps", [])
                    ]
                    # First-column value used as a human-readable suffix component
                    first_col_val = row_vals.get(header_cells[0], "") if header_cells else ""
                    self._emit_one(
                        expanded_name, scenario, row_vals,
                        background_steps, expanded_steps,
                        feature_id, feature_name, file_path,
                        entities, relationships,
                        row_index=row_idx,
                        example_label=first_col_val,
                    )
        else:
            # Plain scenario
            raw_steps = [_step_text(s) for s in scenario.get("steps", [])]
            self._emit_one(
                scenario.get("name", ""),
                scenario, {},
                background_steps, raw_steps,
                feature_id, feature_name, file_path,
                entities, relationships,
            )

    def _emit_rule(
        self,
        rule: dict,
        feature_id: str,
        feature_name: str,
        background_steps: List[str],
        file_path: str,
        entities: List[Dict[str, Any]],
        relationships: List[Dict[str, Any]],
    ) -> None:
        """Walk a Rule block and emit its scenarios."""
        rule_bg: List[str] = list(background_steps)  # inherit feature background
        for child in rule.get("children", []):
            if "background" in child:
                rule_bg = background_steps + [
                    _step_text(s) for s in child["background"].get("steps", [])
                ]
            elif "scenario" in child:
                self._emit_scenario(
                    child["scenario"], feature_id, feature_name,
                    rule_bg, file_path, entities, relationships,
                )

    def _emit_one(
        self,
        name: str,
        scenario: dict,
        row_vals: dict[str, str],
        background_steps: List[str],
        raw_steps: List[str],
        feature_id: str,
        feature_name: str,
        file_path: str,
        entities: List[Dict[str, Any]],
        relationships: List[Dict[str, Any]],
        row_index: "int | None" = None,       # SPEC-004 (Wave 5): 0-based row in Examples table
        example_label: "str | None" = None,   # SPEC-004: first-column value for readable suffix
    ) -> None:
        """Persist a single concrete scenario entity.

        Spec 007 (Wave 3): emits structural relationships:
        - ``PART_OF`` → scenario belongs to its feature.
        - ``TESTS``   → explicit ``@rule:<slug>`` tags link scenario to a rule
                         node with ``confidence = 1.0`` (deterministic, no
                         token-overlap required).

        SPEC-004 (Wave 5): Scenario Outline rows that share an identical
        expanded name (because the title has no ``<placeholder>`` tokens) would
        previously collide to the same node ID. ``row_index`` is now always
        passed for Outline rows and a ``_ex{N}`` (plus optional slug) suffix is
        appended, guaranteeing uniqueness without altering the human-readable
        ``name`` field.
        """
        all_steps = background_steps + raw_steps
        tags = _tag_names(scenario.get("tags", []))

        # SPEC-004 (Wave 5): suffix outline row IDs to prevent graph upsert collisions.
        base_id = generate_node_id("test_scenario", name or "unnamed")
        if row_index is not None:
            label_slug = ""
            if example_label:
                label_slug = re.sub(r'[^a-z0-9]+', '_', example_label.lower()).strip('_')[:20]
            suffix = f"_ex{row_index + 1}_{label_slug}" if label_slug else f"_ex{row_index + 1}"
            node_id = f"{base_id}{suffix}"
        else:
            node_id = base_id

        entities.append({
            "id": node_id,
            "type": "test_scenario",
            "name": f"Scenario: {name} ({feature_name})",
            "description": "Extracted BDD test scenario:\n" + "\n".join(all_steps),
            "metadata": {
                "source_file": file_path,
                "feature": feature_name,
                "scenario": name,
                "steps": all_steps,
                "tags": tags,
                "example_row_index": row_index,    # None for plain scenarios
                "example_row_values": row_vals,    # {} for plain scenarios
            },
        })

        # Structural PART_OF: scenario → feature
        relationships.append(
            self.make_relationship(
                node_id, feature_id, "PART_OF",
                parser_name="feature",
                notes="scenario_in_feature",
            )
        )

        # Explicit TESTS: @rule:<slug> tag → deterministic link to rule node
        for raw_tag in scenario.get("tags", []):
            tag_name: str = raw_tag.get("name", "")
            if tag_name.startswith("@rule:"):
                slug = tag_name[len("@rule:"):]
                target_rule_id = f"rule_{slug}"
                relationships.append(
                    self.make_relationship(
                        node_id, target_rule_id, "TESTS",
                        parser_name="feature",
                        notes=f"explicit_tag:{tag_name}",
                    )
                )
