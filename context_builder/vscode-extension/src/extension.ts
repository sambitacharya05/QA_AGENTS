import * as vscode from "vscode";
import * as fs from "fs";
import * as path from "path";
import { MCPClient } from "./mcpClient";

let mcpClient: MCPClient;

/**
 * Reads a single setting directly from <workspaceRoot>/.vscode/settings.json.
 *
 * vscode.workspace.getConfiguration() is unreliable for folder-level settings
 * when the extension activates before the workspace settings are fully scoped —
 * it silently falls back to user-level config and returns the default value even
 * when the key is present in .vscode/settings.json.  Reading the file directly
 * bypasses VS Code's configuration API entirely and is always correct.
 */
function readVSCodeSetting<T>(workspaceRoot: string, key: string, defaultValue: T): T {
  try {
    const settingsPath = path.join(workspaceRoot, '.vscode', 'settings.json');
    if (fs.existsSync(settingsPath)) {
      const raw = fs.readFileSync(settingsPath, 'utf8');
      const settings = JSON.parse(raw);
      if (Object.prototype.hasOwnProperty.call(settings, key)) {
        return settings[key] as T;
      }
    }
  } catch (e) {
    console.error('[Config] Failed to read .vscode/settings.json:', e);
  }
  return defaultValue;
}

/**
 * Unwraps the Python MCP server's ToolResponse envelope.
 *
 * Tools that use _ok()/_err() in main.py return:
 *   { ok: true,  data: T,    error: null }   — success
 *   { ok: false, data: null, error: {...} }  — failure
 *
 * Tools that return raw JSON directly (get_graph_summary, get_raw_documents,
 * get_all_edges) do NOT use the envelope and are passed through unchanged.
 *
 * @throws {Error} if the server returned ok=false
 */
function unwrapMCP<T>(raw: any): T {
  if (raw && typeof raw === "object" && "ok" in raw) {
    if (!raw.ok) {
      const msg = raw.error?.message ?? raw.error?.code ?? "MCP tool returned an error";
      throw new Error(msg);
    }
    return raw.data as T;
  }
  // Raw JSON path (no envelope) — return as-is
  return raw as T;
}

const BUILD_CONTEXT_HELP_TEXT =
  "## @build_context — Available Commands\n\n" +
  "| Command | Description |\n" +
  "| :--- | :--- |\n" +
  "| `/ingest` | Scan the `./ingest` folder and build the Semantic Context Graph. Runs 4 stages: structural parsing → rule extraction → tech mapping → relationship linking. |\n" +
  "| `/query <keyword>` | Search the context graph with a free-text keyword. Returns matched entities (business rules, endpoints, components) with an LLM-synthesized answer. |\n" +
  "| `/trace <rule_id>` | Show full traceability for a rule ID — all linked components, endpoints, tests, and edges. Example: `/trace doc_policy_rule_age_limit`. |\n" +
  "| `/status` | Check whether the indexed graph is in sync with files in `./ingest`. Shows new, modified, and removed files. |\n" +
  "| `/view` | Launch an interactive visual diagram of the entire Context Graph inside your IDE. |\n" +
  "| `/help` | Show this help message. |\n\n" +
  "## Underlying MCP Tools\n\n" +
  "Communicates with a local Python FastMCP server (`context_builder/main.py`) exposing **21 tools**, **4 prompts**, and **2 resources**. Key tools:\n" +
  "*   `ingest_workspace` — Full workspace parse and Semantic Context Graph build\n" +
  "*   `query_semantic_graph` — BM25-ranked node search\n" +
  "*   `get_rule_traceability` — BFS subgraph traversal for a specific rule\n" +
  "*   `add_node` / `add_edge` — Manually insert nodes or edges into the graph\n" +
  "*   `check_workspace_sync` — File-level sync check against the indexed store\n" +
  "*   `get_behavioral_drift_report` — Detect requirement-implementation drift\n" +
  "*   `apply_edge_proposals` — Review and apply Copilot-proposed edges\n" +
  "*   `plan_parallel_ingestion` — Fan-out ingestion for large workspaces\n\n" +
  "## Quick Start\n\n" +
  "1. Place specs, `.feature` files, and source code inside `./ingest`\n" +
  "2. Run `/ingest` to build the Semantic Context Graph\n" +
  "3. Run `/query <keyword>` to explore the graph\n" +
  "4. Run `/trace <rule_id>` to trace a specific business rule";

export function activate(context: vscode.ExtensionContext) {
  console.log(
    'Congratulations, "@build_context" agent extension is now active!',
  );

  const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri?.fsPath;
  if (!workspaceRoot) {
    vscode.window.showErrorMessage(
      "Please open a workspace before using @build_context.",
    );
    return;
  }

  // Supply serverDirectory as a lambda so every MCPClient.start() call —
  // including the automatic restart inside callTool() — reads the live value
  // from .vscode/settings.json rather than whatever was on disk at activation
  // time (which may have been empty if VS Code activated the extension in a
  // different workspace, or before the install script finished writing config).
  const getServerDirectory = (): string | undefined => {
    const dir = readVSCodeSetting<string>(workspaceRoot, 'contextBuilder.serverDirectory', '').trim();
    return dir || undefined;
  };

  const serverDirectoryNow = getServerDirectory();
  console.log(`[MCP Client] serverDirectory at activation: "${serverDirectoryNow ?? '(not set)'}" (workspaceRoot: "${workspaceRoot}")`);

  mcpClient = new MCPClient(workspaceRoot, getServerDirectory);

  // Only eagerly start if the setting is already configured.  If the extension
  // activated before the install script wrote settings.json (first-install race
  // condition), skip the eager start here.  callTool() already has an
  // auto-restart guard (`if (!this.process) { this.start(); }`) that will fire
  // on the first command — by which time a Reload Window will have put the
  // correct workspace in scope and getServerDirectory() will return the right path.
  if (serverDirectoryNow) {
    mcpClient.start();
  } else {
    console.warn(
      '[MCP Client] contextBuilder.serverDirectory is not yet configured — ' +
      'MCP server will start lazily on the first command. ' +
      'If you just ran the install script, reload VS Code: Cmd+Shift+P → Developer: Reload Window'
    );
  }

  const watcher = vscode.workspace.onDidSaveTextDocument(async (document) => {
    const filePath = document.uri.fsPath;
    if (filePath.includes(path.join(workspaceRoot, "ingest"))) {
      try {
        await mcpClient.callTool("ingest_workspace", {
          workspace_path: path.join(workspaceRoot, "ingest"),
        });
      } catch (err) {
        console.error(`Background ingestion failure: ${err}`);
      }
    }
  });
  context.subscriptions.push(watcher);

  const ingestCommand = vscode.commands.registerCommand(
    "build_context.runIngestAgent",
    async () => {
      await runSilentIngestion(workspaceRoot);
    },
  );
  context.subscriptions.push(ingestCommand);

  const participant = vscode.chat.createChatParticipant(
    "build_context",
    async (request, context, stream, token) => {
      const command = request.command;
      const prompt = request.prompt.trim();

      if (command === "ingest") {
        await handleIngest(workspaceRoot, stream, token, request.model);
      } else if (command === "query") {
        await handleQuery(prompt, stream, token, request.model);
      } else if (command === "trace") {
        await handleTrace(prompt, stream);
      } else if (command === "status") {
        await handleStatus(workspaceRoot, stream);
      } else if (command === "view") {
        await handleView(workspaceRoot, stream);
      } else if (command === "anomalies") {
        await handleAnomalies(stream, prompt);
      } else if (command === "help") {
        if (stream)
          stream.markdown(BUILD_CONTEXT_HELP_TEXT);
      } else {
        if (stream)
          stream.markdown(
            "Hello! I am **`@build_context`**, your local Insurance Context Gathering agent.\n\n" +
              "I parse your specs and code inside `./ingest` and build a Semantic Context Graph.\n\n" +
              "Here are the commands you can run:\n" +
              "*   `/ingest` - Scan `./ingest` folder and map business rules, code components, and BDD scenarios.\n" +
              "*   `/query <keyword>` - Search the graph store directly.\n" +
              "*   `/trace <rule_id>` - Print rule traceability to tech endpoints & tests.\n" +
              "*   `/status` - Check if context database is synchronized with files.\n" +
              "*   `/view` - Open an interactive visual diagram of the Context Graph inside your IDE!\n" +
              "*   `/anomalies [severity]` - Review behavioral anomalies recorded by the relationship-linker. Optional severity filter: `critical`, `warning`, or `info`.",
          );
      }
    },
  );

  context.subscriptions.push(participant);
}

export function deactivate() {
  if (mcpClient) {
    mcpClient.stop();
  }
}

async function runSilentIngestion(workspaceRoot: string) {
  try {
    await handleIngest(workspaceRoot);
  } catch (err) {
    console.error(`Silent ingestion failed: ${err}`);
  }
}

async function runLLMAgentWithTools(
  prompt: string,
  tools: vscode.LanguageModelChatTool[],
  toolHandler: (
    name: string,
    input: Record<string, unknown>,
  ) => Promise<unknown>,
  model: vscode.LanguageModelChat,
  token?: vscode.CancellationToken,
): Promise<void> {
  const cancelToken = token ?? new vscode.CancellationTokenSource().token;

  const messages: vscode.LanguageModelChatMessage[] = [
    vscode.LanguageModelChatMessage.User(prompt),
  ];

  // Cap at 3 rounds: each round is a premium LLM call.
  // The original limit of 10 rounds × 3 stages = 30 LLM calls per /ingest.
  for (let round = 0; round < 3; round++) {
    const response = await model.sendRequest(messages, { tools }, cancelToken);

    const toolCallParts: vscode.LanguageModelToolCallPart[] = [];
    for await (const part of response.stream) {
      if (part instanceof vscode.LanguageModelToolCallPart) {
        toolCallParts.push(part);
      }
    }

    if (toolCallParts.length === 0) {
      break;
    }

    messages.push(
      vscode.LanguageModelChatMessage.Assistant([...toolCallParts]),
    );

    const toolResults: vscode.LanguageModelToolResultPart[] = [];
    for (const call of toolCallParts) {
      try {
        const result = await toolHandler(
          call.name,
          call.input as Record<string, unknown>,
        );
        toolResults.push(
          new vscode.LanguageModelToolResultPart(call.callId, [
            new vscode.LanguageModelTextPart(JSON.stringify(result)),
          ]),
        );
      } catch (err) {
        toolResults.push(
          new vscode.LanguageModelToolResultPart(call.callId, [
            new vscode.LanguageModelTextPart(`Error: ${err}`),
          ]),
        );
      }
    }
    messages.push(vscode.LanguageModelChatMessage.User([...toolResults]));
  }
}

async function handleIngest(
  workspaceRoot: string,
  stream?: vscode.ChatResponseStream,
  token?: vscode.CancellationToken,
  model?: vscode.LanguageModelChat,
) {
  const ingestDir = path.join(workspaceRoot, "ingest");
  if (!fs.existsSync(ingestDir)) {
    if (stream)
      stream.markdown(
        `⚠️ Ingest folder not found. Please create a folder named \`ingest\` at your project root and place all your input files there.`,
      );
    return;
  }

  if (stream)
    stream.markdown(
      "**Stage 1/4: structural parsing...** Ingesting requirements and controllers inside `ingest`...\n",
    );

  try {
    const rawResponse = await mcpClient.callTool("ingest_workspace", {
      workspace_path: ingestDir,
    });
    // ingest_workspace returns ToolResponse<IngestResult> envelope: { ok, data: {...} }
    const summary = unwrapMCP<{
      parsed_files: number;
      cached_files: number;
      total_nodes: number;
      total_edges: number;
    }>(JSON.parse(rawResponse));

    if (stream)
      stream.markdown(
        `✅ Structural parse completed successfully! Ingested **${summary.parsed_files}** new files, cached **${summary.cached_files}** unchanged files.\n\n`,
      );

    // ── Early-exit guard ────────────────────────────────────────────────────
    // If nothing was newly parsed, the graph is already up to date.
    // Skip Stages 2-4 entirely — they would re-analyse the same files and
    // waste premium LLM requests without adding any new information.
    if (summary.parsed_files === 0) {
      const graphSummary = await mcpClient.callTool("get_graph_summary");
      const counts = JSON.parse(graphSummary);
      if (stream)
        stream.markdown(
          `### ✅ Graph already up to date — skipping LLM analysis stages.\n\n` +
            `No new or modified files were detected in \`./ingest\`, so Stages 2-4 have been skipped to preserve your premium usage.\n\n` +
            `*   **Total Entities**: ${counts.total_nodes}\n` +
            `*   **Total Connections**: ${counts.total_edges}\n\n` +
            `Add or modify files in \`./ingest\` and re-run \`/ingest\` to update the graph.`,
        );
      return;
    }
    // ────────────────────────────────────────────────────────────────────────

    const rawDocs = await mcpClient.callTool("get_raw_documents");
    const docs: Array<{ path: string; content: string }> = JSON.parse(rawDocs);

    // SPEC-2 Wave 1: generic tool declarations replace the retired pseudo-tools
    // (add_business_rule_node / add_tech_component_node / add_semantic_edge).
    // Agents now call the real MCP tools directly with node_type as a parameter.
    const addNodeTool: vscode.LanguageModelChatTool = {
      name: "add_node",
      description:
        "Registers a node in the Semantic Context Graph. node_type must be one of the supported types " +
        "(business_rule, validation_rule, eligibility_rule, ui_business_rule, security_rule, " +
        "product_feature, field_specification, api_endpoint, data_model, code_component, " +
        "test_scenario, ui_page_object, ui_element, rule_constant, test_utility). " +
        "metadata MUST include sync_governance.caller='agent'.",
      inputSchema: {
        type: "object" as const,
        properties: {
          node_id: { type: "string", description: "Stable ID, prefix per node_type (see SPEC-1 §0.3)" },
          node_type: { type: "string", description: "One of the supported node types" },
          name: { type: "string" },
          description: { type: "string" },
          metadata: {
            type: "string",
            description:
              "JSON string. MUST include sync_governance, source_file, extraction_mode.",
          },
        },
        required: ["node_id", "node_type", "name", "description", "metadata"],
      },
    };

    const addEdgeTool: vscode.LanguageModelChatTool = {
      name: "add_edge",
      description:
        "Registers a directed edge in the Semantic Context Graph. relationship must be allowed by " +
        "edge_policy.json for the (source_type, target_type) pair. metadata must include " +
        "source='agent:<agent-name>' and confidence.",
      inputSchema: {
        type: "object" as const,
        properties: {
          source_id: { type: "string" },
          target_id: { type: "string" },
          relationship: {
            type: "string",
            description:
              "TESTS|IMPLEMENTS|VALIDATES|USES_MODEL|MAPS_TO|REFERENCES|CALLS|USES_DATA|PART_OF",
          },
          metadata: {
            type: "string",
            description: "JSON string with source, confidence, rationale.",
          },
        },
        required: ["source_id", "target_id", "relationship", "metadata"],
      },
    };

    const querySemanticGraphTool: vscode.LanguageModelChatTool = {
      name: "query_semantic_graph",
      description:
        "Read-only graph lookup. Returns nodes matching the query string and optional node_type filter. " +
        "Agents call this BEFORE add_node to avoid duplicating existing canonical nodes.",
      inputSchema: {
        type: "object" as const,
        properties: {
          query: { type: "string", description: "Concept name to search for; empty string lists all." },
          node_type: { type: "string", description: "Optional type filter." },
        },
        required: ["query"],
      },
    };

    // SPEC-2 Wave 2: read-only tools added for the broader agent surface.
    const getRawDocsTool: vscode.LanguageModelChatTool = {
      name: "get_raw_documents",
      description:
        "Returns all parsed raw document text content. Filter callsites client-side.",
      inputSchema: { type: "object" as const, properties: {}, required: [] },
    };

    const getAllEdgesTool: vscode.LanguageModelChatTool = {
      name: "get_all_edges",
      description:
        "Returns all edges in the graph. The linker uses this to detect missing/duplicate edges and orphan nodes.",
      inputSchema: { type: "object" as const, properties: {}, required: [] },
    };

    const proposeEdgeCandidatesTool: vscode.LanguageModelChatTool = {
      name: "propose_edge_candidates",
      description:
        "Returns sub-threshold TF-IDF edge candidates from the heuristic mapper as a shortlist for LLM validation.",
      inputSchema: {
        type: "object" as const,
        properties: {
          limit: { type: "number", description: "Max candidates returned. Default 100." },
        },
        required: [],
      },
    };

    const TOOL_REGISTRY: Record<string, vscode.LanguageModelChatTool> = {
      add_node: addNodeTool,
      add_edge: addEdgeTool,
      query_semantic_graph: querySemanticGraphTool,
      get_raw_documents: getRawDocsTool,
      get_all_edges: getAllEdgesTool,
      propose_edge_candidates: proposeEdgeCandidatesTool,
    };

    const toolsForAgent = (agentId: AgentId): vscode.LanguageModelChatTool[] => {
      const perm = AGENT_TOOL_PERMISSIONS[agentId];
      return [...perm.canWrite, ...perm.canRead].map((n) => TOOL_REGISTRY[n]);
    };

    const toolHandler = async (
      name: string,
      input: Record<string, unknown>,
    ): Promise<unknown> => {
      if (name === "add_node") {
        await mcpClient.callTool("add_node", {
          node_id: input.node_id,
          node_type: input.node_type,
          name: input.name,
          description: input.description,
          metadata:
            typeof input.metadata === "string"
              ? input.metadata
              : JSON.stringify(input.metadata ?? {}),
        });
        return { success: true };
      }
      if (name === "add_edge") {
        await mcpClient.callTool("add_edge", {
          source_id: input.source_id,
          target_id: input.target_id,
          relationship: input.relationship,
          metadata:
            typeof input.metadata === "string"
              ? input.metadata
              : JSON.stringify(input.metadata ?? {}),
        });
        return { success: true };
      }
      if (name === "query_semantic_graph") {
        const raw = await mcpClient.callTool("query_semantic_graph", {
          query: typeof input.query === "string" ? input.query : "",
          ...(input.node_type ? { node_type: input.node_type } : {}),
        });
        return raw;
      }
      if (name === "get_raw_documents") {
        return await mcpClient.callTool("get_raw_documents");
      }
      if (name === "get_all_edges") {
        return await mcpClient.callTool("get_all_edges");
      }
      if (name === "propose_edge_candidates") {
        return await mcpClient.callTool("propose_edge_candidates", {
          ...(typeof input.limit === "number" ? { limit: input.limit } : {}),
        });
      }
      throw new Error(`Unknown tool: ${name}`);
    };

    // Use the model from the chat request directly (VS Code recommends this for
    // chat participants). For background/command contexts where no model is
    // passed, fall back to the broadest Copilot selector.
    let resolvedModel: vscode.LanguageModelChat | undefined = model;
    if (!resolvedModel) {
      const found = await vscode.lm.selectChatModels({ vendor: "copilot" });
      if (found.length === 0) {
        throw new Error(
          "No language model available. Please ensure GitHub Copilot is installed and signed in.",
        );
      }
      resolvedModel = found[0];
    }
    const modelLabel = resolvedModel.name ?? resolvedModel.family ?? "Copilot";

    // ── Stage 2: Pre-flight agent scan ──────────────────────────────────────
    const agentsToRun = scanIngestForAgents(ingestDir);
    const extractors = Array.from(agentsToRun).filter(
      (a) => a !== "relationship-linker",
    );
    if (stream)
      stream.markdown(
        `**Stage 2/4: Pre-flight scan...** ${extractors.length} extractor(s) to dispatch: ${extractors.join(", ") || "(none)"}.\n\n`,
      );

    // ── Stage 3: Extractor agents in parallel (SPEC-2 Wave 2) ──────────────
    if (stream)
      stream.markdown(
        `**Stage 3/4: Parallel extraction (${extractors.length} agents)** using **${modelLabel}**.\n`,
      );

    const extractorTasks: Promise<void>[] = extractors.map((agentId) =>
      runAgent(
        agentId,
        workspaceRoot,
        docs,
        toolsForAgent(agentId),
        toolHandler,
        resolvedModel!,
        stream,
        token,
      ),
    );
    await Promise.allSettled(extractorTasks);

    // ── Stage 4: Relationship Linker (SPEC-2 Wave 3) ───────────────────────
    if (agentsToRun.has("relationship-linker")) {
      if (stream)
        stream.markdown(
          `**Stage 4/4: Relationship Linker** using **${modelLabel}**.\n`,
        );
      // Linker reads the post-extraction graph, so it doesn't need the raw
      // docs context — its toolset includes get_all_edges / propose_edge_candidates
      // / record_anomaly instead.
      await runAgent(
        "relationship-linker",
        workspaceRoot,
        [],
        toolsForAgent("relationship-linker"),
        toolHandler,
        resolvedModel,
        stream,
        token,
      );
    }

    // ── Stage 5: Enriched summary with anomaly breakdown ───────────────────
    await emitEnrichedSummary(stream);
  } catch (err) {
    if (stream) stream.markdown(`❌ Failed to complete ingestion: ${err}`);
  }
}

async function emitEnrichedSummary(
  stream?: vscode.ChatResponseStream,
): Promise<void> {
  let graphPayload: Record<string, unknown> = {};
  let driftPayload: Record<string, unknown> = {};
  try {
    const [graphRaw, driftRaw] = await Promise.all([
      mcpClient.callTool("get_graph_summary"),
      mcpClient.callTool("get_behavioral_drift_report"),
    ]);
    graphPayload = JSON.parse(graphRaw);
    driftPayload = JSON.parse(driftRaw);
  } catch (err) {
    if (stream)
      stream.markdown(`⚠️ Could not build summary: ${err}\n`);
    return;
  }

  const nodeTypes = (graphPayload.node_types_breakdown ?? {}) as Record<string, number>;
  const relBreakdown = (graphPayload.relationships_breakdown ?? {}) as Record<string, number>;
  const byKind = (driftPayload.by_kind ?? {}) as Record<string, number>;
  const nodeAnomalies = (driftPayload.node_anomalies ?? []) as unknown[];
  const driftEdges = (driftPayload.drift_edges ?? []) as unknown[];
  const totalAnomalies = nodeAnomalies.length + driftEdges.length;

  const fmt = (obj: Record<string, number>) =>
    Object.entries(obj)
      .map(([k, v]) => `- ${k}: ${v}`)
      .join("\n") || "- (none)";

  if (stream) {
    stream.markdown(
      `### 📊 Ingest Summary\n\n` +
      `**Total entities**: ${graphPayload.total_nodes ?? 0}\n` +
      `**Total edges**: ${graphPayload.total_edges ?? 0}\n\n` +
      `**Nodes by type**\n${fmt(nodeTypes)}\n\n` +
      `**Edges by relationship**\n${fmt(relBreakdown)}\n\n` +
      `**Behavioral anomalies: ${totalAnomalies}**\n` +
      `- ui_without_requirement: ${byKind.ui_without_requirement ?? 0}\n` +
      `- rule_without_implementation: ${byKind.rule_without_implementation ?? 0}\n` +
      `- constant_spec_divergence: ${byKind.constant_spec_divergence ?? 0} ⚠️ (critical)\n` +
      `- endpoint_without_test: ${byKind.endpoint_without_test ?? 0}\n\n` +
      `Run \`/anomalies\` to review individual records, or \`/view\` to explore the graph.\n`,
    );
  }
}

// SPEC-2 Wave 3 §3.3: /anomalies slash command.
async function handleAnomalies(
  stream: vscode.ChatResponseStream | undefined,
  filter?: string,
): Promise<void> {
  if (!stream) return;
  let drift: Record<string, unknown>;
  try {
    const raw = await mcpClient.callTool("get_behavioral_drift_report");
    drift = JSON.parse(raw);
  } catch (err) {
    stream.markdown(`❌ Could not fetch behavioral drift report: ${err}`);
    return;
  }

  const records = (drift.node_anomalies ?? []) as Array<{
    node_id: string;
    anomaly_kind: string;
    severity: string;
    evidence: { summary?: string; source_file?: string };
    suggested_action?: string;
  }>;

  const sev = filter?.trim().toLowerCase();
  const filtered = sev
    ? records.filter((r) => r.severity === sev)
    : records;

  if (filtered.length === 0) {
    stream.markdown(
      `✅ No anomalies recorded${sev ? ` at severity '${sev}'` : ""}.`,
    );
    return;
  }

  stream.markdown(
    `### ${filtered.length} Behavioral Anomalies${sev ? ` (${sev})` : ""}\n\n`,
  );
  for (const r of filtered) {
    const icon =
      r.severity === "critical" ? "🔴" :
      r.severity === "warning" ? "🟡" : "ℹ️";
    let body =
      `${icon} **${r.anomaly_kind}** — \`${r.node_id}\`\n` +
      `  - ${r.evidence?.summary ?? "(no summary)"}\n`;
    if (r.evidence?.source_file) {
      body += `  - Source: \`${r.evidence.source_file}\`\n`;
    }
    if (r.suggested_action) {
      body += `  - Suggested: ${r.suggested_action}\n`;
    }
    body += "\n";
    stream.markdown(body);
  }
}

// ── SPEC-2 Wave 1: agent-dispatch helpers ──────────────────────────────────

type AgentId =
  | "rule-extractor"
  | "field-spec-parser"
  | "api-contract-mapper"
  | "test-infra-mapper"
  | "coding-standards-extractor"
  | "relationship-linker";

interface AgentTrigger {
  extensions: string[];
  pathPatterns?: RegExp[];
  contentSniff?: (sample: string, filename: string) => boolean;
}

// SPEC-2 Wave 2: file-extension manifest is the single source of truth for
// dispatch. Each agent's pathPatterns are advisory (prioritise scanning).
// contentSniff is the gate that decides routing per-file.
const AGENT_TRIGGERS: Record<AgentId, AgentTrigger> = {
  "rule-extractor": {
    extensions: [".docx", ".pdf", ".md", ".xlsx"],
  },
  "field-spec-parser": {
    extensions: [".xlsx", ".csv"],
  },
  "api-contract-mapper": {
    extensions: [".json", ".yaml", ".yml", ".java", ".ts"],
    pathPatterns: [/\/api\//i, /\/schemas?\//i, /\/openapi\//i, /\/dto\//i, /\/payloads?\//i],
    contentSniff: (sample, filename) => {
      const ext = path.extname(filename).toLowerCase();
      if ([".json", ".yaml", ".yml"].includes(ext)) {
        const head = sample.slice(0, 2000);
        return /\b(openapi|swagger)\s*[:"]/.test(head)
          || /"\$schema"\s*:/.test(head)
          || /"type"\s*:\s*"object"/.test(head);
      }
      if (ext === ".java") {
        return /\bRestAssured\b|\bgiven\(\)/.test(sample)
          || /@Data\b|@Builder\b/.test(sample);
      }
      if (ext === ".ts") {
        return /\brequest\.(post|get|put|delete|patch)\(/.test(sample)
          || /\bapiContext\b|\bAPIRequestContext\b/.test(sample)
          || /\binterface\s+\w+\s*\{/.test(sample);
      }
      return false;
    },
  },
  "test-infra-mapper": {
    extensions: [".feature", ".java", ".ts", ".properties", ".loc", ".csv", ".xlsx", ".json"],
    pathPatterns: [
      /\/pages?\//i, /\/page_objects?\//i, /\/pom\//i,
      /\/steps?\//i, /\/stepdefs?\//i,
      /\/features?\//i,
      /\/testdata\//i, /\/resources\//i, /\/locators?\//i, /\/objectrepo\//i,
    ],
    contentSniff: (sample, filename) => {
      const ext = path.extname(filename).toLowerCase();
      if (ext === ".feature") return true;
      if (ext === ".properties" || ext === ".loc") return true;
      if (ext === ".java") {
        return /@FindBy\b|@QAFTestStep\b|@Given\b|@When\b|@Then\b/.test(sample)
          || /extends\s+\w*Page\b/.test(sample);
      }
      if (ext === ".ts") {
        return /page\.locator\(|page\.getBy\w+\(|defineStep\(/.test(sample);
      }
      return false;
    },
  },
  "coding-standards-extractor": {
    extensions: [".java", ".ts", ".tsx", ".js", ".py"],
    pathPatterns: [
      /\/utils?\//i, /\/utilities\//i, /\/helpers\//i,
      /\/common\//i, /\/shared\//i, /\/lib\//i, /\/base\//i, /\/framework\//i,
    ],
  },
  "relationship-linker": { extensions: [] },
};

const AGENT_PROMPT_FILES: Record<AgentId, string> = {
  "rule-extractor": "rule_extractor.agent.md",
  "field-spec-parser": "field_spec_parser.agent.md",
  "api-contract-mapper": "api_contract_mapper.agent.md",
  "test-infra-mapper": "test_infra_mapper.agent.md",
  "coding-standards-extractor": "coding_standards_extractor.agent.md",
  "relationship-linker": "relationship_linker.agent.md",
};

const AGENT_TASK_INSTRUCTION: Record<AgentId, string> = {
  "rule-extractor":
    "Extract every functional, validation, eligibility, UI, security, and NFR business rule from these requirement documents. Emit one atomic node per constraint per the rules in your prompt.",
  "field-spec-parser":
    "Decompose any field-spec sheet (header tokens include field_id/field_name/type/length/mandatory) into one field_specification node per row. Emit MAPS_TO edges (confidence ≥ 0.6) to existing rule nodes via query_semantic_graph.",
  "api-contract-mapper":
    "Extract api_endpoint nodes (documented or test-code-inferred) and data_model nodes (one per DTO/schema, fields-in-metadata). Emit USES_MODEL edges where the source explicitly references the schema. Do NOT extract Spring/NestJS controllers.",
  "test-infra-mapper":
    "Extract test_scenario (one per Scenario/Outline with examples in metadata, Background merged), ui_page_object + ui_element from page-object classes, code_component per step-def class, and test_utility per utility class. Wire MAPS_TO edges from parser-emitted rule_constants to matching rule nodes (confidence ≥ 0.6).",
  "coding-standards-extractor":
    "Walk utility folders (utils/, utilities/, helpers/) and emit enriched test_utility nodes. Add small structured coding_standards_observation metadata blocks to representative ui_page_object / code_component / data_model nodes. Do NOT emit edges.",
  "relationship-linker": "",
};

function scanIngestForAgents(ingestDir: string): Set<AgentId> {
  const present = new Set<AgentId>();
  if (!fs.existsSync(ingestDir)) return present;

  const PRUNE = new Set([
    "node_modules", ".git", "target", "build", "dist",
    ".gradle", ".idea", ".vscode", ".context_builder",
    "__pycache__", "venv", ".venv",
  ]);

  const considerFile = (full: string, ext: string) => {
    let sampled: string | null = null;
    const readSample = (): string => {
      if (sampled === null) {
        try {
          sampled = fs.readFileSync(full, "utf8").slice(0, 8192);
        } catch {
          sampled = "";
        }
      }
      return sampled;
    };

    for (const agentId of Object.keys(AGENT_TRIGGERS) as AgentId[]) {
      if (present.has(agentId)) continue;
      const trigger = AGENT_TRIGGERS[agentId];
      if (!trigger.extensions.includes(ext)) continue;
      if (trigger.contentSniff && !trigger.contentSniff(readSample(), full)) continue;
      present.add(agentId);
    }
  };

  const walk = (dir: string) => {
    let entries: fs.Dirent[];
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const e of entries) {
      const full = path.join(dir, e.name);
      if (e.isDirectory()) {
        if (!PRUNE.has(e.name)) walk(full);
        continue;
      }
      considerFile(full, path.extname(e.name).toLowerCase());
    }
  };

  walk(ingestDir);
  // SPEC-2 Wave 2: linker always runs after extractors complete.
  if (present.size > 0) present.add("relationship-linker");
  return present;
}

// SPEC-2 Wave 2: per-agent tool-permission matrix. The orchestrator passes
// each agent only the LanguageModelChatTool entries it is authorised to use,
// so e.g. the linker cannot accidentally call add_edge during coding-standards work.
interface ToolPermissions {
  canWrite: ("add_node" | "add_edge")[];
  canRead: ("query_semantic_graph" | "get_raw_documents" | "get_all_edges" | "propose_edge_candidates")[];
}

const AGENT_TOOL_PERMISSIONS: Record<AgentId, ToolPermissions> = {
  "rule-extractor":             { canWrite: ["add_node"],             canRead: ["query_semantic_graph", "get_raw_documents"] },
  "field-spec-parser":          { canWrite: ["add_node", "add_edge"], canRead: ["query_semantic_graph", "get_raw_documents"] },
  "api-contract-mapper":        { canWrite: ["add_node", "add_edge"], canRead: ["query_semantic_graph", "get_raw_documents"] },
  "test-infra-mapper":          { canWrite: ["add_node", "add_edge"], canRead: ["query_semantic_graph", "get_raw_documents"] },
  "coding-standards-extractor": { canWrite: ["add_node"],             canRead: ["query_semantic_graph", "get_raw_documents"] },
  "relationship-linker":        { canWrite: ["add_node", "add_edge"], canRead: ["query_semantic_graph", "get_all_edges", "propose_edge_candidates"] },
};

function contextForAgent(
  agentId: AgentId,
  docs: Array<{ path: string; content: string }>,
): string {
  const trigger = AGENT_TRIGGERS[agentId];
  const allowed = new Set(trigger.extensions.map((e) => e.toLowerCase()));
  return docs
    .filter((d) => {
      const ext = path.extname(d.path).toLowerCase();
      if (!allowed.has(ext)) return false;
      // SPEC-2 Wave 2: honour the same contentSniff used at scan time so the
      // agent only sees files that actually match its routing criteria.
      if (trigger.contentSniff && !trigger.contentSniff(d.content.slice(0, 8192), d.path)) {
        return false;
      }
      return true;
    })
    .map((d) => `### FILE: ${d.path}\n${d.content}\n`)
    .join("\n");
}

async function runAgent(
  agentId: AgentId,
  workspaceRoot: string,
  docs: Array<{ path: string; content: string }>,
  tools: vscode.LanguageModelChatTool[],
  toolHandler: (name: string, input: Record<string, unknown>) => Promise<unknown>,
  model: vscode.LanguageModelChat,
  stream?: vscode.ChatResponseStream,
  token?: vscode.CancellationToken,
): Promise<void> {
  const promptPath = path.join(
    workspaceRoot,
    ".github",
    "agents",
    AGENT_PROMPT_FILES[agentId],
  );
  if (!fs.existsSync(promptPath)) {
    if (stream)
      stream.markdown(`  ⚠️ ${agentId}: prompt file not found at ${promptPath}; skipping.\n`);
    return;
  }
  const prompt = fs.readFileSync(promptPath, "utf8");
  const context = contextForAgent(agentId, docs);
  if (!context.trim()) {
    if (stream)
      stream.markdown(`  ℹ️ ${agentId}: no applicable files in ingest; skipping.\n`);
    return;
  }
  try {
    await runLLMAgentWithTools(
      `${prompt}\n\n${AGENT_TASK_INSTRUCTION[agentId]}\n\n${context}`,
      tools,
      toolHandler,
      model,
      token,
    );
    if (stream) stream.markdown(`  ✅ ${agentId} completed.\n`);
  } catch (err) {
    if (stream) stream.markdown(`  ⚠️ ${agentId} failed: ${err}\n`);
  }
}

async function handleQuery(
  prompt: string,
  stream?: vscode.ChatResponseStream,
  token?: vscode.CancellationToken,
  model?: vscode.LanguageModelChat,
) {
  if (!prompt) {
    if (stream)
      stream.markdown(
        "Please enter a query. Example: `/query minimum age for a policy`",
      );
    return;
  }

  try {
    const rawResults = await mcpClient.callTool("query_semantic_graph", {
      query: prompt,
    });
    // query_semantic_graph returns ToolResponse<QueryResult>; actual nodes in .results
    const queryResponse = unwrapMCP<{ results: any[] }>(JSON.parse(rawResults));
    const results = queryResponse.results ?? [];

    if (results.length === 0) {
      if (stream)
        stream.markdown(
          `No matches found in the context graph for "**${prompt}**".`,
        );
      return;
    }

    // --- LLM synthesis ---
    // Resolve the model (same pattern as handleIngest)
    let resolvedModel: vscode.LanguageModelChat | undefined = model;
    if (!resolvedModel) {
      const found = await vscode.lm.selectChatModels({ vendor: "copilot" });
      if (found.length > 0) {
        resolvedModel = found[0];
      }
    }

    if (resolvedModel) {
      const cancelToken = token ?? new vscode.CancellationTokenSource().token;
      const contextBlock = results
        .map((n: any) =>
          `[${n.type.toUpperCase()}] ${n.name}\n${n.description ?? ""}`.trim(),
        )
        .join("\n\n");

      const messages = [
        vscode.LanguageModelChatMessage.User(
          `You are a precise assistant for a software project context graph.\n` +
            `Answer the user's question using ONLY the entities provided below. ` +
            `Be concise. If the answer cannot be determined from the context, say so.\n\n` +
            `CONTEXT ENTITIES:\n${contextBlock}\n\n` +
            `USER QUESTION: ${prompt}`,
        ),
      ];

      if (stream) stream.markdown(`### 💬 Answer\n\n`);
      const response = await resolvedModel.sendRequest(
        messages,
        {},
        cancelToken,
      );
      for await (const part of response.stream) {
        if (part instanceof vscode.LanguageModelTextPart && stream) {
          stream.markdown(part.value);
        }
      }
      if (stream) stream.markdown(`\n\n---\n\n`);
    }

    // --- Raw results table ---
    if (stream)
      stream.markdown(
        `### 🔍 Matched Entities (${results.length})\n\n` +
          `| ID | Type | Name | Description |\n| :--- | :--- | :--- | :--- |\n`,
      );
    for (const n of results) {
      const desc = n.description
        ? n.description.substring(0, 100).replace(/\n/g, " ") + "..."
        : "-";
      if (stream)
        stream.markdown(
          `| \`${n.id}\` | **${n.type.toUpperCase()}** | ${n.name} | ${desc} |\n`,
        );
    }
  } catch (err) {
    if (stream) stream.markdown(`❌ Query failure: ${err}`);
  }
}

async function handleTrace(prompt: string, stream?: vscode.ChatResponseStream) {
  if (!prompt) {
    if (stream)
      stream.markdown(
        "Please specify a rule ID to trace. Example: `/trace doc_auto_policy_spec_docx_rule_age_limit_eligibility`",
      );
    return;
  }

  try {
    const rawResults = await mcpClient.callTool("get_rule_traceability", {
      rule_id: prompt,
    });
    // get_rule_traceability returns ToolResponse<TraceabilityGraph>
    // On not-found it returns ok=false with NODE_NOT_FOUND — not a plain string
    const parsed = JSON.parse(rawResults);
    if (parsed && typeof parsed === "object" && "ok" in parsed && !parsed.ok) {
      if (stream)
        stream.markdown(`⚠️ No rule found with ID \`${prompt}\`. Check the ID and try again.`);
      return;
    }
    const subgraph = unwrapMCP<{ nodes: any[]; edges: any[] }>(parsed);

    if (stream)
      stream.markdown(`### 🔗 Traceability Path for \`${prompt}\`:\n\n`);

    if (stream) stream.markdown("**Linked Entities Discovered:**\n");
    for (const n of (subgraph.nodes ?? [])) {
      const star = n.id === prompt ? "⭐ (Target) " : "";
      if (stream)
        stream.markdown(
          `*   ${star}[**${n.type.toUpperCase()}**] \`${n.id}\` - ${n.name}\n`,
        );
    }

    if (stream) stream.markdown("\n**Logical Connections (Edges):**\n");
    for (const e of (subgraph.edges ?? [])) {
      if (stream)
        stream.markdown(
          `*   \`${e.source_id}\` -- [**${e.relationship}**] --> \`${e.target_id}\`\n`,
        );
    }
  } catch (err) {
    if (stream) stream.markdown(`❌ Traceability resolution failed: ${err}`);
  }
}

async function handleStatus(
  workspaceRoot: string,
  stream?: vscode.ChatResponseStream,
) {
  try {
    const ingestDir = path.join(workspaceRoot, "ingest");

    const [rawSummary, rawSync] = await Promise.all([
      mcpClient.callTool("get_graph_summary"),
      mcpClient.callTool("check_workspace_sync", { workspace_path: ingestDir }),
    ]);

    // get_graph_summary returns raw JSON (no envelope) — parse directly
    const counts = JSON.parse(rawSummary);
    // check_workspace_sync returns ToolResponse<WorkspaceSyncReport>
    const sync = unwrapMCP<{
      is_in_sync: boolean;
      new_files: string[];
      modified_files: string[];
      removed_files: string[];
      up_to_date_files: string[];
    }>(JSON.parse(rawSync));

    if (!stream) {
      return;
    }

    stream.markdown(
      `### 📊 Context Graph Synchronization Status\n\n` +
        `*   **Indexed entities**: ${counts.total_nodes}\n` +
        `*   **Edges (relationships)**: ${counts.total_edges}\n\n`,
    );

    if (sync.is_in_sync) {
      stream.markdown(
        `✅ All **${sync.up_to_date_files.length}** file(s) are indexed and up to date. No changes detected.`,
      );
    } else {
      stream.markdown(`⚠️ **Out of sync** — run \`/ingest\` to update.\n\n`);

      if (sync.new_files.length > 0) {
        stream.markdown(
          `**🆕 ${sync.new_files.length} new (unindexed) file(s):**\n`,
        );
        for (const f of sync.new_files) {
          stream.markdown(`*   \`${path.relative(workspaceRoot, f)}\`\n`);
        }
        stream.markdown(`\n`);
      }

      if (sync.modified_files.length > 0) {
        stream.markdown(
          `**✏️ ${sync.modified_files.length} modified (stale) file(s):**\n`,
        );
        for (const f of sync.modified_files) {
          stream.markdown(`*   \`${path.relative(workspaceRoot, f)}\`\n`);
        }
        stream.markdown(`\n`);
      }

      if (sync.removed_files.length > 0) {
        stream.markdown(
          `**🗑️ ${sync.removed_files.length} removed file(s) still in index:**\n`,
        );
        for (const f of sync.removed_files) {
          stream.markdown(`*   \`${path.relative(workspaceRoot, f)}\`\n`);
        }
      }
    }
  } catch (err) {
    if (stream) stream.markdown(`❌ Status check failed: ${err}`);
  }
}

async function handleView(
  workspaceRoot: string,
  stream?: vscode.ChatResponseStream,
) {
  if (stream)
    stream.markdown(
      "🤖 Compiling Context Graph nodes and launching Interactive Diagram...",
    );

  try {
    const rawNodes = await mcpClient.callTool("query_semantic_graph", {
      query: "",
    });
    // query_semantic_graph returns ToolResponse<QueryResult>; nodes live in .results
    const nodes: any[] = unwrapMCP<{ results: any[] }>(JSON.parse(rawNodes)).results ?? [];

    const rawEdges = await mcpClient.callTool("get_all_edges");
    // get_all_edges returns raw JSON array (no envelope)
    const edges: any[] = JSON.parse(rawEdges);

    const typeColors: Record<string, { border: string; background: string }> = {
      business_rule: { border: "#eab308", background: "#fef08a" },
      product_feature: { border: "#10b981", background: "#a7f3d0" },
      api_endpoint: { border: "#3b82f6", background: "#bfdbfe" },
      data_model: { border: "#8b5cf6", background: "#ddd6fe" },
      code_component: { border: "#64748b", background: "#e2e8f0" },
      test_scenario: { border: "#ef4444", background: "#fecaca" },
    };

    const visNodes = nodes.map((n: any) => {
      const colors = typeColors[n.type] || {
        border: "#64748b",
        background: "#cbd5e1",
      };
      return {
        id: n.id,
        label: n.name.length > 25 ? n.name.substring(0, 22) + "..." : n.name,
        color: {
          border: colors.border,
          background: colors.background,
          highlight: { border: "#38bdf8", background: "#bae6fd" },
        },
        type: n.type,
        name: n.name,
        description: n.description,
        metadata: n.metadata,
      };
    });

    const visEdges = edges.map((e: any) => ({
      from: e.source_id,
      to: e.target_id,
      label: e.relationship,
      font: { align: "middle" },
    }));

    const skillPath = path.join(
      workspaceRoot,
      ".github",
      "skills",
      "graph-visualization",
      "SKILL.md",
    );
    if (!fs.existsSync(skillPath)) {
      if (stream)
        stream.markdown(
          "❌ Visualization skill file not found in `.github/skills/graph-visualization/SKILL.md`. Please compile correctly.",
        );
      return;
    }

    const skillText = fs.readFileSync(skillPath, "utf8");
    const htmlMatch = skillText.match(/```html([\s\S]*?)```/);
    if (!htmlMatch) {
      if (stream)
        stream.markdown(
          "❌ Failed to extract Vis.js HTML template from the visualization skill.",
        );
      return;
    }

    let htmlString = htmlMatch[1];
    htmlString = htmlString.replace("__NODES_JSON__", JSON.stringify(visNodes));
    htmlString = htmlString.replace("__EDGES_JSON__", JSON.stringify(visEdges));

    const panel = vscode.window.createWebviewPanel(
      "contextGraphViewer",
      "Interactive Context Graph Viewer",
      vscode.ViewColumn.One,
      {
        enableScripts: true,
        retainContextWhenHidden: true,
      },
    );

    panel.webview.html = htmlString;
    if (stream)
      stream.markdown(
        "\n\n🎨 **Interactive Viewer launched!** Explorable network map has opened in your editor view.",
      );
  } catch (err) {
    if (stream)
      stream.markdown(`\n\n❌ Graph visualization rendering failed: ${err}`);
  }
}
