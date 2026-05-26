import * as vscode from "vscode";
import * as fs from "fs";
import * as path from "path";
import * as crypto from "crypto";
import { McpClient } from "./mcpClient";
import {
  findGraphPath,
  loadGraph,
  calculateMetrics,
  resolveTraceabilityMatrix,
  findUntestedGaps,
  compileMarkdownReport,
  mockCompiledMarkdown,
} from "./coverageReporter";
import {
  TestCase,
  ProposedCategory,
  CategoryProposal,
  GapLog,
  ModuleAnalyzerOutput,
  CategoryProposalSchema,
  TestCasesArraySchema,
  GapLogSchema,
  ModuleAnalyzerOutputSchema,
} from "./schemas";
import {
  PipelineState,
  GraphContext,
  BusinessRuleNode,
  ScenarioNode,
  ApiEndpointNode,
  DataModelNode,
} from "./types";
import { parseAgentOutput, ParseSanitizer, coerceAnalystNumericValues } from "./agentUtils";
import { ZodSchema } from "zod";

// ---------------------------------------------------------------------------
// Generator output sanitizer — fixes Step 1 assertion violations before Zod
// ---------------------------------------------------------------------------
//
// The Generator LLM frequently writes BVA / state-transition test cases where
// Step 1 is a pure assertion ("Verify that X is Y") instead of a setup step.
// TestCasesArraySchema.superRefine detects this but throws, killing the whole
// pipeline.  This sanitizer intercepts the violation pre-Zod and fixes it by:
//   1. Inserting a clean navigation/setup step as the new Step 1
//   2. Shifting all original steps up by one (renumbering)
// For the rarer case where only Step 1's *expected* field contains assertion
// keywords, the leading assertion verb is stripped from the expected string.
//
// Runs inside parseAgentOutput before schema validation so the pipeline can
// continue.  The Verifier still audits the corrected output for quality.
//
const _STEP1_ASSERTION_RE =
  /\b(assert|verify|status[\s-]?code|responds with|returns|validates|should\s+return|expect)\b/i;

const sanitizeTestCasesStep1: ParseSanitizer = (parsed: unknown) => {
  if (!Array.isArray(parsed)) return { data: parsed, fixedCount: 0 };

  let fixedCount = 0;

  const data = parsed.map((tc: any) => {
    if (!tc || !Array.isArray(tc.steps)) return tc;

    const step1 = tc.steps.find((s: any) => s.step_number === 1);
    if (!step1) return tc;

    const actionViolation = _STEP1_ASSERTION_RE.test(step1.action ?? "");
    const expectedViolation = _STEP1_ASSERTION_RE.test(step1.expected ?? "");

    if (!actionViolation && !expectedViolation) return tc; // Already clean

    fixedCount++;

    if (actionViolation) {
      // The whole Step 1 action is an assertion — insert a proper setup step
      // as the new Step 1 and shift all original steps up by one.
      const setupStep = {
        step_number: 1,
        action:
          "Navigate to the relevant screen and establish test preconditions for this scenario.",
        expected: "The application is in the correct initial state to proceed.",
      };
      const shiftedSteps = tc.steps.map((s: any) => ({
        ...s,
        step_number: s.step_number + 1,
      }));
      return { ...tc, steps: [setupStep, ...shiftedSteps] };
    }

    // Only step1.expected contains assertion words — strip the leading verb so
    // it reads as an outcome description rather than an active assertion.
    const fixedSteps = tc.steps.map((s: any) => {
      if (s.step_number !== 1) return s;
      const cleanExpected = (s.expected as string).replace(
        /^(assert(?:\s+that)?|verify(?:\s+that)?|expect(?:\s+that)?|validates?(?:\s+that)?)\s+/i,
        "",
      );
      return { ...s, expected: cleanExpected || s.expected };
    });
    return { ...tc, steps: fixedSteps };
  });

  return { data, fixedCount };
};

/**
 * Reads a single setting directly from <workspaceRoot>/.vscode/settings.json.
 *
 * vscode.workspace.getConfiguration() is unreliable for folder-level settings
 * when the extension activates before the workspace settings are fully scoped.
 * Reading the file directly bypasses VS Code's configuration API entirely.
 */
function readVSCodeSetting<T>(
  workspaceRoot: string,
  key: string,
  defaultValue: T,
): T {
  try {
    const settingsPath = path.join(workspaceRoot, ".vscode", "settings.json");
    if (fs.existsSync(settingsPath)) {
      const raw = fs.readFileSync(settingsPath, "utf8");
      const settings = JSON.parse(raw);
      if (Object.prototype.hasOwnProperty.call(settings, key)) {
        return settings[key] as T;
      }
    }
  } catch (e) {
    console.error("[Config] Failed to read .vscode/settings.json:", e);
  }
  return defaultValue;
}

const CREATE_TESTS_HELP_TEXT =
  "## @create_tests — Available Commands\n\n" +
  "| Command | Description |\n" +
  "| :--- | :--- |\n" +
  "| `/generate [query]` | Start the full test generation pipeline. Optionally specify a query (e.g. `/generate Correlation 26`) to target specific rules. |\n" +
  "| `/status` | View all generated test artifacts (CSV files and coverage reports) in `.test_artifacts/`. |\n" +
  "| `/help` | Show this help message. |\n\n" +
  "## /generate Pipeline (7 Stages)\n\n" +
  "1. **Pre-flight** — Validates `graph.json` exists and is structurally sound\n" +
  "2. **Module Analysis** — Identifies the relevant business rules from the graph\n" +
  "3. **Semantic Linter** — Prompts for any missing numeric thresholds in rules\n" +
  "4. **Maker 1 (Analyst)** — Proposes test categories with candidate counts\n" +
  "5. **Maker 2 (Generator)** — Drafts full step-by-step test cases per category\n" +
  "6. **Checker (Verifier)** — Audits test quality against the QA rubric; auto-corrects gaps\n" +
  "7. **Export** — Writes `test_cases_<timestamp>.csv` (Azure DevOps) + `coverage_report_<timestamp>.md`\n\n" +
  "## Underlying MCP Tools\n\n" +
  "Calls a local Python FastMCP server (`Test_case_creator/engine/mcp_server.py`):\n" +
  "*   `validate_graph` — Structural integrity check on `graph.json`\n" +
  "*   `extract_subgraph` — BFS subgraph slice for targeted rule IDs\n" +
  "*   `write_azure_csv` — Serialize test cases to RFC 4180 Azure DevOps CSV\n" +
  "*   `write_coverage_report` — Write the markdown coverage report to disk\n\n" +
  "## Command Palette Actions\n\n" +
  "*   **Test Case Creator: Validate Graph** — Run graph validation without starting generation\n" +
  "*   **Test Case Creator: Status** — Check background system status\n\n" +
  "## Quick Start\n\n" +
  "1. Run `@build_context /ingest` first to generate `graph.json`\n" +
  "2. Run `/generate` to start the test generation pipeline\n" +
  "3. Confirm (or customize) the proposed test categories when prompted\n" +
  "4. Find outputs in `.test_artifacts/`";

// Active response stream registry to support concurrent requests cleanly
export const activeStreams = new Map<string, vscode.ChatResponseStream>();

// Global state reference for runtime commands (mainly command palette direct triggers)
let globalState: PipelineState = {
  proposal: { target_rules: [], rule_analysis: [], proposed_categories: [] },
  confirmedCategoryIds: [],
  graphContext: {
    matchedRuleIds: [],
    businessRules: [],
    linkedScenarios: [],
    linkedEndpoints: [],
    linkedSchemas: [],
    rawSubgraphJson: "{}",
  },
  userConstraints: {},
  iterationCount: 0,
  prevGapCount: Infinity,
  gapLog: null,
  testCases: [],
  sessionId: "global-session",
};

export function activate(context: vscode.ExtensionContext) {
  console.log("Test Case Creator Extension activated.");

  const workspaceRoot =
    vscode.workspace.workspaceFolders?.[0]?.uri.fsPath || "";

  // Pass live-config lambdas so setting changes take effect on the next tool
  // call (or next server start) without requiring an extension reload.
  // Use readVSCodeSetting() to read directly from .vscode/settings.json —
  // getConfiguration() is unreliable for folder-level settings at activation time.
  const mcpClient = new McpClient(
    workspaceRoot,
    // Live lambda: reads timeout from settings on every MCP call (no reload needed)
    () =>
      readVSCodeSetting<number>(
        workspaceRoot,
        "testCaseCreator.mcpTimeoutSeconds",
        30,
      ) * 1000,
    // Live lambda: reads engineDirectory from settings when server (re)starts
    () =>
      readVSCodeSetting<string>(
        workspaceRoot,
        "testCaseCreator.engineDirectory",
        "",
      ).trim(),
  );

  mcpClient.start().catch((err) => {
    console.error("Failed to start McpClient:", err);
  });

  // ==========================================================================
  // Command Registrations
  // ==========================================================================

  // 1. Confirm & Generate All Command (Button re-entry)
  const generateAllCommand = vscode.commands.registerCommand(
    "test_case_creator.generateAll",
    async (
      proposal: CategoryProposal,
      graphContext: GraphContext,
      userConstraints: Record<string, string>,
      sessionId: string,
    ) => {
      const stream = activeStreams.get(sessionId);
      if (!stream) {
        vscode.window.showErrorMessage(
          "Session expired. Please run /generate again.",
        );
        return;
      }

      const confirmedCategoryIds = proposal.proposed_categories.map(
        (c) => c.category_id,
      );
      const state = createInitialPipelineState(
        proposal,
        confirmedCategoryIds,
        graphContext,
        userConstraints,
        sessionId,
      );

      // Save to globalState for potential backward compatibility commands
      globalState = state;

      await runGenerationPipeline(state, stream, mcpClient, workspaceRoot);
    },
  );

  // 2. Customize Command (Button re-entry with QuickPick checklist)
  const customizeCommand = vscode.commands.registerCommand(
    "test_case_creator.customize",
    async (
      proposal: CategoryProposal,
      graphContext: GraphContext,
      userConstraints: Record<string, string>,
      sessionId: string,
    ) => {
      const stream = activeStreams.get(sessionId);
      if (!stream) {
        vscode.window.showErrorMessage(
          "Session expired. Please run /generate again.",
        );
        return;
      }

      const items = proposal.proposed_categories.map(
        (cat: ProposedCategory) => ({
          label: cat.category_name,
          description: `${cat.candidate_count} test case(s)`,
          detail: cat.description,
          picked: true, // Default checked
          id: cat.category_id,
        }),
      );

      const selection = await vscode.window.showQuickPick(items, {
        canPickMany: true,
        placeHolder: "Select the test categories you wish to create",
      });

      if (selection && selection.length > 0) {
        const confirmedCategoryIds = selection.map((item) => item.id);
        const state = createInitialPipelineState(
          proposal,
          confirmedCategoryIds,
          graphContext,
          userConstraints,
          sessionId,
        );
        globalState = state;
        await runGenerationPipeline(state, stream, mcpClient, workspaceRoot);
      }
    },
  );

  // 3. Stateful Auto-Fix Resume Command
  const autoFixCommand = vscode.commands.registerCommand(
    "test_case_creator.autoFixGaps",
    async (state?: PipelineState) => {
      const activeState = state || globalState;
      const stream = activeStreams.get(activeState.sessionId);
      if (!stream) {
        vscode.window.showErrorMessage(
          "Session expired or invalid state. Please run /generate again.",
        );
        return;
      }
      await resumeGenerationPipeline(
        activeState,
        stream,
        mcpClient,
        workspaceRoot,
      );
    },
  );

  // 4. Export anyway command (HITL Gate escape)
  const exportAnywayCommand = vscode.commands.registerCommand(
    "test_case_creator.exportAnyway",
    async (state?: PipelineState) => {
      const activeState = state || globalState;
      const stream = activeStreams.get(activeState.sessionId);
      if (!stream) {
        vscode.window.showErrorMessage(
          "Session expired or invalid state. Please run /generate again.",
        );
        return;
      }
      activeState.terminationReason = "user_export";
      await proceedToExport(activeState, stream, mcpClient, workspaceRoot);
    },
  );

  // 5. Retro-compatibility / Palette Commands
  const paletteGenerateCommand = vscode.commands.registerCommand(
    "testCaseCreator.generate",
    async () => {
      vscode.window.showInformationMessage(
        "Please run `/generate` inside the Copilot Chat panel to initiate the Test Case Creator Agent.",
      );
    },
  );

  const paletteStatusCommand = vscode.commands.registerCommand(
    "testCaseCreator.status",
    () => {
      vscode.window.showInformationMessage(
        "Test Case Creator: All background systems are active.",
      );
    },
  );

  const paletteAutoFixCommand = vscode.commands.registerCommand(
    "testCaseCreator.autoFixGaps",
    async () => {
      await vscode.commands.executeCommand(
        "test_case_creator.autoFixGaps",
        globalState,
      );
    },
  );

  const paletteExportAnywayCommand = vscode.commands.registerCommand(
    "testCaseCreator.exportBestEffort",
    async () => {
      await vscode.commands.executeCommand(
        "test_case_creator.exportAnyway",
        globalState,
      );
    },
  );

  // 6. Standalone Graph Validation Command
  // Runs validate_graph MCP tool and shows results in a VS Code notification.
  const validateGraphCommand = vscode.commands.registerCommand(
    "testCaseCreator.validateGraph",
    async () => {
      const graphPath = findGraphPath(workspaceRoot);
      if (!graphPath || !fs.existsSync(graphPath)) {
        vscode.window.showErrorMessage(
          "Test Case Creator: graph.json not found. " +
            "Run the Context Builder first to generate it.",
        );
        return;
      }

      vscode.window.withProgress(
        {
          location: vscode.ProgressLocation.Notification,
          title: "Validating graph.json…",
          cancellable: false,
        },
        async () => {
          try {
            const result = await mcpClient.callTool("validate_graph", {
              graph_path: graphPath,
            });

            if (result.is_valid) {
              const stats = result.stats || {};
              const detail =
                `${stats.total_nodes ?? "?"} nodes · ` +
                `${stats.business_rule_count ?? "?"} business rules · ` +
                `${stats.edge_count ?? "?"} edges`;
              const warnCount = (result.warnings || []).length;
              const warnSuffix =
                warnCount > 0
                  ? ` (${warnCount} warning${warnCount > 1 ? "s" : ""})`
                  : "";
              vscode.window.showInformationMessage(
                `✅ graph.json is valid${warnSuffix}: ${detail}`,
              );
              if (warnCount > 0) {
                for (const w of result.warnings) {
                  vscode.window.showWarningMessage(`Test Case Creator: ${w}`);
                }
              }
            } else {
              const firstError =
                (result.errors || [])[0] || "Unknown structural error.";
              vscode.window.showErrorMessage(
                `❌ graph.json has ${result.errors.length} error(s): ${firstError}`,
              );
            }
          } catch (err) {
            vscode.window.showErrorMessage(
              `Test Case Creator: Graph validation failed — ${err instanceof Error ? err.message : String(err)}`,
            );
          }
        },
      );
    },
  );

  // Chat Participant `@create_tests` integration
  const chatParticipant = vscode.chat.createChatParticipant(
    "create_tests",
    async (request, context, response, token) => {
      if (request.command === "help") {
        response.markdown(CREATE_TESTS_HELP_TEXT);
        return;
      }

      if (request.command === "status") {
        const artifactsDir = path.join(workspaceRoot, ".test_artifacts");
        if (!fs.existsSync(artifactsDir)) {
          response.markdown(
            "> ⚠️ No `.test_artifacts` directory found. Run `/generate` first.\n",
          );
          return;
        }

        const files = fs
          .readdirSync(artifactsDir)
          .filter((f) => f.endsWith(".csv") || f.endsWith(".md"))
          .sort()
          .reverse(); // Most recent first

        if (files.length === 0) {
          response.markdown(
            "> ⚠️ `.test_artifacts` exists but contains no generated files.\n",
          );
          return;
        }

        response.markdown("### 📂 Generated Test Artifacts\n\n");
        response.markdown("| File | Type | Size |\n| :--- | :--- | :--- |\n");
        for (const file of files) {
          const fullPath = path.join(artifactsDir, file);
          const stats = fs.statSync(fullPath);
          const type = file.endsWith(".csv")
            ? "Azure DevOps CSV"
            : "Coverage Report";
          const sizeKb = (stats.size / 1024).toFixed(1);
          response.markdown(`| \`${file}\` | ${type} | ${sizeKb} KB |\n`);
        }
        return;
      }

      if (request.command === "generate") {
        const sessionId = crypto.randomUUID();
        activeStreams.set(sessionId, response);

        // Step 0a: Pre-flight Guard Check
        const graphPath = findGraphPath(workspaceRoot);
        if (!graphPath || !fs.existsSync(graphPath)) {
          response.markdown(
            `> ❌ **Context graph not found**\n` +
              `> Expected standard path e.g.: \`ingest/.context_builder/graph.json\`\n\n` +
              `> Please run the **Context Builder** agent first to generate \`graph.json\` before running \`/generate\`.\n`,
          );
          activeStreams.delete(sessionId);
          return;
        }

        let graphContent: any;
        try {
          graphContent = JSON.parse(fs.readFileSync(graphPath, "utf8"));
        } catch {
          response.markdown(
            `> ❌ **Invalid graph.json**: File exists but could not be parsed. Re-run \`@build_context /ingest\`.\n`,
          );
          activeStreams.delete(sessionId);
          return;
        }

        // Step 0a-ii: Structural graph validation (controlled by validateGraphOnGenerate setting)
        const shouldValidate = vscode.workspace
          .getConfiguration("testCaseCreator")
          .get("validateGraphOnGenerate", true);
        if (shouldValidate) {
          response.markdown("🔍 **Validating graph structure...**\n");
          try {
            const validationResult = await mcpClient.callTool(
              "validate_graph",
              { graph_path: graphPath },
            );

            if (!validationResult.is_valid) {
              response.markdown(
                `> ❌ **graph.json structural validation failed** — pipeline halted.\n\n`,
              );
              for (const err of validationResult.errors || []) {
                response.markdown(`> - ${err}\n`);
              }
              response.markdown(
                `\n> Run \`@validate_graph\` from the command palette for details, ` +
                  `or re-run the **\`@build_context /ingest\`** to regenerate the graph after removing ingest/.context_builder directory.\n`,
              );
              activeStreams.delete(sessionId);
              return;
            }

            // Non-blocking warnings: show as advisories and continue
            for (const warn of validationResult.warnings || []) {
              response.markdown(`> ⚠️ *Graph advisory*: ${warn}\n`);
            }

            const s = validationResult.stats || {};
            response.markdown(
              `> ✅ Graph valid — ` +
                `${s.total_nodes ?? "?"} nodes, ` +
                `${s.business_rule_count ?? "?"} business rules, ` +
                `${s.edge_count ?? "?"} edges.\n\n`,
            );
          } catch (err) {
            // Validation tool failure is non-fatal: warn and continue so existing
            // workflows are not broken if the MCP server is temporarily unavailable.
            response.markdown(
              `> ⚠️ **Graph validation skipped** (MCP tool error: ` +
                `${err instanceof Error ? err.message : String(err)}). Proceeding anyway.\n\n`,
            );
          }
        }

        // Wrap the entire pipeline in try/catch so any LLM or parse failure
        // cleans up the activeStreams entry rather than leaking the session.
        try {
          response.markdown("**Loading semantic requirements...**\n");

          // Step 0b: Module Analyzer Call
          const flatRules = buildRuleIndex(graphContent);
          const rawAnalyzerOutput = await callLanguageModel(
            "module_analyzer.agent.md",
            {
              userQuery: request.prompt || "all rules",
              ruleIndex: flatRules,
            },
            response,
          );

          const analyzerOutput = await parseAgentOutput(
            rawAnalyzerOutput,
            ModuleAnalyzerOutputSchema,
            "Module Analyzer",
            response,
          );

          // Resolve the wildcard sentinel that the Module Analyzer returns for generic
          // "all rules" queries (matched_rule_ids: ["*"]).  Passing "*" verbatim to
          // extract_subgraph causes a silent BFS miss — the Python BFS tries to locate
          // a node whose id is literally the string "*", finds nothing, and returns an
          // empty subgraph_nodes dict.  The same wildcard breaks the semantic linter
          // filter below.  Substituting the full rule-ID list here, once, fixes both.
          const resolvedRuleIds: string[] =
            analyzerOutput.matched_rule_ids.includes("*")
              ? flatRules.map((r) => r.id).filter(Boolean)
              : analyzerOutput.matched_rule_ids;

          if (analyzerOutput.matched_rule_ids.includes("*")) {
            response.markdown(
              `> Generic query detected — targeting all **${resolvedRuleIds.length}** business rules.\n\n`,
            );
          }

          // Step 0c: Semantic Threshold Linter
          const userConstraints: Record<string, string> = {};
          // Unwrap the same envelope as buildRuleIndex — payload.nodes is an array.
          const _rawLinterNodes: unknown =
            graphContent.payload?.nodes ?? graphContent.nodes ?? [];
          const _linterNodesArray: any[] = Array.isArray(_rawLinterNodes)
            ? _rawLinterNodes
            : Object.values(_rawLinterNodes as Record<string, unknown>);
          const targetedRules = _linterNodesArray.filter(
            (node: any) =>
              node &&
              node.type === "business_rule" &&
              resolvedRuleIds.includes(node.id || node.rule_id),
          );

          for (const rule of targetedRules as any[]) {
            const desc = (rule.description || "").toLowerCase();
            const impliesLimits =
              desc.includes("limit") ||
              desc.includes("threshold") ||
              desc.includes("exceed") ||
              desc.includes("above") ||
              desc.includes("below");
            const hasDigits = /\b\d+\b/.test(desc);

            if (impliesLimits && !hasDigits) {
              const ruleId = rule.id || rule.rule_id;
              const ruleName = rule.name || rule.rule_name || ruleId;
              const userInput = await vscode.window.showInputBox({
                prompt: `Rule "${ruleName}" (${ruleId}) implies numeric limits but lacks thresholds. Please specify:`,
                placeHolder: "e.g., 60 days, $500 deductible, age 18",
              });
              if (userInput) {
                userConstraints[ruleId] = userInput;
              }
            }
          }

          // Step 1: Subgraph Extraction & GraphContext Construction
          response.markdown(
            "⚡ **Extracting minimal context subgraph via FastMCP...**\n",
          );
          const graphContext = await extractGraphContext(
            graphPath,
            resolvedRuleIds,
            mcpClient,
          );

          // Step 2: Maker-1 Category Proposal
          response.markdown("**Designing test scenarios** ...\n");
          const constraintBlock = buildConstraintBlock(userConstraints);
          const rawProposal = await callLanguageModel(
            "test_case_analyst.agent.md",
            {
              businessRules: graphContext.businessRules,
              userConstraints: constraintBlock,
            },
            response,
          );

          const proposal = await parseAgentOutput(
            rawProposal,
            CategoryProposalSchema,
            "Test Case Analyst",
            response,
            coerceAnalystNumericValues,
          );

          // Render proposed categories table in chat response
          response.markdown("\n### 📋 Proposed Test Categories\n\n");
          response.markdown(
            "| Select | Category | category_id | Test Count |\n",
          );
          response.markdown("| :--- | :--- | :--- | :--- |\n");
          for (const cat of proposal.proposed_categories) {
            response.markdown(
              `| Proposed | **${cat.category_name}** | ${cat.category_id} | ${cat.candidate_count} |\n`,
            );
          }
          response.markdown(
            "\nWould you like to proceed with generating the detailed test steps for all categories, or customize your selection?\n\n",
          );

          // Ask the user what to do with the proposed categories. We do this inline
          // (before the handler returns) so the response stream stays alive for all
          // subsequent pipeline output. Using response.button() + a stored stream
          // reference does NOT work: VS Code seals the ChatResponseStream the moment
          // the handler function returns, so any stream.markdown() call from a later
          // command handler is silently discarded.
          const userChoice = await vscode.window.showInformationMessage(
            "Proceed with all proposed categories, or customize your selection?",
            { modal: true },
            "Confirm & Generate All",
            "Customize Selection",
          );

          let confirmedCategoryIds: string[];

          if (userChoice === "Customize Selection") {
            // Build a label → category_id map so we never need a non-standard `id`
            // property on QuickPickItem (VS Code may return new objects on selection,
            // stripping any non-standard properties and leaving item.id undefined).
            const labelToId = new Map<string, string>(
              proposal.proposed_categories.map((c) => [
                c.category_name,
                c.category_id,
              ]),
            );
            const picks = proposal.proposed_categories.map((cat) => ({
              label: cat.category_name,
              description: `${cat.candidate_count} test case(s)`,
              detail: cat.description,
              picked: true,
            }));
            const selection = await vscode.window.showQuickPick(picks, {
              canPickMany: true,
              placeHolder: "Select the test categories you wish to create",
            });
            if (!selection || selection.length === 0) {
              response.markdown(
                "> ⚠️ No categories selected. Run `/generate` again to restart.\n",
              );
              activeStreams.delete(sessionId);
              return;
            }
            confirmedCategoryIds = selection
              .map((item) => labelToId.get(item.label))
              .filter((id): id is string => id !== undefined);
          } else {
            // 'Confirm & Generate All' or dismissed (ESC) — run full suite
            confirmedCategoryIds = proposal.proposed_categories.map(
              (c) => c.category_id,
            );
          }

          const state = createInitialPipelineState(
            proposal,
            confirmedCategoryIds,
            graphContext,
            userConstraints,
            sessionId,
          );
          globalState = state;
          await runGenerationPipeline(
            state,
            response,
            mcpClient,
            workspaceRoot,
          );
        } catch (err) {
          response.markdown(
            `\n> ❌ **Pipeline Error**: ${err instanceof Error ? err.message : String(err)}\n`,
          );
          activeStreams.delete(sessionId);
        }
        return;
      }

      response.markdown(
        "Please use `/generate` to run test case designs or `/status` to view generated artifacts.\n",
      );
    },
  );

  context.subscriptions.push(chatParticipant);
  context.subscriptions.push(
    generateAllCommand,
    customizeCommand,
    autoFixCommand,
    exportAnywayCommand,
    paletteGenerateCommand,
    paletteStatusCommand,
    paletteAutoFixCommand,
    paletteExportAnywayCommand,
    validateGraphCommand,
  );

  context.subscriptions.push({
    dispose: () => {
      mcpClient.stop();
    },
  });
}

export function deactivate() {}

// ==========================================================================
// Helper Functions & Pipeline Orchestration Loop
// ==========================================================================

export function createInitialPipelineState(
  proposal: CategoryProposal,
  confirmedCategoryIds: string[],
  graphContext: GraphContext,
  userConstraints: Record<string, string>,
  sessionId: string,
): PipelineState {
  return {
    proposal,
    confirmedCategoryIds,
    graphContext,
    userConstraints,
    iterationCount: 0,
    prevGapCount: Infinity,
    gapLog: null,
    testCases: [],
    sessionId,
    // SPEC-5 Wave 1 (gap B7): replaces hardcoded "Claims" default. The real
    // value is set after module_analyzer completes; "<unresolved>" makes any
    // unresolved propagation visible in downstream reports.
    targetModule: "<unresolved>",
  };
}

export async function runGenerationPipeline(
  state: PipelineState,
  stream: vscode.ChatResponseStream,
  mcpClient: McpClient,
  workspaceRoot: string,
) {
  try {
    // Step 2b: Run Generator
    const testCases = await runGenerator(state, stream);

    // Step 3: Run Verifier Checker
    const verifierResult = await runVerifier(
      testCases,
      state.graphContext,
      state.userConstraints,
      state.proposal,
      stream,
    );

    // Step 4: Compliance evaluation
    await evaluateComplianceAndHalt(
      state,
      testCases,
      verifierResult,
      stream,
      mcpClient,
      workspaceRoot,
    );
  } catch (err) {
    stream.markdown(
      `\n> ❌ **Orchestration Pipeline Error**: ${err instanceof Error ? err.message : String(err)}\n`,
    );
    activeStreams.delete(state.sessionId);
  }
}

export async function resumeGenerationPipeline(
  state: PipelineState,
  stream: vscode.ChatResponseStream,
  mcpClient: McpClient,
  workspaceRoot: string,
) {
  try {
    // Step 2a (Correction): Run Analyst Correction
    const revisedProposal = await runAnalystCorrection(state, stream);

    // Step 2b: Run Generator with Revised Proposal
    const testCases = await runGeneratorWithRevisedProposal(
      state,
      revisedProposal,
      stream,
    );

    // Step 3: Run Verifier
    const verifierResult = await runVerifier(
      testCases,
      state.graphContext,
      state.userConstraints,
      state.proposal,
      stream,
    );

    // Step 4: Compliance evaluation
    await evaluateComplianceAndHalt(
      state,
      testCases,
      verifierResult,
      stream,
      mcpClient,
      workspaceRoot,
    );
  } catch (err) {
    stream.markdown(
      `\n> ❌ **Orchestration Correction Loop Error**: ${err instanceof Error ? err.message : String(err)}\n`,
    );
    activeStreams.delete(state.sessionId);
  }
}

export async function runGenerator(
  state: PipelineState,
  stream: vscode.ChatResponseStream,
): Promise<TestCase[]> {
  stream.markdown(`\n**Drafting test steps** ...\n`);

  // Ingest immutable confirmed category Scope Lock block
  const scopeLock = `\n[SCOPE LOCK — IMMUTABLE]\nYou are operating in test generation mode. You MUST only generate detailed step-by-step test cases for these confirmed categories:\n${state.confirmedCategoryIds.join(", ")}\nDo NOT generate tests for any category outside this list.\n`;

  // Build constraints block
  const constraintBlock = buildConstraintBlock(state.userConstraints);

  // Filter proposal proposed_categories to match only confirmed ones
  const filteredProposal = {
    ...state.proposal,
    proposed_categories: state.proposal.proposed_categories.filter((c) =>
      state.confirmedCategoryIds.includes(c.category_id),
    ),
  };

  const rawOutput = await callLanguageModel(
    "test_generator.agent.md",
    {
      proposal: filteredProposal,
      subgraph: state.graphContext.rawSubgraphJson,
      scopeLock,
      constraints: constraintBlock,
    },
    stream,
  );

  return await parseAgentOutput(
    rawOutput,
    TestCasesArraySchema,
    "Test Case Generator",
    stream,
    sanitizeTestCasesStep1,
  );
}

export async function runVerifier(
  testCases: TestCase[],
  graphContext: GraphContext,
  userConstraints: Record<string, string>,
  proposal: CategoryProposal,
  stream: vscode.ChatResponseStream,
): Promise<GapLog> {
  stream.markdown(
    `\n **Test Verifier** is auditing generated test cases ...\n`,
  );

  const areaPath = vscode.workspace?.getConfiguration
    ? vscode.workspace
        .getConfiguration("testCaseCreator")
        .get("areaPath", "TechInsurance\\Claims")
    : "TechInsurance\\Claims";
  const constraintBlock = buildConstraintBlock(userConstraints);

  const rawOutput = await callLanguageModel(
    "test_verifier.agent.md",
    {
      testCases: testCases,
      subgraph: graphContext.rawSubgraphJson,
      areaPath,
      constraints: constraintBlock,
      proposal,   // Provides rule_analysis for Points 0 and 1 of the 7-point rubric
    },
    stream,
  );

  return await parseAgentOutput(
    rawOutput,
    GapLogSchema,
    "Test Verifier",
    stream,
  );
}

export async function runAnalystCorrection(
  state: PipelineState,
  stream: vscode.ChatResponseStream,
): Promise<CategoryProposal> {
  stream.markdown(
    `\n🔄 **Revisiting test scenarios in **Correction-Mode** (Iteration ${state.iterationCount}) to fix gaps...\n`,
  );

  // Force strict category scope lock
  const scopeLock = `\n[SCOPE LOCK — IMMUTABLE]\nYou are operating in gap-correction mode (iteration ${state.iterationCount}). You MUST only design or refine test scenarios within these confirmed categories:\n${state.confirmedCategoryIds.join(", ")}\nDo NOT propose, add, or reference any category outside this list.\n`;

  const constraintBlock = buildConstraintBlock(state.userConstraints);

  const rawOutput = await callLanguageModel(
    "test_case_analyst.agent.md",
    {
      proposal: state.proposal,
      gapLog: state.gapLog,
      scopeLock,
      constraints: constraintBlock,
      businessRules: state.graphContext.businessRules,
    },
    stream,
  );

  const parsedResult = await parseAgentOutput(
    rawOutput,
    CategoryProposalSchema,
    "Test Case Analyst (Correction)",
    stream,
    coerceAnalystNumericValues,
  );

  // Invariant Guard: programmatically strip categories outside state.confirmedCategoryIds
  parsedResult.proposed_categories = parsedResult.proposed_categories.filter(
    (c) => state.confirmedCategoryIds.includes(c.category_id),
  );

  return parsedResult;
}

export async function runGeneratorWithRevisedProposal(
  state: PipelineState,
  revisedProposal: CategoryProposal,
  stream: vscode.ChatResponseStream,
): Promise<TestCase[]> {
  stream.markdown(
    `\n🔄 **Recreating test steps in **Correction-Mode** (Iteration ${state.iterationCount}) to fix gaps** ...\n`,
  );

  const scopeLock = `\n[SCOPE LOCK — IMMUTABLE]\nYou are operating in test generation mode. You MUST only generate detailed step-by-step test cases for these confirmed categories:\n${state.confirmedCategoryIds.join(", ")}\nDo NOT generate tests for any category outside this list.\n`;
  const constraintBlock = buildConstraintBlock(state.userConstraints);

  const rawOutput = await callLanguageModel(
    "test_generator.agent.md",
    {
      proposal: revisedProposal,
      subgraph: state.graphContext.rawSubgraphJson,
      scopeLock,
      constraints: constraintBlock,
    },
    stream,
  );

  return await parseAgentOutput(
    rawOutput,
    TestCasesArraySchema,
    "Test Case Generator",
    stream,
    sanitizeTestCasesStep1,
  );
}

export async function evaluateComplianceAndHalt(
  state: PipelineState,
  testCases: TestCase[],
  verifierResult: GapLog,
  stream: vscode.ChatResponseStream,
  mcpClient: McpClient,
  workspaceRoot: string,
) {
  if (verifierResult.is_compliant) {
    state.terminationReason = "success";
    state.testCases = testCases;
    state.gapLog = verifierResult;
    stream.markdown(
      `\n🎉 **Verification complete**: Generated test cases are 100% compliant with the quality rubric!\n`,
    );
    await proceedToExport(state, stream, mcpClient, workspaceRoot);
    return;
  }

  const currentGapCount = verifierResult.detected_gaps.length;
  stream.markdown(
    `\n⚠️ **Checker found ${currentGapCount} quality gaps/anomalies** in the generated test cases.\n`,
  );

  // Divergence check
  if (currentGapCount >= state.prevGapCount) {
    state.terminationReason = "divergence";
    state.testCases = testCases; // Save best-effort tests
    stream.markdown(
      `\n> ⚠️ **Divergence Detected**: Gap count did not decrease (${state.prevGapCount} → ${currentGapCount}). ` +
        `Stopping correction to prevent token burn. Exporting best-effort outputs.\n`,
    );
    await proceedToExport(state, stream, mcpClient, workspaceRoot);
    return;
  }

  // Update state counts
  state.prevGapCount = currentGapCount;
  state.gapLog = verifierResult;
  state.testCases = testCases;
  state.iterationCount++; // Prepared for the next iteration run on re-entry

  let maxIterations = 3;
  if (state.maxIterations !== undefined) {
    maxIterations = state.maxIterations;
  } else if (
    typeof vscode !== "undefined" &&
    vscode.workspace &&
    vscode.workspace.getConfiguration
  ) {
    maxIterations = vscode.workspace
      .getConfiguration("testCaseCreator")
      .get("maxCorrectionIterations", 3);
  }

  if (state.iterationCount >= maxIterations) {
    state.terminationReason = "ceiling";
    stream.markdown(
      `\n Reached maximum auto-correction iterations (${maxIterations}). Stopping loop.\n`,
    );
    await proceedToExport(state, stream, mcpClient, workspaceRoot);
    return;
  }

  // Render gap table into the stream while it is still alive (we are still inside
  // the chat handler's async call stack at this point).
  // currentGapCount is already declared above; reuse it here.
  stream.markdown(
    `### ⚠️ ${currentGapCount} Quality Gap(s) Detected (Iteration ${state.iterationCount})\n\n`,
  );
  stream.markdown(
    `| Severity | Gap | Target Rule ID |\n| :--- | :--- | :--- |\n`,
  );
  for (const gap of verifierResult.detected_gaps) {
    stream.markdown(
      `| ${gap.severity} | ${gap.description} | \`${gap.target_rule_id}\` |\n`,
    );
  }
  stream.markdown(
    "\nWould you like to auto-correct these gaps or export the best-effort test cases as-is?\n\n",
  );

  const hitlChoice = await vscode.window.showInformationMessage(
    `${currentGapCount} quality gap(s) detected. Auto-fix or export as-is?`,
    { modal: true },
    "Auto-fix Gaps → Re-run",
    "Export As-Is (gaps logged in report)",
  );

  if (hitlChoice === "Auto-fix Gaps → Re-run") {
    await resumeGenerationPipeline(state, stream, mcpClient, workspaceRoot);
  } else {
    state.terminationReason = "user_export";
    await proceedToExport(state, stream, mcpClient, workspaceRoot);
  }
}

/**
 * @deprecated No longer called by the pipeline. The HITL gate is now resolved
 * inline inside `evaluateComplianceAndHalt` using `defaultHitlSelector` (or an
 * injected `state.hitlSelector`). Kept to avoid breaking any external callers.
 */
export async function triggerHitlGate(
  state: PipelineState,
  verifierResult: GapLog,
  stream: vscode.ChatResponseStream,
): Promise<void> {
  const currentGapCount = verifierResult.detected_gaps.length;
  stream.markdown(
    `### ⚠️ ${currentGapCount} Quality Gap(s) Detected (Iteration ${state.iterationCount})\n\n`,
  );
  stream.markdown(
    `| Severity | Gap | Target Rule ID |\n| :--- | :--- | :--- |\n`,
  );
  for (const gap of verifierResult.detected_gaps) {
    stream.markdown(
      `| ${gap.severity} | ${gap.description} | \`${gap.target_rule_id}\` |\n`,
    );
  }
  stream.markdown(
    "\nWould you like to auto-correct these gaps or export the best-effort test cases as-is?\n\n",
  );

  if (stream.button) {
    stream.button({
      command: "test_case_creator.autoFixGaps",
      title: "Auto-fix Gaps → Re-run",
      arguments: [state],
    });
    stream.button({
      command: "test_case_creator.exportAnyway",
      title: "Export As-Is (gaps logged in report)",
      arguments: [state],
    });
  } else {
    stream.markdown(
      `\n*(Interactive buttons unavailable. Triggering command palette for auto-fix or best-effort export)*\n`,
    );
  }
}

export async function proceedToExport(
  state: PipelineState,
  stream: vscode.ChatResponseStream,
  mcpClient: McpClient,
  workspaceRoot: string,
) {
  stream.markdown("\n **Compiling final test cases and coverage report...**\n");

  // Load graph data so the programmatic reporter can compute structured metrics.
  const graphPath = findGraphPath(workspaceRoot);
  const graphData = graphPath
    ? loadGraph(graphPath)
    : { nodes: [], links: [], edges: [] };

  // Compute structured coverage metrics using the programmatic reporter functions.
  // These replace the previous approach of passing raw state JSON to the LLM and
  // hoping it inferred the numbers correctly.
  const targetRuleIds = state.proposal.target_rules;
  const proposedCategories = state.proposal.proposed_categories;
  const metrics = calculateMetrics(
    targetRuleIds,
    proposedCategories,
    state.testCases,
  );
  const traceabilityMatrix = resolveTraceabilityMatrix(
    graphData,
    targetRuleIds,
    state.testCases,
  );
  const gapAnalysis = findUntestedGaps(
    targetRuleIds,
    state.testCases,
    graphData,
  );

  // Compute Coordinated UTC Timestamp: YYYYMMDD_HHMMSSz to match tests perfectly
  const now = new Date();
  const timestamp =
    now
      .toISOString()
      .replace(/T/, "_")
      .replace(/\..+/, "")
      .replace(/:/g, "")
      .replace(/-/g, "") + "z";

  const csvFilename = `test_cases_${timestamp}.csv`;
  const mdFilename = `coverage_report_${timestamp}.md`;

  const artifactsDir = path.join(workspaceRoot, ".test_artifacts");
  if (!fs.existsSync(artifactsDir)) {
    fs.mkdirSync(artifactsDir, { recursive: true });
  }

  const csvPath = path.join(artifactsDir, csvFilename);
  const reportPath = path.join(artifactsDir, mdFilename);

  state.csvOutputPath = csvPath;
  state.reportOutputPath = reportPath;

  const areaPath = vscode.workspace?.getConfiguration
    ? vscode.workspace
        .getConfiguration("testCaseCreator")
        .get("areaPath", "TechInsurance\\Claims")
    : "TechInsurance\\Claims";

  // Load coverage reporter agent prompt for LLM-assisted report generation.
  let promptPath = path.join(__dirname, "agents", "coverage_reporter.agent.md");
  if (!fs.existsSync(promptPath)) {
    promptPath = path.join(
      __dirname,
      "..",
      "src",
      "agents",
      "coverage_reporter.agent.md",
    );
  }
  const agentPrompt = fs.existsSync(promptPath)
    ? fs.readFileSync(promptPath, "utf8")
    : "";

  const initialGapCount =
    state.prevGapCount === Infinity ? 0 : state.prevGapCount;
  const finalGapCount = state.gapLog?.detected_gaps?.length ?? 0;

  // Build the structured payload that compileMarkdownReport (and its mock fallback) expects.
  const reportPayload = {
    metadata: {
      executionDate: now.toISOString(),
      // SPEC-5 Wave 1 (gap B7): no more silent "Claims" mislabel — surface
      // unresolved state explicitly.
      targetModule: state.targetModule ?? "<unresolved>",
      totalTargetRules: targetRuleIds.length,
      pairedExportCsv: csvFilename,
    },
    metrics,
    traceabilityMatrix,
    gapAnalysis,
    verificationLoopHistory: {
      iterationsRun: state.iterationCount,
      terminationReason: state.terminationReason || "success",
      initialGapCount,
      finalGapCount,
      gapsResolved: Math.max(0, initialGapCount - finalGapCount),
    },
  };

  // Generate the coverage report: LLM-assisted with programmatic fallback.
  let rawCoverageReportMarkdown: string;
  try {
    rawCoverageReportMarkdown = await compileMarkdownReport(
      agentPrompt,
      reportPayload,
    );
  } catch (err) {
    stream.markdown(
      `> ⚠️ **Coverage report LLM call failed** ` +
        `(${err instanceof Error ? err.message : String(err)}) — using computed fallback report.\n`,
    );
    rawCoverageReportMarkdown = mockCompiledMarkdown(reportPayload);
  }

  let csvWritten = false;
  let mdWritten = false;

  try {
    await mcpClient.callTool("write_azure_csv", {
      test_cases_json: JSON.stringify(state.testCases),
      output_path: csvPath,
      area_path: areaPath,
    });
    csvWritten = true;
  } catch (err) {
    stream.markdown(
      `> ❌ **CSV export failed**: ${err instanceof Error ? err.message : String(err)}\n`,
    );
  }

  try {
    await mcpClient.callTool("write_coverage_report", {
      markdown_content: rawCoverageReportMarkdown,
      output_path: reportPath,
    });
    mdWritten = true;
  } catch (err) {
    stream.markdown(
      `> ❌ **Coverage report export failed**: ${err instanceof Error ? err.message : String(err)}\n`,
    );
  }

  activeStreams.delete(state.sessionId);

  if (!csvWritten && !mdWritten) {
    stream.markdown(
      `> ❌ **Export failed entirely. Please check the Python MCP server process.**\n`,
    );
    return;
  }

  if (
    state.terminationReason === "divergence" ||
    state.terminationReason === "ceiling"
  ) {
    stream.markdown(`### ⚠️ Exported with Gaps\n`);
  } else {
    stream.markdown(`### 🎉 Export Completed Successfully\n`);
  }

  stream.markdown(
    `- **Azure CSV File**: [${csvFilename}](file://${csvPath})\n` +
      `- **Coverage Report**: [${mdFilename}](file://${reportPath})\n`,
  );
}

// ==========================================================================
// Language Model & Prompt Handlers
// ==========================================================================

export async function callLanguageModel(
  agentFile: string,
  contextObject: any,
  stream: vscode.ChatResponseStream,
): Promise<string> {
  let promptPath = path.join(__dirname, "agents", agentFile);
  if (!fs.existsSync(promptPath)) {
    promptPath = path.join(__dirname, "..", "src", "agents", agentFile);
  }
  const agentPrompt = fs.existsSync(promptPath)
    ? fs.readFileSync(promptPath, "utf8")
    : "System prompt placeholder";

  // ── Dev-mode escape hatch ────────────────────────────────────────────────────
  // Set  PIPELINE_MOCK_MODE=true  in your VS Code launch.json (env section) to
  // run the full pipeline without a real LLM connection, e.g. for UI layout
  // work, demos, or offline development.
  //
  // This must be an EXPLICIT opt-in — it is never triggered automatically.
  // The mock data lives in getMockAgentOutput() below and represents realistic
  // pipeline state for each agent so the full UI flow can be exercised.
  if (process.env.PIPELINE_MOCK_MODE === "true") {
    const mockOutput = getMockAgentOutput(
      agentFile,
      null as any,
      agentFile,
      globalState,
    );
    return JSON.stringify(mockOutput);
  }

  // Hard-fail when the VS Code Language Model API is absent.
  // Never silently fall back to mock data — that hides real availability
  // failures and lets users unknowingly export hardcoded test cases instead
  // of real LLM-generated ones.
  if (typeof vscode === "undefined" || !vscode.lm?.selectChatModels) {
    throw new Error(
      "VS Code Language Model API is unavailable. " +
        "Ensure you are running inside VS Code ≥ 1.90 with GitHub Copilot installed.",
    );
  }

  try {
    // Select gpt-5.4-mini per target instructions
    const found = await vscode.lm.selectChatModels({
      vendor: "copilot",
      family: "gpt-5.4-mini",
    });
    let model = found[0];
    if (found.length === 0) {
      const fallback = await vscode.lm.selectChatModels({ vendor: "copilot" });
      if (fallback.length === 0) {
        throw new Error(`No Copilot models available for ${agentFile}`);
      }
      model = fallback[0];
    }

    const messages = [
      vscode.LanguageModelChatMessage.User(agentPrompt),
      vscode.LanguageModelChatMessage.User(JSON.stringify(contextObject)),
    ];

    // Dispose the CancellationTokenSource when the request completes so it
    // does not leak the underlying timer/listener that VS Code registers.
    const cts = new vscode.CancellationTokenSource();
    try {
      const llmResponse = await model.sendRequest(messages, {}, cts.token);
      let text = "";
      let lastDotTime = Date.now();
      let dotsEmitted = 0;

      for await (const part of llmResponse.stream) {
        if (part instanceof vscode.LanguageModelTextPart) {
          text += part.value;
          // Emit a progress dot approximately every 1.5 s to show live activity.
          // This provides real-time feedback during long JSON-generation passes
          // without displaying raw token fragments in the chat panel.
          if (Date.now() - lastDotTime >= 1500) {
            stream.markdown("·");
            lastDotTime = Date.now();
            dotsEmitted++;
          }
        }
      }

      // Close the dot-progress line with a checkmark so the UI doesn't run on.
      if (dotsEmitted > 0) {
        stream.markdown(" ✓\n");
      }

      return text.trim();
    } finally {
      cts.dispose();
    }
  } catch (err) {
    // Re-throw so the pipeline handler surfaces the error in the chat panel.
    // Never silently fall back to mock data — that hides real LLM failures.
    const msg = err instanceof Error ? err.message : String(err);
    throw new Error(`[${agentFile}] LLM invocation failed: ${msg}`);
  }
}

export function buildRuleIndex(
  graphContent: any,
): Array<{ id: string; name: string }> {
  // graph.json is written by Context Builder as:
  //   { schema_version, generator, payload: { nodes: [...], edges: [...] } }
  // Nodes live under `payload.nodes` (an array), NOT at the top-level `nodes` key.
  // Fall back to a bare `nodes` key so hand-authored graphs still work.
  const rawNodes: unknown =
    graphContent.payload?.nodes ?? graphContent.nodes ?? [];
  const nodesArray: any[] = Array.isArray(rawNodes)
    ? rawNodes
    : Object.values(rawNodes as Record<string, unknown>);
  return nodesArray
    .filter((n: any) => n && n.type === "business_rule")
    .map((n: any) => ({
      id: n.id || n.rule_id,
      name: n.name || n.rule_name || n.id || n.rule_id,
    }));
}

export function buildConstraintBlock(
  userConstraints: Record<string, string>,
): string {
  if (Object.keys(userConstraints).length === 0) return "";
  const lines = Object.entries(userConstraints).map(
    ([ruleId, threshold]) => `- Rule \`${ruleId}\`: ${threshold}`,
  );
  return `\n\n[USER-SUPPLIED CONSTRAINT THRESHOLDS — treat as authoritative]\n${lines.join("\n")}\n`;
}

export async function extractGraphContext(
  graphPath: string,
  ruleIds: string[],
  mcpClient: McpClient,
): Promise<GraphContext> {
  const rawSubgraph = await mcpClient.callTool("extract_subgraph", {
    graph_path: graphPath,
    rule_ids: ruleIds,
  });

  function extractNodesByType<T>(
    nodes: Record<string, any>,
    nodeType: string,
  ): T[] {
    return Object.values(nodes).filter((n) => n && n.type === nodeType) as T[];
  }

  const nodes = rawSubgraph.subgraph_nodes || {};

  return {
    matchedRuleIds: ruleIds,
    businessRules: extractNodesByType<BusinessRuleNode>(nodes, "business_rule"),
    linkedScenarios: extractNodesByType<ScenarioNode>(nodes, "test_scenario"),
    linkedEndpoints: extractNodesByType<ApiEndpointNode>(nodes, "api_endpoint"),
    linkedSchemas: extractNodesByType<DataModelNode>(nodes, "data_model"),
    rawSubgraphJson: JSON.stringify(rawSubgraph, null, 2),
  };
}

// Retro-compatibility wrapper
export async function callAgentWrapper<T>(
  agentFile: string,
  userInput: string,
  schema: ZodSchema<T>,
  agentName: string,
  stream: vscode.ChatResponseStream,
  state: PipelineState,
): Promise<T> {
  const jsonInput = JSON.parse(userInput);
  const rawOutput = await callLanguageModel(agentFile, jsonInput, stream);
  return await parseAgentOutput(rawOutput, schema, agentName, stream);
}

// High-fidelity fallback / unit test mock data
function getMockAgentOutput<T>(
  agentFile: string,
  schema: ZodSchema<T>,
  agentName: string,
  state: PipelineState,
): T {
  if (agentFile.includes("test_case_analyst")) {
    const targetRules =
      state.proposal?.target_rules.length > 0
        ? state.proposal.target_rules
        : ["rule_denial_reason_codes"];
    const ruleId = targetRules[0];
    const proposal: CategoryProposal = {
      target_rules: targetRules,
      rule_analysis: [
        {
          rule_id: ruleId,
          rule_type: "numeric_threshold",
          classification_reasoning:
            "The rule governs claim denial based on coverage status — a categorical condition. BVA is inapplicable as no numeric boundary is specified in this mock.",
          applicable_methodologies: ["EP", "LinearExpansion", "RBAC"],
          skipped_methodologies: [
            {
              name: "BVA",
              reason:
                "No explicit numeric threshold or date boundary is defined in this rule. The condition is categorical (active vs. inactive coverage).",
            },
            {
              name: "StateTransition",
              reason:
                "The rule describes a single denial outcome, not a multi-stage lifecycle with ordered state transitions.",
            },
          ],
          attributes: [
            {
              name: "coverage_status",
              display_name: "Coverage Status",
              type: "enum",
              possible_values: ["active", "inactive"],
              source: "explicit",
            },
            {
              name: "auth_state",
              display_name: "Auth State",
              type: "boolean",
              possible_values: ["authenticated", "unauthenticated"],
              source: "implicit",
            },
          ],
          equivalence_class_table: [
            {
              attribute: "coverage_status",
              class_label: "Active Coverage",
              class_value: "active",
              partition: "valid",
              expected_outcome:
                "Claim is accepted and transitions to in_review status with HTTP 201",
              is_straight_through: true,
            },
            {
              attribute: "coverage_status",
              class_label: "Inactive Coverage",
              class_value: "inactive",
              partition: "invalid",
              expected_outcome:
                "API responds with HTTP 422 and reason code CLM001 (inactive coverage)",
              is_straight_through: false,
            },
            {
              attribute: "auth_state",
              class_label: "Authenticated User",
              class_value: "authenticated",
              partition: "valid",
              expected_outcome: "Session token is valid and user identity is established",
              is_straight_through: true,
            },
            {
              attribute: "auth_state",
              class_label: "Unauthenticated",
              class_value: "unauthenticated",
              partition: "invalid",
              expected_outcome:
                "Request is rejected with HTTP 401 and redirected to the login page",
              is_straight_through: false,
            },
          ],
          straight_through_case: {
            description:
              "Authenticated user submits a claim with active coverage — accepted with HTTP 201",
            attribute_values: {
              coverage_status: "active",
              auth_state: "authenticated",
            },
            expected_outcome:
              "Claim is accepted and transitions to in_review status with HTTP 201",
          },
          linear_expansion_matrix: {
            column_headers: ["Coverage Status", "Auth State"],
            straight_through_values: {
              coverage_status: "active",
              auth_state: "authenticated",
            },
            rows: [
              {
                test_number: 1,
                is_straight_through: true,
                varied_attribute: null,
                attribute_values: { coverage_status: "active", auth_state: "authenticated" },
                expected_outcome:
                  "Claim is accepted and transitions to in_review status with HTTP 201",
                maps_to_scenario_title: "Happy path — active coverage claim accepted",
              },
              {
                test_number: 2,
                is_straight_through: false,
                varied_attribute: "coverage_status",
                attribute_values: { coverage_status: "inactive", auth_state: "authenticated" },
                expected_outcome:
                  "API responds with HTTP 422 and reason code CLM001 (inactive coverage)",
                maps_to_scenario_title: "Assert claim denial inactive coverage",
              },
              {
                test_number: 3,
                is_straight_through: false,
                varied_attribute: "auth_state",
                attribute_values: { coverage_status: "active", auth_state: "unauthenticated" },
                expected_outcome:
                  "Request is rejected with HTTP 401 and redirected to the login page",
                maps_to_scenario_title: "Unauthenticated claim request denied with HTTP 401",
              },
            ],
            formula_applied: "1 + (2-1) + (2-1) = 3 EP/Linear tests",
            total_tests_derived: 3,
          },
        },
      ],
      proposed_categories: [
        {
          category_id: "api_functional",
          category_name: "API Functional Validation",
          description: "Verifies claim denial code transitions.",
          candidate_count: 3,
          total_conditions_count: 4,
          test_scenarios: [
            {
              title: "Happy path — active coverage claim accepted",
              qa_technique: "Happy Path — Straight Through",
              target_requirement: ruleId,
              description:
                "Authenticated user submits a claim with active coverage — accepted with HTTP 201.",
            },
            {
              title: "Assert claim denial inactive coverage",
              qa_technique: "Equivalence Partitioning",
              target_requirement: ruleId,
              description:
                "Assert denial code CLM001 for inactive member coverage.",
            },
            {
              title: "Unauthenticated claim request denied with HTTP 401",
              qa_technique: "Equivalence Partitioning",
              target_requirement: ruleId,
              description:
                "Unauthenticated request to the claims endpoint is rejected with HTTP 401.",
            },
          ],
        },
      ],
    };
    return proposal as any as T;
  }

  if (agentFile.includes("test_generator")) {
    if (state.iterationCount === 0) {
      const defectiveCases: TestCase[] = [
        {
          // SPEC-5 Wave 1 (A6): mandatory tc_id + target_feature_id on every
          // TestCase. Mock fixture values are clearly synthetic.
          tc_id: "TC_TESTFIX_FUNC_001",
          target_feature_id: "<test-fixture-feature>",
          title: "Assert claim denial inactive coverage",
          category_id: "api_functional",
          target_rule_id: "rule_denial_reason_codes",
          steps: [
            {
              step_number: 1,
              action: "Assert and verify login session is active",
              expected: "Session is active and dashboard is loaded",
            },
            {
              step_number: 2,
              action:
                "Submit claim request with date of service exceeding coverage",
              expected: "API responds with status 422 and reason code CLM001",
            },
          ],
        },
      ];
      return defectiveCases as any as T;
    } else {
      const compliantCases: TestCase[] = [
        {
          // SPEC-5 Wave 1 (A6): mandatory tc_id + target_feature_id.
          tc_id: "TC_TESTFIX_FUNC_001",
          target_feature_id: "<test-fixture-feature>",
          title: "Assert claim denial inactive coverage",
          category_id: "api_functional",
          target_rule_id: "rule_denial_reason_codes",
          steps: [
            {
              step_number: 1,
              action: "Navigate to API client console and authenticate session",
              expected: "API console is loaded and session is ready",
            },
            {
              step_number: 2,
              action:
                "Submit claim request with date of service exceeding coverage",
              expected: "API responds with status 422 and reason code CLM001",
            },
          ],
        },
      ];
      return compliantCases as any as T;
    }
  }

  if (agentFile.includes("test_verifier")) {
    const testCases = state.testCases || [];
    const step1Defect = testCases.some((tc) => {
      const step1 = tc.steps.find((s) => s.step_number === 1);
      return (
        step1 &&
        (/\b(assert|verify|status[\s-]?code)\b/i.test(step1.action) ||
          /\b(assert|verify|status[\s-]?code)\b/i.test(step1.expected))
      );
    });

    if (step1Defect && state.iterationCount === 0) {
      const gapLog: GapLog = {
        is_compliant: false,
        detected_gaps: [
          {
            gap_id: "GAP-01",
            target_rule_id: "rule_denial_reason_codes",
            severity: "High",
            description:
              "Step 1 of 'Assert claim denial inactive coverage' contains an active assertion ('Assert and verify'). Step 1 must purely represent preconditions.",
            remediation: {
              action_type: "FIX_EXPECTED_OUTCOME",
              target_test_title: "Assert claim denial inactive coverage",
              target_step_number: 1,
              correct_value: "Page is navigated and session is prepared",
            },
          },
        ],
      };
      return gapLog as any as T;
    } else {
      const compliantLog: GapLog = {
        is_compliant: true,
        detected_gaps: [],
      };
      return compliantLog as any as T;
    }
  }

  if (agentFile.includes("module_analyzer")) {
    const analyzer: ModuleAnalyzerOutput = {
      is_module_specific: true,
      // SPEC-5 Wave 1 (gap B7): mock module name made obviously synthetic.
      detected_module: "<test-fixture-module>",
      matched_rule_ids: ["rule_denial_reason_codes"],
      reasoning: "Matches claims keywords semantically.",
      feature_name: "<test-fixture-feature>",
      area_path: "<dummy>",
      is_llm_fallback: false,
      prioritized_rule_ids: ["rule_denial_reason_codes"],
    };
    return analyzer as any as T;
  }

  if (agentFile.includes("coverage_reporter")) {
    return "# Dummy coverage report content generated by mock fallback" as any as T;
  }

  throw new Error(`No mock output defined for agent prompt file: ${agentFile}`);
}
