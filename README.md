# QA Agents — Context Builder + Test Case Creator

> An end-to-end AI-assisted QA pipeline for VS Code: parse your specification documents into a semantic knowledge graph, then generate, verify, and export structured test cases — all through GitHub Copilot Chat.

---

## Pipeline Overview

```
Specification Documents                    Generated Outputs
(.docx · .pdf · .xlsx · .feature · .java)
          │
          ▼
  ┌───────────────────┐      graph.json      ┌────────────────────────┐
  │  Context Builder  │ ──────────────────▶  │  Test Case Creator     │
  │  @build_context   │                      │  @create_tests         │
  └───────────────────┘                      └────────────────────────┘
          │                                            │
          │  Semantic Knowledge Graph                  │  test_cases_*.csv
          │  (NetworkX JSON)                           │  coverage_report_*.md
          ▼                                            ▼
   Business rules, API endpoints,          Azure DevOps–ready test suite
   data models, code components,           + traceability matrix
   UI elements — fully linked
```

---

## Tools

### Context Builder (`@build_context`)

**What it does:** Scans your workspace, parses heterogeneous specification documents (Word, PDF, Excel, CSV, Java, TypeScript, Gherkin BDD, OpenAPI), and compiles everything into a **Semantic Context Graph** stored as `graph.json`. Downstream agents and the Test Case Creator consume this graph to understand business rules, API contracts, data models, and their relationships.

| Attribute | Detail |
|---|---|
| **Language** | Python 3.10+ (MCP server) + TypeScript (VS Code extension) |
| **Protocol** | Model Context Protocol (MCP) via FastMCP, stdio transport |
| **Graph engine** | NetworkX (in-memory, serialized to JSON) |
| **VS Code participant** | `@build_context` |
| **Python entry point** | `context_builder/main.py` |
| **Extension entry point** | `context_builder/vscode-extension/src/extension.ts` |

**Supported input formats:** `.docx` · `.pdf` · `.xlsx` · `.csv` · `.java` · `.ts` · `.py` · `.go` · `.feature` (Gherkin BDD) · `.yaml`/`.json` (OpenAPI)

**Key capabilities:**
- Tree-sitter AST parsing for Java, TypeScript, Python, and Go source files
- Hybrid edge generation: heuristic TF-IDF scoring + Copilot-validated edge proposals
- Parallel ingestion via configurable shard strategies
- Behavioral drift detection between requirements and implementation
- Framework blueprint extraction (coding standards, reusable method index)

**Core MCP tools:** `ingest_workspace` · `query_semantic_graph` · `get_rule_traceability` · `apply_edge_proposals` · `get_behavioral_drift_report`

---

### Test Case Creator (`@create_tests`)

**What it does:** Reads the `graph.json` produced by Context Builder and orchestrates a dual-agent pipeline to plan, draft, verify, and export high-quality test cases. It follows a rigorous 6-phase QA methodology (Rule Pre-Classification → Attribute Identification → Equivalence Class Table → Happy Path Design → Linear Expansion Matrix → Boundary/RBAC augmentation) with a self-correcting verification loop.

| Attribute | Detail |
|---|---|
| **Language** | Python 3.10+ (MCP engine) + TypeScript (VS Code extension) |
| **Protocol** | Model Context Protocol (MCP) via FastMCP, stdio transport |
| **VS Code participant** | `@create_tests` |
| **Python entry point** | `Test_case_creator/engine/mcp_server.py` |
| **Extension entry point** | `Test_case_creator/vscode-extension/src/extension.ts` |

**Output formats:**
- `test_cases_*.csv` — RFC 4180, Azure DevOps–compatible (import via **Test Plans → Import Test Cases**)
- `coverage_report_*.md` — traceability matrix, gap analysis, verification loop history

**Agent pipeline:**

| Agent | Role |
|---|---|
| Module Analyzer | Routes user query to matched business rules in the graph |
| Test Case Analyst (Maker-1) | 6-phase scenario designer |
| Test Generator (Maker-2) | Produces numbered step-by-step manual test cases |
| Test Verifier (Checker) | 7-point quality audit with gap correction loop |
| Coverage Reporter | Generates markdown traceability matrix and coverage report |

**Core MCP tools:** `validate_graph` · `extract_subgraph`

---

## Prerequisites

| Requirement | Minimum Version | Check |
|---|---|---|
| **Python** | 3.10 | `python3 --version` |
| **pip** | any | `python3 -m pip --version` |
| **Node.js** | 18.x | `node --version` |
| **npm** | 9.x | `npm --version` |
| **VS Code `code` CLI** | any | `code --version` |
| **GitHub Copilot** | active subscription | VS Code status bar → Accounts |

> **`code` CLI is soft-required.** If it is not on your PATH the installer will still complete — it builds the `.vsix` files and prints manual installation instructions. See [Troubleshooting](#troubleshooting) below.

---

## Installation

The combined installer (`install_all.sh` / `install_all.ps1`) sets up **both** agents in a single automated run. It creates isolated Python virtual environments, builds and installs both VS Code extensions, and wires all MCP server configuration into your target project — without copying anything into your project's own Python environment.

> **Run the installer from your target project directory, not from inside `agents/`.**

### macOS / Linux

```bash
cd /path/to/your-project
bash /path/to/agents/install_all.sh
```

### Windows (PowerShell)

```powershell
cd C:\path\to\your-project
Set-ExecutionPolicy Bypass -Scope Process
& "C:\path\to\agents\install_all.ps1"
```

> `Set-ExecutionPolicy Bypass -Scope Process` applies only to the current terminal session and does not change your system-wide policy.

### After installation — reload VS Code

```
Cmd+Shift+P  (macOS)
Ctrl+Shift+P (Windows / Linux)
→ Developer: Reload Window
```

---

### What the installer does (10 steps)

| # | Step | Action |
|---|---|---|
| 0 | Resolve directories | Detects `AGENTS_DIR` and `TARGET_WORKSPACE`. Aborts if they are the same. |
| 1 | Check prerequisites | Verifies Python 3.10+, pip, Node/npm, and VS Code `code` CLI. |
| 2 | context_builder Python env | Creates `agents/context_builder/.venv/` and installs `requirements.txt`. |
| 3 | Test_case_creator Python env | Creates `agents/Test_case_creator/.venv/` and installs `requirements.txt` + `requirements-dev.txt`. |
| 4 | Build & install context_builder extension | `npm install` → `npm run compile` → `vsce package` → `code --install-extension`. |
| 5 | Build & install Test_case_creator extension | Same process for Test_case_creator. VSIX removed after install. |
| 6 | Merge `.github/` content | Copies agent prompt files and skills into `TARGET_WORKSPACE/.github/`. Appends Copilot instructions without duplicating on re-runs. |
| 7 | Register MCP servers | Writes both server entries into `TARGET_WORKSPACE/.vscode/mcp.json`. |
| 8 | Write VS Code settings | Writes `contextBuilder.serverDirectory` and `testCaseCreator.engineDirectory` into `settings.json`. |
| 9 | Initialize `ingest/` folder | Creates `TARGET_WORKSPACE/ingest/` if absent. |
| 10 | Print summary | Lists what was installed and exact next steps. |

The installer is **idempotent** — safe to re-run after pulling updates. It reuses existing `.venv` directories, merges (never overwrites) `.vscode/` config, and skips Copilot instructions that are already present.

---

## Quick Start

```
1. Drop specification files into ./ingest/
   (Word docs, PDFs, Excel sheets, Gherkin .feature files, OpenAPI specs, Java/TS source)

2. Build the semantic graph:
   @build_context /ingest

3. Generate test cases:
   @create_tests /generate
```

---

## Outputs

After `@create_tests /generate` completes, find your outputs in `.test_artifacts/`:

| File | Description | How to use |
|---|---|---|
| `test_cases_*.csv` | Azure DevOps–compatible test case export | Test Plans → Import Test Cases |
| `coverage_report_*.md` | Traceability matrix, gap analysis, verification loop history | Review in VS Code or any Markdown viewer |

---

## Verification

After reloading VS Code, run these checks:

**1. Confirm extensions are installed:**
```bash
code --list-extensions | grep -E "context-builder|test-case-creator"
```

**2. Confirm chat participants are active:**
Open Copilot Chat (`Cmd+Ctrl+I` / `Ctrl+Alt+I`) and type `@` — both `@build_context` and `@create_tests` should appear.

**3. Confirm MCP servers are registered:**
```bash
python3 -c "
import json
d = json.load(open('.vscode/mcp.json'))
servers = d.get('servers', {})
for name in ['context-builder', 'test-case-creator']:
    assert name in servers, f'MISSING: {name}'
    print(f'OK: {name}  →  {servers[name][\"command\"]}')
"
```

**4. Run a status check:**
```
@build_context /status
```
Expected: agent responds confirming access to the MCP server and `ingest/` directory.

---

## Troubleshooting

| Symptom | Quick fix |
|---|---|
| `Do not run install_all.sh from inside the agents/ directory` | `cd /path/to/your-project` first, then re-run |
| `Python 3.10+ required` | Install from [python.org](https://www.python.org/downloads/) or `brew install python@3.12` |
| `npm/Node.js not found` | Install LTS from [nodejs.org](https://nodejs.org/) |
| `code: command not found` (non-fatal) | macOS: `Cmd+Shift+P → Shell Command: Install 'code' command in PATH` |
| `@build_context` / `@create_tests` not in Copilot Chat | Extensions panel → verify both installed and enabled → Reload Window |
| `ModuleNotFoundError: No module named 'engine'` | Re-run installer; check `testCaseCreator.engineDirectory` in `settings.json` |
| `Failed to get GitHub Copilot token` | Status bar → Accounts → sign in with an active Copilot subscription |

For full troubleshooting details see **[INSTALL.md](INSTALL.md)**.

---

## Repository Structure

```
agents/
├── README.md                    ← this file
├── INSTALL.md                   ← full installation guide
├── REVIEW_FINDINGS.md           ← graph & test quality review findings
├── context_builder_spec.md      ← Context Builder technical specification
├── test_case_creator_spec.md    ← Test Case Creator technical specification
├── install_all.sh               ← combined installer (macOS / Linux)
├── install_all.ps1              ← combined installer (Windows PowerShell)
├── package_agent.sh             ← agent packaging utility
├── context_builder/             ← Context Builder source
│   ├── main.py                  ← MCP server entry point
│   ├── requirements.txt
│   ├── parsers/                 ← document parsers (Word, PDF, Excel, Gherkin, AST…)
│   ├── engine/                  ← extractor, edge mappers, search, ingestion pipeline
│   ├── db/                      ← graph store, local store, blueprint store, provenance
│   ├── vscode-extension/src/    ← TypeScript VS Code extension (@build_context)
│   ├── tests/                   ← unit + integration tests
│   └── docs/                    ← user guide
└── Test_case_creator/           ← Test Case Creator source
    ├── engine/                  ← MCP server, graph validator, CSV writer
    ├── requirements.txt
    ├── requirements-dev.txt
    ├── vscode-extension/src/    ← TypeScript VS Code extension (@create_tests)
    ├── tests/                   ← unit + integration tests
    └── docs/                    ← user guide
```

---

## Further Reading

- [`INSTALL.md`](INSTALL.md) — full installation guide including idempotency notes and all troubleshooting scenarios
- [`context_builder/docs/USER_GUIDE.md`](context_builder/docs/USER_GUIDE.md) — `@build_context` command reference and advanced configuration
- [`Test_case_creator/docs/USER_GUIDE.md`](Test_case_creator/docs/USER_GUIDE.md) — `@create_tests` command reference and methodology guide
- [`context_builder_spec.md`](context_builder_spec.md) — Context Builder technical specification
- [`test_case_creator_spec.md`](test_case_creator_spec.md) — Test Case Creator technical specification
- [`REVIEW_FINDINGS.md`](REVIEW_FINDINGS.md) — graph and test quality review with improvement backlog
