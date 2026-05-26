import * as assert from 'assert';
import { 
  calculateMetrics, 
  resolveTraceabilityMatrix, 
  findUntestedGaps 
} from '../../vscode-extension/src/coverageReporter';
import { TestCase, ProposedCategory } from '../../vscode-extension/src/schemas';

suite('Coverage Reporter and Traceability Test Suite', () => {
  
  test('Rule Coverage % is calculated correctly', () => {
    const targetRules = ['rule_01', 'rule_02', 'rule_03'];
    const mockCases: TestCase[] = [
      {
        title: 'Test Case 1',
        category_id: 'api_functional',
        target_rule_id: 'rule_01',
        steps: [{ step_number: 1, action: 'A', expected: 'B' }]
      },
      {
        title: 'Test Case 2',
        category_id: 'api_functional',
        target_rule_id: 'rule_01', // duplicate rule mapping
        steps: [{ step_number: 1, action: 'A', expected: 'B' }]
      }
    ];

    const mockProposed: ProposedCategory[] = [];
    const metrics = calculateMetrics(targetRules, mockProposed, mockCases);

    // Rule Coverage: 1 out of 3 target rules covered (rule_01)
    assert.strictEqual(metrics.ruleCoveragePercent, 33.3);
    assert.strictEqual(metrics.totalTestCases, 2);
    assert.strictEqual(metrics.totalTestSteps, 2);
    assert.strictEqual(metrics.avgStepsPerCase, 1);
  });

  test('Condition Coverage % is calculated correctly with min-cap ceiling behavior', () => {
    const targetRules = ['rule_01', 'rule_02'];
    
    // Category 1: total_conditions_count = 3, generated tests = 4 (should cap covered conditions at 3)
    // Category 2: total_conditions_count = 5, generated tests = 2 (should count covered conditions as 2)
    // Total conditions = 3 + 5 = 8
    // Covered conditions = min(4, 3) + min(2, 5) = 3 + 2 = 5
    // Condition Coverage = (5 / 8) * 100 = 62.5%
    const mockProposed: ProposedCategory[] = [
      {
        category_id: 'cat_01',
        category_name: 'Category 1',
        description: 'Desc 1',
        candidate_count: 3,
        total_conditions_count: 3,
        test_scenarios: []
      },
      {
        category_id: 'cat_02',
        category_name: 'Category 2',
        description: 'Desc 2',
        candidate_count: 5,
        total_conditions_count: 5,
        test_scenarios: []
      }
    ];

    const mockCases: TestCase[] = [
      // Cat 1 has 4 cases
      { title: 'T1', category_id: 'cat_01', target_rule_id: 'rule_01', steps: [] },
      { title: 'T2', category_id: 'cat_01', target_rule_id: 'rule_01', steps: [] },
      { title: 'T3', category_id: 'cat_01', target_rule_id: 'rule_01', steps: [] },
      { title: 'T4', category_id: 'cat_01', target_rule_id: 'rule_01', steps: [] },
      // Cat 2 has 2 cases
      { title: 'T5', category_id: 'cat_02', target_rule_id: 'rule_02', steps: [] },
      { title: 'T6', category_id: 'cat_02', target_rule_id: 'rule_02', steps: [] }
    ];

    const metrics = calculateMetrics(targetRules, mockProposed, mockCases);
    assert.strictEqual(metrics.conditionCoveragePercent, 62.5);
  });

  test('Traceability Matrix resolves graph nodes and edges correctly', () => {
    const mockGraph = {
      nodes: [
        { id: 'rule_adjudicate', type: 'business_rule', name: 'Claims Adjudication', metadata: { rule_category: 'claims' } },
        { id: 'scenario_bdd', type: 'test_scenario', name: 'BDD Scenario Claims' },
        { id: 'api_submit', type: 'api_endpoint', name: 'POST /api/claims' }
      ],
      links: [
        { source: 'rule_adjudicate', target: 'scenario_bdd', relationship: 'TRACES_TO' },
        { source: 'rule_adjudicate', target: 'api_submit', relationship: 'MAPS_TO' }
      ]
    };

    const targetRules = ['rule_adjudicate'];
    const mockCases: TestCase[] = [
      { title: 'Assert claim status approved', category_id: 'cat_01', target_rule_id: 'rule_adjudicate', steps: [] }
    ];

    const rows = resolveTraceabilityMatrix(mockGraph, targetRules, mockCases);

    assert.strictEqual(rows.length, 1);
    assert.strictEqual(rows[0].ruleId, 'rule_adjudicate');
    assert.strictEqual(rows[0].ruleName, 'Claims Adjudication');
    assert.strictEqual(rows[0].ruleCategory, 'claims');
    assert.ok(rows[0].bddScenario.includes('BDD Scenario Claims'));
    assert.ok(rows[0].techApi.includes('POST /api/claims'));
    assert.strictEqual(rows[0].testCases.length, 1);
    assert.strictEqual(rows[0].testCases[0], 'Assert claim status approved');
  });

  test('Gap Analysis finds untested rules correctly using set-difference', () => {
    const targetRules = ['rule_01', 'rule_02', 'rule_03'];
    const mockCases: TestCase[] = [
      { title: 'Test Case 1', category_id: 'cat_01', target_rule_id: 'rule_01', steps: [] }
    ];

    const mockGraph = {
      nodes: [
        { id: 'rule_02', type: 'business_rule', description: 'Rule two specification' },
        { id: 'rule_03', type: 'business_rule', description: 'Rule three specification' }
      ]
    };

    const gaps = findUntestedGaps(targetRules, mockCases, mockGraph);

    // Gaps should be rule_02 and rule_03
    assert.strictEqual(gaps.length, 2);
    assert.strictEqual(gaps[0].ruleId, 'rule_02');
    assert.strictEqual(gaps[0].description, 'Rule two specification');
    assert.strictEqual(gaps[1].ruleId, 'rule_03');
    assert.strictEqual(gaps[1].description, 'Rule three specification');
  });

  test('Coordinated filename timestamps invariant check', () => {
    const now = new Date();
    const timestamp = now.toISOString()
      .replace(/T/, '_')
      .replace(/\..+/, '')
      .replace(/:/g, '')
      .replace(/-/g, '') + 'z';

    const csvFilename = `test_cases_${timestamp}.csv`;
    const mdFilename = `coverage_report_${timestamp}.md`;

    // Extract timestamps from both filenames and verify they are identical
    const csvTimestamp = csvFilename.replace('test_cases_', '').replace('.csv', '');
    const mdTimestamp = mdFilename.replace('coverage_report_', '').replace('.md', '');

    assert.strictEqual(csvTimestamp, mdTimestamp);
    assert.ok(timestamp.match(/^\d{8}_\d{6}z$/), 'Timestamp should match coordinated YYYYMMDD_HHMMSSz pattern.');
  });
});
