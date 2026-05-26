"""Tests for parsers/excel_parser.py — field-spec sheet decomposition.

SPEC-3 Wave 2: Excel sheets whose header row matches the field-spec layout
(≥3 of field_id, field_name, type, length, mandatory, validation, ...) get
decomposed into one field_specification node per data row. Other sheet
layouts keep the legacy one-node-per-sheet behaviour.
"""

import os
import sys

import openpyxl
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parsers.excel_parser import ExcelParser


def _write_xlsx(tmp_path, sheet_data):
    """Write an Excel workbook with named sheets and (rows_per_sheet) content.

    sheet_data: dict[sheet_name, list[list[Any]]]
    """
    wb = openpyxl.Workbook()
    # Remove the default sheet
    default = wb.active
    wb.remove(default)
    for sheet_name, rows in sheet_data.items():
        ws = wb.create_sheet(title=sheet_name)
        for row in rows:
            ws.append(row)
    path = tmp_path / "test.xlsx"
    wb.save(path)
    return str(path)


def test_field_spec_sheet_emits_one_node_per_row(tmp_path):
    """SPEC-3 Wave 2 §2.1: 10 data rows → 10 field_specification nodes."""
    rows = [
        ["Field ID", "Field Name", "Type", "Length", "Mandatory", "Validation"],
    ]
    for i in range(1, 11):
        rows.append([
            f"FLD_{i:03d}",
            f"Field {i}",
            "input_text",
            "10",
            "Y" if i % 2 == 0 else "N",
            f"Validation rule {i}",
        ])
    file_path = _write_xlsx(tmp_path, {"Field Requirements": rows})

    result = ExcelParser().parse(file_path)
    fs_nodes = [e for e in result["entities"] if e["type"] == "field_specification"]
    assert len(fs_nodes) == 10
    ids = {n["id"] for n in fs_nodes}
    for i in range(1, 11):
        assert f"field_spec_fld_{i:03d}" in ids


def test_narrative_sheet_falls_back_to_legacy(tmp_path):
    """SPEC-3 Wave 2 §2.1: prose sheet → one business_rule blob (unchanged)."""
    rows = [
        ["Section", "Text"],
        ["Overview", "This is a narrative description of the product feature."],
        ["Goals", "Improve resume application throughput."],
    ]
    file_path = _write_xlsx(tmp_path, {"Overview": rows})

    result = ExcelParser().parse(file_path)
    fs_nodes = [e for e in result["entities"] if e["type"] == "field_specification"]
    br_nodes = [e for e in result["entities"] if e["type"] == "business_rule"]
    assert fs_nodes == []
    assert len(br_nodes) == 1
    assert "Overview" in br_nodes[0]["name"]


def test_dataresource_sheet_with_reference_column_stays_legacy(tmp_path):
    """SPEC-3 Wave 2 §2.1: a 'reference' header → dataresource → not decomposed.

    test_infra_mapper consumes these via raw_text; field_spec_parser should
    not split them per-row."""
    rows = [
        ["Reference", "Mobile", "DOB", "Expected"],
        ["TC_001", "9876543210", "01/01/1990", "valid"],
        ["TC_002", "0000000000", "01/01/2030", "invalid"],
    ]
    file_path = _write_xlsx(tmp_path, {"DataResource": rows})

    result = ExcelParser().parse(file_path)
    fs_nodes = [e for e in result["entities"] if e["type"] == "field_specification"]
    br_nodes = [e for e in result["entities"] if e["type"] == "business_rule"]
    assert fs_nodes == []
    assert len(br_nodes) == 1, (
        "Dataresource sheets must stay as a single legacy node so the agent "
        "can consume the raw_text rather than getting per-row test cases."
    )
