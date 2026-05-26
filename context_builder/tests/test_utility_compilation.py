import os
import sys
import json
import pytest

# Ensure parent directory is in python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.graph_store import GraphStore
from db.blueprint_store import BlueprintStore
from engine.extractor import ContextExtractor

def test_compile_utility_blueprints(tmp_path):
    # 1. Create a dummy workspace layout
    workspace = str(tmp_path)
    
    # Write a Java utility file under a simulated workspace
    src_dir = os.path.join(workspace, "src", "utils")
    os.makedirs(src_dir, exist_ok=True)
    
    java_code = """
    package com.project.utils;
    
    /**
     * Formatting utility class.
     */
    public class StringUtils {
        
        /**
         * Formats a given name.
         * @param name The name to format
         * @return The formatted name string
         */
        public static String formatName(String name) {
            return name.trim().toUpperCase();
        }
        
        public int add(int a, int b) {
            return a + b;
        }
    }
    """
    util_file = os.path.join(src_dir, "StringUtils.java")
    with open(util_file, "w", encoding="utf-8") as f:
        f.write(java_code)
        
    # 2. Directly run ContextExtractor on the mock workspace
    extractor = ContextExtractor(workspace)
    
    # Pre-populate the GraphStore with the test_utility node since QafAutomationParser / CodeParser
    # would register it. We can insert it directly to test the compilation block.
    extractor.store.upsert_node(
        "util_stringutils",
        "test_utility",
        "Test Utility: StringUtils",
        "Java test_utility defined in automation suite",
        {
            "source_file": util_file,
            "class_name": "StringUtils",
            "language": "java"
        }
    )
    extractor.store.save_graph()
    
    # Run compile utility blueprints
    extractor._compile_utility_blueprints()
    
    # 3. Assert blueprint.json was created and contains the compiled methods
    blueprint_path = os.path.join(workspace, ".context_builder", "blueprint.json")
    assert os.path.exists(blueprint_path)
    
    with open(blueprint_path, 'r', encoding='utf-8') as f:
        blueprint_data = json.load(f)
        
    assert "reusable_capabilities" in blueprint_data
    assert "util_stringutils" in blueprint_data["reusable_capabilities"]
    
    util_entry = blueprint_data["reusable_capabilities"]["util_stringutils"]
    assert util_entry["class_name"] == "StringUtils"
    assert util_entry["source_file"] == os.path.relpath(util_file, workspace).replace('\\', '/')
    
    methods = util_entry["methods"]
    assert len(methods) == 2
    
    # Check public static String formatName(String name)
    format_method = [m for m in methods if m["method_name"] == "formatName"][0]
    assert format_method["signature"] == "public static String formatName(String name)"
    assert format_method["parameters"] == {"name": "String"}
    assert format_method["return_type"] == "String"
    assert "Formats a given name" in format_method["description"]
    
    # Check public int add(int a, int b)
    add_method = [m for m in methods if m["method_name"] == "add"][0]
    assert add_method["signature"] == "public int add(int a, int b)"
    assert add_method["parameters"] == {"a": "int", "b": "int"}
    assert add_method["return_type"] == "int"

def test_compile_utility_blueprints_python(tmp_path):
    workspace = str(tmp_path)
    src_dir = os.path.join(workspace, "tests", "utils")
    os.makedirs(src_dir, exist_ok=True)
    
    python_code = """
def wait_and_click(driver: Any, locator: tuple, timeout_sec: int = 10) -> None:
    \"\"\"
    Explicitly waits for an element and clicks it.
    \"\"\"
    pass

def _private_helper():
    pass
"""
    py_file = os.path.join(src_dir, "helpers.py")
    with open(py_file, "w", encoding="utf-8") as f:
        f.write(python_code)
        
    extractor = ContextExtractor(workspace)
    extractor.store.upsert_node(
        "util_helpers",
        "test_utility",
        "Test Utility: helpers",
        "Python test_utility defined in automation suite",
        {
            "source_file": py_file,
            "class_name": "helpers",
            "language": "python"
        }
    )
    extractor.store.save_graph()
    
    extractor._compile_utility_blueprints()
    
    blueprint_path = os.path.join(workspace, ".context_builder", "blueprint.json")
    assert os.path.exists(blueprint_path)
    
    with open(blueprint_path, 'r', encoding='utf-8') as f:
        blueprint_data = json.load(f)
        
    assert "util_helpers" in blueprint_data["reusable_capabilities"]
    methods = blueprint_data["reusable_capabilities"]["util_helpers"]["methods"]
    
    assert len(methods) == 1
    m = methods[0]
    assert m["method_name"] == "wait_and_click"
    assert m["return_type"] == "None"
    assert "Explicitly waits" in m["description"]
    assert m["parameters"] == {"driver": "Any", "locator": "tuple", "timeout_sec": "int = 10"}

def test_compile_utility_blueprints_go(tmp_path):
    workspace = str(tmp_path)
    src_dir = os.path.join(workspace, "utils")
    os.makedirs(src_dir, exist_ok=True)
    
    go_code = """
package utils

/*
FormatAmount converts a float value to formatted string.
*/
func FormatAmount(val float64, currency string) string {
    return "$10.00"
}

func unexportedHelper() {
}
"""
    go_file = os.path.join(src_dir, "utils.go")
    with open(go_file, "w", encoding="utf-8") as f:
        f.write(go_code)
        
    extractor = ContextExtractor(workspace)
    extractor.store.upsert_node(
        "util_utils",
        "test_utility",
        "Test Utility: utils",
        "Go test_utility defined in automation suite",
        {
            "source_file": go_file,
            "class_name": "utils",
            "language": "go"
        }
    )
    extractor.store.save_graph()
    
    extractor._compile_utility_blueprints()
    
    blueprint_path = os.path.join(workspace, ".context_builder", "blueprint.json")
    assert os.path.exists(blueprint_path)
    
    with open(blueprint_path, 'r', encoding='utf-8') as f:
        blueprint_data = json.load(f)
        
    assert "util_utils" in blueprint_data["reusable_capabilities"]
    methods = blueprint_data["reusable_capabilities"]["util_utils"]["methods"]
    
    assert len(methods) == 1
    m = methods[0]
    assert m["method_name"] == "FormatAmount"
    assert m["return_type"] == "string"
    assert "FormatAmount converts" in m["description"]
    assert m["parameters"] == {"val": "float64", "currency": "string"}
