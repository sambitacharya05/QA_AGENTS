# Installation Guide: `@build_context` Agent

This guide outlines how to set up, install, and verify the **Context Builder Agent (`@build_context`)** in a separate development workspace. The setup is highly automated via the bundled installer script.

---

## 📋 Prerequisites

Before running the installer, ensure the target system meets these requirements:

1. **Python 3.10+** (with `pip3` installed)
   * Verify: `python3 --version` and `pip3 --version`
2. **Node.js & npm**
   * Verify: `node --version` and `npm --version`
3. **VS Code** (with the `code` CLI added to PATH)
   * The installer automatically registers the extension in VS Code using the command line.
   * **Verification**: Open a terminal and run `code --version`. 
   * **Setup (macOS)**: Open VS Code, open the Command Palette (`Cmd+Shift+P`), type `Shell Command: Install 'code' command in PATH`, and click enter.
   * **Setup (Windows/Linux)**: Enabled by default during installation.

---

## ⚡ Automated Installation

To install the agent, follow the steps for your respective operating system:

### 🍏 macOS & Linux
1. Copy the release package `context_builder_release.zip` to the root of your target project repository.
2. Unpack the ZIP archive:
   ```bash
   unzip context_builder_release.zip
   ```
3. Make the installer executable and run it:
   ```bash
   chmod +x install.sh
   ./install.sh
   ```

### 🪟 Windows (PowerShell)
1. Copy the release package `context_builder_release.zip` to the root of your target project repository.
2. Extract the ZIP archive (you can right-click and select **Extract All...** or use PowerShell):
   ```powershell
   Expand-Archive -Path context_builder_release.zip -DestinationPath . -Force
   ```
3. Open a PowerShell terminal in the target repository root and run the native installer:
   ```powershell
   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
   .\install.ps1
   ```

---

## ⚙️ What the Installer Does Automatically

The installation scripts (`install.sh` and `install.ps1`) execute these operations with zero manual intervention:

1. **Prerequisite Check**: Validates that a compatible Python 3.10+ installation, the `pip` environment module, and the VS Code `code` CLI are correctly installed.
2. **Backend Library Provisioning**: Runs python-based library checks and pulls requirements listed in `requirements.txt` to install FastMCP and essential document parsing packages (`openpyxl`, `python-docx`, `pypdf`, `pydantic`).
3. **VSIX Extension Registration**: Seamlessly registers `context-builder-agent-1.1.0.vsix` directly into your active VS Code profile.
4. **Awesome-Copilot Workspace Setup**:
   * **Smart Merge**: Gracefully merges agents, skills, and watcher hooks recursively without deleting or skipping your existing `.github` assets. Appends `@build_context` custom instructions cleanly to any pre-existing `copilot-instructions.md` repository prompt file.
   * Initializes a blank `./ingest/` folder at the target repository root to act as the guarded scope for context parsing.

---

## 🧪 Verification of Successful Installation

To guarantee that all components are fully integrated and functioning, execute the following three verification steps:

### Step 1: Confirm VS Code Extension is Registered
Run the following command in your terminal:
```bash
code --list-extensions | grep context-builder
```
**Expected Output**:
`insurance-tech.context-builder-agent` should be listed.

### Step 2: Launch VS Code and Verify Copilot Chat integration
1. Open your target project root folder in VS Code:
   ```bash
   code .
   ```
2. Open the **GitHub Copilot Chat** view (`Cmd+Ctrl+I` on macOS, `Ctrl+Alt+I` on Windows/Linux).
3. Type `@` in the chat input area.
4. **Expected Result**: `@build_context` appears as a valid chat participant.

### Step 3: Run the Status Command
Inside Copilot Chat, type and send:
```chat
@build_context /status
```
**Expected Response**:
The `@build_context` agent should respond, indicating that it successfully accessed the local Python MCP Server and scanned the `./ingest/` folder. It will report `0 files ingested` (since the directory is currently empty) and confirm that the Semantic Context Graph store is initialized and in sync.

---

## 🛠️ Troubleshooting

### Error: `code: command not found`
* **Cause**: The VS Code command line runner (`code`) is not registered in your system's PATH.
* **Resolution (macOS)**: 
  1. Open VS Code.
  2. Press `Cmd+Shift+P` to open the Command Palette.
  3. Search for and select `Shell Command: Install 'code' command in PATH`.
  4. Restart your terminal and run the installer again.
* **Resolution (Windows)**:
  1. The easiest way is to reinstall VS Code and ensure the **"Add to PATH"** checkbox is selected during installation.
  2. **Manual way**: Open Windows Search -> "Edit the system environment variables" -> "Environment Variables" -> Edit the `Path` variable and add `C:\Users\<YourUsername>\AppData\Local\Programs\Microsoft VS Code\bin`. Restart PowerShell.
* **Resolution (Linux)**:
  1. If you installed via Snap or a package manager, it should be automatic. If you downloaded the tarball, manually create a symlink: 
     `sudo ln -s /path/to/vscode/bin/code /usr/local/bin/code`

### Error: `Failed to get GitHub Copilot token`
* **Details**: When running the agent, you receive an error about failing to acquire the Copilot token.
* **Context**: The `@build_context` agent requires an active GitHub Copilot subscription to execute its LLM refinement pipeline.
* **Resolution**: Ensure you are signed into GitHub Copilot within VS Code. Click the Accounts icon in the bottom left corner of VS Code and verify your GitHub session is active and has Copilot access.
