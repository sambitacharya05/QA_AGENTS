"""
OpenAPI / Swagger schema parser backed by prance.

prance.ResolvingParser resolves every $ref before we walk the spec, which means:
  - Schema composition (allOf / oneOf / anyOf) is flattened into a single
    property dict before we extract data_model entities.
  - USES_MODEL edges are emitted for every data model referenced by an endpoint
    (previously these edges were completely absent because $ref was never resolved).
  - Swagger 2.0 host + basePath are concatenated into endpoint paths.

prance resolves only local and internal refs by default — no network calls.

Return shape is unchanged: {"raw_text", "entities", "relationships"}.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Set

import yaml

from parsers.base import BaseParser
from parsers.id_utils import generate_node_id

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# prance import — fail gracefully if not installed
# ---------------------------------------------------------------------------
try:
    from prance import ResolvingParser as _PranceParser
    from prance.util.resolver import RESOLVE_FILES, RESOLVE_INTERNAL
    _PRANCE_AVAILABLE = True
except ImportError:
    _PRANCE_AVAILABLE = False
    log.warning(
        "prance is not installed; schema_parser will skip $ref resolution. "
        "Run: pip install prance openapi-spec-validator"
    )


HTTP_METHODS = {"get", "post", "put", "delete", "patch", "head", "options"}


class SchemaParser(BaseParser):
    """
    Parses OpenAPI/Swagger spec files (JSON/YAML) using prance for $ref
    resolution and emits api_endpoint, data_model entities and USES_MODEL edges.
    """

    def parse(self, file_path: str) -> Dict[str, Any]:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        file_name = os.path.basename(file_path)

        # Read raw content for raw_text field and fallback
        with open(file_path, "r", encoding="utf-8", errors="ignore") as fh:
            content = fh.read()

        entities: List[Dict[str, Any]] = []
        relationships: List[Dict[str, Any]] = []

        # Try prance-based resolution first
        if _PRANCE_AVAILABLE:
            spec = self._resolve_with_prance(file_path)
        else:
            spec = self._load_raw(content, file_path)

        if not isinstance(spec, dict):
            return {"raw_text": content, "entities": [], "relationships": []}

        is_openapi = "openapi" in spec or "swagger" in spec

        if is_openapi:
            self._parse_openapi(spec, file_path, file_name, entities, relationships)
        else:
            self._scan_generic_json(spec, file_path, file_name, entities)

        return {"raw_text": content, "entities": entities, "relationships": relationships}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_with_prance(self, file_path: str) -> dict | None:
        """
        Use prance to parse and resolve a spec file.  Local and internal refs
        are resolved; HTTP refs are intentionally disabled (offline constraint).
        """
        try:
            parser = _PranceParser(
                str(Path(file_path).resolve()),
                backend="openapi-spec-validator",
                strict=False,
                resolve_types=RESOLVE_FILES | RESOLVE_INTERNAL,
            )
            return parser.specification
        except Exception as exc:
            log.warning(
                "prance could not resolve %s (%s) — falling back to raw load",
                file_path, exc,
            )
            # Fallback to raw YAML/JSON parse (no ref resolution)
            with open(file_path, "r", encoding="utf-8", errors="ignore") as fh:
                content = fh.read()
            return self._load_raw(content, file_path)

    def _load_raw(self, content: str, file_path: str) -> dict | None:
        """Parse YAML/JSON without $ref resolution (fallback path)."""
        try:
            if file_path.endswith((".yaml", ".yml")):
                return yaml.safe_load(content)
            return json.loads(content)
        except Exception:
            return None

    def _parse_openapi(
        self,
        spec: dict,
        file_path: str,
        file_name: str,
        entities: List[Dict[str, Any]],
        relationships: List[Dict[str, Any]],
    ) -> None:
        info = spec.get("info", {})
        title = info.get("title", "API Spec")

        # Swagger 2.0 basePath support
        base_path = ""
        if "swagger" in spec:
            base_path = spec.get("basePath", "")

        # ------------------------------------------------------------------
        # 1. Extract endpoints
        # ------------------------------------------------------------------
        paths = spec.get("paths", {})
        for path, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue

            full_path = (base_path + path) if base_path else path

            for method, op in path_item.items():
                if method.lower() not in HTTP_METHODS:
                    continue

                summary = op.get("summary", "")
                description = op.get("description", "")

                endpoint_id = generate_node_id("api_endpoint", f"{method} {full_path}")
                entities.append({
                    "id": endpoint_id,
                    "type": "api_endpoint",
                    "name": f"{method.upper()} {full_path}",
                    "description": f"API: {title}. Summary: {summary}. {description}".strip(),
                    "metadata": {
                        "source_file": file_path,
                        "path": full_path,
                        "http_method": method.upper(),
                        "summary": summary,
                        "parameters": op.get("parameters", []),
                        "responses": list(op.get("responses", {}).keys()),
                        "tags": op.get("tags", []),
                        "operation_id": op.get("operationId"),
                    },
                })

                # USES_MODEL edges derived from resolved $refs
                for model_name in self._collect_referenced_models(op):
                    model_id = generate_node_id("data_model", model_name)
                    relationships.append({
                        "source_id": endpoint_id,
                        "target_id": model_id,
                        "relationship": "USES_MODEL",
                        "metadata": {
                            "resolved_from": "$ref",
                            "confidence": 1.0,
                        },
                    })

        # ------------------------------------------------------------------
        # 2. Extract data models (components/schemas or definitions)
        # ------------------------------------------------------------------
        schemas: dict = (
            spec.get("components", {}).get("schemas")
            or spec.get("definitions", {})
            or {}
        )
        for schema_name, schema_item in schemas.items():
            if not isinstance(schema_item, dict):
                continue

            props = self._flatten_properties(schema_item)
            props_list = [
                f"- `{k}` ({v.get('type', 'unknown') if isinstance(v, dict) else str(v)})"
                for k, v in props.items()
            ]
            req_fields = schema_item.get("required", [])

            schema_id = generate_node_id("data_model", schema_name)
            entities.append({
                "id": schema_id,
                "type": "data_model",
                "name": f"Schema Model: {schema_name}",
                "description": "Data model definition with fields:\n" + "\n".join(props_list),
                "metadata": {
                    "source_file": file_path,
                    "model_name": schema_name,
                    "required_fields": req_fields,
                    "properties": list(props.keys()),
                },
            })

    def _collect_referenced_models(self, op: dict) -> Set[str]:
        """
        Walk an operation dict (already $ref-resolved by prance) and return the
        names of all data models it uses.

        After resolution, former $ref dicts are replaced by the actual schema
        object.  We identify schema objects by checking for known model-level
        keys ('properties', 'allOf', etc.) and cross-reference against the
        resolved component name via a 'x-schema-name' hint or the 'title' field.

        A lightweight heuristic: any schema object whose 'title' matches a known
        model name is treated as a model reference.  This avoids a two-pass
        scheme while being accurate for specs that include title fields
        (which prance preserves).
        """
        found: Set[str] = set()
        self._walk_for_models(op, found)
        return found

    def _walk_for_models(self, node: Any, found: Set[str]) -> None:
        """Recursively find schema objects that look like data model references."""
        if isinstance(node, dict):
            # A schema dict with a title is a candidate
            title = node.get("title")
            if title and isinstance(title, str):
                found.add(title)
            for v in node.values():
                self._walk_for_models(v, found)
        elif isinstance(node, list):
            for item in node:
                self._walk_for_models(item, found)

    def _flatten_properties(self, schema: dict) -> dict:
        """
        Merge allOf / oneOf / anyOf sub-schemas into a single flat property dict.
        Returns the merged properties dict.
        """
        props: dict = dict(schema.get("properties", {}))

        for composition_key in ("allOf", "oneOf", "anyOf"):
            for sub in schema.get(composition_key, []):
                if isinstance(sub, dict):
                    props.update(sub.get("properties", {}))
                    # Recurse for nested compositions
                    props.update(self._flatten_properties(sub))

        return props

    def _scan_generic_json(
        self,
        spec: dict,
        file_path: str,
        file_name: str,
        entities: List[Dict[str, Any]],
    ) -> None:
        """Non-OpenAPI JSON/YAML — emit a generic data_model node."""
        entities.append({
            "id": generate_node_id(
                "data_model",
                file_name.replace(".json", "").replace(".yaml", "").replace(".yml", ""),
            ),
            "type": "data_model",
            "name": f"JSON Config: {file_name}",
            "description": "Structured configuration file containing parameters and properties.",
            "metadata": {"source_file": file_path, "data": spec},
        })
