"""
Java AST-based endpoint and class extractor.

Replaces the line-anchored regex scanner with a full tree-sitter parse so that:
- Multi-line Spring annotations are handled correctly.
- Multiple top-level classes / interfaces per file are all captured.
- @RestController detection no longer fires on string literals / comments.
- Method-signature look-ahead cannot pick up Javadoc or stacked annotations.
"""

from __future__ import annotations

import logging
from typing import Any

from parsers.ast.loader import parser_for, language_for
from parsers.id_utils import generate_node_id

log = logging.getLogger(__name__)

# Spring MVC shorthand annotation → HTTP verb
ENDPOINT_ANNOS: dict[str, str] = {
    "GetMapping": "GET",
    "PostMapping": "POST",
    "PutMapping": "PUT",
    "DeleteMapping": "DELETE",
    "PatchMapping": "PATCH",
}

CONTROLLER_ANNOS = {"RestController", "Controller"}


# ---------------------------------------------------------------------------
# Internal tree-walk helpers
# ---------------------------------------------------------------------------

def _find_all(node, *types: str) -> list:
    """Recursively collect all descendant nodes matching any of *types*."""
    results = []
    if node.type in types:
        results.append(node)
    for child in node.children:
        results.extend(_find_all(child, *types))
    return results


def _first_child_of_type(node, *types: str):
    """Return the first direct child whose type is in *types*, or None."""
    for child in node.children:
        if child.type in types:
            return child
    return None


def _annotation_name(ann_node) -> str | None:
    """Extract the bare name from a tree-sitter annotation / marker_annotation node."""
    for child in ann_node.children:
        if child.type == "identifier":
            return child.text.decode("utf-8")
    return None


def _extract_path_from_arg_list(arg_list_node) -> str | None:
    """
    Extract a path string from an annotation_argument_list, handling both:
      - Direct string:  @GetMapping("/users")
      - Named value:    @GetMapping(value = "/users")
      - value array:    @GetMapping({"/users", "/user"}) → first element
    Returns None if no string can be found.
    """
    if arg_list_node is None:
        return None

    for child in arg_list_node.children:
        # Bare string literal
        if child.type == "string_literal":
            frag = _first_child_of_type(child, "string_fragment")
            return frag.text.decode("utf-8") if frag else None

        # Named pair:  value = "/path"
        if child.type == "element_value_pair":
            val = _first_child_of_type(child, "string_literal")
            if val:
                frag = _first_child_of_type(val, "string_fragment")
                if frag:
                    return frag.text.decode("utf-8")

        # Array of strings: {"/a", "/b"} — take the first
        if child.type == "element_value_array_initializer":
            for arr_child in child.children:
                if arr_child.type == "string_literal":
                    frag = _first_child_of_type(arr_child, "string_fragment")
                    if frag:
                        return frag.text.decode("utf-8")

    return None


def _parse_type_decl(decl_node, class_mapping_prefix: str, file_path: str) -> dict[str, Any]:
    """
    Parse a single class_declaration or interface_declaration node into:
      { "class_entity": {...}, "endpoints": [...] }
    """
    named = decl_node.named_children  # [modifiers?, identifier, body]

    # --- class name ---
    class_name: str | None = None
    for nc in named:
        if nc.type == "identifier":
            class_name = nc.text.decode("utf-8")
            break
    if not class_name:
        return {"class_entity": None, "endpoints": []}

    # --- annotations on the class ---
    class_annotations: list[str] = []
    class_mapping = class_mapping_prefix  # may be overridden by @RequestMapping on the class

    modifiers_node = None
    for nc in named:
        if nc.type == "modifiers":
            modifiers_node = nc
            break

    if modifiers_node:
        for ann in _find_all(modifiers_node, "annotation", "marker_annotation"):
            name = _annotation_name(ann)
            if name:
                class_annotations.append(name)
                if name == "RequestMapping":
                    arg_list = _first_child_of_type(ann, "annotation_argument_list")
                    path = _extract_path_from_arg_list(arg_list)
                    if path:
                        class_mapping = path

    is_controller = bool(CONTROLLER_ANNOS & set(class_annotations))

    # --- build class entity ---
    file_name = file_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    normalized_path = file_path.replace("\\", "/").lower()
    class_lower = class_name.lower()
    is_util = (
        any(ind in normalized_path for ind in ["/utils/", "/helpers/", "/support/", "/lib/", "/common/"])
        or any(ind in class_lower or ind in file_name.lower() for ind in ["util", "helper", "support"])
    )

    if is_util and not is_controller:
        node_type = "test_utility"
        component_id = generate_node_id("test_utility", class_name)
        name_str = f"Test Utility: {class_name}"
        desc_str = f"Java Test Utility Class defined in {file_name}"
    else:
        node_type = "code_component"
        component_id = generate_node_id("code_component", class_name)
        name_str = f"Java Class: {class_name}"
        desc_str = (
            f"Source file: {file_name}. "
            f"Controller: {is_controller}. "
            f"RequestMapping prefix: '{class_mapping}'"
        )

    class_entity = {
        "id": component_id,
        "type": node_type,
        "name": name_str,
        "description": desc_str,
        "metadata": {
            "source_file": file_path,
            "language": "java",
            "class_name": class_name,
            "is_controller": is_controller,
            "base_path": class_mapping,
        },
    }

    # --- extract endpoints from class body ---
    endpoints: list[dict] = []
    body_node = None
    for nc in named:
        if nc.type in ("class_body", "interface_body"):
            body_node = nc
            break

    if body_node:
        methods = _find_all(body_node, "method_declaration")
        for method in methods:
            method_name_node = None
            for mnc in method.named_children:
                if mnc.type == "identifier":
                    method_name_node = mnc
                    break
            method_name = method_name_node.text.decode("utf-8") if method_name_node else "unknown"

            method_mods = _first_child_of_type(method, "modifiers")
            if method_mods is None:
                for mnc in method.named_children:
                    if mnc.type == "modifiers":
                        method_mods = mnc
                        break

            if method_mods is None:
                continue

            for ann in _find_all(method_mods, "annotation", "marker_annotation"):
                ann_name = _annotation_name(ann)
                if ann_name not in ENDPOINT_ANNOS:
                    continue

                http_method = ENDPOINT_ANNOS[ann_name]
                arg_list = _first_child_of_type(ann, "annotation_argument_list")
                path = _extract_path_from_arg_list(arg_list)
                if path is None:
                    # @GetMapping with no args → root of class mapping
                    path = ""

                full_path = (class_mapping + path).replace("//", "/") or "/"

                endpoint_id = generate_node_id("api_endpoint", f"{http_method} {full_path}")
                endpoints.append({
                    "id": endpoint_id,
                    "type": "api_endpoint",
                    "name": f"{http_method} {full_path}",
                    "description": (
                        f"Spring controller endpoint mapped to method: `{method_name}`"
                    ),
                    "metadata": {
                        "source_file": file_path,
                        "language": "java",
                        "http_method": http_method,
                        "path": full_path,
                        "class": class_name,
                        "signature": method_name,
                    },
                })

    return {"class_entity": class_entity, "endpoints": endpoints}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class JavaEndpointExtractor:
    """
    Extracts code_component and api_endpoint entities from Java source using tree-sitter.
    Handles multi-line annotations, multiple top-level classes, and avoids @RestController
    matches in comments or string literals.
    """

    def __init__(self) -> None:
        self._parser = parser_for("java")

    def extract(
        self,
        source: str | bytes,
        file_path: str,
        *,
        class_mapping_prefix: str = "",
    ) -> dict[str, Any]:
        """
        Parse *source* and return ``{"entities": [...], "relationships": []}``.

        *class_mapping_prefix* is an additional path prefix prepended to every
        endpoint discovered (used when the caller already knows the @RequestMapping
        from an outer scope — not normally needed for single-file parsing).
        """
        if isinstance(source, str):
            source = source.encode("utf-8")

        try:
            tree = self._parser.parse(source)
        except Exception as exc:
            log.warning("Java AST parse failed for %s: %s", file_path, exc)
            return {"entities": [], "relationships": []}

        root = tree.root_node

        # Top-level type declarations (class, interface, enum at program level)
        type_decls = _find_all(root, "class_declaration", "interface_declaration", "enum_declaration")

        entities: list[dict] = []
        relationships: list[dict] = []

        for decl in type_decls:
            result = _parse_type_decl(decl, class_mapping_prefix, file_path)
            if result["class_entity"]:
                entities.append(result["class_entity"])
                class_id = result["class_entity"]["id"]
                class_name = result["class_entity"]["metadata"].get("class_name", "")
                for ep in result["endpoints"]:
                    entities.append(ep)
                    # Spec 007: emit IMPLEMENTS (endpoint → containing class)
                    relationships.append({
                        "source_id": ep["id"],
                        "target_id": class_id,
                        "relationship": "IMPLEMENTS",
                        "metadata": {
                            "source": "parser:java",
                            "confidence": 1.0,
                            "evidence": {
                                "method": "explicit_ref",
                                "score": 1.0,
                                "notes": f"controller_method_in_class:{class_name}",
                            },
                        },
                    })
            else:
                entities.extend(result["endpoints"])

        return {"entities": entities, "relationships": relationships}
