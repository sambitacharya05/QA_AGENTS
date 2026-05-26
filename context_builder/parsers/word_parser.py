"""Word (.docx) parser with heading-hierarchy relationship emission.

Spec 007 (Wave 3): tracks ``Heading 1`` / ``Heading 2`` / ``Heading 3`` styles
from python-docx and emits ``PART_OF`` edges so structural document hierarchy
is reflected in the graph without relying on the heuristic mapper.

SPEC-002 (Wave 5): ``_create_section_entity`` now guards against empty,
whitespace-only, and pagination-only content (e.g. Word auto-field
``"Page 1 of 3"``).  Skipped sections are recorded in ``self._warnings``
and surfaced in the ``parse()`` return dict under the ``"warnings"`` key.

SPEC-005 (Wave 5): Section and table nodes now carry
``metadata["rule_origin"]`` — either ``"nfr"`` (non-functional requirements)
or ``"functional"`` — so ``HeuristicEdgeMapper`` can exclude NFR nodes from
the ``TESTS`` scoring loop.
"""

import os
import re
from typing import Any, Dict, List, Optional

from docx import Document
from parsers.base import BaseParser
from parsers.id_utils import generate_node_id

# ---------------------------------------------------------------------------
# SPEC-002 (Wave 5) — content-quality constants
# ---------------------------------------------------------------------------

# Matches strings that are entirely Word pagination artifacts:
#   "Page 1 of 3", "1/3", "1|3", or bare page-number digits.
_PAGINATION_RE = re.compile(
    r'^\s*(?:Page\s+\d+\s+of\s+\d+\s*|\d+\s*[\|/]\s*\d+\s*|\d+\s*)*$',
    re.IGNORECASE | re.MULTILINE,
)
# Sections with fewer than this many non-whitespace characters are considered
# too sparse to be meaningful graph nodes.
_MIN_MEANINGFUL_CHARS = 30

# ---------------------------------------------------------------------------
# SPEC-005 (Wave 5) — NFR origin detection keywords
# ---------------------------------------------------------------------------
_NFR_HEADING_KEYWORDS = frozenset([
    "non-functional", "nfr", "performance", "security",
    "accessibility", "compatibility",
])

_PARSER_NAME = "word"


def _heading_level(style_name: str) -> Optional[int]:
    """Return the integer heading level for a python-docx paragraph style.

    ``"Heading 1"`` → 1, ``"Heading 2"`` → 2, etc.
    Returns ``None`` for non-heading styles.
    """
    m = re.match(r"Heading\s+(\d+)", style_name)
    return int(m.group(1)) if m else None


class WordParser(BaseParser):
    """Parses Word (.docx) documents, extracting structured text, headers, and tables.

    Emits ``PART_OF`` edges reflecting the heading hierarchy:
    - ``business_rule`` → ``product_feature``  (Heading 2 inside Heading 1)
    - deeper headings → their nearest recognised ancestor
    """

    def parse(self, file_path: str) -> Dict[str, Any]:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        # SPEC-002 (Wave 5): reset warnings list for each parse call so the
        # parser is safe to reuse across files.
        self._warnings: List[Dict[str, Any]] = []

        doc = Document(file_path)
        raw_text_parts: List[str] = []
        entities: List[Dict[str, Any]] = []
        relationships: List[Dict[str, Any]] = []

        file_name = os.path.basename(file_path)

        # Parent ID tracking keyed by heading level
        # Level 1 → product_feature; level 2+ → business_rule
        parent_id_by_level: Dict[int, Optional[str]] = {}

        current_section = "General"
        section_content: List[str] = []
        current_entity_level: Optional[int] = None

        def flush_section() -> None:
            if section_content and current_section != "General":
                self._create_section_entity(
                    entities, file_name, file_path,
                    current_section, "\n".join(section_content),
                )
            section_content.clear()

        for para in doc.paragraphs:
            text = para.text.strip()
            if not text:
                continue

            level = _heading_level(para.style.name)

            if level is not None:
                flush_section()

                current_section = text
                current_entity_level = level
                raw_text_parts.append(f"\n# {text}\n")

                # Determine node type + ID
                lower_heading = text.lower()
                if any(kw in lower_heading for kw in [
                    "rule", "eligibility", "condition", "exclusion",
                    "matrix", "guideline", "limit",
                ]):
                    node_type = "business_rule"
                else:
                    node_type = "product_feature" if level == 1 else "business_rule"

                node_id = generate_node_id(node_type, text)

                # Emit PART_OF to nearest ancestor of lower level number
                parent_id: Optional[str] = None
                for ancestor_level in sorted(parent_id_by_level):
                    if ancestor_level < level and parent_id_by_level[ancestor_level]:
                        parent_id = parent_id_by_level[ancestor_level]

                if parent_id:
                    relationships.append(
                        self.make_relationship(
                            node_id, parent_id, "PART_OF",
                            parser_name=_PARSER_NAME,
                            notes=f"word_h{level}_in_parent",
                        )
                    )

                # Register this heading as the current ancestor at this level
                parent_id_by_level[level] = node_id
                # Invalidate any deeper levels
                for deeper in list(parent_id_by_level):
                    if deeper > level:
                        del parent_id_by_level[deeper]

            else:
                section_content.append(text)
                raw_text_parts.append(text)

        flush_section()

        # Parse Tables
        for table_idx, table in enumerate(doc.tables):
            table_markdown: List[str] = []
            headers: List[str] = []

            if table.rows:
                headers = [cell.text.strip() for cell in table.rows[0].cells]
                headers = [h if h else f"Col_{i}" for i, h in enumerate(headers)]
                table_markdown.append("| " + " | ".join(headers) + " |")
                table_markdown.append("| " + " | ".join(["---"] * len(headers)) + " |")

            for row in table.rows[1:]:
                cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
                table_markdown.append("| " + " | ".join(cells) + " |")

            table_str = "\n".join(table_markdown)
            raw_text_parts.append(f"\n### Table {table_idx + 1}\n{table_str}\n")

            table_id = generate_node_id(
                "business_rule", f"{current_section} table {table_idx + 1}"
            )
            # SPEC-005 (Wave 5): tag NFR vs. functional tables so HeuristicEdgeMapper
            # can exclude NFR nodes from the TESTS scoring loop.
            table_rule_origin = (
                "nfr"
                if any(kw in current_section.lower() for kw in _NFR_HEADING_KEYWORDS)
                else "functional"
            )
            entities.append({
                "id": table_id,
                "type": "business_rule",
                "name": f"Rule Table {table_idx + 1} ({file_name})",
                "description": "Extracted tabular grid representing insurance guidelines/limits.",
                "metadata": {
                    "source_file": file_path,
                    "table_index": table_idx,
                    "table_data": table_str,
                    "headers": headers,
                    "rule_origin": table_rule_origin,   # SPEC-005
                },
            })

        return {
            "raw_text": "\n".join(raw_text_parts),
            "entities": entities,
            "relationships": relationships,
            "warnings": self._warnings,   # SPEC-002 (Wave 5)
        }

    # ------------------------------------------------------------------
    # Helper — identical to previous implementation
    # ------------------------------------------------------------------

    def _create_section_entity(
        self,
        entities: List[Dict[str, Any]],
        file_name: str,
        file_path: str,
        heading: str,
        content: str,
    ) -> None:
        """Create a logical business_rule or product_feature node from a document section.

        SPEC-002 (Wave 5): Skips sections whose content is empty,
        whitespace-only, or consists entirely of pagination artifacts (e.g.
        Word auto-field ``"Page 1 of 3"``).  Skipped sections are recorded
        in ``self._warnings`` instead of emitting a graph node.

        SPEC-005 (Wave 5): Adds ``rule_origin`` metadata tag (``"nfr"`` or
        ``"functional"``) so ``HeuristicEdgeMapper`` can exclude NFR-origin
        nodes from ``TESTS`` edge scoring.
        """
        # --- SPEC-002: content quality gate ---
        stripped = content.strip()
        if not stripped:
            self._warnings.append({
                "type": "empty_section",
                "file": file_name,
                "heading": heading,
                "reason": "section_empty",
                "raw_content_length": len(content),
            })
            return
        if _PAGINATION_RE.fullmatch(stripped):
            self._warnings.append({
                "type": "empty_section",
                "file": file_name,
                "heading": heading,
                "reason": "pagination_only",
                "raw_content_length": len(content),
            })
            return
        if len(stripped) < _MIN_MEANINGFUL_CHARS:
            self._warnings.append({
                "type": "empty_section",
                "file": file_name,
                "heading": heading,
                "reason": "content_too_short",
                "raw_content_length": len(content),
            })
            return
        # --- end content quality gate ---

        lower_heading = heading.lower()
        node_type = "product_feature"
        if any(kw in lower_heading for kw in [
            "rule", "eligibility", "condition", "exclusion",
            "matrix", "guideline", "limit",
        ]):
            node_type = "business_rule"

        node_id = generate_node_id(node_type, heading)

        # SPEC-005 (Wave 5): classify origin for the heuristic edge mapper.
        rule_origin = (
            "nfr"
            if any(kw in lower_heading for kw in _NFR_HEADING_KEYWORDS)
            else "functional"
        )

        entities.append({
            "id": node_id,
            "type": node_type,
            "name": f"{heading} ({file_name})",
            "description": content[:1000] + ("..." if len(content) > 1000 else ""),
            "metadata": {
                "source_file": file_path,
                "heading": heading,
                "full_text": content,
                "rule_origin": rule_origin,   # SPEC-005
            },
        })
