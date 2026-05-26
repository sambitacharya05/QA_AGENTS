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

// SPEC-5 Wave 1: featureIndex / ruleIndex entries surfaced to module_analyzer.
export interface FeatureIndexEntry {
  id: string;
  name: string;
  area_path: string;
  rule_count: number;
  tests_edge_count: number;
}

export interface RuleIndexEntry {
  id: string;
  name: string;
  description: string;
  type: string;
  feature_id: string | null;
  tests_edge_count: number;
  implements_edge_count?: number;
  validates_edge_count?: number;
  anomaly_flags: string[];
  rule_origin: string;
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

  // SPEC-5 Wave 1: pre-allocated graph context for module_analyzer and
  // post-analyzer feature / TC sequence state.
  featureIndex?: FeatureIndexEntry[];
  ruleIndex?: RuleIndexEntry[];
  selectedFeature?: { id: string; name: string; area_path: string } | null;
  tcSequence?: Record<string, Record<string, number>>;

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
