import * as assert from 'assert';
import { z } from 'zod';
import { parseAgentOutput, AgentOutputError } from '../../vscode-extension/src/agentUtils';
import { TestCasesArraySchema, GapLogSchema, TestCase } from '../../vscode-extension/src/schemas';

const MockSchema = z.object({
  id: z.string().min(1),
  active: z.boolean()
});

suite('agentUtils and Schema Validation Test Suite', () => {
  
  // ==========================================================================
  // 1. parseAgentOutput Markdown Stripping Tests
  // ==========================================================================
  
  test('parseAgentOutput strips ```json markdown fences and parses successfully', async () => {
    const rawMarkdown = "```json\n{\n  \"id\": \"TEST-01\",\n  \"active\": true\n}\n```";
    const mockStream: any = { markdown: () => {} };

    const parsed = await parseAgentOutput(rawMarkdown, MockSchema, "MockAgent", mockStream);
    assert.strictEqual(parsed.id, "TEST-01");
    assert.strictEqual(parsed.active, true);
  });

  test('parseAgentOutput strips plain ``` markdown fences and parses successfully', async () => {
    const rawMarkdown = "```\n{\n  \"id\": \"TEST-02\",\n  \"active\": false\n}\n```";
    const mockStream: any = { markdown: () => {} };

    const parsed = await parseAgentOutput(rawMarkdown, MockSchema, "MockAgent", mockStream);
    assert.strictEqual(parsed.id, "TEST-02");
    assert.strictEqual(parsed.active, false);
  });

  test('parseAgentOutput parses raw JSON strings without markdown fences successfully', async () => {
    const rawJson = "{\n  \"id\": \"TEST-03\",\n  \"active\": true\n}";
    const mockStream: any = { markdown: () => {} };

    const parsed = await parseAgentOutput(rawJson, MockSchema, "MockAgent", mockStream);
    assert.strictEqual(parsed.id, "TEST-03");
    assert.strictEqual(parsed.active, true);
  });

  test('parseAgentOutput strips markdown with trailing/leading white space cleanly', async () => {
    const messyMarkdown = "   \n```json\n{\n  \"id\": \"TEST-04\",\n  \"active\": true\n}\n```   \n ";
    const mockStream: any = { markdown: () => {} };

    const parsed = await parseAgentOutput(messyMarkdown, MockSchema, "MockAgent", mockStream);
    assert.strictEqual(parsed.id, "TEST-04");
    assert.strictEqual(parsed.active, true);
  });

  // ==========================================================================
  // 2. parseAgentOutput Error Handling Tests
  // ==========================================================================

  test('parseAgentOutput throws AgentOutputError on invalid non-JSON strings', async () => {
    const invalidText = "This is not a JSON object at all.";
    const mockStream: any = { markdown: () => {} };

    await assert.rejects(
      async () => {
        await parseAgentOutput(invalidText, MockSchema, "MockAgent", mockStream);
      },
      (err: any) => {
        assert.ok(err instanceof AgentOutputError);
        assert.strictEqual(err.agentName, "MockAgent");
        assert.strictEqual(err.rawOutput, invalidText);
        assert.strictEqual(err.detail, "JSON.parse failure");
        return true;
      }
    );
  });

  test('parseAgentOutput throws AgentOutputError on schema constraint violations', async () => {
    // Missing required field "active" and "id" is empty which violates min(1) constraint
    const invalidPayload = "{\n  \"id\": \"\"\n}";
    const mockStream: any = { markdown: () => {} };

    await assert.rejects(
      async () => {
        await parseAgentOutput(invalidPayload, MockSchema, "MockAgent", mockStream);
      },
      (err: any) => {
        assert.ok(err instanceof AgentOutputError);
        assert.strictEqual(err.agentName, "MockAgent");
        assert.ok(err.detail.includes("id"));
        assert.ok(err.detail.includes("active"));
        return true;
      }
    );
  });

  // ==========================================================================
  // 3. TestCasesArraySchema Refinements (Step 1 Navigation Invariant)
  // ==========================================================================

  test('TestCasesArraySchema accepts valid test case where Step 1 is assertion-free and Step 2 contains assertions', () => {
    const validCases: TestCase[] = [
      {
        tc_id: "TC_PRIORAUTH_HP_001",
        target_feature_id: "<test-fixture-feature>",
        title: "Submit prior auth request",
        category_id: "prior_auth",
        target_rule_id: "rule_pa_01",
        steps: [
          { step_number: 1, action: "Navigate to PA dashboard and authenticate session", expected: "Dashboard is loaded and token is ready" },
          { step_number: 2, action: "Enter member details and click Submit PA", expected: "API responds with status 200 and auth approved status" }
        ]
      }
    ];

    const result = TestCasesArraySchema.safeParse(validCases);
    assert.strictEqual(result.success, true);
  });

  test('TestCasesArraySchema rejects test case where Step 1 action contains assertions', () => {
    const invalidCases: TestCase[] = [
      {
        tc_id: "TC_PRIORAUTH_FUNC_001",
        target_feature_id: "<test-fixture-feature>",
        title: "Submit prior auth request with bad step 1 action",
        category_id: "prior_auth",
        target_rule_id: "rule_pa_01",
        steps: [
          { step_number: 1, action: "Assert status code is 200 on login endpoint", expected: "User is logged in" }
        ]
      }
    ];

    const result = TestCasesArraySchema.safeParse(invalidCases);
    assert.strictEqual(result.success, false);
    if (!result.success) {
      assert.ok(result.error.issues[0].message.includes("Step 1 must not contain active assertions"));
    }
  });

  test('TestCasesArraySchema rejects test case where Step 1 expected outcome contains assertions', () => {
    const invalidCases: TestCase[] = [
      {
        tc_id: "TC_PRIORAUTH_NEG_001",
        target_feature_id: "<test-fixture-feature>",
        title: "Submit prior auth request with bad step 1 expected",
        category_id: "prior_auth",
        target_rule_id: "rule_pa_01",
        steps: [
          { step_number: 1, action: "Navigate to page", expected: "Verify response returns status code 200" }
        ]
      }
    ];

    const result = TestCasesArraySchema.safeParse(invalidCases);
    assert.strictEqual(result.success, false);
    if (!result.success) {
      assert.ok(result.error.issues[0].message.includes("Step 1 must not contain active assertions"));
    }
  });

  // ==========================================================================
  // 4. GapLogSchema Refinements (Compliance Invariant)
  // ==========================================================================

  test('GapLogSchema accepts compliant log with empty gaps array', () => {
    const compliantLog = {
      is_compliant: true,
      detected_gaps: []
    };

    const result = GapLogSchema.safeParse(compliantLog);
    assert.strictEqual(result.success, true);
  });

  test('GapLogSchema accepts non-compliant log with detected gaps array', () => {
    const nonCompliantLog = {
      is_compliant: false,
      detected_gaps: [
        {
          gap_id: "GAP-01",
          target_rule_id: "rule_01",
          severity: "High",
          description: "Missing boundary condition check for age limit 75.",
          remediation: {
            action_type: "ADD_BOUNDARY_TEST",
            target_field: "age",
            correct_value: "75",
            new_test_description: "Verify eligibility rejects age 76."
          }
        }
      ]
    };

    const result = GapLogSchema.safeParse(nonCompliantLog);
    assert.strictEqual(result.success, true);
  });

  test('GapLogSchema rejects non-compliant log containing empty gaps array', () => {
    const invalidLog = {
      is_compliant: false,
      detected_gaps: []
    };

    const result = GapLogSchema.safeParse(invalidLog);
    assert.strictEqual(result.success, false);
    if (!result.success) {
      assert.ok(result.error.issues[0].message.includes("Non-compliant result must include at least one detected gap"));
    }
  });

  test('GapLogSchema rejects compliant log containing detected gaps array', () => {
    const invalidLog = {
      is_compliant: true,
      detected_gaps: [
        {
          gap_id: "GAP-02",
          target_rule_id: "rule_02",
          severity: "Low",
          description: "Minor description mismatch.",
          remediation: {
            action_type: "ADD_NEGATIVE_TEST",
            new_test_description: "Assert denial logs error code."
          }
        }
      ]
    };

    const result = GapLogSchema.safeParse(invalidLog);
    assert.strictEqual(result.success, false);
    if (!result.success) {
      assert.ok(result.error.issues[0].message.includes("Compliant result must not include detected gaps"));
    }
  });
});
