"""
TypeScript/JavaScript AST-based endpoint and class extractor.

Uses tree-sitter to reliably detect:
- NestJS @Controller/@Get/@Post etc. decorators (no false positives from string literals)
- Express router.get/post/put/delete routing calls

The existing regex scanner was case-insensitive for Express, catching user-defined
`Router.Get(...)` in unrelated code.  tree-sitter parses the actual AST so we
match only real selector-expression calls.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from parsers.ast.loader import parser_for
from parsers.id_utils import generate_node_id

log = logging.getLogger(__name__)

# NestJS HTTP method decorators
NESTJS_ANNOS: dict[str, str] = {
    "Get": "GET",
    "Post": "POST",
    "Put": "PUT",
    "Delete": "DELETE",
    "Patch": "PATCH",
    "Options": "OPTIONS",
    "Head": "HEAD",
    "All": "ALL",
}

# Express / Fastify router method names (lowercase)
EXPRESS_METHODS = {"get", "post", "put", "delete", "patch"}

# Known Express variable names used as router/app
EXPRESS_VARS = {"app", "router", "route"}


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
    """Extract the string content from a tree-sitter 'string' node (TS uses 'string')."""
    if node is None:
        return None
    # TS string node: children are [ quote, string_fragment*, quote ]
    for child in node.children:
        if child.type in ("string_fragment", "template_chars"):
            return child.text.decode("utf-8")
    # Fallback: strip quotes from raw text
    raw = node.text.decode("utf-8")
    if raw and raw[0] in ('"', "'", "`"):
        return raw[1:-1]
    return None


def _decorator_func_and_arg(decorator_node) -> tuple[str | None, str | None]:
    """
    Given a decorator node, return (func_name, first_string_arg | None).

    Handles:
      @Controller('users')   → ('Controller', 'users')
      @Get()                 → ('Get', None)
      @Get('/list')          → ('Get', '/list')
    """
    call = _first_child_of_type(decorator_node, "call_expression")
    if call is None:
        return None, None

    func_name: str | None = None
    arg_str: str | None = None

    for child in call.children:
        if child.type == "identifier":
            func_name = child.text.decode("utf-8")
        elif child.type == "arguments":
            # First string argument
            str_node = _first_child_of_type(child, "string")
            if str_node:
                arg_str = _string_value(str_node)

    return func_name, arg_str


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class TypeScriptEndpointExtractor:
    """
    Extracts code_component and api_endpoint entities from TypeScript/JavaScript source.
    Supports NestJS decorator-based controllers and Express/Fastify router calls.
    """

    def __init__(self) -> None:
        self._parser = parser_for("typescript")

    def extract(self, source: str | bytes, file_path: str) -> dict[str, Any]:
        if isinstance(source, str):
            source = source.encode("utf-8")

        try:
            tree = self._parser.parse(source)
        except Exception as exc:
            log.warning("TypeScript AST parse failed for %s: %s", file_path, exc)
            return {"entities": [], "relationships": []}

        root = tree.root_node
        file_name = file_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        entities: list[dict] = []
        seen_ids: set[str] = set()

        # -----------------------------------------------------------------------
        # 1. Detect class & NestJS controller decorators
        # -----------------------------------------------------------------------
        class_decls = _find_all(root, "class_declaration")
        for cd in class_decls:
            class_name_node = _first_child_of_type(cd, "type_identifier")
            class_name = class_name_node.text.decode("utf-8") if class_name_node else file_name.split(".")[0]

            # Decorators sit as siblings *before* the class_declaration in the parent
            # In tree-sitter-typescript the decorator nodes appear directly as children
            # of the class_declaration OR as preceding siblings in the export_statement.
            # We collect all decorators that precede this class within the root program.
            base_path = ""
            has_nestjs_controller = False
            class_decorators = _find_all(cd.parent or cd, "decorator") if cd.parent else _find_all(cd, "decorator")

            # Filter to decorators that are children of the class parent and appear before the class
            class_start = cd.start_byte
            for dec in class_decorators:
                if dec.end_byte > class_start:
                    continue
                func, arg = _decorator_func_and_arg(dec)
                if func == "Controller":
                    has_nestjs_controller = True
                    if arg:
                        base_path = arg.lstrip("/")

            # Determine component type
            normalized_path = file_path.replace("\\", "/").lower()
            class_lower = class_name.lower()
            is_util = (
                any(ind in normalized_path for ind in ["/utils/", "/helpers/", "/support/", "/lib/", "/common/"])
                or any(ind in class_lower or ind in file_name.lower() for ind in ["util", "helper", "support"])
            )

            if is_util and not has_nestjs_controller:
                comp_id = generate_node_id("test_utility", class_name)
                entities.append({
                    "id": comp_id,
                    "type": "test_utility",
                    "name": f"Test Utility: {class_name}",
                    "description": f"TypeScript Test Utility Class defined in {file_name}",
                    "metadata": {
                        "source_file": file_path,
                        "language": "typescript",
                        "class_name": class_name,
                        "base_path": base_path,
                    },
                })
            else:
                comp_id = generate_node_id("code_component", class_name)
                entities.append({
                    "id": comp_id,
                    "type": "code_component",
                    "name": f"TypeScript Component: {class_name}",
                    "description": f"Source file: {file_name}. NestJS base path: '{base_path}'",
                    "metadata": {
                        "source_file": file_path,
                        "language": "typescript",
                        "class_name": class_name,
                        "base_path": base_path,
                    },
                })
            seen_ids.add(comp_id)

            # NestJS method endpoints inside class body
            if has_nestjs_controller:
                class_body = _first_child_of_type(cd, "class_body")
                if class_body:
                    # Walk children looking for decorator + method_definition pairs
                    pending_dec: tuple[str, str | None] | None = None
                    for body_child in class_body.children:
                        if body_child.type == "decorator":
                            func, arg = _decorator_func_and_arg(body_child)
                            if func in NESTJS_ANNOS:
                                pending_dec = (func, arg)
                        elif body_child.type == "method_definition" and pending_dec is not None:
                            dec_func, dec_arg = pending_dec
                            pending_dec = None

                            http_method = NESTJS_ANNOS[dec_func]
                            method_path = dec_arg or ""
                            full_path = (f"/{base_path}/{method_path}".replace("//", "/"))
                            if not full_path.startswith("/"):
                                full_path = "/" + full_path

                            # Method name
                            sig_node = _first_child_of_type(body_child, "property_identifier")
                            method_sig = sig_node.text.decode("utf-8") if sig_node else "unknown"

                            ep_id = generate_node_id("api_endpoint", f"{http_method} {full_path}")
                            if ep_id not in seen_ids:
                                seen_ids.add(ep_id)
                                entities.append({
                                    "id": ep_id,
                                    "type": "api_endpoint",
                                    "name": f"{http_method} {full_path}",
                                    "description": (
                                        f"NestJS controller endpoint mapped to method: `{method_sig}`"
                                    ),
                                    "metadata": {
                                        "source_file": file_path,
                                        "language": "typescript",
                                        "http_method": http_method,
                                        "path": full_path,
                                        "class": class_name,
                                        "signature": method_sig,
                                    },
                                })
                        elif body_child.type not in ("{", "}", "comment"):
                            # Non-decorator, non-method resets the pending decorator
                            pending_dec = None

        # -----------------------------------------------------------------------
        # 2. If no class was found, synthesise a component from the file name
        # -----------------------------------------------------------------------
        if not class_decls:
            stub_name = file_name.split(".")[0]
            comp_id = generate_node_id("code_component", stub_name)
            if comp_id not in seen_ids:
                seen_ids.add(comp_id)
                entities.append({
                    "id": comp_id,
                    "type": "code_component",
                    "name": f"TypeScript Component: {stub_name}",
                    "description": f"Source file: {file_name}. NestJS base path: ''",
                    "metadata": {
                        "source_file": file_path,
                        "language": "typescript",
                        "class_name": stub_name,
                        "base_path": "",
                    },
                })

        # -----------------------------------------------------------------------
        # 3. Express router calls  (router.get / app.post / route.delete …)
        #    Only real selector_expression calls — no case-insensitive matching.
        # -----------------------------------------------------------------------
        call_exprs = _find_all(root, "call_expression")
        for call in call_exprs:
            sel = _first_child_of_type(call, "selector_expression")
            if sel is None:
                continue

            # selector_expression: <object> . <field>
            sel_text = sel.text.decode("utf-8") if sel.text else ""
            parts = sel_text.split(".")
            if len(parts) != 2:
                continue
            obj_name, method_name = parts[0], parts[1]

            if obj_name not in EXPRESS_VARS or method_name not in EXPRESS_METHODS:
                continue

            args_node = _first_child_of_type(call, "arguments")
            if args_node is None:
                continue

            str_node = _first_child_of_type(args_node, "string")
            if str_node is None:
                continue

            full_path = _string_value(str_node)
            if not full_path:
                continue

            http_method = method_name.upper()
            ep_id = generate_node_id("api_endpoint", f"{http_method} {full_path}")
            if ep_id not in seen_ids:
                seen_ids.add(ep_id)
                entities.append({
                    "id": ep_id,
                    "type": "api_endpoint",
                    "name": f"{http_method} {full_path}",
                    "description": f"Express routing endpoint defined in {file_name}",
                    "metadata": {
                        "source_file": file_path,
                        "language": "typescript",
                        "http_method": http_method,
                        "path": full_path,
                        "framework": "express",
                    },
                })

        return {"entities": entities, "relationships": []}
