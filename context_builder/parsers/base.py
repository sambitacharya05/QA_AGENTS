"""Abstract base parser and canonical helper factories.

Spec 007 (Wave 3): adds ``make_entity()`` and ``make_relationship()`` static
helpers so every parser builds entity/relationship dicts with a consistent
shape.  Parser-emitted relationships default to ``confidence = 1.0`` because
they are *structural* (syntactically deterministic), not inferential.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class BaseParser(ABC):
    """Abstract base class defining the contract for all document parsers."""

    @abstractmethod
    def parse(self, file_path: str) -> Dict[str, Any]:
        """Parse *file_path* and extract structural information.

        Returns
        -------
        dict with keys:
        - ``"raw_text"`` (str): raw document text for caching / indexing.
        - ``"entities"`` (list[dict]): extracted node dicts.  Each must contain:
            - ``"id"`` (str): unique node identifier
            - ``"type"`` (str): node type (business_rule, api_endpoint, …)
            - ``"name"`` (str): human-friendly name
            - ``"description"`` (str): summary or detail text
            - ``"metadata"`` (dict): parser-specific contextual attributes
        - ``"relationships"`` (list[dict]): relationship dicts.  Each must contain:
            - ``"source_id"`` (str): source node ID
            - ``"target_id"`` (str): target node ID
            - ``"relationship"`` (str): edge type (IMPLEMENTS, VALIDATES, PART_OF, …)
            - ``"metadata"`` (dict): relationship-specific context including
              ``confidence``, ``source``, and ``evidence`` block (see
              ``make_relationship``).
        """
        pass

    # ------------------------------------------------------------------
    # Canonical factory helpers (Spec 007)
    # ------------------------------------------------------------------

    @staticmethod
    def make_entity(
        node_id: str,
        node_type: str,
        name: str,
        description: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> dict:
        """Build a canonical entity dict for inclusion in ``parse()`` results.

        Keeps every parser from hand-rolling the dict structure and ensures the
        ``"metadata"`` key is always present (never ``None``).
        """
        return {
            "id": node_id,
            "type": node_type,
            "name": name,
            "description": description,
            "metadata": metadata or {},
        }

    @staticmethod
    def make_relationship(
        source_id: str,
        target_id: str,
        relationship: str,
        confidence: float = 1.0,
        parser_name: str = "",
        notes: str = "",
    ) -> dict:
        """Build a canonical relationship dict for inclusion in ``parse()`` results.

        Parser-emitted relationships default to ``confidence = 1.0`` because
        they are structurally certain (syntactically deterministic).  Heuristic
        mappers (Spec 006) and Copilot mappers (Spec 005) produce lower values.

        Parameters
        ----------
        source_id:
            Source node ID.
        target_id:
            Target node ID.
        relationship:
            Edge type string (e.g. ``"PART_OF"``, ``"IMPLEMENTS"``).
        confidence:
            Float in [0, 1].  Defaults to 1.0 for parser-level structural edges.
        parser_name:
            Short parser identifier included in ``metadata.source`` for audit.
        notes:
            Human-readable origin description (e.g. ``"markdown_h2_in_h1"``).
        """
        return {
            "source_id": source_id,
            "target_id": target_id,
            "relationship": relationship,
            "metadata": {
                "source": f"parser:{parser_name}" if parser_name else "parser",
                "confidence": confidence,
                "evidence": {
                    "method": "explicit_ref",
                    "score": confidence,
                    "notes": notes,
                },
            },
        }
