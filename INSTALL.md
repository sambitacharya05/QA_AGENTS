# Installation Guide: Context Builder + Test Case Creator (Combined)

This guide documents the **combined installer** (`install_all.sh` / `install_all.ps1`) that sets up both the `@build_context` (Context Builder) and `@create_tests` (Test Case Creator) agents in a single automated run. No files need to be copied into your project — the installer wires up VS Code and MCP settings pointing back to this central `agents/` directory.

---

## 📋 Prerequisites

Before running the installer, ensure the following are available on your system:

| Requirement | Minimum Version | How to Verify |
|-------------|----------------|---------------|
| **Python** | 3.10 | `python3 --version` |
| **pip** | any | `python3 -m pip --version` |
| **Node.js** | 18.x | `node --version` |
| **npm** | 9.x | `npm --version` |
| **VS Code `code` CLI** | any | `code --version` |

> **Note on `code` CLI**: The `code` CLI is used to auto-install the VS Code extensions. If it is not on your PATH, the installer will still complete — it will build the `.vsix` files and print instructions for manual installation. See [Troubleshooting](#-troubleshooting) for how to add `code` to PATH.

---

## ⚡ Installation

### Step 1 — Navigate to your target project

`cd` to the project workspace where you want to install the agents. **Do not run the installer from inside the `agents/` directory itself** — it will detect this and abort with a clear error.

```bash
cd /path/to/your-project
```

### Step 2 — Run the installer

#### 🍏 macOS & Linux

```bash
bash /path/to/agents/install_all.sh
```

#### 🪟 Windows (PowerShell)

Open a PowerShell terminal in your target project directory, then:

```powershell
Set-ExecutionPolicy Bypass -Scope Process
& "C:\path\to\agents\install_all.ps1"
```

> The `Set-ExecutionPolicy Bypass -Scope Process` line is required only for the current terminal session and does not change your system-wide policy.

### Step 3 — Reload VS Code

After the installer completes, reload your VS Code window to activate both extensions and start the MCP servers:

```
Cmd+Shift+P (macOS) / Ctrl+Shift+P (Windows/Linux)  →  Developer: Reload Window
```

---

## ⚙️ What the Installer Does (10 Steps)

The scripts execute the following operations automatically, in order:

| # | Step | What Happens |
|---|------|-------------|
| 0 | **Resolve directories** | Detects `AGENTS_DIR` (where the script lives) and `TARGET_WORKSPACE` (your current directory). Aborts if both are the same. |
| 1 | **Check prerequisites** | Verifies Python 3.10+, pip, Node/npm, and VS Code `code` CLI. Python and npm are hard requirements; `code` CLI is soft (VSIX install is skipped with a warning if missing). |
| 2 | **context_builder Python env** | Creates `agents/context_builder/.venv/` (or reuses if it exists) and installs Python packages from `requirements.txt`. |
| 3 | **Test_case_creator Python env** | Creates `agents/Test_case_creator/.venv/` (or reuses) and installs packages from `requirements.txt` and `requirements-dev.txt`. |
| 4 | **Build & install context_builder extension** | Runs `npm install` + `npm run compile` + `vsce package` in `context_builder/vscode-extension/`, then installs the generated `.vsix`. |
| 5 | **Build & install Test_case_creator extension** | Same process for `Test_case_creator/vscode-extension/`. VSIX is removed after installation to keep directories clean. |
| 6 | **Merge `.github/` content** | Copies agent prompt files (`.agent.md`), skills, and hooks into `TARGET_WORKSPACE/.github/`. Appends `@build_context` and `@create_tests` instructions to `copilot-instructions.md` without duplicating on re-runs. |
| 7 | **Register MCP servers** | Writes (or merges) both MCP server entries into `TARGET_WORKSPACE/.vscode/mcp.json`, preserving any existing server configurations. |
| 8 | **Write VS Code settings** | Writes (or merges) `contextBuilder.serverDirectory` and `testCaseCreator.engineDirectory` into `TARGET_WORKSPACE/.vscode/settings.json`, preserving all existing settings. |
| 9 | **Initialize `ingest/` folder** | Creates `TARGET_WORKSPACE/ingest/` if it doesn't already exist. |
| 10 | **Print summary** | Lists what was installed and the exact next steps to start using the agents. |

### Files Created / Modified in Your Project

After a successful run, your target project will contain:

```
your-project/
├── .github/
│   ├── agents/
│   │   ├── build_context.agent.md
│   │   ├── module_analyzer.agent.md
│   │   ├── test_case_analyst.agent.md
│   │   ├── test_generator.agent.md
│   │   ├── test_verifier.agent.md
│   │   └── coverage_reporter.agent.md
│   ├── skills/
│   │   └── (context_builder skill files)
│   ├── hooks/
│   │   └── on-save.json
│   └── copilot-instructions.md
├── .vscode/
│   ├── mcp.json          ← Both MCP servers registered
│   └── settings.json     ← engineDirectory + serverDirectory configured
└── ingest/               ← Drop your specification files here
```

> **Nothing is copied into your project's Python environment** — both tools run from their own isolated `.venv` directories inside `agents/`.

---

## 🧪 Verification

After reloading VS Code, run these checks to confirm everything is working.

### Step 1 — Verify extensions are installed

In a terminal:

```bash
code --list-extensions | grep -E "context-builder|test-case-creator"
```

**Expected**: Both extension IDs appear in the list.

### Step 2 — Verify chat participants are active

Open Copilot Chat (`Cmd+Ctrl+I` on macOS / `Ctrl+Alt+I` on Windows) and type `@` in the input. Both `@build_context` and `@create_tests` should appear as valid participants.

### Step 3 — Verify MCP server registration

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

**Expected**:
```
OK: context-builder  →  /path/to/agents/context_builder/.venv/bin/python3
OK: test-case-creator  →  /path/to/agents/Test_case_creator/.venv/bin/python3
```

### Step 4 — Run a status check

In Copilot Chat, run:

```
@build_context /status
```

**Expected**: The agent responds confirming it has accessed the Python MCP server and scanned the `ingest/` directory (reporting 0 files since it is empty).

---

## 🔄 Re-running the Installer (Idempotency)

The installer is safe to re-run at any time. It will:

- **Reuse** existing `.venv` directories (but still re-installs any missing packages)
- **Rebuild** extension VSIXes from source and reinstall them
- **Merge** — never overwrite — your existing `.vscode/mcp.json` and `settings.json`
- **Skip** `.github/copilot-instructions.md` sections that are already present (no duplicates)
- **Backup** `on-save.json` to `on-save.json.bak` before replacing it (only if it exists and needs updating)

Run it again after pulling updates to the `agents/` repository to pick up new agent files, updated dependencies, or extension changes.

---

## 🛠️ Troubleshooting

### `Do not run install_all.sh from inside the agents/ directory`

**Cause**: You ran the script while your working directory was the `agents/` folder itself.

**Resolution**: `cd` to your target project first:
```bash
cd /path/to/your-project
bash /path/to/agents/install_all.sh
```

---

### `Python 3.10+ required. Found: 3.x`

**Cause**: Your default `python3` is older than 3.10.

**Resolution**:
- **macOS**: Install via [python.org](https://www.python.org/downloads/) or `brew install python@3.12`
- **Windows**: Download from [python.org](https://www.python.org/downloads/windows/) and ensure "Add to PATH" is checked
- **Linux**: `sudo apt install python3.12` (or your distro's equivalent)

After installing, verify: `python3 --version`

---

### `npm/Node.js not found`

**Cause**: Node.js is not installed.

**Resolution**: Download and install from [nodejs.org](https://nodejs.org/) (LTS version recommended). After installing, open a new terminal and retry.

---

### `code: command not found` (non-fatal)

**Cause**: The VS Code `code` CLI is not on your PATH. The installer continues but cannot auto-install the `.vsix` files.

**Resolution (macOS)**:
1. Open VS Code
2. Press `Cmd+Shift+P` → type `Shell Command: Install 'code' command in PATH` → press Enter
3. Restart your terminal and re-run the installer

**Resolution (Windows)**: Reinstall VS Code and ensure **"Add to PATH"** is selected. Or manually add `%LOCALAPPDATA%\Programs\Microsoft VS Code\bin` to your `Path` environment variable.

**Resolution (Linux)**: If installed via Snap or `.deb`, the CLI should be automatic. For tarballs: `sudo ln -s /path/to/vscode/bin/code /usr/local/bin/code`

**Manual VSIX install** (when `code` CLI is unavailable): In VS Code, open the Extensions panel → click `...` (More Actions) → **Install from VSIX...** → select the `.vsix` file printed in the installer output.

---

### `VSIX packaging failed` warning

**Cause**: `@vscode/vsce package` encountered an error (commonly: missing `publisher` field in `package.json`, or a broken TypeScript compile).

**Resolution**:
1. Check the error output printed by the installer
2. If it is a TypeScript error, investigate the source files
3. The rest of the installation (Python envs, `.github/`, `.vscode/` config) is **not affected** by a VSIX failure — it completes successfully

---

### `@build_context` or `@create_tests` not appearing in Copilot Chat

**Cause**: Extension is not installed, not enabled, or VS Code has not been reloaded.

**Resolution**:
1. Open the Extensions panel (`Cmd+Shift+X`) and search for "Context Builder" / "Test Case Creator"
2. Ensure both are installed and enabled (not disabled)
3. Reload the VS Code window: `Cmd+Shift+P` → `Developer: Reload Window`

---

### MCP server fails to start / `ModuleNotFoundError: No module named 'engine'`

**Cause**: The `testCaseCreator.engineDirectory` or `contextBuilder.serverDirectory` setting points to the wrong path, or the Python virtual environment was not created correctly.

**Resolution**:
1. Open `.vscode/settings.json` and verify the paths match the actual location of the `agents/` directory
2. Re-run the installer: `bash /path/to/agents/install_all.sh`
3. After the installer completes, reload VS Code

---

### `Failed to get GitHub Copilot token`

**Cause**: GitHub Copilot is not signed in or subscription has lapsed.

**Resolution**: Click the Accounts icon in the VS Code status bar (bottom-left) and ensure your GitHub account with an active Copilot subscription is signed in.

---

## 🚀 Next Steps

Once installation is verified:

1. **Drop your specification files** (PDFs, Word docs, Excel sheets, Markdown) into `./ingest/`
2. **Build the semantic context graph**: In Copilot Chat, run `@build_context /ingest`
3. **Generate test cases**: Once the graph is built, run `@create_tests /generate`
4. **Find your outputs** in `.test_artifacts/`:
   - `test_cases_*.csv` — import into Azure DevOps via **Test Plans → Import Test Cases**
   - `coverage_report_*.md` — traceability matrix and gap analysis

For detailed usage of each agent, refer to:
- [`context_builder/docs/USER_GUIDE.md`](context_builder/docs/USER_GUIDE.md) — `@build_context` reference
- [`Test_case_creator/docs/USER_GUIDE.md`](Test_case_creator/docs/USER_GUIDE.md) — `@create_tests` reference
