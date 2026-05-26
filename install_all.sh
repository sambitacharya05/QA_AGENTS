#!/bin/bash
# ==============================================================================
# Combined Installer: Context Builder + Test Case Creator
# ==============================================================================
#
# Usage: cd /path/to/your-project && bash /path/to/agents/install_all.sh
#
# What this script does:
#   0. Detects AGENTS_DIR (where this script lives) and TARGET_WORKSPACE (cwd)
#   1. Checks prerequisites: Python 3.10+, pip, npm, VS Code CLI
#   2. Creates .venv and installs Python deps for context_builder
#   3. Creates .venv and installs Python deps for Test_case_creator
#   4. Writes/merges .vscode/mcp.json  ← BEFORE any VSIX install
#   5. Writes/merges .vscode/settings.json  ← BEFORE any VSIX install
#   6. Builds and installs context_builder VS Code extension VSIX
#   7. Builds and installs Test_case_creator VS Code extension VSIX
#   8. Merges .github/ content from both tools into the target project
#   9. Creates ./ingest/ folder and prints next-steps summary
#
# NOTE: Steps 4 & 5 are intentionally ordered BEFORE the VSIX installs (6 & 7).
# Installing a VSIX triggers VS Code extension activation immediately. If settings
# haven't been written yet, the extension reads empty config and starts the MCP
# server from the wrong directory. Writing config first prevents this race.
# ==============================================================================

set -e

# ── ANSI Colors ────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'
BOLD='\033[1m'

print_header() { echo -e "\n${CYAN}${BOLD}=== $1 ===${NC}"; }
print_ok()     { echo -e "  ${GREEN}✓${NC} $1"; }
print_warn()   { echo -e "  ${YELLOW}⚠️  $1${NC}"; }
print_err()    { echo -e "  ${RED}❌ $1${NC}"; }
print_step()   { echo -e "\n${BOLD}$1${NC}"; }

echo -e "${CYAN}${BOLD}"
echo "=============================================================="
echo "  🛠️  Combined Installer: Context Builder + Test Case Creator"
echo "=============================================================="
echo -e "${NC}"

# ── Step 0: Resolve Directories ────────────────────────────────────────────────
print_step "🔍 Resolving directories..."

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENTS_DIR="$SCRIPT_DIR"
TARGET_WORKSPACE="$(pwd)"

# Guard: refuse to install into the agents directory itself
if [ "$TARGET_WORKSPACE" = "$AGENTS_DIR" ]; then
    print_err "Do not run install_all.sh from inside the agents/ directory."
    echo "  Please cd to your target project first, then run:"
    echo "  ${CYAN}bash $AGENTS_DIR/install_all.sh${NC}"
    exit 1
fi

echo "  AGENTS_DIR:        $AGENTS_DIR"
echo "  TARGET_WORKSPACE:  $TARGET_WORKSPACE"

# Verify both tool directories exist
if [ ! -d "$AGENTS_DIR/context_builder" ] || [ ! -d "$AGENTS_DIR/Test_case_creator" ]; then
    print_err "Expected context_builder/ and Test_case_creator/ inside: $AGENTS_DIR"
    echo "  Make sure you are running install_all.sh from the agents/ directory."
    exit 1
fi

# ── Step 1: Prerequisites ──────────────────────────────────────────────────────
print_step "🔍 Step 1: Checking system prerequisites..."

# Detect Python
PYTHON_CMD=""
for candidate in python3 python; do
    if command -v "$candidate" &>/dev/null; then
        PYTHON_CMD="$candidate"
        break
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    print_err "Python could not be found. Please install Python 3.10+ and retry."
    exit 1
fi

# Verify Python 3.10+
PYTHON_MAJOR=$($PYTHON_CMD -c "import sys; print(sys.version_info.major)")
PYTHON_MINOR=$($PYTHON_CMD -c "import sys; print(sys.version_info.minor)")
PYTHON_VERSION_STR="$PYTHON_MAJOR.$PYTHON_MINOR"

if [ "$PYTHON_MAJOR" -lt 3 ] || { [ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 10 ]; }; then
    print_err "Python 3.10+ required. Found: $($PYTHON_CMD --version). Aborting."
    exit 1
fi
print_ok "Python $PYTHON_VERSION_STR found: $($PYTHON_CMD --version)"

# Verify pip
if ! $PYTHON_CMD -m pip --version &>/dev/null; then
    print_err "pip is not available. Install pip alongside Python and retry."
    exit 1
fi
print_ok "pip available"

# Detect npm
if ! command -v npm &>/dev/null; then
    print_err "npm/Node.js not found. Install Node.js 18+ and retry."
    exit 1
fi
print_ok "Node/npm found: $(node --version)"

# Detect VS Code CLI (non-fatal — VSIX install skipped if missing)
CODE_CLI_AVAILABLE=true
if ! command -v code &>/dev/null; then
    print_warn "VS Code 'code' CLI not found. Extension VSIX files will be built but NOT installed automatically."
    print_warn "After the script completes, install manually via: Extensions panel → ... → Install from VSIX"
    CODE_CLI_AVAILABLE=false
else
    print_ok "VS Code 'code' CLI found"
fi

# ── Step 2: context_builder Python venv ────────────────────────────────────────
print_step "🐍 Step 2: Setting up context_builder Python environment..."

CB_DIR="$AGENTS_DIR/context_builder"
CB_VENV="$CB_DIR/.venv"

if [ ! -d "$CB_VENV" ]; then
    echo "  Creating .venv in $CB_VENV ..."
    $PYTHON_CMD -m venv "$CB_VENV"
    print_ok "Virtual environment created"
else
    print_ok "Virtual environment already exists — reusing"
fi

CB_PIP="$CB_VENV/bin/pip"
CB_PYTHON="$CB_VENV/bin/python3"

# Upgrade pip before installing deps (avoids wheel build warnings)
"$CB_PIP" install --quiet --upgrade pip
"$CB_PIP" install --quiet -r "$CB_DIR/requirements.txt"
print_ok "context_builder Python dependencies installed"

# ── Step 3: Test_case_creator Python venv ──────────────────────────────────────
print_step "🐍 Step 3: Setting up Test_case_creator Python environment..."

TCC_DIR="$AGENTS_DIR/Test_case_creator"
TCC_VENV="$TCC_DIR/.venv"

if [ ! -d "$TCC_VENV" ]; then
    echo "  Creating .venv in $TCC_VENV ..."
    $PYTHON_CMD -m venv "$TCC_VENV"
    print_ok "Virtual environment created"
else
    print_ok "Virtual environment already exists — reusing"
fi

TCC_PIP="$TCC_VENV/bin/pip"
TCC_PYTHON="$TCC_VENV/bin/python3"

"$TCC_PIP" install --quiet --upgrade pip
"$TCC_PIP" install --quiet -r "$TCC_DIR/requirements.txt"
"$TCC_PIP" install --quiet -r "$TCC_DIR/requirements-dev.txt"
print_ok "Test_case_creator Python dependencies installed"

# ── Step 4: Write .vscode/mcp.json ────────────────────────────────────────────
# Must happen BEFORE VSIX installs so the extension reads correct config on first activation.
print_step "⚙️  Step 4: Registering MCP servers in .vscode/mcp.json..."

mkdir -p "$TARGET_WORKSPACE/.vscode"

export JSON_TARGET_PATH="$TARGET_WORKSPACE/.vscode/mcp.json"
export JSON_MERGE_DATA
JSON_MERGE_DATA=$(cat <<EOF
{
  "servers": {
    "context-builder": {
      "type": "stdio",
      "command": "$CB_PYTHON",
      "args": ["main.py"],
      "cwd": "$CB_DIR"
    },
    "test-case-creator": {
      "type": "stdio",
      "command": "$TCC_PYTHON",
      "args": ["-m", "engine.mcp_server"],
      "cwd": "$TCC_DIR"
    }
  }
}
EOF
)

$PYTHON_CMD << 'PYEOF'
import json, os, sys

target_path = os.environ['JSON_TARGET_PATH']
merge_data   = json.loads(os.environ['JSON_MERGE_DATA'])

existing = {}
if os.path.exists(target_path):
    try:
        with open(target_path, 'r', encoding='utf-8') as f:
            existing = json.load(f)
        print(f"  Found existing mcp.json — merging...")
    except (json.JSONDecodeError, OSError) as e:
        print(f"  WARNING: Could not parse existing {target_path}: {e}. Overwriting.")

def deep_update(base, updates):
    for k, v in updates.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            deep_update(base[k], v)
        else:
            base[k] = v
    return base

deep_update(existing, merge_data)

with open(target_path, 'w', encoding='utf-8') as f:
    json.dump(existing, f, indent=2)
    f.write('\n')
PYEOF

print_ok ".vscode/mcp.json written with context-builder and test-case-creator servers"

# ── Step 5: Write .vscode/settings.json ───────────────────────────────────────
# Must happen BEFORE VSIX installs so the extension reads correct config on first activation.
print_step "⚙️  Step 5: Writing VS Code extension settings to .vscode/settings.json..."

export JSON_TARGET_PATH="$TARGET_WORKSPACE/.vscode/settings.json"
export JSON_MERGE_DATA
JSON_MERGE_DATA=$(cat <<EOF
{
  "contextBuilder.serverDirectory": "$CB_DIR",
  "testCaseCreator.engineDirectory": "$TCC_DIR"
}
EOF
)

$PYTHON_CMD << 'PYEOF'
import json, os

target_path = os.environ['JSON_TARGET_PATH']
merge_data   = json.loads(os.environ['JSON_MERGE_DATA'])

existing = {}
if os.path.exists(target_path):
    try:
        with open(target_path, 'r', encoding='utf-8') as f:
            content = f.read().strip()
            if content:
                existing = json.loads(content)
        print(f"  Found existing settings.json — merging...")
    except (json.JSONDecodeError, OSError) as e:
        print(f"  WARNING: Could not parse existing settings.json: {e}. Overwriting.")

existing.update(merge_data)

with open(target_path, 'w', encoding='utf-8') as f:
    json.dump(existing, f, indent=2)
    f.write('\n')
PYEOF

print_ok ".vscode/settings.json updated with contextBuilder.serverDirectory and testCaseCreator.engineDirectory"

# ── Step 6: Build & Install context_builder VSIX ──────────────────────────────
print_step "🔌 Step 6: Building and installing context_builder VS Code extension..."

CB_EXT_DIR="$CB_DIR/vscode-extension"
cd "$CB_EXT_DIR"

echo "  Installing Node dependencies (npm install)..."
npm install --silent

echo "  Compiling TypeScript..."
npm run compile

echo "  Packaging VSIX..."
# Use || true so set -e does not abort on vsce failure (non-fatal step)
VSCE_OUTPUT=""
VSCE_EXIT=0
VSCE_OUTPUT=$(npx -y @vscode/vsce package 2>&1) || VSCE_EXIT=$?

if [ "$VSCE_EXIT" -ne 0 ]; then
    print_warn "context_builder VSIX packaging failed. Continuing without extension install."
    print_warn "Error: $VSCE_OUTPUT"
else
    CB_VSIX=$(ls "$CB_EXT_DIR"/*.vsix 2>/dev/null | head -1)
    if [ -z "$CB_VSIX" ]; then
        print_warn "Could not locate generated .vsix file for context_builder"
    else
        if [ "$CODE_CLI_AVAILABLE" = true ]; then
            code --install-extension "$CB_VSIX" --force
            print_ok "context_builder extension installed: $(basename "$CB_VSIX")"
        else
            print_warn "Manually install: $CB_VSIX"
        fi
        rm -f "$CB_VSIX"
        print_ok "Cleaned up .vsix file"
    fi
fi

cd "$AGENTS_DIR"

# ── Step 7: Build & Install Test_case_creator VSIX ────────────────────────────
print_step "🔌 Step 7: Building and installing Test_case_creator VS Code extension..."

TCC_EXT_DIR="$TCC_DIR/vscode-extension"
cd "$TCC_EXT_DIR"

echo "  Installing Node dependencies (npm install)..."
npm install --silent

echo "  Compiling TypeScript + webpack bundling..."
npm run compile

echo "  Packaging VSIX..."
VSCE_OUTPUT=""
VSCE_EXIT=0
VSCE_OUTPUT=$(npx -y @vscode/vsce package 2>&1) || VSCE_EXIT=$?

if [ "$VSCE_EXIT" -ne 0 ]; then
    print_warn "Test_case_creator VSIX packaging failed. Continuing without extension install."
    print_warn "Error: $VSCE_OUTPUT"
else
    TCC_VSIX=$(ls "$TCC_EXT_DIR"/*.vsix 2>/dev/null | head -1)
    if [ -z "$TCC_VSIX" ]; then
        print_warn "Could not locate generated .vsix file for Test_case_creator"
    else
        if [ "$CODE_CLI_AVAILABLE" = true ]; then
            code --install-extension "$TCC_VSIX" --force
            print_ok "Test_case_creator extension installed: $(basename "$TCC_VSIX")"
        else
            print_warn "Manually install: $TCC_VSIX"
        fi
        rm -f "$TCC_VSIX"
        print_ok "Cleaned up .vsix file"
    fi
fi

cd "$AGENTS_DIR"

# ── Step 8: Merge .github/ content ────────────────────────────────────────────
print_step "📂 Step 8: Merging .github/ configuration files into target project..."

mkdir -p "$TARGET_WORKSPACE/.github/agents"
mkdir -p "$TARGET_WORKSPACE/.github/skills"
mkdir -p "$TARGET_WORKSPACE/.github/hooks"

# 6a. Copy context_builder agents
if [ -d "$CB_DIR/.github/agents" ]; then
    cp -f "$CB_DIR/.github/agents/"*.agent.md "$TARGET_WORKSPACE/.github/agents/" 2>/dev/null && \
        print_ok "context_builder agent files copied" || \
        print_warn "No .agent.md files found in context_builder/.github/agents/"
fi

# 6b. Copy Test_case_creator agents (source-of-truth is src/agents/)
if [ -d "$TCC_DIR/vscode-extension/src/agents" ]; then
    cp -f "$TCC_DIR/vscode-extension/src/agents/"*.agent.md "$TARGET_WORKSPACE/.github/agents/" 2>/dev/null && \
        print_ok "Test_case_creator agent files copied" || \
        print_warn "No .agent.md files found in Test_case_creator/vscode-extension/src/agents/"
fi

# 6c. Copy context_builder skills
if [ -d "$CB_DIR/.github/skills" ]; then
    cp -Rf "$CB_DIR/.github/skills/." "$TARGET_WORKSPACE/.github/skills/"
    print_ok "context_builder skill files copied"
fi

# 6d. Copy/merge hooks/on-save.json
if [ -f "$CB_DIR/.github/hooks/on-save.json" ]; then
    if [ -f "$TARGET_WORKSPACE/.github/hooks/on-save.json" ]; then
        if grep -q "Workspace Ingest Syncer" "$TARGET_WORKSPACE/.github/hooks/on-save.json"; then
            print_ok "on-save.json hook already up to date (Workspace Ingest Syncer present)"
        else
            cp "$TARGET_WORKSPACE/.github/hooks/on-save.json" "$TARGET_WORKSPACE/.github/hooks/on-save.json.bak"
            cp "$CB_DIR/.github/hooks/on-save.json" "$TARGET_WORKSPACE/.github/hooks/"
            print_ok "on-save.json replaced (backup saved as on-save.json.bak)"
        fi
    else
        cp "$CB_DIR/.github/hooks/on-save.json" "$TARGET_WORKSPACE/.github/hooks/"
        print_ok "on-save.json deployed"
    fi
fi

# 6e. Merge copilot-instructions.md (safe append, never duplicate)
INSTRUCTIONS_TARGET="$TARGET_WORKSPACE/.github/copilot-instructions.md"

append_if_missing() {
    local marker="$1"
    local source_file="$2"
    local label="$3"
    if [ -f "$source_file" ]; then
        if [ -f "$INSTRUCTIONS_TARGET" ]; then
            if grep -q "$marker" "$INSTRUCTIONS_TARGET"; then
                print_ok "$label instructions already present in copilot-instructions.md"
            else
                {
                    echo ""
                    echo ""
                    echo "---"
                    echo ""
                    cat "$source_file"
                } >> "$INSTRUCTIONS_TARGET"
                print_ok "$label instructions appended to copilot-instructions.md"
            fi
        else
            cp "$source_file" "$INSTRUCTIONS_TARGET"
            print_ok "$label copilot-instructions.md deployed"
        fi
    fi
}

append_if_missing "@build_context" "$CB_DIR/.github/copilot-instructions.md" "context_builder"
append_if_missing "@create_tests"  "$TCC_DIR/.github/copilot-instructions.md" "Test_case_creator"

# ── Step 9: Initialize ./ingest/ folder ──────────────────────────────────────
print_step "📂 Step 9: Initializing ingest/ folder..."

if [ ! -d "$TARGET_WORKSPACE/ingest" ]; then
    mkdir -p "$TARGET_WORKSPACE/ingest"
    print_ok "ingest/ directory created"
else
    print_ok "ingest/ directory already exists"
fi

# ── Step 10: Summary ──────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}=============================================================="
echo "  🎉 Installation Completed Successfully!"
echo -e "==============================================================${NC}"
echo ""
echo -e "${BOLD}What was installed:${NC}"
echo "  • context_builder Python environment: $CB_VENV"
echo "  • Test_case_creator Python environment: $TCC_VENV"
echo "  • .github/agents/ populated with agent prompt files"
echo "  • .vscode/mcp.json registered both MCP servers"
echo "  • .vscode/settings.json configured with server directories"
echo ""
echo -e "${BOLD}Next steps:${NC}"
echo "  1. Reload VS Code window: ${CYAN}Cmd+Shift+P → Developer: Reload Window${NC}"
echo "  2. Open Copilot Chat (${BOLD}Cmd+Ctrl+I${NC}) and verify ${BOLD}@build_context${NC} is listed"
echo "  3. Place your specification files in: ${CYAN}./ingest/${NC}"
echo "  4. Run ${BOLD}@build_context /ingest${NC} to build the semantic context graph"
echo "  5. Run ${BOLD}@create_tests /generate${NC} to generate Azure DevOps test cases"
echo ""
echo -e "📋 MCP server config written to: ${CYAN}$TARGET_WORKSPACE/.vscode/mcp.json${NC}"
echo -e "⚙️  Settings written to:          ${CYAN}$TARGET_WORKSPACE/.vscode/settings.json${NC}"
echo ""
