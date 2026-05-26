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
              "*   `/view` - Open an interactive visual diagram of the Context Graph inside your IDE!",
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

    const rulePromptPath = path.join(
      workspaceRoot,
      ".github",
      "agents",
      "rule_extractor.agent.md",
    );
    const rulePrompt = fs.existsSync(rulePromptPath)
      ? fs.readFileSync(rulePromptPath, "utf8")
      : "";

    const techPromptPath = path.join(
      workspaceRoot,
      ".github",
      "agents",
      "tech_mapper.agent.md",
    );
    const techPrompt = fs.existsSync(techPromptPath)
      ? fs.readFileSync(techPromptPath, "utf8")
      : "";

    const linkPromptPath = path.join(
      workspaceRoot,
      ".github",
      "agents",
      "relationship_linker.agent.md",
    );
    const linkPrompt = fs.existsSync(linkPromptPath)
      ? fs.readFileSync(linkPromptPath, "utf8")
      : "";

    const rawDocs = await mcpClient.callTool("get_raw_documents");
    const docs = JSON.parse(rawDocs);

    // Stage 2 (rule extraction) only needs spec/requirements files.
    // Stage 3 (tech mapping) only needs code/feature files.
    // Sending all 26 files (158 KB) to every LLM call burns ~40 K tokens per round
    // and triggers extended-reasoning mode, which drains Copilot premium requests.
    const SPEC_EXTS   = new Set([".docx", ".pdf", ".xlsx", ".csv", ".md"]);
    const CODE_EXTS   = new Set([".java", ".ts", ".tsx", ".js", ".py", ".feature"]);

    const specContext = docs
      .filter((d: any) => SPEC_EXTS.has(path.extname(d.path).toLowerCase()))
      .map((d: any) => `### FILE: ${d.path}\n${d.content}\n`)
      .join("\n");

    const codeContext = docs
      .filter((d: any) => CODE_EXTS.has(path.extname(d.path).toLowerCase()))
      .map((d: any) => `### FILE: ${d.path}\n${d.content}\n`)
      .join("\n");

    const businessRuleTool: vscode.LanguageModelChatTool = {
      name: "add_business_rule_node",
      description:
        "Records an identified business rule or product feature in the database.",
      inputSchema: {
        type: "object" as const,
        properties: {
          id: { type: "string" },
          type: { type: "string" },
          name: { type: "string" },
          description: { type: "string" },
          metadata: {
            type: "string",
            description:
              "Optional JSON string of metadata including source_file, implementation_status, and code_expression_profile",
          },
        },
        required: ["id", "type", "name", "description"],
      },
    };

    const techComponentTool: vscode.LanguageModelChatTool = {
      name: "add_tech_component_node",
      description:
        "Registers an API endpoint or data model schema in the database.",
      inputSchema: {
        type: "object" as const,
        properties: {
          id: { type: "string" },
          type: { type: "string" },
          name: { type: "string" },
          description: { type: "string" },
          metadata: {
            type: "string",
            description:
              "Optional JSON string of metadata including source_file and technical details",
          },
        },
        required: ["id", "type", "name", "description"],
      },
    };

    const semanticEdgeTool: vscode.LanguageModelChatTool = {
      name: "add_semantic_edge",
      description:
        "Registers a logical connection or edge between two existing nodes.",
      inputSchema: {
        type: "object" as const,
        properties: {
          source_id: { type: "string" },
          target_id: { type: "string" },
          relationship: { type: "string" },
        },
        required: ["source_id", "target_id", "relationship"],
      },
    };

    const toolHandler = async (
      name: string,
      input: Record<string, unknown>,
    ): Promise<unknown> => {
      if (
        name === "add_business_rule_node" ||
        name === "add_tech_component_node"
      ) {
        await mcpClient.callTool("add_node", {
          node_id: input.id,
          node_type: input.type,
          name: input.name,
          description: input.description,
          metadata:
            typeof input.metadata === "string"
              ? input.metadata
              : JSON.stringify(input.metadata ?? {}),
        });
        return { success: true };
      } else if (name === "add_semantic_edge") {
        await mcpClient.callTool("add_edge", {
          source_id: input.source_id,
          target_id: input.target_id,
          relationship: input.relationship,
          metadata: "{}",
        });
        return { success: true };
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

    if (stream)
      stream.markdown(
        `**Stage 2/4: Rule Extraction...** Running rule extractor subagent using **${modelLabel}**...\n`,
      );
    try {
      await runLLMAgentWithTools(
        `${rulePrompt}\n\nExtract all insurance business rules and product features from these specification documents:\n\n${specContext}`,
        [businessRuleTool],
        toolHandler,
        resolvedModel,
        token,
      );
      if (stream)
        stream.markdown(
          `  ✅ Successfully executed Rule Extraction using **${modelLabel}**.\n\n`,
        );
    } catch (err) {
      if (stream) stream.markdown(`  ⚠️ Rule extraction failed: ${err}.\n\n`);
    }

    if (stream)
      stream.markdown(
        `**Stage 3/4: Technical Component Mapping...** Running technical mapper subagent using **${modelLabel}**...\n`,
      );
    try {
      await runLLMAgentWithTools(
        `${techPrompt}\n\nExtract all API endpoints and data model schemas from these code files:\n\n${codeContext}`,
        [techComponentTool],
        toolHandler,
        resolvedModel,
        token,
      );
      if (stream)
        stream.markdown(
          `  ✅ Successfully executed Technical Mapping using **${modelLabel}**.\n\n`,
        );
    } catch (err) {
      if (stream) stream.markdown(`  ⚠️ Technical mapping failed: ${err}.\n\n`);
    }

    if (stream)
      stream.markdown(
        `**Stage 4/4: Relationship Linking...** Running relationship linker subagent using **${modelLabel}**...\n`,
      );
    try {
      const currentNodesRaw = await mcpClient.callTool("query_semantic_graph", {
        query: "",
      });
      // query_semantic_graph returns ToolResponse<QueryResult>; nodes live in .results
      const queryResult = unwrapMCP<{ results: any[] }>(JSON.parse(currentNodesRaw));
      const nodesListContext = (queryResult.results ?? []).map((n: any) => ({
        id: n.id,
        type: n.type,
        name: n.name,
        description: n.description,
      }));

      await runLLMAgentWithTools(
        `${linkPrompt}\n\nEstablish all valid logical connections (edges) between these discovered entities:\n\n${JSON.stringify(nodesListContext, null, 2)}`,
        [semanticEdgeTool],
        toolHandler,
        resolvedModel,
        token,
      );
      if (stream)
        stream.markdown(
          `  ✅ Successfully executed Relationship Linking using **${modelLabel}**.\n\n`,
        );
    } catch (err) {
      if (stream)
        stream.markdown(`  ⚠️ Relationship linker failed: ${err}.\n\n`);
    }

    const graphSummary = await mcpClient.callTool("get_graph_summary");
    const counts = JSON.parse(graphSummary);

    if (stream)
      stream.markdown(
        `### 🎉 CONTEXT SYNCHRONIZATION COMPLETE!\n\n` +
          `The Semantic Context Graph has been fully updated.\n` +
          `*   **Total Entities Discovered**: ${counts.total_nodes}\n` +
          `*   **Total Logical Connections**: ${counts.total_edges}\n\n` +
          `You can run \`/view\` now to explore your graph visually!`,
      );
  } catch (err) {
    if (stream) stream.markdown(`❌ Failed to complete ingestion: ${err}`);
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
