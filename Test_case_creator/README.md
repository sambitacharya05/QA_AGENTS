# Test Case Creator Agent

The **Test Case Creator Agent** is a state-of-the-art dual-agent system that ingests semantic graphs to plan, draft, verify, and export highly compliant test cases to Azure DevOps (CSV format) and produce comprehensive traceability and coverage reports.

## 🏗️ Architecture

Decoupled, dual-language modular architecture:
1. **VS Code Frontend Extension (TypeScript)**: Standard `@create_tests` chat participant and commands orchestrating module analyses, Maker scenario proposals, generator stepping, and Verifier loop checking.
2. **FastMCP Execution Engine (Python)**: Local tool execution layer providing BFS subgraph slicing, RFC 4180 Excel CSV formatting, and markdown reports persistence.

## 📁 Directory Structure

The standardized layout matches:
- `vscode-extension/`: TS source code, configurations, Zod schemas, and agent prompts.
- `engine/`: Python modules defining FastMCP server tools.
- `tests/`: Extension integration and Python unit test suites.
- `specs/`: Prioritized index and design documentation specs.

## 🛠️ Scaffolding Installation

Configure both TS and Python dependencies on macOS/Linux:
```bash
chmod +x install.sh
./install.sh
```

On Windows (PowerShell):
```powershell
Set-ExecutionPolicy Bypass -Scope Process
.\install.ps1
```

## 🧪 Scaffolding Verification

Validate python backend FastMCP tool definitions and writer modules:
```bash
source .venv/bin/activate
pytest tests/engine/
```

Launch the VS Code Extension debug session inside `.vscode/launch.json` to execute extension and integration tests.
