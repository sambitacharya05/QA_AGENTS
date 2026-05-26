import os
import csv
import re
from typing import Dict, List, Any
import openpyxl
from parsers.base import BaseParser
from parsers.id_utils import generate_node_id


# SPEC-3 Wave 2: field-spec sheet detection. Sheets whose header row contains
# ≥3 of these tokens are decomposed one node per row (field_specification),
# instead of the legacy one-node-per-sheet (business_rule) behaviour.
_FIELD_SPEC_HEADER_TOKENS = {
    "field_id", "field_name", "type", "length",
    "mandatory", "validation", "error_message",
    "default", "description", "placeholder",
    "element_type", "selector",
}


def _normalise_header_token(text: str) -> str:
    """Lowercase, strip, collapse non-alphanumerics → underscores."""
    return re.sub(r"[^a-z0-9_]", "_", text.lower().strip())


def _classify_sheet(headers: List[str]) -> str:
    """Return one of: 'field_spec', 'dataresource', 'narrative_or_data'.

    Detection rules (SPEC-3 §2.1):
      - 'reference' header column → 'dataresource' (test_infra_mapper's domain).
      - ≥3 field-spec header tokens → 'field_spec' (per-row decomposition).
      - otherwise → 'narrative_or_data' (legacy one-node-per-sheet).
    """
    norm = {_normalise_header_token(h) for h in headers if h}
    if "reference" in norm:
        return "dataresource"
    if len(norm & _FIELD_SPEC_HEADER_TOKENS) >= 3:
        return "field_spec"
    return "narrative_or_data"


def _parse_field_spec_sheet(
    file_path: str,
    sheet_name: str,
    rows: List[List[str]],
    headers: List[str],
    entities: List[Dict[str, Any]],
) -> bool:
    """One field_specification node per data row. Preserves field IDs verbatim.

    Returns True if any nodes were emitted, False if the sheet had no usable
    Field ID column (caller should fall back to legacy behaviour).
    """
    norm_idx = {
        _normalise_header_token(h): i for i, h in enumerate(headers) if h
    }
    field_id_col = None
    for candidate in ("field_id", "id", "element_id", "key"):
        if candidate in norm_idx:
            field_id_col = norm_idx[candidate]
            break
    if field_id_col is None:
        return False

    emitted = False
    for row_idx, row in enumerate(rows[1:], start=2):
        if not any(row):
            continue
        field_id_raw = (
            row[field_id_col].strip() if field_id_col < len(row) else ""
        )
        if not field_id_raw:
            continue

        slug = re.sub(r"[^a-z0-9]+", "_", field_id_raw.lower()).strip("_")
        node_id = f"field_spec_{slug}"

        row_meta: Dict[str, Any] = {}
        for col_key, col_idx in norm_idx.items():
            if col_idx < len(row):
                row_meta[col_key] = row[col_idx]

        mandatory_token = str(row_meta.get("mandatory", "")).lower()
        mandatory = mandatory_token in {"y", "yes", "true", "1"}
        kind = row_meta.get("type") or row_meta.get("element_type") or "field"
        validation = row_meta.get("validation", "")

        entities.append({
            "id": node_id,
            "type": "field_specification",
            "name": f"Field Spec: {row_meta.get('field_name', field_id_raw)}",
            "description": (
                f"{row_meta.get('field_name', field_id_raw)} "
                f"({kind}, {'mandatory' if mandatory else 'optional'}). "
                f"{validation}"
            ).strip(),
            "metadata": {
                "source_file": file_path,
                "sheet_name": sheet_name,
                "row_number": row_idx,
                "field_id": field_id_raw,
                **row_meta,
            },
        })
        emitted = True
    return emitted


class ExcelParser(BaseParser):
    """Parses Excel (.xlsx) and CSV files into clean tabular markdown structures."""

    def parse(self, file_path: str) -> Dict[str, Any]:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        file_name = os.path.basename(file_path)
        entities = []
        relationships = []
        raw_text_parts = []

        if file_path.endswith('.csv'):
            self._parse_csv(file_path, file_name, entities, raw_text_parts)
        else:
            self._parse_xlsx(file_path, file_name, entities, raw_text_parts)

        return {
            "raw_text": "\n".join(raw_text_parts),
            "entities": entities,
            "relationships": relationships
        }

    def _parse_csv(self, file_path: str, file_name: str, entities: List[Dict[str, Any]], raw_text_parts: List[str]) -> None:
        """Parses a raw CSV file."""
        rows = []
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            reader = csv.reader(f)
            for row in reader:
                rows.append([cell.strip() for cell in row])

        if not rows:
            return

        # Convert to Markdown table
        headers = rows[0]
        markdown_lines = []
        markdown_lines.append("| " + " | ".join(headers) + " |")
        markdown_lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
        for row in rows[1:]:
            # Ensure row matches header length
            row_cells = row + [""] * (len(headers) - len(row))
            markdown_lines.append("| " + " | ".join(row_cells[:len(headers)]) + " |")

        markdown_str = "\n".join(markdown_lines)
        raw_text_parts.append(f"# CSV File: {file_name}\n{markdown_str}\n")

        node_id = generate_node_id("business_rule", file_name.replace('.csv', '').replace('.CSV', ''))
        entities.append({
            "id": node_id,
            "type": "business_rule",
            "name": f"CSV Data Grid ({file_name})",
            "description": f"Tabular rule grid extracted from CSV file: {file_name}.",
            "metadata": {
                "source_file": file_path,
                "headers": headers,
                "table_markdown": markdown_str
            }
        })

    def _parse_xlsx(self, file_path: str, file_name: str, entities: List[Dict[str, Any]], raw_text_parts: List[str]) -> None:
        """Parses an Excel (.xlsx) file, reading all sheets.

        SPEC-3 Wave 2: per-sheet dispatch on detected layout.
          - field_spec → one field_specification node per data row.
          - dataresource / narrative_or_data → legacy one-node-per-sheet
            (LLM agents read raw_text and pick what they need).
        """
        wb = openpyxl.load_workbook(file_path, data_only=True)

        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            rows = []
            for row in ws.iter_rows(values_only=True):
                cleaned_row = [str(val).strip() if val is not None else "" for val in row]
                if any(cleaned_row):
                    rows.append(cleaned_row)

            if not rows:
                continue

            headers = rows[0]
            markdown_lines = []
            markdown_lines.append("| " + " | ".join(headers) + " |")
            markdown_lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
            for row in rows[1:]:
                row_cells = row + [""] * (len(headers) - len(row))
                markdown_lines.append("| " + " | ".join(row_cells[:len(headers)]) + " |")

            markdown_str = "\n".join(markdown_lines)
            raw_text_parts.append(f"# Sheet: {sheet_name} ({file_name})\n{markdown_str}\n")

            sheet_kind = _classify_sheet(headers)
            if sheet_kind == "field_spec":
                emitted = _parse_field_spec_sheet(
                    file_path, sheet_name, rows, headers, entities,
                )
                if emitted:
                    continue
                # No usable Field ID column → fall through to legacy emission
                # so the sheet still produces SOMETHING (the markdown blob).

            node_id = generate_node_id("business_rule", sheet_name)
            entities.append({
                "id": node_id,
                "type": "business_rule",
                "name": f"Excel Sheet: {sheet_name} ({file_name})",
                "description": f"Tabular grid sheet '{sheet_name}' representing insurance calculations, values, or pricing structures.",
                "metadata": {
                    "source_file": file_path,
                    "sheet_name": sheet_name,
                    "headers": headers,
                    "table_markdown": markdown_str
                }
            })
