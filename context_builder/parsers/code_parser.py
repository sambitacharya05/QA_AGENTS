"""
Source-code parser for Java, TypeScript/JS, Python, and Go.

Internals now delegate to tree-sitter-based AST extractors (parsers/ast/).
The public contract is unchanged:
  - parse(file_path) → {"raw_text", "entities", "relationships"}
  - _scan_java / _scan_typescript / _scan_python / _scan_go remain callable
    for direct invocation (e.g. from tests), same signatures as before.

Benefits over the previous regex scanner:
  - Multi-line Spring annotations (@GetMapping across several lines) work.
  - Multiple top-level classes in one Java file are all captured.
  - @RestController only fires on actual annotations, not string literals.
  - Express routes are matched case-sensitively by actual selector expressions.
  - Phantom RestAssured / list.get() false-positives cannot occur here
    (those live in test_framework_parsers.py for QAF, a separate scanner).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

from parsers.base import BaseParser
from parsers.id_utils import generate_node_id

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lazy extractor singletons — imported on first use to avoid import-time cost
# ---------------------------------------------------------------------------

def _java_extractor():
    from parsers.ast.java_extractor import JavaEndpointExtractor
    return JavaEndpointExtractor()


def _ts_extractor():
    from parsers.ast.typescript_extractor import TypeScriptEndpointExtractor
    return TypeScriptEndpointExtractor()


def _py_extractor():
    from parsers.ast.python_extractor import PythonEndpointExtractor
    return PythonEndpointExtractor()


def _go_extractor():
    from parsers.ast.go_extractor import GoEndpointExtractor
    return GoEndpointExtractor()


class CodeParser(BaseParser):
    """
    Parses Java, TypeScript, JavaScript, Python, and Go source files using
    tree-sitter AST extractors to identify endpoints and components.
    """

    def parse(self, file_path: str) -> Dict[str, Any]:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        file_name = os.path.basename(file_path)
        with open(file_path, "r", encoding="utf-8", errors="ignore") as fh:
            code = fh.read()

        entities: List[Dict[str, Any]] = []
        relationships: List[Dict[str, Any]] = []

        if file_path.endswith(".java"):
            self._scan_java(code, file_path, file_name, entities, relationships)
        elif file_path.endswith((".ts", ".tsx", ".js", ".jsx")):
            self._scan_typescript(code, file_path, file_name, entities, relationships)
        elif file_path.endswith(".py"):
            self._scan_python(code, file_path, file_name, entities, relationships)
        elif file_path.endswith(".go"):
            self._scan_go(code, file_path, file_name, entities, relationships)

        return {"raw_text": code, "entities": entities, "relationships": relationships}

    # ------------------------------------------------------------------
    # Language-specific scanners (delegating to AST extractors)
    # The signatures are kept identical to the previous regex implementation
    # so existing call-sites and tests remain compatible.
    # ------------------------------------------------------------------

    def _scan_java(
        self,
        code: str,
        file_path: str,
        file_name: str,
        entities: List[Dict[str, Any]],
        relationships: List[Dict[str, Any]] = None,
    ) -> None:
        """Scan Java source for Spring controllers, classes, and endpoint mappings.

        Spec 007: collects IMPLEMENTS relationships emitted by the AST extractor.
        """
        try:
            result = _java_extractor().extract(code, file_path)
            entities.extend(result["entities"])
            if relationships is not None:
                relationships.extend(result.get("relationships", []))
        except Exception as exc:
            log.warning("Java AST extraction failed for %s: %s — falling back to empty result", file_path, exc)

    def _scan_typescript(
        self,
        code: str,
        file_path: str,
        file_name: str,
        entities: List[Dict[str, Any]],
        relationships: List[Dict[str, Any]] = None,
    ) -> None:
        """Scan TS/JS source for NestJS controllers, Express routes, and components."""
        try:
            result = _ts_extractor().extract(code, file_path)
            entities.extend(result["entities"])
            if relationships is not None:
                relationships.extend(result.get("relationships", []))
        except Exception as exc:
            log.warning("TypeScript AST extraction failed for %s: %s — falling back to empty result", file_path, exc)

    def _scan_python(
        self,
        code: str,
        file_path: str,
        file_name: str,
        entities: List[Dict[str, Any]],
        relationships: List[Dict[str, Any]] = None,
    ) -> None:
        """Scan Python source for FastAPI / Flask route decorators."""
        try:
            result = _py_extractor().extract(code, file_path)
            entities.extend(result["entities"])
            if relationships is not None:
                relationships.extend(result.get("relationships", []))
        except Exception as exc:
            log.warning("Python AST extraction failed for %s: %s — falling back to empty result", file_path, exc)

    def _scan_go(
        self,
        code: str,
        file_path: str,
        file_name: str,
        entities: List[Dict[str, Any]],
        relationships: List[Dict[str, Any]] = None,
    ) -> None:
        """Scan Go source for Gin / net/http route registrations."""
        try:
            result = _go_extractor().extract(code, file_path)
            entities.extend(result["entities"])
            if relationships is not None:
                relationships.extend(result.get("relationships", []))
        except Exception as exc:
            log.warning("Go AST extraction failed for %s: %s — falling back to empty result", file_path, exc)
