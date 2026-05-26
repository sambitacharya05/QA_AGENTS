import * as assert from 'assert';
import * as fs from 'fs';
import * as path from 'path';

suite('Subagent Prompts Validation Test Suite', () => {
  const agentsDir = path.join(process.cwd(), 'vscode-extension/src/agents');

  test('All three subagent prompt files exist and are readable', () => {
    const files = [
      'module_analyzer.agent.md',
      'test_case_analyst.agent.md',
      'test_generator.agent.md'
    ];

    files.forEach(file => {
      const filePath = path.join(agentsDir, file);
      assert.ok(fs.existsSync(filePath), `Agent file ${file} should exist.`);
      const stat = fs.statSync(filePath);
      assert.ok(stat.size > 200, `Agent file ${file} should contain substantial prompt content.`);
    });
  });

  test('Module Analyzer prompt defines semantic query routing and output schema details', () => {
    const filePath = path.join(agentsDir, 'module_analyzer.agent.md');
    const content = fs.readFileSync(filePath, 'utf8');

    assert.ok(content.includes('Module Analyzer'), 'Prompt should define Agent name');
    assert.ok(content.includes('is_module_specific'), 'Prompt should mention is_module_specific');
    assert.ok(content.includes('matched_rule_ids'), 'Prompt should mention matched_rule_ids');
    assert.ok(content.includes('reasoning'), 'Prompt should mention reasoning');
  });

  test('Test Case Analyst prompt defines EP, BVA, state transitions, total_conditions_count, and Correction-Mode', () => {
    const filePath = path.join(agentsDir, 'test_case_analyst.agent.md');
    const content = fs.readFileSync(filePath, 'utf8');

    assert.ok(content.includes('Test Case Analyst'), 'Prompt should define Agent name');
    assert.ok(content.includes('Equivalence Partitioning'), 'Prompt should define EP technique');
    assert.ok(content.includes('Boundary Value Analysis'), 'Prompt should define BVA technique');
    assert.ok(content.includes('State Transition'), 'Prompt should define state transition technique');
    assert.ok(content.includes('total_conditions_count'), 'Prompt should define total_conditions_count for coverage percentage calculation');
    assert.ok(content.includes('Correction-Mode'), 'Prompt should define Correction-Mode for gap resolution');
    assert.ok(content.includes('Scope Lock'), 'Prompt should enforce confirmed categories Scope Lock');
  });

  test('Test Generator prompt defines Step 1 Precondition Invariant, Scope Lock, and output format', () => {
    const filePath = path.join(agentsDir, 'test_generator.agent.md');
    const content = fs.readFileSync(filePath, 'utf8');

    assert.ok(content.includes('Test Generator'), 'Prompt should define Agent name');
    assert.ok(content.includes('Precondition Invariant'), 'Prompt should define Step 1 Precondition Invariant');
    assert.ok(content.includes('No assertions in Step 1'), 'Prompt should enforce zero active assertions in Step 1');
    assert.ok(content.includes('Scope Lock'), 'Prompt should enforce Scope Lock for confirmed categories');
    assert.ok(content.includes('TestCasesArraySchema'), 'Prompt should adhere to TestCasesArraySchema');
  });
});
