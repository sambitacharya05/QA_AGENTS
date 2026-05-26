# Test Case Creator Agent — User Guide

> **Version**: 1.0 | **Extension**: `test-case-creator` | **VS Code Chat Participant**: `@create_tests`

---

## Table of Contents

1. [Introduction & Architecture](#1-introduction--architecture)
2. [Prerequisites](#2-prerequisites)
3. [Installation](#3-installation)
4. [VS Code Extension Reference](#4-vs-code-extension-reference)
5. [End-to-End Workflow](#5-end-to-end-workflow)
6. [Python MCP Engine Tool Reference](#6-python-mcp-engine-tool-reference)
7. [Agent Personas Reference](#7-agent-personas-reference)
8. [Troubleshooting](#8-troubleshooting)
9. [Advanced Configuration](#9-advanced-configuration)

---

## 1. Introduction & Architecture

### What It Is

The **Test Case Creator Agent** is a dual-component tool for QA engineers and test architects. It combines a **VS Code extension** (the pipeline orchestrator) with a **Python FastMCP engine** (graph-aware file I/O backend) to automatically generate comprehensive, structured QA test cases from a semantic context graph.

Test cases are exported as **Azure DevOps-importable CSV files** alongside a **Markdown coverage and traceability report** — giving your team a complete audit trail from business rule to verified test step.

### Dependency on Context Builder

Test Case Creator requires a **pre-built semantic context graph** (`graph.json`) produced by the companion **`@build_context` (Context Builder) agent**. The graph encodes your codebase's business rules, API endpoints, data models, and their traceability relationships. Without it, `@create_tests /generate` cannot proceed.

**Prerequisite flow**:

```
@build_context /ingest   →   graph.json built in ingest/.context_builder/
        ↓
@create_tests /generate  →   test cases designed, verified, and exported
```

### Outputs

Each successful `/generate` run produces two files in `.test_artifacts/`:

| File | Purpose |
|------|---------|
| `test_cases_{YYYYMMDDTHHMMSS}z.csv` | Azure DevOps RFC 4180 CSV — import directly via Test Plans |
| `coverage_report_{YYYYMMDDTHHMMSS}z.md` | Markdown traceability matrix, coverage percentages, gap log |

### Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│  VS Code Copilot Chat  (@create_tests /generate)             │
│                                                              │
│  TypeScript Extension (extension.ts)                         │
│  ┌──────────────────────────────────────────────────────┐    │
│  │   Pipeline Orchestrator                              │    │
│  │                                                      │    │
│  │  1. Module Analyzer  ─→  Rule ID routing             │    │
│  │  2. Test Case Analyst ─→  Category proposal (EP/BVA) │    │
│  │  3. Test Generator   ─→  Step-by-step test cases     │    │
│  │  4. Test Verifier    ─→  Quality rubric audit        │    │
│  │  5. Coverage Reporter ─→  Markdown traceability doc  │    │
│  └────────────────────────┬─────────────────────────────┘    │
│                           │ JSON-RPC / stdio                 │
│                           ▼                                   │
│  ┌──────────────────────────────────────────────────────┐    │
│  │   Python FastMCP Engine (engine/mcp_server.py)       │    │
│  │                                                      │    │
│  │  • validate_graph     — graph integrity check        │    │
│  │  • extract_subgraph   — BFS subgraph extraction      │    │
│  │  • write_azure_csv    — CSV serialization + write    │    │
│  │  • write_coverage_report — Markdown file write       │    │
│  └──────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────┘
```

**Communication**: The TypeScript extension spawns the Python engine as a subprocess and communicates via the **Model Context Protocol (MCP)** over stdio — JSON-RPC 2.0 line-delimited messages.

**LLM access**: The 5 subagent personas run via the VS Code Language Model API, using the active GitHub Copilot model. No separate API key is required.

---

## 2. Prerequisites

Before installing, ensure all of the following are in place:

| Requirement | Minimum Version | How to Check | Notes |
|-------------|----------------|--------------|-------|
| **VS Code** | 1.93.0 | `code --version` | Required for `stream.button()` chat API used by HITL gates |
| **GitHub Copilot** | Active subscription | VS Code Extensions panel | Required for LLM model access in chat; must be signed in |
| **Python** | 3.10 | `python3 --version` | FastMCP engine uses `match` statements and 3.10+ typing features |
| **Node.js** | 18.x | `node --version` | Required for extension compilation |
| **npm** | 9.x | `npm --version` | Bundled with Node.js 18.x |
| **`@vscode/vsce`** | Latest | Installed via `npx` during install | Used to package the `.vsix` extension file |

> **Note**: The `@build_context` (Context Builder) agent is a separate VS Code extension that must also be installed if you want to build graphs from scratch. The Test Case Creator can use a `graph.json` produced by any means, not only by the Context Builder extension.

---

## 3. Installation

### 3.1 Combined Installer (Recommended)

The `install_all.sh` script (macOS/Linux) or `install_all.ps1` (Windows) installs both Context Builder and Test Case Creator in a single run from the `agents/` repository directory.

```bash
# Step 1: Navigate to your target project directory
cd /path/to/your-project

# Step 2: Run the combined installer from the agents/ repository
bash /path/to/agents/install_all.sh
```

**Windows (PowerShell)**:
```powershell
cd C:\path\to\your-project
Set-ExecutionPolicy Bypass -Scope Process
& "C:\path\to\agents\install_all.ps1"
```

**What the installer does**:

1. Detects your target project directory (current working directory)
2. Checks system prerequisites (Python 3.10+, Node.js 18+, VS Code `code` CLI)
3. Creates `.venv` and installs Python dependencies for `context_builder`
4. Creates `.venv` and installs Python dependencies for `Test_case_creator`
5. Builds and installs the **context_builder** VS Code extension VSIX
6. Builds and installs the **Test Case Creator** VS Code extension VSIX
7. Copies `.github/agents/`, `.github/skills/`, and `copilot-instructions.md` to your project
8. Writes `.vscode/mcp.json` with both MCP server registrations (`context-builder`, `test-case-creator`)
9. Writes `.vscode/settings.json` with `engineDirectory` and `serverDirectory` pointing to the shared installation
10. Creates the `./ingest/` directory for your specification documents

**After the installer completes**:

```
Cmd+Shift+P → Developer: Reload Window
```

Then type `@create_tests` in Copilot Chat to confirm the extension is active.

---

### 3.2 Manual Installation

For environments where the combined installer cannot be used:

**Step 1 — Python environment**:

```bash
cd /path/to/agents/Test_case_creator
python3 -m venv .venv

# macOS/Linux:
source .venv/bin/activate

# Windows:
.venv\Scripts\activate

pip install -r requirements.txt -r requirements-dev.txt
```

**Step 2 — Build and install the VS Code extension**:

```bash
cd vscode-extension
npm install
npm run compile
npx -y @vscode/vsce package
code --install-extension *.vsix --force
rm *.vsix
```

**Step 3 — Configure the engine directory** (`.vscode/settings.json`):

```json
{
  "testCaseCreator.engineDirectory": "/absolute/path/to/agents/Test_case_creator"
}
```

> **Why this setting is required**: The Python engine (`engine/mcp_server.py`) must run with `Test_case_creator/` as its working directory so that `engine/` is importable. Without this setting, the extension tries to run the engine from your project root, causing `ModuleNotFoundError: No module named 'engine'`.

**Step 4 — Register the MCP server** (`.vscode/mcp.json`):

```json
{
  "servers": {
    "test-case-creator": {
      "type": "stdio",
      "command": "/absolute/path/to/agents/Test_case_creator/.venv/bin/python3",
      "args": ["-m", "engine.mcp_server"],
      "cwd": "/absolute/path/to/agents/Test_case_creator"
    }
  }
}
```

> **Windows**: Use `Scripts\python.exe` instead of `bin/python3`.

**Step 5 — Copy agent files** (required for GitHub Copilot cloud agent use):

```bash
mkdir -p /path/to/your-project/.github/agents
cp /path/to/agents/Test_case_creator/vscode-extension/src/agents/*.agent.md \
   /path/to/your-project/.github/agents/
```

**Step 6 — Reload VS Code** and verify `@create_tests` appears in Copilot Chat.

---

## 4. VS Code Extension Reference

### 4.1 Chat Participant

Invoke `@create_tests` in the VS Code Copilot Chat panel (open with `Cmd+Ctrl+I` on macOS or `Ctrl+Alt+I` on Windows).

| Command | Syntax | Description |
|---------|--------|-------------|
| `/generate` | `@create_tests /generate [optional module/rule description]` | Run the full multi-agent test case generation pipeline. Optionally scope to a specific module or rule by appending a description (e.g., `@create_tests /generate billing rules`). |
| `/status` | `@create_tests /status` | List all generated test artifacts in `.test_artifacts/`. Shows file names, types (CSV/Markdown), and sizes. |

### 4.2 Command Palette Commands

These commands can also be invoked from `Cmd+Shift+P` → `Test Case Creator: ...`:

| Display Name | Command ID | Description |
|--------------|-----------|-------------|
| Test Case Creator: Generate | `testCaseCreator.generate` | Alias for `/generate` — starts the pipeline |
| Test Case Creator: Status | `testCaseCreator.status` | Alias for `/status` — lists artifacts |
| Test Case Creator: Auto-fix Gaps | `testCaseCreator.autoFixGaps` | HITL gate: triggers gap auto-remediation |
| Test Case Creator: Export Best-Effort | `testCaseCreator.exportBestEffort` | HITL gate: exports with gaps logged |
| Confirm & Generate All Test Cases | `test_case_creator.generateAll` | Inline button re-entry: approves all categories |
| Customize Test Category Selection | `test_case_creator.customize` | Inline button re-entry: opens QuickPick for category selection |
| Auto-fix Detected Gaps | `test_case_creator.autoFixGaps` | Inline HITL button: re-runs analyst + generator for gap remediation |
| Export Best-Effort Test Cases | `test_case_creator.exportAnyway` | Inline HITL button: skips correction, exports immediately |
| Test Case Creator: Validate Graph | `testCaseCreator.validateGraph` | Standalone graph validation without running the full pipeline |

### 4.3 Settings Reference

Configure via `File → Preferences → Settings` → search `testCaseCreator`, or edit `.vscode/settings.json` directly.

| Setting | Type | Default | Valid Range | When it Takes Effect |
|---------|------|---------|-------------|---------------------|
| `testCaseCreator.areaPath` | string | `TechInsurance\Claims` | Any valid ADO path | Immediately on next export |
| `testCaseCreator.maxCorrectionIterations` | integer | `3` | 1–5 | Immediately on next `/generate` run |
| `testCaseCreator.mcpTimeoutSeconds` | integer | `30` | 10–300 | Immediately on next MCP tool call (no reload needed) |
| `testCaseCreator.validateGraphOnGenerate` | boolean | `true` | `true`/`false` | Immediately on next `/generate` run |
| `testCaseCreator.engineDirectory` | string | `""` (workspace root) | Absolute path | **When MCP server process next starts** (after a crash or VS Code window reload) |

> **⚠️ Important for `engineDirectory`**: This setting is read when the Python MCP server process starts, not at extension activation. If the server is already running (which it is after the first tool call), changing this setting does **not** take effect until you reload VS Code: `Cmd+Shift+P → Developer: Reload Window`.

> **`areaPath` format**: Use the Azure DevOps format `Project\Team\Area`. In JSON, escape backslashes: `"InsurancePlatform\\QA\\Claims"`.

### 4.4 Interactive Pipeline Buttons

The pipeline pauses at two HITL (Human-in-the-Loop) gates:

**Gate 1 — Category Confirmation** (after Test Case Analyst phase):

The analyst proposes a table of test categories. Two inline buttons appear in the chat:

| Button | Action |
|--------|--------|
| **Confirm & Generate All** | Proceeds immediately with all proposed categories |
| **Customize Selection** | Opens a VS Code QuickPick multi-select panel where you can deselect unwanted categories |

**Gate 2 — Quality Gate** (after Test Verifier phase, only if gaps are found):

If the verifier detects quality issues and the iteration ceiling has not been reached:

| Button | Action |
|--------|--------|
| **Auto-fix Gaps → Re-run** | The analyst redesigns only the gap scenarios; Generator and Verifier re-run (counts toward `maxCorrectionIterations`) |
| **Export As-Is (gaps logged)** | Exports current test cases immediately; gaps are listed in the coverage report |

If `maxCorrectionIterations` is reached without resolving all gaps, the pipeline automatically switches to Export As-Is mode and notes the remaining gaps in the report.

---

## 5. End-to-End Workflow

### Prerequisites for This Walkthrough

- Context Builder (`@build_context`) is installed and has run `/ingest` on your project files
- `graph.json` exists at `{workspaceRoot}/ingest/.context_builder/graph.json`
- Test Case Creator extension is installed and active

### Step-by-Step

**1. Open Copilot Chat**

- macOS: `Cmd+Ctrl+I`
- Windows: `Ctrl+Alt+I`

**2. Verify the extension is active**

Type `@create_tests` — it should appear in the participant autocomplete list. If it does not, see [Troubleshooting §8](#8-troubleshooting).

**3. Start the pipeline**

For all rules in the graph:
```
@create_tests /generate
```

For a specific module or feature area:
```
@create_tests /generate billing rules
@create_tests /generate claims eligibility
```

**4. Pipeline stages** (watch the chat for progress messages):

| Stage | What Happens |
|-------|-------------|
| 🔍 **Graph Validation** | `validate_graph` tool verifies graph.json structural integrity (skippable via `validateGraphOnGenerate: false`) |
| ✨ **Module Routing** | Module Analyzer LLM matches your query to specific business rule IDs in the graph |
| ⚡ **Subgraph Extraction** | `extract_subgraph` BFS traversal pulls the minimal connected subgraph for the matched rules |
| 🧠 **Category Design** | Test Case Analyst proposes test categories using EP, BVA, State Transition, and RBAC methodologies |

**5. Review the category proposal table**

The analyst outputs a table like:

| # | Category | Methodology | Candidate Count | Conditions |
|---|----------|-------------|-----------------|------------|
| 1 | Valid Premium Tiers | EP | 6 | tier_A, tier_B, ... |
| 2 | Boundary Premium Limits | BVA | 4 | min_edge, max_edge |
| 3 | Ineligible Applicant Rejection | RBAC Error-guessing | 3 | underage, lapsed |

**6. Choose your action**

- Click **`[Confirm & Generate All]`** to proceed with all categories
- Click **`[Customize Selection]`** to open the QuickPick and deselect any categories

**7. Generator and Verifier run**

The Test Generator produces step-by-step test cases for each confirmed category. The Test Verifier audits them against a 5-point quality rubric (logical flow, EP/BVA coverage, business rule traceability, CSV format compliance, Step 1 precondition invariant).

**8. HITL Quality Gate** (if gaps detected)

If the verifier finds issues:
- Click **`[Auto-fix Gaps → Re-run]`** to attempt automated remediation
- Click **`[Export As-Is]`** to skip correction and export immediately (gaps logged in report)

**9. Artifacts are written**

The Coverage Reporter compiles the final Markdown document. The extension calls `write_azure_csv` and `write_coverage_report` to write both files to `.test_artifacts/`.

**10. Import to Azure DevOps**

1. Open Azure DevOps → **Test Plans** → select your test plan
2. Click **Import test cases** (or use the `...` menu)
3. Upload `test_cases_{timestamp}z.csv`
4. Map the `Area Path` column to your configured `testCaseCreator.areaPath`

**11. Check artifact summary**

```
@create_tests /status
```

This shows all generated files with their timestamps and sizes.

---

## 6. Python MCP Engine Tool Reference

The MCP engine is a FastMCP server running as a subprocess of the VS Code extension. Communication uses the **Model Context Protocol** (JSON-RPC 2.0 over stdio). These are the 4 tools it exposes:

---

### `validate_graph`

**Purpose**: Validates the structural integrity of `graph.json` before the generation pipeline uses it. Called automatically at the start of `/generate` when `validateGraphOnGenerate` is `true`.

**Signature**:
```python
def validate_graph(graph_path: str) -> dict
```

**Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `graph_path` | string | ✅ | Absolute path to `graph.json` |

**Return value**:
```json
{
  "is_valid": true,
  "errors": [],
  "warnings": ["No 'test_scenario' nodes — BDD column will be empty"],
  "stats": {
    "total_nodes": 42,
    "business_rule_count": 8,
    "edge_count": 15,
    "traversable_edge_count": 11
  }
}
```

**Blocking errors** (cause `is_valid: false`):
- Graph JSON is malformed or missing the `nodes` key
- Zero `business_rule` nodes found
- One or more nodes are missing the `id` field
- One or more edges reference a `source` or `target` node that does not exist

**Non-blocking warnings** (`is_valid: true`, warnings list populated):
- No `test_scenario` nodes (BDD column in CSV will be empty)
- No traversable edges (subgraph extraction will return only directly matched nodes)

**Example JSON-RPC call**:
```json
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "params": {
    "name": "validate_graph",
    "arguments": {
      "graph_path": "/your-project/ingest/.context_builder/graph.json"
    }
  },
  "id": 1
}
```

---

### `extract_subgraph`

**Purpose**: Performs a BFS traversal from the given business rule IDs to extract the minimal connected subgraph that the test generation pipeline should focus on. This ensures the Analyst and Generator only see the relevant context for the requested rules.

**Signature**:
```python
def extract_subgraph(graph_path: str, rule_ids: list[str], bidirectional: bool = True) -> dict
```

**Parameters**:

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `graph_path` | string | ✅ | — | Absolute path to `graph.json` |
| `rule_ids` | list[string] | ✅ | — | Business rule IDs to use as BFS seeds. Use `["*"]` to include all rules |
| `bidirectional` | boolean | ❌ | `true` | When `true`, traverses edges in both directions; discovers test scenarios that `TRACE_TO` a rule |

**Return value**:
```json
{
  "matched_rule_ids": ["RULE_001", "RULE_002"],
  "subgraph_nodes": {
    "RULE_001": { "id": "RULE_001", "type": "business_rule", "label": "Premium cap rule" },
    "SCENARIO_005": { "id": "SCENARIO_005", "type": "test_scenario", "label": "Test cap at $10k" }
  },
  "subgraph_edges": [
    { "source": "RULE_001", "target": "SCENARIO_005", "type": "TRACE_TO" }
  ]
}
```

**Behavior notes**:
- If a `rule_id` in the input list is not found in the graph, it is **silently skipped** (no error raised, no warning in the return value)
- Passing `rule_ids: ["*"]` returns the entire graph (all nodes and edges)
- The BFS depth is bounded by the graph diameter — no infinite loops

---

### `write_azure_csv`

**Purpose**: Serializes a `TestCasesArray` JSON object into Azure DevOps RFC 4180 CSV format and writes it to disk in `.test_artifacts/`.

**Signature**:
```python
def write_azure_csv(test_cases_json: str, output_path: str, area_path: str) -> dict
```

**Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `test_cases_json` | string | ✅ | JSON string of the `TestCasesArray` schema (an array of test case objects) |
| `output_path` | string | ✅ | Absolute path where the CSV file should be written (extension creates this path) |
| `area_path` | string | ✅ | Azure DevOps Area Path written into the CSV `Area Path` column (e.g., `TechInsurance\Claims`) |

**Return value**:
```json
{
  "success": true,
  "rows_written": 24,
  "output_path": "/your-project/.test_artifacts/test_cases_20250525T143022z.csv"
}
```

**Error conditions**:
- `ValueError`: `test_cases_json` is malformed or does not match the expected schema
- `IOError`: Write to `output_path` fails (disk full, permission error)

---

### `write_coverage_report`

**Purpose**: Writes a pre-compiled Markdown string to disk as a UTF-8 encoded `.md` file in `.test_artifacts/`. The Markdown content is assembled by the Coverage Reporter LLM agent; this tool handles the file write only.

**Signature**:
```python
def write_coverage_report(markdown_content: str, output_path: str) -> dict
```

**Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `markdown_content` | string | ✅ | The complete Markdown document string to write |
| `output_path` | string | ✅ | Absolute path where the Markdown file should be written |

**Return value**:
```json
{
  "success": true,
  "output_path": "/your-project/.test_artifacts/coverage_report_20250525T143025z.md"
}
```

**Error conditions**:
- `IOError`: Write to `output_path` fails (disk full, permission error)

---

## 7. Agent Personas Reference

The Test Case Creator pipeline uses **5 LLM subagent personas** that run sequentially. Each persona is a prompt file loaded from `out/agents/` (compiled output of `src/agents/`). Understanding each agent's role helps you interpret pipeline output and troubleshoot unexpected behavior.

---

### Agent 1: Module Analyzer

| Attribute | Value |
|-----------|-------|
| **File** | `module_analyzer.agent.md` |
| **Stage** | 1 — Routing |
| **Role** | Semantic routing agent. Classifies the user's query against the business rule index to determine which rule IDs to target. |

**Input** (from extension):
```json
{
  "userQuery": "billing rules",
  "ruleIndex": [
    { "id": "RULE_001", "label": "Premium cap rule", "category": "billing" },
    ...
  ]
}
```

**Output** (JSON object):
```json
{
  "is_module_specific": true,
  "detected_module": "billing",
  "matched_rule_ids": ["RULE_001", "RULE_007", "RULE_012"],
  "reasoning": "User query 'billing rules' matches rules in the billing category."
}
```

**Key invariant**: For generic queries (e.g., "generate all tests"), must return `"matched_rule_ids": ["*"]` — **never an empty array**. An empty array would cause the subgraph extraction to return no nodes, silently breaking the pipeline.

---

### Agent 2: Test Case Analyst (Maker-1)

| Attribute | Value |
|-----------|-------|
| **File** | `test_case_analyst.agent.md` |
| **Stage** | 2 — Planning |
| **Role** | QA Scenario Designer. Applies EP, BVA, State Transition, and RBAC methodologies to propose a comprehensive set of test categories. |

**Input**: Business rule details and user constraints from the subgraph.

**Output** (`CategoryProposalSchema` JSON): An array of proposed test categories with:
- `category_id`, `category_name`, `methodology`, `step_count`, `total_conditions_count`
- `condition_ids` (list of business rule conditions covered)

**Key invariant**: Every `step_count` and `total_conditions_count` must be a non-zero positive integer. A zero value indicates the analyst failed to meaningfully analyze the rule — the extension treats this as an error condition.

**Correction-Mode**: When re-invoked after verifier feedback, the analyst operates in **Correction-Mode** — it re-designs only the specific gap categories identified in the Gap Log, leaving non-gap categories unchanged.

---

### Agent 3: Test Generator (Maker-2)

| Attribute | Value |
|-----------|-------|
| **File** | `test_generator.agent.md` |
| **Stage** | 3 — Generation |
| **Role** | Step-action outcome generator. Produces concrete, numbered, step-by-step manual test cases for each confirmed category. |

**Input**: The confirmed category list from Gate 1 and the full subgraph context.

**Output** (`TestCasesArraySchema` JSON): An array of test case objects, each with:
- `test_case_id`, `title`, `area_path`, `priority`, `category`
- `steps`: array of `{ step_number, action, expected_result }`
- `bdd_scenario` (optional), `mapped_rule_ids`

**Step 1 Precondition Invariant**: Step 1 of every test case **must** be a setup or navigation step (e.g., "Log into the application as a valid user with billing access"). Step 1 must **never** contain an assertion or a validation action. This invariant is enforced by the verifier and will be flagged as a gap if violated.

**Scope Lock**: The generator must not create test cases for categories that were not confirmed by the user. If the user deselected a category at Gate 1, that category must not appear in the output.

---

### Agent 4: Test Verifier (Checker)

| Attribute | Value |
|-----------|-------|
| **File** | `test_verifier.agent.md` |
| **Stage** | 4 — Auditing |
| **Role** | Independent quality auditor. Audits the generated test cases against a 5-point quality rubric. |

**Input**: The generated test cases array and the original subgraph context.

**Output** (`GapLogSchema` JSON):
```json
{
  "is_compliant": false,
  "compliance_score": 0.85,
  "detected_gaps": [
    {
      "gap_id": "GAP_001",
      "severity": "major",
      "affected_test_case_id": "TC_003",
      "issue": "Step 1 contains an assertion ('Verify that...')",
      "recommendation": "Change Step 1 to a navigation or precondition setup step"
    }
  ],
  "rubric_scores": {
    "logical_flow": 1.0,
    "ep_bva_coverage": 0.9,
    "business_rule_traceability": 0.8,
    "azure_csv_format_compliance": 1.0,
    "step1_precondition_invariant": 0.5
  }
}
```

**5-Point Quality Rubric**:

| Point | Criterion |
|-------|-----------|
| 1 | **Logical flow** — steps follow a realistic user journey |
| 2 | **EP and BVA coverage** — each EP partition and boundary has at least one test |
| 3 | **Business rule traceability** — every test case maps to at least one `rule_id` from the subgraph |
| 4 | **Azure DevOps CSV format compliance** — all required fields present and correctly typed |
| 5 | **Step 1 Precondition Invariant** — Step 1 is setup only, never an assertion |

**Key invariant**: `is_compliant` must always be a boolean. `detected_gaps` must always be an array (can be empty if fully compliant). The extension reads these fields to decide whether to show the quality gate buttons.

---

### Agent 5: Coverage Reporter (Compiler)

| Attribute | Value |
|-----------|-------|
| **File** | `coverage_reporter.agent.md` |
| **Stage** | 5 — Reporting |
| **Role** | Coverage and traceability report compiler. Generates the final Markdown document summarizing the entire generation session. |

**Input**: Complete pipeline state — test cases, gap log, subgraph metrics, iteration history, user constraints.

**Output**: A raw Markdown string (the entire document content, not wrapped in a code block). The extension passes this directly to `write_coverage_report`.

**Report sections** include:
- Executive summary with compliance percentage
- Requirement coverage matrix (rules → test cases)
- EP/BVA condition coverage table
- End-to-end traceability matrix (rules → steps → scenarios)
- Verification loop history (iterations, gap counts, corrections made)
- Remaining gap analysis (if any)

**Key invariant**: The output must be **raw Markdown only** — do NOT wrap in ` ```markdown ``` ` code fences. The extension writes this string verbatim to the `.md` file.

---

## 8. Troubleshooting

### Common Errors and Resolutions

| Error / Symptom | Root Cause | Resolution |
|-----------------|------------|------------|
| "Context graph not found. Expected: `ingest/.context_builder/graph.json`" | Context Builder (`@build_context`) has not been run on this project | Run `@build_context /ingest` first to build the semantic graph. The graph file must exist at `{workspaceRoot}/ingest/.context_builder/graph.json`. |
| "MCP tool call 'extract_subgraph' timed out after 30s" | Graph is very large or machine is under load | Increase `testCaseCreator.mcpTimeoutSeconds` in settings to `60` or `120`. Changes take effect immediately without reloading. |
| "No Copilot models available for module_analyzer.agent.md" | GitHub Copilot is not signed in or subscription lapsed | Sign in to GitHub Copilot via the bottom-left user icon in VS Code. Verify your subscription at github.com/settings/billing. |
| `@create_tests` not found when typing `@` in Copilot Chat | Extension not installed or not activated | Check the Extensions panel for "Test Case Creator Agent". If installed, try `Cmd+Shift+P → Developer: Reload Window`. If not found, run `install_all.sh` or install the VSIX manually. |
| `ModuleNotFoundError: No module named 'engine'` | `testCaseCreator.engineDirectory` is not set or points to the wrong directory | Set `testCaseCreator.engineDirectory` to the absolute path of the `Test_case_creator/` directory (the folder containing `engine/`). Then reload VS Code to restart the MCP server. |
| MCP server process exits immediately with code 1 | Python virtual environment not created, or dependencies missing | Run `install_all.sh` again. Or manually: `cd Test_case_creator && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt`. |
| `test_cases_*.csv` not appearing in `.test_artifacts/` | `write_azure_csv` MCP tool call failed | Open VS Code Output panel → select "Test Case Creator" from the dropdown. Look for `[MCP Server Error]` lines showing the Python traceback. Common causes: disk full, write permission denied, or malformed JSON from the generator. |
| Session expired error on button click | VS Code session reloaded (e.g., extension host crashed) between pipeline start and button click | The pipeline state is lost on session reload. Re-run `@create_tests /generate` from the beginning. |
| Agent returns non-JSON output or gibberish | LLM hallucinated free text instead of the required JSON schema | The extension uses a fallback mock for parsing errors — this is non-fatal. Check the Output panel for a `[parseAgentOutput] Fallback` message. Consider re-running the pipeline; if it persists, reduce the scope of the query. |
| "Graph validation fails: No business_rule nodes found" | Context Builder did not extract any business rules from the ingest documents | Verify that your ingest documents are in supported formats (PDF, DOCX, XLSX, TXT, Markdown). Re-run `@build_context /ingest` and check the Copilot Chat output for extraction results. |
| "Confirm & Generate All" button does nothing | VS Code Copilot Chat `stream.button()` requires VS Code 1.93.0+ | Check `code --version`. If below 1.93.0, update VS Code. |

### Checking the Output Panel

For detailed diagnostic output:

1. `View → Output` (or `Cmd+Shift+U`)
2. Select **"Test Case Creator"** from the dropdown
3. Look for `[MCP Client]`, `[MCP Server]`, and `[MCP Server Error]` messages

---

## 9. Advanced Configuration

### 9.1 Multi-Project Setups

When using Test Case Creator across multiple projects, each project needs its own `.vscode/` configuration, but all projects share a **single** Python engine installation in the `agents/` directory.

**Recommended structure**:

```
agents/
├── context_builder/           ← Shared Python engine (context builder)
│   └── .venv/
├── Test_case_creator/         ← Shared Python engine (test creator)
│   └── .venv/
└── install_all.sh             ← Run once per target project

project-alpha/                 ← Target project 1
├── .vscode/
│   ├── mcp.json               ← Points to agents/ engines
│   └── settings.json          ← testCaseCreator.engineDirectory set
└── ingest/                    ← Project-specific spec documents

project-beta/                  ← Target project 2
├── .vscode/
│   ├── mcp.json               ← Same engine paths
│   └── settings.json          ← Same testCaseCreator.engineDirectory
└── ingest/                    ← Project-specific spec documents
```

Run `install_all.sh` from each project directory:

```bash
cd ~/projects/project-alpha && bash ~/agents/install_all.sh
cd ~/projects/project-beta  && bash ~/agents/install_all.sh
```

### 9.2 Configuring the Azure DevOps Area Path

The `areaPath` setting determines where generated test cases appear in Azure DevOps Test Plans. Format:

```
ProjectName\Team\FeatureArea
```

In `.vscode/settings.json` (use double backslashes for JSON escape):

```json
{
  "testCaseCreator.areaPath": "InsurancePlatform\\QA\\ClaimsProcessing"
}
```

The `area_path` value is written directly into the `Area Path` column of the exported CSV. If the path does not match a valid Azure DevOps area path in your organization, the import will succeed but the test cases will appear at the default area.

**Finding your Area Path**: In Azure DevOps → Project Settings → Boards → Areas → copy the full hierarchy path shown.

### 9.3 Disabling Graph Validation for Large Graphs

On graphs with 1,000+ nodes, the `validate_graph` MCP round-trip adds 2–5 seconds to each `/generate` run. If you have already confirmed your graph is structurally correct, you can skip this check:

```json
{
  "testCaseCreator.validateGraphOnGenerate": false
}
```

> **When to re-enable**: After any change to your ingest documents and after re-running `@build_context /ingest`. Large graphs can develop structural issues (orphaned edges, missing node IDs) after partial re-ingests.

### 9.4 Adjusting the Verification-Correction Loop Ceiling

The verifier-generator correction loop runs up to `maxCorrectionIterations` times before falling back to Export As-Is mode.

**Reduce to 1** for small, well-understood rule sets to skip the correction cycle entirely:
```json
{
  "testCaseCreator.maxCorrectionIterations": 1
}
```

**Increase to 5** for complex, multi-rule generation sessions where test quality is critical and the LLM may need multiple attempts to satisfy all rubric points:
```json
{
  "testCaseCreator.maxCorrectionIterations": 5
}
```

> **Performance note**: Each correction iteration re-calls the Analyst, Generator, and Verifier in sequence — typically 3 LLM requests and 2 MCP tool calls. With `maxCorrectionIterations: 5`, a worst-case run calls the LLM up to 15 times.

### 9.5 Increasing MCP Timeout for Slow Environments

If you regularly see timeout errors (especially with graphs over 500 nodes), increase the per-call timeout:

```json
{
  "testCaseCreator.mcpTimeoutSeconds": 120
}
```

This setting takes effect **immediately** — no reload required. The timeout applies per individual MCP tool call, not to the entire pipeline.

### 9.6 Understanding the `engineDirectory` Setting Behavior

The `testCaseCreator.engineDirectory` setting is read by the TypeScript extension when it **spawns the Python MCP server process** — not at extension activation time. This means:

- If you set `engineDirectory` and the server is **not yet running**: the new path takes effect on the first tool call
- If you set `engineDirectory` and the server **is already running**: the new path takes effect **after** the current server process exits (crash, extension deactivation, or VS Code window reload)

**To force the new setting to take effect**: `Cmd+Shift+P → Developer: Reload Window`

This "live lambda" design means that if your Python server crashes and restarts automatically (via `callTool()` auto-restart), it picks up the latest `engineDirectory` without requiring a manual reload.
