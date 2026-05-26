"""Per-key rule_constant node emission from .properties / .config files.

SPEC-3 Wave 2: replaces the misrouted SchemaParser fallback for
application.properties, bootstrap.properties, and pom.properties. The
QafAutomationParser keeps handling .properties files that contain locator
definitions (id=/xpath=/etc).

The classifier in parsers/__init__.py:get_parser_for_file routes to this
parser when content has no locator-strategy markers.
"""

import os
import re
from typing import Any, Dict

from parsers.base import BaseParser

# Lines like "key = value" or "key=value" (whitespace tolerant).
_KV_RE = re.compile(r"^\s*([a-zA-Z_][a-zA-Z0-9_.\-]*)\s*=\s*(.*?)\s*$")
# Lines starting with # or ! are comments per .properties spec.
_COMMENT_RE = re.compile(r"^\s*[#!]")


class PropertiesParser(BaseParser):
    """Per-key rule_constant node emission."""

    def parse(self, file_path: str) -> Dict[str, Any]:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        file_name = os.path.basename(file_path)
        entities = []
        raw_text_parts = [f"# Properties File: {file_name}\n"]

        with open(file_path, "r", encoding="utf-8", errors="ignore") as fh:
            for line_no, raw_line in enumerate(fh, start=1):
                line = raw_line.rstrip("\n")
                if not line.strip() or _COMMENT_RE.match(line):
                    continue
                m = _KV_RE.match(line)
                if not m:
                    continue
                key, value = m.group(1), m.group(2)
                raw_text_parts.append(f"{key}={value}")

                key_slug = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
                node_id = f"rule_constant_{key_slug}"

                value_type = self._infer_value_type(value)
                typed_value = self._coerce_value(value, value_type)

                entities.append({
                    "id": node_id,
                    "type": "rule_constant",
                    "name": f"Rule Constant: {key}",
                    "description": (
                        f"Configuration constant {key}={value} from {file_name}."
                    ),
                    "metadata": {
                        "source_file": file_path,
                        "config_key": key,
                        "value": typed_value,
                        "value_type": value_type,
                        "raw_value": value,
                        "line_number": line_no,
                        "namespace": key.split(".")[0] if "." in key else None,
                    },
                })

        return {
            "raw_text": "\n".join(raw_text_parts),
            "entities": entities,
            "relationships": [],
        }

    @staticmethod
    def _infer_value_type(value: str) -> str:
        v = value.strip()
        if not v:
            return "string"
        if v.lower() in {"true", "false"}:
            return "boolean"
        try:
            int(v)
            return "integer"
        except ValueError:
            pass
        try:
            float(v)
            return "float"
        except ValueError:
            pass
        if "," in v and not v.startswith(("'", '"')):
            return "list"
        return "string"

    @staticmethod
    def _coerce_value(value: str, value_type: str):
        v = value.strip()
        if value_type == "boolean":
            return v.lower() == "true"
        if value_type == "integer":
            try:
                return int(v)
            except ValueError:
                return v
        if value_type == "float":
            try:
                return float(v)
            except ValueError:
                return v
        if value_type == "list":
            return [item.strip() for item in v.split(",") if item.strip()]
        return v
