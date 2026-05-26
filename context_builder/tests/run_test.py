import os
import sys
import json
import shutil
from typing import Dict, Any

# Ensure parent directory is in python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.extractor import ContextExtractor
from db.graph_store import GraphStore

def create_mock_workspace(workspace_dir: str) -> None:
    """Programmatically constructs a mock workspace with representative context documents and code files."""
    os.makedirs(workspace_dir, exist_ok=True)
    
    # 1. Feature file
    feature_content = """Feature: Policy Eligibility Rules
  As an insurance company
  I want to verify eligibility criteria for applicants
  So that we can assess risk profiles

  Scenario: Applicant is too young
    Given an applicant is 17 years old
    When they apply for standard auto insurance
    Then their eligibility should be Rejected
    And the reason should be "Applicant is under the age limit of 18"

  Scenario: Applicant is eligible for standard auto policy
    Given an applicant is 25 years old
    When they apply for standard auto insurance
    Then their eligibility should be Approved
"""
    with open(os.path.join(workspace_dir, "eligibility.feature"), "w") as f:
        f.write(feature_content)
        
    # 2. Mock Java
    java_content = """package com.insurance.calculator;

import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/v1/calculator")
public class PremiumCalculatorController {

    @PostMapping("/calculate")
    public PremiumResponse calculatePremium(@RequestBody QuoteRequest request) {
        // Eligibility rule validation is triggered here
        return new PremiumResponse(250.00);
    }
}
"""
    with open(os.path.join(workspace_dir, "PremiumCalculatorController.java"), "w") as f:
        f.write(java_content)
        
    # 3. Mock TS
    ts_content = """import { Controller, Post, Body } from '@nestjs/common';

@Controller('quotes')
export class QuoteController {
    
    @Post('premium')
    async getQuote(@Body() quoteDto: any) {
        // Runs standard policy eligibility checks
        return { premium: 120.00 };
    }
}
"""
    with open(os.path.join(workspace_dir, "QuoteController.ts"), "w") as f:
        f.write(ts_content)
        
    # 4. OpenAPI
    openapi_content = {
        "openapi": "3.0.0",
        "info": { "title": "Insurance Quote API", "version": "1.0.0" },
        "paths": {
            "/api/v1/calculator/calculate": {
                "post": { "summary": "Calculate Premium", "description": "Calculates monthly premium based on applicant profile" }
            }
        }
    }
    with open(os.path.join(workspace_dir, "openapi_spec.json"), "w") as f:
        json.dump(openapi_content, f, indent=2)

    # 5. SDD Markdown Spec
    md_content = """# Feature: Quote Calculator
The Quote Calculator module is responsible for calculating insurance premiums.

## Rule: Minimum Premium Rule
All quotes must have a minimum premium of $50.00 regardless of the base calculation.

### Scenario: High risk driver gets standard minimum
* Given a driver with 5 accidents
* When the premium is calculated
* Then the final premium is floored at the minimum premium threshold
"""
    with open(os.path.join(workspace_dir, "spec.md"), "w") as f:
        f.write(md_content)

def main():
    print("==================================================")
    print("STARTING INTEGRATION VERIFICATION TEST")
    print("==================================================")
    
    # Establish local paths
    test_dir = os.path.dirname(os.path.abspath(__file__))
    mock_workspace = os.path.join(test_dir, "mock_workspace")
    
    # Clean up from previous run
    if os.path.exists(mock_workspace):
        shutil.rmtree(mock_workspace)
        
    print("Step 1: Constructing Mock Workspace...")
    create_mock_workspace(mock_workspace)
    print(f"Mock workspace set up at: {mock_workspace}\n")
    
    print("Step 2: Initializing ContextExtractor...")
    extractor = ContextExtractor(mock_workspace)
    
    print("Step 3: Ingesting Mock Workspace...")
    result = extractor.ingest_workspace()
    print("Ingestion complete. Response summary:")
    print(json.dumps(result, indent=2))
    print()
    
    print("Step 4: Inspecting Generated Graph Nodes...")
    store = GraphStore(mock_workspace)
    nodes = store.query_nodes()
    print(f"Found {len(nodes)} nodes in Semantic Context Graph:")
    for n in nodes:
        print(f"  - [{n['type'].upper()}] ID: {n['id']} | Name: {n['name']}")
    print()
    
    print("Step 5: Inspecting Generated Graph Edges...")
    edges = store.get_edges()
    print(f"Found {len(edges)} relationships in Semantic Context Graph:")
    for e in edges:
        print(f"  - {e['source_id']} -- [{e['relationship']}] --> {e['target_id']}")
    print()
    
    # Find a rule node to test traceability
    rule_nodes = [n for n in nodes if n["type"] == "business_rule"]
    if rule_nodes:
        target_rule = rule_nodes[0]["id"]
        print(f"Step 6: Testing Traceability Graph for Node: '{target_rule}'...")
        trace_graph = store.get_traceability_graph(target_rule)
        print(f"Successfully retrieved traceability path. Includes {len(trace_graph['nodes'])} nodes and {len(trace_graph['edges'])} edges.")
        print("Connected Nodes:")
        for cn in trace_graph["nodes"]:
            print(f"  * [{cn['type'].upper()}] {cn['id']}")
    else:
        print("Step 6: Skip - No business rule nodes found to trace.")
        
    # Clean up test directories
    print("\nCleaning up temporary files...")
    if os.path.exists(mock_workspace):
        shutil.rmtree(mock_workspace)
        
    print("\n==================================================")
    print("VERIFICATION TEST COMPLETED SUCCESSFULLY!")
    print("==================================================")

if __name__ == "__main__":
    main()
