"""
Go AST-based endpoint extractor using tree-sitter.

Detects Gin / net/http route registrations:
    r.GET("/ping", handler)
    router.POST("/items", handler)
    http.HandleFunc("/health", handler)

The original regex was case-insensitive and caught unrelated calls like
`someRouter.Get(...)` in variable names.  We match only known variable names
and exact method spellings in the AST.
"""

from __future__ import annotations

import logging
from typing import Any

from parsers.ast.loader import parser_for
from parsers.id_utils import generate_node_id

log = logging.getLogger(__name__)

# Gin router HTTP method names (exact case as used in Go)
GIN_METHODS: dict[str, str] = {
    "GET": "GET",
    "POST": "POST",
    "PUT": "PUT",
    "DELETE": "DELETE",
    "PATCH": "PATCH",
    "OPTIONS": "OPTIONS",
    "HEAD": "HEAD",
    "Any": "ANY",
}

# net/http registration function
HTTP_FUNCS = {"HandleFunc", "Handle"}

# Recognised router / app variable names (common Gin conventions)
GIN_VARS = {"r", "router", "g", "engine", "group"}
HTTP_PKGS = {"http"}


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
    """Extract content from a Go interpreted_string_literal or raw_string_literal."""
    if node is None:
        return None
    raw = node.text.decode("utf-8") if node.text else ""
    if raw.startswith('"') and raw.endswith('"'):
        return raw[1:-1]
    if raw.startswith("`") and raw.endswith("`"):
        return raw[1:-1]
    return raw


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class GoEndpointExtractor:
    """Extracts api_endpoint entities from Go source using tree-sitter."""

    def __init__(self) -> None:
        self._parser = parser_for("go")

    def extract(self, source: str | bytes, file_path: str) -> dict[str, Any]:
        if isinstance(source, str):
            source = source.encode("utf-8")

        try:
            tree = self._parser.parse(source)
        except Exception as exc:
            log.warning("Go AST parse failed for %s: %s", file_path, exc)
            return {"entities": [], "relationships": []}

        root = tree.root_node
        file_name = file_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        entities: list[dict] = []

        call_exprs = _find_all(root, "call_expression")
        for call in call_exprs:
            sel = _first_child_of_type(call, "selector_expression")
            if sel is None:
                continue

            sel_text = sel.text.decode("utf-8") if sel.text else ""
            parts = sel_text.split(".")
            if len(parts) != 2:
                continue
            obj_name, method_name = parts[0], parts[1]

            http_method: str | None = None

            # Gin-style: r.GET("/path", ...)
            if obj_name in GIN_VARS and method_name in GIN_METHODS:
                http_method = GIN_METHODS[method_name]

            # net/http: http.HandleFunc("/path", ...)
            elif obj_name in HTTP_PKGS and method_name in HTTP_FUNCS:
                http_method = "ANY"

            if http_method is None:
                continue

            # First string argument is the route path
            arg_list = _first_child_of_type(call, "argument_list")
            if arg_list is None:
                continue

            route_path: str | None = None
            for arg_child in arg_list.children:
                if arg_child.type in (
                    "interpreted_string_literal",
                    "raw_string_literal",
                ):
                    route_path = _string_value(arg_child)
                    break

            if route_path is None:
                continue

            ep_id = generate_node_id("api_endpoint", f"{http_method} {route_path}")
            entities.append({
                "id": ep_id,
                "type": "api_endpoint",
                "name": f"{http_method} {route_path}",
                "description": f"Go routing endpoint defined in {file_name}",
                "metadata": {
                    "source_file": file_path,
                    "language": "go",
                    "http_method": http_method,
                    "path": route_path,
                },
            })

        return {"entities": entities, "relationships": []}
