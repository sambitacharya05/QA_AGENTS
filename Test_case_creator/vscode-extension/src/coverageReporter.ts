import * as fs from 'fs';
import * as path from 'path';
import * as vscode from 'vscode';
import { TestCase, ProposedCategory, CategoryProposal } from './schemas';
import { PipelineState } from './types';

export interface CoverageMetrics {
  ruleCoveragePercent: number;
  conditionCoveragePercent: number;
  totalTestCases: number;
  totalTestSteps: number;
  avgStepsPerCase: number;
}

export interface TraceabilityRow {
  ruleId: string;
  ruleName: string;
  ruleCategory: string;
  bddScenario: string;
  techApi: string;
  testCases: string[];
}

export interface GapItem {
  ruleId: string;
  description: string;
}

/**
 * Searches for graph.json inside standard workspace subdirectories.
 */
export function findGraphPath(workspaceRoot: string): string | null {
  const potentialPaths = [
    path.join(workspaceRoot, 'ingest', '.context_builder', 'graph.json'),
    path.join(workspaceRoot, '.context_builder', 'graph.json'),
    path.join(workspaceRoot, 'context_builder', 'ingest', '.context_builder', 'graph.json'),
    path.join(workspaceRoot, 'Test_case_creator', 'ingest', '.context_builder', 'graph.json')
  ];

  for (const p of potentialPaths) {
    if (fs.existsSync(p)) {
      return p;
    }
  }
  return null;
}

/**
 * Loads graph.json context graph safely.
 */
export function loadGraph(graphPath: string): any {
  try {
    if (!graphPath || !fs.existsSync(graphPath)) {
      return { nodes: [], links: [], edges: [] };
    }
    const content = fs.readFileSync(graphPath, 'utf8');
    return JSON.parse(content);
  } catch (err) {
    console.error('Failed to load context graph:', err);
    return { nodes: [], links: [], edges: [] };
  }
}

/**
 * Resolves traceability matrix linking rules to connected nodes and test cases.
 */
export function resolveTraceabilityMatrix(
  graphData: any,
  targetRuleIds: string[],
  generatedCases: TestCase[]
): TraceabilityRow[] {
  const nodes = graphData.nodes || [];
  const links = graphData.links || graphData.edges || [];

  // Group generated test cases by their target rule ID
  const testCaseMap = new Map<string, string[]>();
  for (const tc of generatedCases) {
    const list = testCaseMap.get(tc.target_rule_id) || [];
    list.push(tc.title);
    testCaseMap.set(tc.target_rule_id, list);
  }

  const rows: TraceabilityRow[] = [];

  for (const ruleId of targetRuleIds) {
    // Find the business rule node
    const ruleNode = nodes.find((n: any) => n.id === ruleId || n.rule_id === ruleId);
    const ruleName = ruleNode?.name || ruleNode?.rule_name || ruleId;
    const ruleCategory = ruleNode?.metadata?.rule_category || ruleNode?.category || 'business_rule';

    // Discovered connected entities
    const bddScenarios: string[] = [];
    const techApis: string[] = [];

    // Follow edges where this rule is source or target
    for (const link of links) {
      const source = link.source_id || link.source;
      const target = link.target_id || link.target;

      if (source === ruleId || target === ruleId) {
        const otherId = source === ruleId ? target : source;
        const otherNode = nodes.find((n: any) => n.id === otherId);

        if (otherNode) {
          const type = (otherNode.type || '').toLowerCase();
          const name = otherNode.name || otherNode.id;

          if (type === 'test_scenario' || otherId.includes('scenario') || otherId.includes('cucumber')) {
            bddScenarios.push(`${name} (\`${otherId}\`)`);
          } else if (
            type === 'api_endpoint' ||
            type === 'data_model' ||
            otherId.includes('api') ||
            otherId.includes('schema')
          ) {
            techApis.push(`${name} (\`${otherId}\`)`);
          }
        }
      }
    }

    rows.push({
      ruleId,
      ruleName,
      ruleCategory,
      bddScenario: bddScenarios.length > 0 ? bddScenarios.join(', ') : 'None Linked',
      techApi: techApis.length > 0 ? techApis.join(', ') : 'None Linked',
      testCases: testCaseMap.get(ruleId) || []
    });
  }

  return rows;
}

/**
 * Calculates Rule Coverage, Condition Coverage, and Functional Test Volume metrics programmatically.
 */
export function calculateMetrics(
  targetRuleIds: string[],
  proposedCategories: ProposedCategory[],
  generatedCases: TestCase[]
): CoverageMetrics {
  const totalTestCases = generatedCases.length;
  let totalTestSteps = 0;
  for (const tc of generatedCases) {
    totalTestSteps += tc.steps ? tc.steps.length : 0;
  }
  const avgStepsPerCase = totalTestCases > 0 ? Number((totalTestSteps / totalTestCases).toFixed(2)) : 0;

  // Rule Coverage %
  const targetRulesSet = new Set(targetRuleIds);
  const coveredRules = new Set<string>();
  for (const tc of generatedCases) {
    if (targetRulesSet.has(tc.target_rule_id)) {
      coveredRules.add(tc.target_rule_id);
    }
  }
  const ruleCoveragePercent = targetRuleIds.length > 0
    ? Number(((coveredRules.size / targetRuleIds.length) * 100).toFixed(1))
    : 0;

  // Condition Coverage %
  let totalConditionsCount = 0;
  let totalCoveredConditions = 0;

  for (const cat of proposedCategories) {
    const totalConditions = cat.total_conditions_count || 1;
    totalConditionsCount += totalConditions;

    // Count tests belonging to this category
    const generatedInCategory = generatedCases.filter((tc) => tc.category_id === cat.category_id).length;
    // Cap covered conditions at the category's declared conditions limit
    totalCoveredConditions += Math.min(generatedInCategory, totalConditions);
  }

  const conditionCoveragePercent = totalConditionsCount > 0
    ? Number(((totalCoveredConditions / totalConditionsCount) * 100).toFixed(1))
    : 0;

  return {
    ruleCoveragePercent,
    conditionCoveragePercent,
    totalTestCases,
    totalTestSteps,
    avgStepsPerCase
  };
}

/**
 * Audit to find untested requirements (the Gap Set G = T - C)
 */
export function findUntestedGaps(
  targetRuleIds: string[],
  generatedCases: TestCase[],
  graphData: any
): GapItem[] {
  const coveredRules = new Set(generatedCases.map((tc) => tc.target_rule_id));
  const nodes = graphData.nodes || [];

  const gaps: GapItem[] = [];
  for (const ruleId of targetRuleIds) {
    if (!coveredRules.has(ruleId)) {
      const ruleNode = nodes.find((n: any) => n.id === ruleId || n.rule_id === ruleId);
      const desc = ruleNode?.description || 'Untested business requirement';
      gaps.push({
        ruleId,
        description: desc
      });
    }
  }
  return gaps;
}

/**
 * Invokes the Coverage Reporter LLM agent to generate the premium markdown report.
 */
export async function compileMarkdownReport(
  agentPrompt: string,
  payload: {
    metadata: any;
    metrics: CoverageMetrics;
    traceabilityMatrix: TraceabilityRow[];
    gapAnalysis: GapItem[];
    verificationLoopHistory: any;
  },
  model?: vscode.LanguageModelChat
): Promise<string> {
  const userMessage = JSON.stringify(payload, null, 2);

  // In test environment, vscode.lm might not be defined. Handle fallback or direct mock return.
  if (typeof vscode === 'undefined' || !vscode.lm || !vscode.lm.selectChatModels) {
    // Return standard formatted report for offline test execution
    return mockCompiledMarkdown(payload);
  }

  let resolvedModel = model;
  if (!resolvedModel) {
    const found = await vscode.lm.selectChatModels({ vendor: 'copilot' });
    if (found.length === 0) {
      throw new Error('No Copilot models available for coverage reporter.');
    }
    resolvedModel = found[0];
  }

  const messages = [
    vscode.LanguageModelChatMessage.User(agentPrompt),
    vscode.LanguageModelChatMessage.User(`Compile the report using this data:\n\n${userMessage}`)
  ];

  // Dispose the CancellationTokenSource when the request completes so VS Code
  // does not hold the underlying timer/listener open for the lifetime of the session.
  const cts = new vscode.CancellationTokenSource();
  try {
    const llmResponse = await resolvedModel.sendRequest(messages, {}, cts.token);
    let text = '';
    for await (const part of llmResponse.stream) {
      if (part instanceof vscode.LanguageModelTextPart) {
        text += part.value;
      }
    }

    return text.trim();
  } finally {
    cts.dispose();
  }
}

/**
 * Static mock report compiler for offline testing and fallback scenarios.
 */
export function mockCompiledMarkdown(payload: any): string {
  const { metadata, metrics, traceabilityMatrix, gapAnalysis, verificationLoopHistory } = payload;

  let traceabilityLines = traceabilityMatrix.map((r: any) => {
    const cases = r.testCases.length > 0 ? r.testCases.join('<br>') : 'None';
    return `| \`${r.ruleId}\` | ${r.ruleName} (${r.ruleCategory}) | ${r.bddScenario} | ${r.techApi} | ${cases} |`;
  }).join('\n');

  let gapsCallout = '';
  if (gapAnalysis.length > 0) {
    const gapList = gapAnalysis.map((g: any) => `> *   \`${g.ruleId}\` - ${g.description}`).join('\n');
    gapsCallout = `> [!WARNING]
> **UNCOVERED REQUIREMENTS DETECTED (Gaps count: ${gapAnalysis.length})**
> The following target business rule lacks test case coverage:
${gapList}`;
  } else {
    gapsCallout = `> [!TIP]
> **COMPLETE COVERAGE ACHIEVED**
> All targeted business rules have been successfully tested.`;
  }

  return `# Test Coverage & Requirement Traceability Report

## 📋 Execution Metadata
| Attribute | Details |
| :--- | :--- |
| **Execution Date** | ${metadata.executionDate} |
| **Target Module** | ${metadata.targetModule} |
| **Total Targeted Rules** | ${metadata.totalTargetRules} |
| **Paired Export CSV** | ${metadata.pairedExportCsv} |

## 📊 Key Quality Metrics
> **Rule Coverage**: **${metrics.ruleCoveragePercent}%**  
> **Condition Coverage**: **${metrics.conditionCoveragePercent}%**  
> **Total Test Cases**: **${metrics.totalTestCases}**  
> **Total Test Steps**: **${metrics.totalTestSteps}**  
> **Average Steps per Case**: **${metrics.avgStepsPerCase}**

Condition Coverage measures the ratio of generated test cases to the total declared equivalence partitions and boundary points.

## 🔗 End-to-End Traceability Matrix
| Target Business Rule ID | Rule Name & Category | Connected BDD Scenario / Code Context | Technical API / Schema | Generated Test Case Title(s) |
| :--- | :--- | :--- | :--- | :--- |
${traceabilityLines}

## 🔍 Gap Analysis & Risk Audit
${gapsCallout}

## 🔄 Verification Loop History
| Field | Description / Mapped Value |
| :--- | :--- |
| **Iterations run** | ${verificationLoopHistory.iterationsRun} |
| **Termination reason** | ${verificationLoopHistory.terminationReason} |
| **Initial gap count** | ${verificationLoopHistory.initialGapCount} |
| **Final gap count** | ${verificationLoopHistory.finalGapCount} |
| **Gaps resolved** | ${verificationLoopHistory.gapsResolved} |
| **Unresolved gaps** | ${gapAnalysis.length > 0 ? gapAnalysis.map((g: any) => g.ruleId).join(', ') : 'None'} |`;
}
