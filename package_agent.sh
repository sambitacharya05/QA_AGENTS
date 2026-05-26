#!/bin/bash
# ==============================================================================
# Combined Distribution Packaging Script
# Context Builder + Test Case Creator Agents
# ==============================================================================
#
# Usage: cd /path/to/agents && bash package_agent.sh
#
# What this script does:
#   1. Validates it is run from the agents/ root directory
#   2. Copies context_builder/ source into a clean staging area
#      (excluding .venv, node_modules, out/, specs/, tests/, scratch/, __pycache__)
#   3. Copies Test_case_creator/ source into staging (same exclusions)
#   4. Copies install_all.sh  → staging/install.sh
#      Copies install_all.ps1 → staging/install.ps1
#   5. Copies INSTALL.md into staging root
#   6. Validates that key files are present in staging
#   7. Compresses staging/ into combined_agents_release.zip
#   8. Cleans up temporary staging directory
#
# Recipients unzip on their machine and run:
#   cd /their-project && bash /path/to/extracted/install.sh
#
# The embedded install.sh mirrors install_all.sh — it builds VSIXes from source
# at install time (requires Node.js/npm on the target machine).
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
print_err()    { echo -e "  ${RED}❌ $1${NC}"; exit 1; }
print_step()   { echo -e "\n${BOLD}$1${NC}"; }

echo -e "${CYAN}${BOLD}"
echo "=============================================================="
echo "  📦 Combined Distribution Packager"
echo "     Context Builder + Test Case Creator Agents"
echo "=============================================================="
echo -e "${NC}"

# ── Step 0: Validate run location ─────────────────────────────────────────────
print_step "🔍 Step 0: Validating run directory..."

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENTS_DIR="$SCRIPT_DIR"
CB_DIR="$AGENTS_DIR/context_builder"
TCC_DIR="$AGENTS_DIR/Test_case_creator"

if [ ! -d "$CB_DIR" ]; then
    print_err "context_builder/ directory not found. Run this script from the agents/ root."
fi
if [ ! -d "$TCC_DIR" ]; then
    print_err "Test_case_creator/ directory not found. Run this script from the agents/ root."
fi
if [ ! -f "$AGENTS_DIR/install_all.sh" ]; then
    print_err "install_all.sh not found in agents/ root. Cannot embed install scripts."
fi

echo "  Agents root:         $AGENTS_DIR"
echo "  context_builder:     $CB_DIR"
echo "  Test_case_creator:   $TCC_DIR"
print_ok "Directory validation passed"

# ── Configuration ──────────────────────────────────────────────────────────────
STAGING_DIR="$AGENTS_DIR/release_staging"
ZIP_NAME="combined_agents_release.zip"

# ── Step 1: Clean staging area ────────────────────────────────────────────────
print_step "📂 Step 1: Preparing clean staging directory..."

rm -rf "$STAGING_DIR"
mkdir -p "$STAGING_DIR/context_builder"
mkdir -p "$STAGING_DIR/Test_case_creator"
print_ok "Staging directory created: $STAGING_DIR"

# ── Step 2: Copy context_builder source ───────────────────────────────────────
print_step "📁 Step 2: Staging context_builder source..."

rsync -a \
    --exclude='.venv/' \
    --exclude='__pycache__/' \
    --exclude='*.pyc' \
    --exclude='*.pyo' \
    --exclude='.DS_Store' \
    --exclude='.pytest_cache/' \
    --exclude='scratch/' \
    --exclude='specs/' \
    --exclude='tests/' \
    --exclude='*.zip' \
    --exclude='release_staging/' \
    --exclude='vscode-extension/node_modules/' \
    --exclude='vscode-extension/out/' \
    "$CB_DIR/" "$STAGING_DIR/context_builder/"

# Validate dotfile directories survived the copy (rsync can silently skip them on some systems)
if [ ! -d "$STAGING_DIR/context_builder/.github" ]; then
    echo "  Dotfile copy fallback: explicitly copying .github..."
    cp -R "$CB_DIR/.github" "$STAGING_DIR/context_builder/"
fi

print_ok "context_builder staged ($(find "$STAGING_DIR/context_builder" -type f | wc -l | tr -d ' ') files)"

# ── Step 3: Copy Test_case_creator source ─────────────────────────────────────
print_step "📁 Step 3: Staging Test_case_creator source..."

rsync -a \
    --exclude='.venv/' \
    --exclude='__pycache__/' \
    --exclude='*.pyc' \
    --exclude='*.pyo' \
    --exclude='.DS_Store' \
    --exclude='.pytest_cache/' \
    --exclude='.vscode/' \
    --exclude='.test_artifacts/' \
    --exclude='specs/' \
    --exclude='tests/' \
    --exclude='tmp_test_dir/' \
    --exclude='out_tests/' \
    --exclude='vscode-extension/node_modules/' \
    --exclude='vscode-extension/out/' \
    "$TCC_DIR/" "$STAGING_DIR/Test_case_creator/"

# Validate dotfile directories survived the copy
if [ ! -f "$STAGING_DIR/Test_case_creator/.github/copilot-instructions.md" ]; then
    echo "  Dotfile copy fallback: explicitly copying .github..."
    cp -R "$TCC_DIR/.github" "$STAGING_DIR/Test_case_creator/"
fi

print_ok "Test_case_creator staged ($(find "$STAGING_DIR/Test_case_creator" -type f | wc -l | tr -d ' ') files)"

# ── Step 4: Copy combined install scripts ─────────────────────────────────────
print_step "📄 Step 4: Copying combined install scripts..."

cp "$AGENTS_DIR/install_all.sh" "$STAGING_DIR/install.sh"
chmod +x "$STAGING_DIR/install.sh"
print_ok "install.sh copied (from install_all.sh)"

if [ -f "$AGENTS_DIR/install_all.ps1" ]; then
    cp "$AGENTS_DIR/install_all.ps1" "$STAGING_DIR/install.ps1"
    print_ok "install.ps1 copied (from install_all.ps1)"
else
    print_warn "install_all.ps1 not found — Windows install script not included"
fi

# ── Step 5: Copy documentation ────────────────────────────────────────────────
print_step "📖 Step 5: Copying documentation..."

if [ -f "$AGENTS_DIR/INSTALL.md" ]; then
    cp "$AGENTS_DIR/INSTALL.md" "$STAGING_DIR/INSTALL.md"
    print_ok "INSTALL.md copied"
else
    print_warn "INSTALL.md not found — documentation not included"
fi

# ── Step 6: Final prune of staging ────────────────────────────────────────────
print_step "🧹 Step 6: Final prune of transient files..."

find "$STAGING_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "$STAGING_DIR" -type f -name "*.pyc"       -delete 2>/dev/null || true
find "$STAGING_DIR" -type f -name "*.pyo"       -delete 2>/dev/null || true
find "$STAGING_DIR" -type f -name ".DS_Store"   -delete 2>/dev/null || true
print_ok "Transient files pruned"

# ── Step 7: Validate staging before zipping ───────────────────────────────────
print_step "🔍 Step 7: Validating staging contents..."

VALIDATION_FAILED=0

check_required() {
    local path="$1"
    local label="$2"
    if [ ! -e "$STAGING_DIR/$path" ]; then
        echo -e "  ${RED}✗ MISSING${NC}: $path ($label)"
        VALIDATION_FAILED=1
    else
        echo -e "  ${GREEN}✓${NC} $path"
    fi
}

# install scripts
check_required "install.sh"                                   "combined bash installer"
check_required "install.ps1"                                  "combined PowerShell installer"

# context_builder essentials
check_required "context_builder/main.py"                      "CB entry point"
check_required "context_builder/requirements.txt"             "CB Python deps"
check_required "context_builder/engine"                       "CB engine package"
check_required "context_builder/parsers"                      "CB parsers package"
check_required "context_builder/db"                           "CB database layer"
check_required "context_builder/.github/copilot-instructions.md" "CB Copilot config"
check_required "context_builder/vscode-extension/src"         "CB TS extension source"
check_required "context_builder/vscode-extension/package.json" "CB extension manifest"

# Test_case_creator essentials
check_required "Test_case_creator/engine/mcp_server.py"       "TCC MCP server"
check_required "Test_case_creator/requirements.txt"           "TCC Python deps"
check_required "Test_case_creator/.github/copilot-instructions.md" "TCC Copilot config"
check_required "Test_case_creator/vscode-extension/src"       "TCC TS extension source"
check_required "Test_case_creator/vscode-extension/package.json" "TCC extension manifest"

if [ "$VALIDATION_FAILED" -ne 0 ]; then
    echo ""
    print_err "Staging validation failed — one or more required files are missing. Aborting."
fi

print_ok "All required files validated"

# ── Step 8: Report staging contents ───────────────────────────────────────────
echo ""
echo "  Staging breakdown:"
echo "    context_builder/:   $(find "$STAGING_DIR/context_builder" -type f | wc -l | tr -d ' ') files"
echo "    Test_case_creator/: $(find "$STAGING_DIR/Test_case_creator" -type f | wc -l | tr -d ' ') files"
echo "    Root files:         $(find "$STAGING_DIR" -maxdepth 1 -type f | wc -l | tr -d ' ') files"
echo "    Total staging size: $(du -sh "$STAGING_DIR" | cut -f1)"

# ── Step 9: Create ZIP ────────────────────────────────────────────────────────
print_step "🤐 Step 9: Compressing into distributable ZIP..."

rm -f "$AGENTS_DIR/$ZIP_NAME"

# Zip from inside staging so the archive root is the package contents (not release_staging/)
cd "$STAGING_DIR"
zip -r "$AGENTS_DIR/$ZIP_NAME" . > /dev/null
cd "$AGENTS_DIR"

ZIP_SIZE=$(du -sh "$AGENTS_DIR/$ZIP_NAME" | cut -f1)
print_ok "ZIP created: $ZIP_NAME ($ZIP_SIZE)"

# ── Step 10: Cleanup ──────────────────────────────────────────────────────────
print_step "🧹 Step 10: Cleaning up staging directory..."

rm -rf "$STAGING_DIR"
print_ok "Staging directory removed"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}=============================================================="
echo "  🎉 Distribution Package Created Successfully!"
echo -e "==============================================================${NC}"
echo ""
echo -e "  Package: ${CYAN}${AGENTS_DIR}/${ZIP_NAME}${NC}  (${ZIP_SIZE})"
echo ""
echo -e "${BOLD}Contents of the package:${NC}"
echo "  install.sh / install.ps1   ← Combined installer (builds VSIXes from source)"
echo "  INSTALL.md                 ← Setup guide"
echo "  context_builder/           ← Python MCP server + VS Code extension source"
echo "  Test_case_creator/         ← Python MCP server + VS Code extension source"
echo ""
echo -e "${BOLD}How recipients install:${NC}"
echo "  1. Transfer and unzip:  ${BOLD}unzip ${ZIP_NAME} -d agents${NC}"
echo "  2. cd to target project: ${BOLD}cd /path/to/their-project${NC}"
echo "  3. Run installer:        ${BOLD}bash /path/to/agents/install.sh${NC}"
echo ""
echo -e "  Prerequisites on target machine: Python 3.10+, Node.js 18+, VS Code 'code' CLI"
echo ""
