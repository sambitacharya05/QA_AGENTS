import csv
import io

def serialize(test_cases: list[dict], area_path: str) -> str:
    """
    Sanitizes, escapes, and structures the provided list of test cases 
    into a RFC 4180 compliant CSV string suited for Azure DevOps bulk-import.
    """
    output = io.StringIO()
    # RFC 4180 compliant writer using double quotes for escaping and unix newlines
    writer = csv.writer(output, delimiter=',', quotechar='"', quoting=csv.QUOTE_MINIMAL, lineterminator='\n')
    
    # Write header for Azure DevOps bulk import (exactly 9 columns)
    writer.writerow([
        "ID", "Work Item Type", "Title", "Test Step", "Step Action", 
        "Step Expected", "Area Path", "Assigned To", "State"
    ])
    
    # Process and write test cases
    for idx, tc in enumerate(test_cases, start=1):
        steps = tc.get("steps", [])
        title = tc.get("title", f"Test Case {idx}")
        
        if not steps:
            # Replicate metadata even for empty test case steps (fallback safety)
            writer.writerow([
                "", "Test Case", title, "", "", "", area_path, "", "Design"
            ])
            continue
            
        for s_idx, step in enumerate(steps):
            step_num = step.get("step_number", s_idx + 1)
            action = step.get("action", "")
            expected = step.get("expected", "")
            
            # Repetitive metadata to satisfy the Metadata Consistency Invariant
            writer.writerow([
                "", "Test Case", title, step_num, action, expected, area_path, "", "Design"
            ])
                
    return output.getvalue()

