import * as assert from 'assert';
import { evaluateComplianceAndHalt } from '../../vscode-extension/src/extension';
import { PipelineState } from '../../vscode-extension/src/types';
import { GapLog, TestCase } from '../../vscode-extension/src/schemas';

suite('Self-Correction and Verification Loop Integration Test Suite', () => {
  
  const mockMcpClient: any = {
    callTool: async (name: string, args: any) => {
      if (name === "write_azure_csv") {
        return { success: true, rows_written: 2 };
      }
      return { success: true };
    }
  };

  const mockStream: any = {
    markdown: (txt: string) => {},
    button: (btn: any) => {}
  };

  const getMockGraphContext = () => ({
    matchedRuleIds: ["rule_01"],
    businessRules: [
      { id: "rule_01", name: "Rule 1", category: "claims", description: "Age check limit 75" }
    ],
    linkedScenarios: [],
    linkedEndpoints: [],
    linkedSchemas: [],
    rawSubgraphJson: '{}'
  });

  const getMockProposal = () => ({
    target_rules: ["rule_01"],
    rule_analysis: [
      {
        rule_id: "rule_01",
        rule_type: "numeric_threshold" as const,
        classification_reasoning:
          "The rule contains a numeric boundary (age 75) that determines claim eligibility, making BVA directly applicable.",
        applicable_methodologies: ["EP", "BVA", "LinearExpansion", "RBAC"] as Array<"EP" | "BVA" | "StateTransition" | "LinearExpansion" | "RBAC">,
        skipped_methodologies: [
          {
            name: "StateTransition" as const,
            reason:
              "The rule governs a single eligibility determination based on a numeric threshold, not a multi-stage lifecycle with ordered state transitions."
          }
        ],
        attributes: [
          {
            name: "member_age",
            display_name: "Member Age",
            type: "numeric_range" as const,
            possible_values: ["0", "75"],
            source: "explicit" as const
          },
          {
            name: "auth_state",
            display_name: "Auth State",
            type: "boolean" as const,
            possible_values: ["authenticated", "unauthenticated"],
            source: "implicit" as const
          }
        ],
        equivalence_class_table: [
          {
            attribute: "member_age",
            class_label: "Well Within Limit",
            class_value: "40",
            partition: "valid" as const,
            expected_outcome: "Claim is accepted and transitions to in_review status with HTTP 201",
            is_straight_through: true
          },
          {
            attribute: "member_age",
            class_label: "At Age Limit Boundary",
            class_value: "75",
            partition: "boundary_at" as const,
            expected_outcome: "Claim is accepted — 75 is the inclusive upper bound for eligibility",
            is_straight_through: false
          },
          {
            attribute: "member_age",
            class_label: "Over Age Limit",
            class_value: "76",
            partition: "boundary_high" as const,
            expected_outcome: "Claim is denied with HTTP 422 and reason code CLM007 (age limit exceeded)",
            is_straight_through: false
          },
          {
            attribute: "auth_state",
            class_label: "Authenticated User",
            class_value: "authenticated",
            partition: "valid" as const,
            expected_outcome: "Session token is valid and user identity is established",
            is_straight_through: true
          },
          {
            attribute: "auth_state",
            class_label: "Unauthenticated",
            class_value: "unauthenticated",
            partition: "invalid" as const,
            expected_outcome: "Request is rejected with HTTP 401 and redirected to the login page",
            is_straight_through: false
          }
        ],
        straight_through_case: {
          description: "Authenticated member aged 40 submits a claim — accepted within the age limit",
          attribute_values: { member_age: "40", auth_state: "authenticated" },
          expected_outcome: "Claim is accepted and transitions to in_review status with HTTP 201"
        },
        linear_expansion_matrix: {
          column_headers: ["Member Age", "Auth State"],
          straight_through_values: { member_age: "40", auth_state: "authenticated" },
          rows: [
            {
              test_number: 1,
              is_straight_through: true,
              varied_attribute: null,
              attribute_values: { member_age: "40", auth_state: "authenticated" },
              expected_outcome: "Claim is accepted and transitions to in_review status with HTTP 201",
              maps_to_scenario_title: "Test 1"
            },
            {
              test_number: 2,
              is_straight_through: false,
              varied_attribute: "member_age",
              attribute_values: { member_age: "75", auth_state: "authenticated" },
              expected_outcome: "Claim is accepted — 75 is the inclusive upper bound for eligibility",
              maps_to_scenario_title: "Test 2"
            },
            {
              test_number: 3,
              is_straight_through: false,
              varied_attribute: "member_age",
              attribute_values: { member_age: "76", auth_state: "authenticated" },
              expected_outcome: "Claim is denied with HTTP 422 and reason code CLM007 (age limit exceeded)",
              maps_to_scenario_title: "Test 3"
            },
            {
              test_number: 4,
              is_straight_through: false,
              varied_attribute: "auth_state",
              attribute_values: { member_age: "40", auth_state: "unauthenticated" },
              expected_outcome: "Request is rejected with HTTP 401 and redirected to the login page",
              maps_to_scenario_title: "Test 4"
            }
          ],
          formula_applied: "1 + (3-1) + (2-1) = 4 EP/BVA/Linear tests",
          total_tests_derived: 4
        }
      }
    ],
    proposed_categories: [
      {
        category_id: "api_functional",
        category_name: "API Functional",
        description: "Test functional api endpoints",
        candidate_count: 1,
        total_conditions_count: 2,
        test_scenarios: [] as any[]
      }
    ]
  });

  test('Orchestrator successfully terminates with success reason on compliant verifier result', async () => {
    const state: PipelineState = {
      proposal: getMockProposal(),
      confirmedCategoryIds: ["api_functional"],
      graphContext: getMockGraphContext(),
      userConstraints: {},
      iterationCount: 0,
      prevGapCount: Infinity,
      gapLog: null,
      testCases: [],
      sessionId: "session-1",
      targetModule: "Claims",
      maxIterations: 3
    };

    const mockCases: TestCase[] = [
      { tc_id: 'TC_TEST_HP_001', target_feature_id: '<test-fixture-feature>', title: "Test 1", category_id: "api_functional", target_rule_id: "rule_01", steps: [] }
    ];

    const mockVerifierResult: GapLog = {
      is_compliant: true,
      detected_gaps: []
    };

    // Act
    await evaluateComplianceAndHalt(state, mockCases, mockVerifierResult, mockStream, mockMcpClient, process.cwd());

    // Assert
    assert.strictEqual(state.terminationReason, 'success');
    assert.strictEqual(state.testCases, mockCases);
    assert.strictEqual(state.gapLog, mockVerifierResult);
  });

  test('Orchestrator successfully detects divergence and halts to prevent token burn', async () => {
    const state: PipelineState = {
      proposal: getMockProposal(),
      confirmedCategoryIds: ["api_functional"],
      graphContext: getMockGraphContext(),
      userConstraints: {},
      iterationCount: 1,
      prevGapCount: 1, // Prior gap count was 1
      gapLog: null,
      testCases: [],
      sessionId: "session-2",
      targetModule: "Claims",
      maxIterations: 3
    };

    const mockCases: TestCase[] = [
      { tc_id: 'TC_TEST_HP_001', target_feature_id: '<test-fixture-feature>', title: "Test 1", category_id: "api_functional", target_rule_id: "rule_01", steps: [] }
    ];

    const mockVerifierResult: GapLog = {
      is_compliant: false,
      detected_gaps: [
        {
          gap_id: "GAP-01",
          target_rule_id: "rule_01",
          severity: "High",
          description: "Age limit 75 check missing.",
          remediation: {
            action_type: "ADD_BOUNDARY_TEST",
            target_field: "age",
            correct_value: "75",
            new_test_description: "Verify age 76 limit."
          }
        }
      ] // Current gap count is 1 (diverged/failed to decrease from 1)
    };

    // Act
    await evaluateComplianceAndHalt(state, mockCases, mockVerifierResult, mockStream, mockMcpClient, process.cwd());

    // Assert
    assert.strictEqual(state.terminationReason, 'divergence');
    assert.strictEqual(state.testCases, mockCases);
  });

  test('Orchestrator halts when safety ceiling iterations limit is reached', async () => {
    const state: PipelineState = {
      proposal: getMockProposal(),
      confirmedCategoryIds: ["api_functional"],
      graphContext: getMockGraphContext(),
      userConstraints: {},
      iterationCount: 2, // Reached 2 iterations run
      prevGapCount: 5,
      gapLog: null,
      testCases: [],
      sessionId: "session-3",
      targetModule: "Claims",
      maxIterations: 2 // Ceiling is 2
    };

    const mockCases: TestCase[] = [
      { tc_id: 'TC_TEST_HP_001', target_feature_id: '<test-fixture-feature>', title: "Test 1", category_id: "api_functional", target_rule_id: "rule_01", steps: [] }
    ];

    const mockVerifierResult: GapLog = {
      is_compliant: false,
      detected_gaps: [
        {
          gap_id: "GAP-01",
          target_rule_id: "rule_01",
          severity: "Medium",
          description: "Minor naming mismatch.",
          remediation: {
            action_type: "ADD_NEGATIVE_TEST",
            new_test_description: "Assert denial logs error."
          }
        }
      ] // Gaps count is 1 (decreased from 5, so no divergence, but ceiling reached)
    };

    // Act
    await evaluateComplianceAndHalt(state, mockCases, mockVerifierResult, mockStream, mockMcpClient, process.cwd());

    // Assert
    assert.strictEqual(state.terminationReason, 'ceiling');
  });

  test('Orchestrator statefully updates counts and triggers HITL gate when converging', async () => {
    // hitlSelector is injected so the test does not block on a real VS Code dialog.
    // We simulate the user choosing "export as-is" so the function terminates
    // cleanly and we can assert on the full final state.
    const state: PipelineState = {
      proposal: getMockProposal(),
      confirmedCategoryIds: ["api_functional"],
      graphContext: getMockGraphContext(),
      userConstraints: {},
      iterationCount: 0,
      prevGapCount: 10, // Prior gap count was 10
      gapLog: null,
      testCases: [],
      sessionId: "session-4",
      targetModule: "Claims",
      maxIterations: 3,
      hitlSelector: async () => 'export' // Simulate user choosing "Export As-Is"
    };

    const mockCases: TestCase[] = [
      { tc_id: 'TC_TEST_HP_001', target_feature_id: '<test-fixture-feature>', title: "Test 1", category_id: "api_functional", target_rule_id: "rule_01", steps: [] }
    ];

    const mockVerifierResult: GapLog = {
      is_compliant: false,
      detected_gaps: [
        {
          gap_id: "GAP-01",
          target_rule_id: "rule_01",
          severity: "High",
          description: "Missing boundary check.",
          remediation: {
            action_type: "ADD_BOUNDARY_TEST",
            target_field: "deductible",
            correct_value: "0",
            new_test_description: "Verify boundary check."
          }
        }
      ] // Current gap count is 1 (converging, decreased from 10!)
    };

    // Act
    await evaluateComplianceAndHalt(state, mockCases, mockVerifierResult, mockStream, mockMcpClient, process.cwd());

    // Assert: state counts are updated correctly before the gate decision
    assert.strictEqual(state.prevGapCount, 1);       // Updated to 1
    assert.strictEqual(state.iterationCount, 1);     // Incremented from 0 to 1
    assert.strictEqual(state.gapLog, mockVerifierResult);
    // The injected selector returned 'export', so the pipeline terminates via user_export
    assert.strictEqual(state.terminationReason, 'user_export');
  });
});
