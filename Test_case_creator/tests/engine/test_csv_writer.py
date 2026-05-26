import pytest
import csv
import io
from engine.csv_writer import serialize

def test_serialize_structure_and_headers():
    test_cases = [
        {
            "title": "Validate Eligibility Claim",
            "category_id": "Eligibility",
            "target_rule_id": "RULE-100",
            "steps": [
                {"step_number": 1, "action": "Open claim portal", "expected": "Portal displays eligibility screen"},
                {"step_number": 2, "action": "Submit claim details", "expected": "System returns status 200 OK"}
            ]
        }
    ]
    area_path = "InsuranceTech\\Billing\\Claims"
    csv_string = serialize(test_cases, area_path)
    
    # Read the serialized output back using standard csv module to verify RFC 4180 compliance
    f = io.StringIO(csv_string)
    reader = csv.reader(f)
    rows = list(reader)
    
    # Assert header has exactly 9 columns in the correct order
    headers = rows[0]
    expected_headers = ["ID", "Work Item Type", "Title", "Test Step", "Step Action", "Step Expected", "Area Path", "Assigned To", "State"]
    assert headers == expected_headers
    
    # Assert there are 3 rows: 1 header and 2 step rows
    assert len(rows) == 3
    
    # Assert metadata repetition on row 1
    row1 = rows[1]
    assert row1[0] == ""
    assert row1[1] == "Test Case"
    assert row1[2] == "Validate Eligibility Claim"
    assert row1[3] == "1"
    assert row1[4] == "Open claim portal"
    assert row1[5] == "Portal displays eligibility screen"
    assert row1[6] == area_path
    assert row1[7] == ""
    assert row1[8] == "Design"
    
    # Assert metadata repetition on row 2 (satisfies the Metadata Consistency Invariant)
    row2 = rows[2]
    assert row2[0] == ""
    assert row2[1] == "Test Case"
    assert row2[2] == "Validate Eligibility Claim"
    assert row2[3] == "2"
    assert row2[4] == "Submit claim details"
    assert row2[5] == "System returns status 200 OK"
    assert row2[6] == area_path
    assert row2[7] == ""
    assert row2[8] == "Design"

def test_serialize_escaping_and_quoting():
    # Input prose with commas, double quotes, and newlines
    test_cases = [
        {
            "title": "Validate Escaping Scenario",
            "steps": [
                {
                    "step_number": 1,
                    "action": 'Click "Submit" button, then wait.',
                    "expected": 'Confirmation message:\n"Operation completed successfully."'
                }
            ]
        }
    ]
    csv_string = serialize(test_cases, "InsuranceTech")
    
    # Verify raw CSV string representation for RFC 4180 quote escaping
    # Quotes around action and expected because they contain commas, newlines or double quotes.
    # Double quotes are escaped by doubling them ("").
    assert ',"Click ""Submit"" button, then wait.",' in csv_string
    assert ',"Confirmation message:\n""Operation completed successfully.""",' in csv_string
    
    # Parse back using csv reader to verify round-trip integrity
    f = io.StringIO(csv_string)
    reader = csv.reader(f)
    rows = list(reader)
    
    assert len(rows) == 2
    row = rows[1]
    assert row[4] == 'Click "Submit" button, then wait.'
    assert row[5] == 'Confirmation message:\n"Operation completed successfully."'

