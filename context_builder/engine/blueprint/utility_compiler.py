"""Utility method footprint compiler.

Extracted verbatim from ``ContextExtractor._compile_utility_blueprints()``
in ``engine/extractor.py`` (Spec 003 — Wave 2).

Key differences from the original:
- ``BlueprintStore`` is *injected* (not constructed inside) — satisfying
  Acceptance Criterion 3 (single construction per ``ContextExtractor``).
- The pruning sweep reuses the same injected ``blueprint`` instead of
  constructing a second ``BlueprintStore`` instance (lines 728-729 of the
  original).  This is purely a resource/correctness improvement; the pruning
  logic itself is unchanged.
"""

from __future__ import annotations

import logging
import os
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from db.graph_store import GraphStore
    from db.blueprint_store import BlueprintStore

log = logging.getLogger(__name__)


class UtilityCompiler:
    """Scans registered ``test_utility`` nodes, parses their source files to
    extract public method signatures, and registers them in a
    :class:`~db.blueprint_store.BlueprintStore`.

    The ``BlueprintStore`` is injected so it is constructed exactly once per
    ``ContextExtractor`` instance.
    """

    def __init__(self, store: "GraphStore", blueprint: "BlueprintStore"):
        self.store = store
        self.blueprint = blueprint  # injected — NOT constructed inside
        self._workspace_path = store.workspace_path

    def compile(self) -> None:
        """Scan all ``test_utility`` nodes and register method footprints."""
        utilities = self.store.query_nodes(node_type="test_utility")
        for util in utilities:
            meta = util.get("metadata") or {}
            source_file = meta.get("source_file")
            class_name = meta.get("class_name") or util["name"]
            utility_id = util["id"]

            abs_source_file = source_file
            if source_file and not os.path.isabs(source_file):
                abs_source_file = os.path.normpath(
                    os.path.join(self._workspace_path, source_file)
                )

            if not abs_source_file or not os.path.exists(abs_source_file):
                continue

            try:
                with open(abs_source_file, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
            except Exception as exc:
                log.warning("Could not read utility source file %s: %s", abs_source_file, exc)
                continue

            methods = []

            # --- Java ---
            if source_file.endswith('.java'):
                pattern = re.compile(
                    r'(?:/\*\*(([^*]|\*(?!/))*)\*/\s*)?public\s+(?:static\s+)?([\w<>\[\]]+)\s+(\w+)\s*\(([^)]*)\)'
                )
                for match in pattern.finditer(content):
                    javadoc = match.group(1)
                    return_type = match.group(3)
                    method_name = match.group(4)
                    params_str = match.group(5)

                    if method_name in ["main", "toString", "hashCode", "equals"]:
                        continue

                    description = ""
                    if javadoc:
                        description = "\n".join(
                            line.strip().lstrip('*').strip()
                            for line in javadoc.strip().split('\n')
                        ).strip()
                    if not description:
                        description = f"Helper utility method: {method_name}"

                    parameters = {}
                    if params_str.strip():
                        for p in params_str.split(','):
                            p = p.strip()
                            if p:
                                parts = p.split()
                                if len(parts) >= 2:
                                    param_name = parts[-1]
                                    param_type = " ".join(parts[:-1])
                                    parameters[param_name] = param_type

                    signature = (
                        f"public static {return_type} {method_name}({params_str.strip()})"
                        if "static" in match.group(0)
                        else f"public {return_type} {method_name}({params_str.strip()})"
                    )

                    methods.append({
                        "method_name": method_name,
                        "signature": signature,
                        "parameters": parameters,
                        "return_type": return_type,
                        "description": description,
                    })

            # --- TypeScript / JavaScript ---
            elif source_file.endswith(('.ts', '.js')):
                pattern = re.compile(
                    r'(?:/\*\*(([^*]|\*(?!/))*)\*/\s*)?(?:public\s+|private\s+|protected\s+)?'
                    r'(?:static\s+)?(?:async\s+)?(\w+)\s*\(([^)]*)\)\s*(?::\s*([\w<>\[\]\s|]+))?\s*\{'
                )
                for match in pattern.finditer(content):
                    javadoc = match.group(1)
                    method_name = match.group(3)
                    params_str = match.group(4)
                    return_type_match = match.group(5)

                    if method_name in ["constructor", "toString"]:
                        continue

                    return_type = return_type_match.strip() if return_type_match else "any"

                    description = ""
                    if javadoc:
                        description = "\n".join(
                            line.strip().lstrip('*').strip()
                            for line in javadoc.strip().split('\n')
                        ).strip()
                    if not description:
                        description = f"Helper utility method: {method_name}"

                    parameters = {}
                    if params_str.strip():
                        for p in params_str.split(','):
                            p = p.strip()
                            if p:
                                parts = p.split(':')
                                if len(parts) >= 2:
                                    param_name = parts[0].strip().replace('?', '')
                                    param_type = parts[1].strip()
                                    parameters[param_name] = param_type
                                else:
                                    parameters[p] = "any"

                    signature = f"{method_name}({params_str.strip()}): {return_type}"

                    methods.append({
                        "method_name": method_name,
                        "signature": signature,
                        "parameters": parameters,
                        "return_type": return_type,
                        "description": description,
                    })

            # --- Python ---
            elif source_file.endswith('.py'):
                pattern = re.compile(
                    r'def\s+(\w+)\s*\(([^)]*)\)(?:\s*->\s*([\w\[\], ]+))?:\s*'
                    r'(?:"""([\s\S]*?)"""|\'\'\'([\s\S]*?)\'\'\')?',
                    re.DOTALL,
                )
                for match in pattern.finditer(content):
                    method_name, params_str, return_type_match, d_doc, s_doc = match.groups()
                    if method_name.startswith('_'):
                        continue

                    return_type = return_type_match.strip() if return_type_match else "Any"
                    docstring = d_doc or s_doc
                    description = (
                        docstring.strip() if docstring else f"Helper utility method: {method_name}"
                    )

                    parameters = {}
                    if params_str.strip():
                        for p in params_str.split(','):
                            p = p.strip()
                            if p and p != 'self' and p != 'cls':
                                parts = p.split(':')
                                param_name = parts[0].strip()
                                param_type = parts[1].strip() if len(parts) > 1 else "Any"
                                parameters[param_name] = param_type

                    signature = f"def {method_name}({params_str.strip()}) -> {return_type}"
                    methods.append({
                        "method_name": method_name,
                        "signature": signature,
                        "parameters": parameters,
                        "return_type": return_type,
                        "description": description,
                    })

            # --- Go ---
            elif source_file.endswith('.go'):
                pattern = re.compile(
                    r'(?:/\*([\s\S]*?)\*/\s*|//\s*(.*?)\n\s*)?func\s+(?:\([^)]*\)\s+)?(\w+)\s*\(([^)]*)\)\s*([^{]*)',
                    re.DOTALL,
                )
                for match in pattern.finditer(content):
                    block_comment, line_comment, method_name, params_str, return_type_match = match.groups()
                    if method_name.startswith('test') or not method_name[0].isupper():
                        continue

                    return_type = return_type_match.strip() if return_type_match else "void"
                    description = (
                        block_comment.strip()
                        if block_comment
                        else (
                            line_comment.strip()
                            if line_comment
                            else f"Go helper utility: {method_name}"
                        )
                    )

                    parameters = {}
                    if params_str.strip():
                        for p in params_str.split(','):
                            p = p.strip()
                            if p:
                                parts = p.split()
                                if len(parts) >= 2:
                                    param_name = parts[0]
                                    param_type = " ".join(parts[1:])
                                    parameters[param_name] = param_type
                                else:
                                    parameters[p] = "any"

                    signature = f"func {method_name}({params_str.strip()}) {return_type}"
                    methods.append({
                        "method_name": method_name,
                        "signature": signature,
                        "parameters": parameters,
                        "return_type": return_type,
                        "description": description,
                    })

            if methods:
                self.blueprint.register_utility_signature(
                    utility_id=utility_id,
                    class_name=class_name,
                    source_file=source_file,
                    methods=methods,
                )

        # Pruning sweep for stale and phantom capabilities
        # Uses the same injected blueprint (no second construction).
        try:
            if "reusable_capabilities" in self.blueprint.blueprint_data:
                to_delete = []
                for cap_id, info in self.blueprint.blueprint_data["reusable_capabilities"].items():
                    cap_source = info.get("source_file")
                    if not cap_source:
                        to_delete.append(cap_id)
                        continue

                    if os.path.isabs(cap_source):
                        resolved_path = cap_source
                    else:
                        resolved_path = os.path.join(self._workspace_path, cap_source)

                    if not os.path.exists(resolved_path):
                        to_delete.append(cap_id)

                if to_delete:
                    for cap_id in to_delete:
                        del self.blueprint.blueprint_data["reusable_capabilities"][cap_id]
                    self.blueprint.save_blueprint()
                    log.info("Pruned %d stale/phantom capabilities.", len(to_delete))
        except Exception as exc:
            log.warning("Failed to prune stale capabilities: %s", exc, exc_info=True)
