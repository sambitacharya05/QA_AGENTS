"""Markdown parser with heading-hierarchy relationship emission.

Spec 007 (Wave 3): emits ``PART_OF`` relationships reflecting the document
heading structure so the graph captures structural containment without relying
on the heuristic mapper to (re-)discover it from token overlap.

Heading → node-type mapping (MDG format):
- H1 (#)  → ``product_feature``
- H2 (##) → ``business_rule``
- H3 (###) → ``test_scenario``
"""

import os
import re
from typing import Any, Dict, List, Optional

from parsers.base import BaseParser
from parsers.id_utils import generate_node_id

_PARSER_NAME = "markdown"

# Generic structural headings that should not produce knowledge-graph nodes.
_GENERIC_HEADERS = {
    "purpose", "scope", "context", "background", "assumptions",
    "references", "revision history", "summary", "description",
    "table of contents", "introduction", "glossary", "appendix",
}


class MarkdownParser(BaseParser):
    """Parses Markdown (.md) files for Spec Driven Development (SDD).

    Industry standard mappings (MDG format):
    - H1 (#)   → product_feature
    - H2 (##)  → business_rule
    - H3 (###) → test_scenario

    Emits ``PART_OF`` edges:
    - business_rule → product_feature (H2 parent)
    - test_scenario → business_rule or product_feature (nearest ancestor)
    """

    def parse(self, file_path: str) -> Dict[str, Any]:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()

        entities: List[Dict[str, Any]] = []
        relationships: List[Dict[str, Any]] = []

        # Rolling parent tracking across heading levels
        h1_id: Optional[str] = None
        h2_id: Optional[str] = None

        # Stateful entity accumulation (flush-on-next-heading pattern)
        current_entity: Optional[Dict[str, Any]] = None
        current_content: List[str] = []

        def flush_entity() -> None:
            if current_entity:
                current_entity["description"] = "\n".join(current_content).strip()
                entities.append(current_entity)

        for line in lines:
            line_stripped = line.strip()

            match = re.match(r"^(#{1,3})\s+(.+)$", line_stripped)
            if match:
                flush_entity()
                current_content = []

                header_level = len(match.group(1))
                header_text = match.group(2).strip()

                # Strip prefixes like "Feature:", "Rule:", "Scenario:"
                clean_name = re.sub(
                    r"^(Feature|Rule|Scenario|Scenario Outline):\s*",
                    "",
                    header_text,
                    flags=re.IGNORECASE,
                ).strip()

                # Skip generic structural headers
                if clean_name.lower() in _GENERIC_HEADERS:
                    current_entity = None
                    if header_level == 1:
                        h1_id = None
                        h2_id = None
                    elif header_level == 2:
                        h2_id = None
                    continue

                if header_level == 1:
                    node_type = "product_feature"
                    name_prefix = "Feature: "
                    node_id = generate_node_id(node_type, clean_name)
                    h1_id = node_id
                    h2_id = None  # reset child tracker

                elif header_level == 2:
                    node_type = "business_rule"
                    name_prefix = "Rule: "
                    node_id = generate_node_id(node_type, clean_name)
                    h2_id = node_id
                    # Emit PART_OF: business_rule → product_feature
                    if h1_id:
                        relationships.append(
                            self.make_relationship(
                                node_id, h1_id, "PART_OF",
                                parser_name=_PARSER_NAME,
                                notes="markdown_h2_in_h1",
                            )
                        )

                else:  # header_level == 3
                    node_type = "test_scenario"
                    name_prefix = "Scenario: "
                    node_id = generate_node_id(node_type, clean_name)
                    parent_id = h2_id or h1_id
                    # Emit PART_OF: test_scenario → nearest H2 or H1
                    if parent_id:
                        relationships.append(
                            self.make_relationship(
                                node_id, parent_id, "PART_OF",
                                parser_name=_PARSER_NAME,
                                notes="markdown_h3_in_parent",
                            )
                        )

                current_entity = {
                    "id": node_id,
                    "type": node_type,
                    "name": f"{name_prefix}{clean_name}",
                    "description": "",
                    "metadata": {
                        "source_file": file_path,
                        "header_level": header_level,
                    },
                }

            else:
                if current_entity and line_stripped:
                    current_content.append(line_stripped)

        flush_entity()

        return {
            "raw_text": "".join(lines),
            "entities": entities,
            "relationships": relationships,
        }
