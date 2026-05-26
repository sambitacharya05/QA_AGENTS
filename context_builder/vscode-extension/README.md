# Context Builder Copilot Extension (`@build_context`)

This directory contains the **install-ready VS Code Extension** that provides the programmatic chat participant **`@build_context`** inside VS Code Copilot Chat.

The extension connects directly to our local Python MCP server process via standard stdio JSON-RPC, coordinates the multi-agent scans, and renders interactive vis.js graph networks in a native VS Code Webview panel.

---

## 🚀 How to Run & Debug Locally

To start a local debugger session and test the `@build_context` agent inside VS Code:

### 1. Install Extension Dependencies
Open your terminal, navigate to this directory, and install npm packages:
```bash
cd vscode-extension
npm install
```

### 2. Compile the TypeScript Source
Run the compilation script to compile the TypeScript classes into the target `out/` folder:
```bash
npm run compile
```

### 3. Launch the Extension Development Host
1. Open the `context_builder` root workspace inside **VS Code**.
2. Press **`F5`** on your keyboard (or click **Run and Debug** in the Activity Bar, then click **Run Extension**).
3. A new VS Code editor window (known as the **[Extension Development Host]**) will launch.
4. In this new window, open Copilot Chat. You will see **`@build_context`** successfully registered as a Chat Participant!

---

## 📦 How to Package into a `.vsix` (Install-Ready)

To package this extension into a standalone, portable, offline-installable `.vsix` file:

### 1. Install the VS Code Extension CLI (vsce)
Ensure you have the packager tool:
```bash
npm install -g @vscode/vsce
```

### 2. Package the Extension
Run the packing script inside the `vscode-extension/` directory:
```bash
vsce package
```
This command compiles the source and produces a file named:
`context-builder-agent-1.0.0.vsix`

### 3. Install in VS Code
You can install this `.vsix` file on any machine instantly:
1. Open VS Code.
2. Open the **Extensions View** (`Ctrl+Shift+X` or `Cmd+Shift+X`).
3. Click the **`...`** (More Actions) button in the top right corner.
4. Select **Install from VSIX...**
5. Select the `context-builder-agent-1.0.0.vsix` file.
6. The `@build_context` agent is now fully installed and ready to run permanently!
