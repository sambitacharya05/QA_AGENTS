import os
import sys
import pytest

# Ensure parent directory is in python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.standards_scanner import StandardsScanner

def test_standards_scanner_python_style(tmp_path):
    # 1. Create a simulated python project directory structure
    workspace = str(tmp_path)
    src_dir = os.path.join(workspace, "src")
    os.makedirs(src_dir, exist_ok=True)
    
    python_code = """
def calculate_premium_discount(age, history):
    # 4 spaces indentation
    if age > 65:
        return 0.15
    return 0.0
"""
    py_file = os.path.join(src_dir, "utils.py")
    with open(py_file, 'w', encoding='utf-8') as f:
        f.write(python_code)
        
    scanner = StandardsScanner(workspace)
    detected = scanner.detect_standards()
    
    assert detected is not None
    assert detected["naming_convention"] == "snake_case"
    assert detected["indentation"] == "4_spaces"

def test_standards_scanner_ts_style(tmp_path):
    # 2. Create a simulated TS project with tab-indented camelCase
    workspace = str(tmp_path)
    src_dir = os.path.join(workspace, "src")
    os.makedirs(src_dir, exist_ok=True)
    
    ts_code = """
export class DateUtils {
\tstatic todayDate(): string {
\t\treturn "2026-05-24";
\t}
}
"""
    ts_file = os.path.join(src_dir, "DateUtils.ts")
    with open(ts_file, 'w', encoding='utf-8') as f:
        f.write(ts_code)
        
    scanner = StandardsScanner(workspace)
    detected = scanner.detect_standards()
    
    assert detected is not None
    assert detected["naming_convention"] == "camelCase"
    assert detected["indentation"] == "tabs"

def test_standards_scanner_no_files(tmp_path):
    # 3. Empty workspace should return None (standard preset fallback)
    scanner = StandardsScanner(str(tmp_path))
    detected = scanner.detect_standards()
    assert detected is None
