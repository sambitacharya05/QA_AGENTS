#!/bin/bash
# ==============================================================================
# Automated Installer: @build_context Agent
# ==============================================================================

# Exit immediately if a command exits with a non-zero status
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
echo -e "${BLUE}${BOLD}        🛠️  Installing @build_context Agent & Backend${NC}"
echo -e "${CYAN}${BOLD}==============================================================${NC}"

# Determine script's own directory (where release assets live)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Define target workspace as the current directory where the script is run
TARGET_WORKSPACE="$(pwd)"

# If the script lives inside a release folder, target the parent as the project root.
if [[ "$(basename "$SCRIPT_DIR")" == context_builder_release* ]]; then
    TARGET_WORKSPACE="$(dirname "$SCRIPT_DIR")"
    echo -e "  ${YELLOW}ℹ️  Detected execution from inside extracted ZIP folder. Targeting parent project root: ${TARGET_WORKSPACE}${NC}"
fi

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

# Check pip module
if ! $PYTHON_CMD -m pip --version &> /dev/null; then
    echo -e "${RED}❌ pip module could not be loaded via '$PYTHON_CMD -m pip'. Please ensure pip is installed alongside Python.${NC}"
    exit 1
else
    echo -e "  - ${GREEN}✓ pip module found${NC}"
fi

# Check Node.js and npm
if ! command -v npm &> /dev/null; then
    echo -e "${YELLOW}⚠️  npm/Node.js could not be found. The pre-compiled VSIX will still install, but local TypeScript compiling will not be available.${NC}"
else
    echo -e "  - ${GREEN}✓ Node/npm found${NC}: $(node --version)"
fi

# Check VS Code 'code' CLI
CODE_CLI_AVAILABLE=true
if ! command -v code &> /dev/null; then
    echo -e "${YELLOW}⚠️  The VS Code 'code' CLI was not found in your system PATH.${NC}"
    echo -e "    The installer requires the 'code' command to automatically register the extension."
    echo -e "    Please add 'code' to your system PATH and run the installer again, or register the extension manually."
    CODE_CLI_AVAILABLE=false
else
    echo -e "  - ${GREEN}✓ VS Code 'code' CLI found${NC}"
fi

# ------------------------------------------------------------------------------
# Step 2: Install Python Dependencies
# ------------------------------------------------------------------------------
echo -e "\n${BOLD}🐍 Step 2: Installing Python backend dependencies...${NC}"
if [ -f "$SCRIPT_DIR/requirements.txt" ]; then
    echo -e "  Installing requirements listed in requirements.txt..."
    $PYTHON_CMD -m pip install -r "$SCRIPT_DIR/requirements.txt"
    echo -e "  - ${GREEN}✓ Python dependencies successfully installed${NC}"
else
    echo -e "${RED}❌ requirements.txt not found in the release directory. Cannot install python dependencies.${NC}"
    exit 1
fi

# ------------------------------------------------------------------------------
# Step 3: Install VS Code Extension VSIX
# ------------------------------------------------------------------------------
echo -e "\n${BOLD}🔌 Step 3: Registering VS Code extension...${NC}"
VSIX_FILE=$(ls "$SCRIPT_DIR"/*.vsix 2>/dev/null | head -n 1)

if [ -z "$VSIX_FILE" ]; then
    echo -e "${RED}❌ No compiled .vsix extension file found in the release folder.${NC}"
    exit 1
fi

if [ "$CODE_CLI_AVAILABLE" = true ]; then
    echo -e "  Registering ${CYAN}$VSIX_FILE${NC} in VS Code..."
    code --install-extension "$VSIX_FILE"
    echo -e "  - ${GREEN}✓ Extension registered successfully in VS Code${NC}"
else
    echo -e "${YELLOW}⚠️  Please manually install the extension inside VS Code:${NC}"
    echo -e "    1. Open VS Code."
    echo -e "    2. Press ${BOLD}Cmd+Shift+X${NC} to open Extensions."
    echo -e "    3. Click the ${BOLD}...${NC} (More Actions) in the top-right corner."
    echo -e "    4. Select ${BOLD}Install from VSIX...${NC} and pick ${CYAN}$VSIX_FILE${NC}."
fi

# ------------------------------------------------------------------------------
# Step 4: Provision Target Workspace Directories & Awesome-Copilot configs
# ------------------------------------------------------------------------------
echo -e "\n${BOLD}📂 Step 4: Configuring workspace directories and Copilot files...${NC}"

# Initialize blank ./ingest folder
echo -e "  Initializing ${CYAN}./ingest/${NC} folder..."
mkdir -p "$TARGET_WORKSPACE/ingest"
echo -e "  - ${GREEN}✓ Ingestion directory initialized${NC}"

# Copy/Merge .github configs if present in release package
if [ -d "$SCRIPT_DIR/install_assets/.github" ]; then
    mkdir -p "$TARGET_WORKSPACE/.github"
    # Compare canonical paths to prevent copying onto itself
    SRC_CANONICAL=$(cd "$SCRIPT_DIR/install_assets/.github" && pwd)
    DST_CANONICAL=$(cd "$TARGET_WORKSPACE/.github" && pwd)
    if [ "$SRC_CANONICAL" != "$DST_CANONICAL" ]; then
        echo -e "  Merging standardized Copilot instructions, agents, and skills to ${CYAN}./.github/${NC}..."
        
        # Copy agents, skills, hooks recursively (adding new agents/skills without skipping/deleting existing ones)
        if [ -d "$SCRIPT_DIR/install_assets/.github/agents" ]; then
            mkdir -p "$TARGET_WORKSPACE/.github/agents"
            cp -R "$SCRIPT_DIR/install_assets/.github/agents/"* "$TARGET_WORKSPACE/.github/agents/"
        fi
        if [ -d "$SCRIPT_DIR/install_assets/.github/skills" ]; then
            mkdir -p "$TARGET_WORKSPACE/.github/skills"
            cp -R "$SCRIPT_DIR/install_assets/.github/skills/"* "$TARGET_WORKSPACE/.github/skills/"
        fi
        if [ -d "$SCRIPT_DIR/install_assets/.github/hooks" ]; then
            mkdir -p "$TARGET_WORKSPACE/.github/hooks"
            if [ -f "$TARGET_WORKSPACE/.github/hooks/on-save.json" ]; then
                if grep -q "Workspace Ingest Syncer" "$TARGET_WORKSPACE/.github/hooks/on-save.json"; then
                    echo -e "  - ${GREEN}✓ Workspace Ingest Syncer hook already present in .github/hooks/on-save.json${NC}"
                else
                    echo -e "  ${YELLOW}⚠️  Existing on-save.json found in .github/hooks/. Backing up to on-save.json.bak${NC}"
                    mv "$TARGET_WORKSPACE/.github/hooks/on-save.json" "$TARGET_WORKSPACE/.github/hooks/on-save.json.bak"
                    cp "$SCRIPT_DIR/install_assets/.github/hooks/on-save.json" "$TARGET_WORKSPACE/.github/hooks/"
                fi
            else
                cp "$SCRIPT_DIR/install_assets/.github/hooks/on-save.json" "$TARGET_WORKSPACE/.github/hooks/"
            fi
        fi
        
        # Safely merge copilot-instructions.md instead of overwriting
        if [ -f "$TARGET_WORKSPACE/.github/copilot-instructions.md" ]; then
            if grep -q "@build_context" "$TARGET_WORKSPACE/.github/copilot-instructions.md"; then
                echo -e "  - ${GREEN}✓ @build_context instructions already present in ./github/copilot-instructions.md${NC}"
            else
                echo -e "  Appending @build_context instructions to existing ${CYAN}./.github/copilot-instructions.md${NC}..."
                echo -e "\n\n---\n" >> "$TARGET_WORKSPACE/.github/copilot-instructions.md"
                cat "$SCRIPT_DIR/install_assets/.github/copilot-instructions.md" >> "$TARGET_WORKSPACE/.github/copilot-instructions.md"
                echo -e "  - ${GREEN}✓ Instructions successfully merged${NC}"
            fi
        else
            cp "$SCRIPT_DIR/install_assets/.github/copilot-instructions.md" "$TARGET_WORKSPACE/.github/"
            echo -e "  - ${GREEN}✓ copilot-instructions.md deployed${NC}"
        fi
        echo -e "  - ${GREEN}✓ Standardized .github configs successfully merged into: ${BOLD}$TARGET_WORKSPACE/.github${NC}"
    else
        echo -e "  - ${GREEN}✓ Standardized .github configs already in place (identical path)${NC}"
    fi
else
    echo -e "${YELLOW}⚠️  No .github configuration directory found in release package.${NC}"
fi

# ------------------------------------------------------------------------------
# Step 5: Finish
# ------------------------------------------------------------------------------
echo -e "\n${GREEN}${BOLD}==============================================================${NC}"
echo -e "${GREEN}${BOLD}🎉 Installation Completed Successfully!${NC}"
echo -e "${GREEN}${BOLD}==============================================================${NC}"
echo -e "\n${BOLD}Next steps to verify your setup:${NC}"
echo -e "  1. Launch VS Code in this directory:"
echo -e "     ${CYAN}code .${NC}"
echo -e "  2. Open the Copilot Chat panel (${BOLD}Cmd+Ctrl+I${NC} or click the chat bubble)."
echo -e "  3. Verify ${BOLD}@build_context${NC} is listed by typing '@'."
echo -e "  4. Place insurance specifications (spreadsheets, specs, code) in ${CYAN}./ingest/${NC}."
echo -e "  5. Type ${BOLD}@build_context /status${NC} in Copilot Chat to verify the backend database connectivity."
echo -e "\nFor advanced tutorials and workflows, read ${CYAN}docs/USER_GUIDE.md${NC}.\n"
