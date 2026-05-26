"""
Python AST-based endpoint extractor using tree-sitter.

Detects FastAPI / Flask / Starlette routes via decorated function definitions:
    @app.get("/items/{id}")
    @router.post("/items")
    @bp.patch("/items/{id}")

Uses tree-sitter for consistent behaviour across the parser suite.  The Python
grammar's `decorated_definition` node pairs each decorator with its function
so there are no accidental cross-matches.
"""

from __future__ import annotations

import logging
from typing import Any

from parsers.ast.loader import parser_for
from parsers.id_utils import generate_node_id

log = logging.getLogger(__name__)

# Recognised route-registrar variable names (attribute owner)
ROUTE_VARS = {"app", "router", "bp", "api"}
# HTTP methods we care about (lowercase as they appear in the decorator)
HTTP_METHODS_PY = {"get", "post", "put", "delete", "patch", "options", "head"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_all(node, *types: str) -> list:
    results = []
    if node.type in types:
        results.append(node)
    for child in node.children:
        results.extend(_find_all(child, *types))
    return results


def _first_child_of_type(node, *types: str):
    for child in node.children:
        if child.type in types:
            return child
    return None


def _string_value(node) -> str | None:
    """Return the content of a Python string node (strips surrounding quotes)."""
    if node is None:
        return None
    raw = node.text
    if raw is None:
        return None
    text = raw.decode("utf-8")
    # Strip surrounding quotes (handles ", ', """, ''')
    for q in ('"""', "'''", '"', "'"):
        if text.startswith(q) and text.endswith(q) and len(text) > len(q) * 2 - 1:
            return text[len(q):-len(q)]
    return text


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class PythonEndpointExtractor:
    """Extracts api_endpoint entities from Python source using tree-sitter."""

    def __init__(self) -> None:
        self._parser = parser_for("python")

    def extract(self, source: str | bytes, file_path: str) -> dict[str, Any]:
        if isinstance(source, str):
            source = source.encode("utf-8")

        try:
            tree = self._parser.parse(source)
        except Exception as exc:
            log.warning("Python AST parse failed for %s: %s", file_path, exc)
            return {"entities": [], "relationships": []}

        root = tree.root_node
        file_name = file_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        entities: list[dict] = []

        # `decorated_definition` reliably pairs each decorator with its function
        for ddef in _find_all(root, "decorated_definition"):
            # Collect all decorator nodes for this definition
            decorators = [c for c in ddef.children if c.type == "decorator"]
            for dec in decorators:
                # decorator → @ + call
                call = _first_child_of_type(dec, "call")
                if call is None:
                    continue

                # call → attribute + argument_list
                attr = _first_child_of_type(call, "attribute")
                if attr is None:
                    continue

                # attribute: <obj> . <method>
                attr_text = attr.text.decode("utf-8") if attr.text else ""
                parts = attr_text.split(".")
                if len(parts) != 2:
                    continue
                obj_name, method_name = parts[0], parts[1].lower()

                if obj_name not in ROUTE_VARS or method_name not in HTTP_METHODS_PY:
                    continue

                # Extract the first positional string argument (the path)
                arg_list = _first_child_of_type(call, "argument_list")
                if arg_list is None:
                    continue

                # Look for a string literal as the first meaningful argument
                route_path: str | None = None
                for arg_child in arg_list.children:
                    if arg_child.type in ("string", "concatenated_string"):
                        route_path = _string_value(arg_child)
                        break

                if route_path is None:
                    continue

                http_method = method_name.upper()
                ep_id = generate_node_id("api_endpoint", f"{http_method} {route_path}")
                entities.append({
                    "id": ep_id,
                    "type": "api_endpoint",
                    "name": f"{http_method} {route_path}",
                    "description": (
                        f"Python (FastAPI/Flask) routing endpoint defined in {file_name}"
                    ),
                    "metadata": {
                        "source_file": file_path,
                        "language": "python",
                        "http_method": http_method,
                        "path": route_path,
                    },
                })

        return {"entities": entities, "relationships": []}
