#!/bin/bash
# ==============================================================================
# Automated Installer: Test Case Creator Agent
# ==============================================================================

set -e

# Curated HSL-tailored ANSI color codes for premium console output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color
BOLD='\033[1m'

echo -e "${CYAN}${BOLD}==============================================================${NC}"
echo -e "${BLUE}${BOLD}        🛠️  Installing Test Case Creator Agent & Backend${NC}"
echo -e "${CYAN}${BOLD}==============================================================${NC}"

# Define target workspace as the current directory
WORKSPACE_ROOT="$(pwd)"

# ------------------------------------------------------------------------------
# Step 1: Validate Prerequisites
# ------------------------------------------------------------------------------
echo -e "\n${BOLD}🔍 Step 1: Checking system prerequisites...${NC}"

# Check python/python3 command
PYTHON_CMD=""
if command -v python3 &> /dev/null; then
    PYTHON_CMD="python3"
elif command -v python &> /dev/null; then
    PYTHON_CMD="python"
fi

if [ -z "$PYTHON_CMD" ]; then
    echo -e "${RED}❌ Python could not be found. Please install Python 3.10+ before running this installer.${NC}"
    exit 1
else
    PYTHON_VERSION=$($PYTHON_CMD --version 2>&1)
    echo -e "  - ${GREEN}✓ Python found${NC}: $PYTHON_VERSION"
fi

# Check Node.js and npm
if ! command -v npm &> /dev/null; then
    echo -e "${RED}❌ npm/Node.js could not be found. TypeScript compilation requires Node.js.${NC}"
    exit 1
else
    echo -e "  - ${GREEN}✓ Node/npm found${NC}: $(node --version)"
fi

# ------------------------------------------------------------------------------
# Step 2: Install NodeJS packages inside vscode-extension/
# ------------------------------------------------------------------------------
echo -e "\n${BOLD}📦 Step 2: Installing NodeJS packages...${NC}"
cd "$WORKSPACE_ROOT/vscode-extension"
npm install
echo -e "  - ${GREEN}✓ NodeJS dependencies successfully installed${NC}"

# ------------------------------------------------------------------------------
# Step 3: Configure Python Virtual Environment & Install Dependencies
# ------------------------------------------------------------------------------
echo -e "\n${BOLD}🐍 Step 3: Provisioning Python virtual environment...${NC}"
cd "$WORKSPACE_ROOT"
if [ ! -d ".venv" ]; then
    $PYTHON_CMD -m venv .venv
    echo -e "  - ${GREEN}✓ Virtual environment created (.venv)${NC}"
else
    echo -e "  - ${YELLOW}ℹ️  Virtual environment already exists. Re-using .venv.${NC}"
fi

# Activate and install
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install -r requirements-dev.txt
echo -e "  - ${GREEN}✓ Python dependencies successfully installed inside .venv${NC}"

# ------------------------------------------------------------------------------
# Step 4: Compiling TypeScript Scaffolding
# ------------------------------------------------------------------------------
echo -e "\n${BOLD}🔨 Step 4: Compiling TypeScript and bundling assets...${NC}"
cd "$WORKSPACE_ROOT/vscode-extension"
npm run compile
echo -e "  - ${GREEN}✓ Webpack compilation succeeded!${NC}"

# ------------------------------------------------------------------------------
# Step 5: Finish
# ------------------------------------------------------------------------------
echo -e "\n${GREEN}${BOLD}==============================================================${NC}"
echo -e "${GREEN}${BOLD}🎉 Scaffolding & Setup Completed Successfully!${NC}"
echo -e "${GREEN}${BOLD}==============================================================${NC}"
echo -e "\n${BOLD}Verification and local running commands:${NC}"
echo -e "  1. Run Python unit tests to check the scaffolding:"
echo -e "     ${CYAN}pytest tests/engine/${NC}"
echo -e "  2. Launch VS Code debugging in this workspace to run extension tests."
echo -e "\nFor advanced tutorials and workflows, read ${CYAN}README.md${NC}.\n"
