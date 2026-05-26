import { CategoryProposal, GapLog, TestCase } from "./schemas";

// ── Graph context (internal runtime types; not LLM output schemas) ───────────
export interface BusinessRuleNode {
  id: string;
  name: string;
  category: string;
  description: string;
}

export interface ScenarioNode {
  id: string;
  type: "bdd_scenario" | "code_scenario";
  description: string;
}

export interface ApiEndpointNode {
  id: string;
  method: string;
  path: string;
  summary: string;
  responses: Record<string, { description: string; errorCode?: string }>;
}

export interface DataModelNode {
  id: string;
  schemaName: string;
  fields: Array<{ name: string; type: string; required: boolean }>;
}

export interface GraphContext {
  matchedRuleIds: string[];
  businessRules: BusinessRuleNode[];
  linkedScenarios: ScenarioNode[];
  linkedEndpoints: ApiEndpointNode[];
  linkedSchemas: DataModelNode[];
  rawSubgraphJson: string;
}

// ── Pipeline state (internal orchestrator flow state) ──────────────────────────
export interface PipelineState {
  proposal: CategoryProposal;
  confirmedCategoryIds: string[]; // Set once at user confirmation; never mutated
  graphContext: GraphContext;     // Minimal connected subgraph context
  userConstraints: Record<string, string>; // ruleId → user-supplied threshold constraint string
  iterationCount: number;         // Current loop count (increments on auto-fix)
  prevGapCount: number;           // Track gaps to detect divergence
  gapLog: GapLog | null;          // Gap report from the Verifier Checker
  testCases: TestCase[];          // Currently generated test cases array
  terminationReason?: 'success' | 'divergence' | 'ceiling' | 'user_export';
  finalIterationCount?: number;
  sessionId: string;              // Maps to active stream in activeStreams map

  // Compatibility fields
  targetModule?: string;
  activeCategoryIds?: string[];
  currentIteration?: number;
  maxIterations?: number;
  csvOutputPath?: string;
  reportOutputPath?: string;

  /**
   * Testability injection point for the HITL gate dialog.
   * When provided, `evaluateComplianceAndHalt` calls this instead of
   * `vscode.window.showInformationMessage`, allowing unit tests to simulate
   * user choices ('autofix' | 'export') without blocking on a real VS Code dialog.
   */
  hitlSelector?: () => Promise<'autofix' | 'export'>;
}
