#!/bin/bash
# ==============================================================================
# Development Packaging Script: @build_context Agent
# ==============================================================================

# Exit immediately if a command exits with a non-zero status
set -e

# Premium ANSI Color Codes for developer terminal feedback
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color
BOLD='\033[1m'

echo -e "${CYAN}${BOLD}==============================================================${NC}"
echo -e "${BLUE}${BOLD}        📦 Packaging @build_context Agent Release Package${NC}"
echo -e "${CYAN}${BOLD}==============================================================${NC}"

# Define workspace directories
WORKSPACE_ROOT="$(pwd)"
EXTENSION_DIR="$WORKSPACE_ROOT/vscode-extension"
STAGING_DIR="$WORKSPACE_ROOT/release_staging"
ZIP_NAME="context_builder_release.zip"

# ------------------------------------------------------------------------------
# Step 1: Compile TypeScript Extension
# ------------------------------------------------------------------------------
echo -e "\n${BOLD}🔨 Step 1: Compiling VS Code TypeScript code...${NC}"
cd "$EXTENSION_DIR"
npm run compile
echo -e "${GREEN}✓ TypeScript compilation succeeded!${NC}"

# ------------------------------------------------------------------------------
# Step 2: Package extension into VSIX using vsce
# ------------------------------------------------------------------------------
echo -e "\n${BOLD}🔌 Step 2: Packaging extension into VSIX...${NC}"
# Use npx @vscode/vsce package to avoid global install requirement
npx -y @vscode/vsce package --out "$WORKSPACE_ROOT"
cd "$WORKSPACE_ROOT"

VSIX_FILE=$(ls *.vsix 2>/dev/null | head -n 1)
if [ -z "$VSIX_FILE" ]; then
    echo -e "${RED}❌ Failed to locate compiled .vsix extension file after packaging.${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Successfully generated extension VSIX${NC}: $VSIX_FILE"

# ------------------------------------------------------------------------------
# Step 3: Create clean staging layout
# ------------------------------------------------------------------------------
echo -e "\n${BOLD}📂 Step 3: Creating clean staging directories...${NC}"
rm -rf "$STAGING_DIR"
mkdir -p "$STAGING_DIR"

# Copy VSIX and Installer
cp "$VSIX_FILE" "$STAGING_DIR/"
cp "$WORKSPACE_ROOT/install.sh" "$STAGING_DIR/"
cp "$WORKSPACE_ROOT/install.ps1" "$STAGING_DIR/"
cp "$WORKSPACE_ROOT/requirements.txt" "$STAGING_DIR/"
cp "$WORKSPACE_ROOT/main.py" "$STAGING_DIR/"

# Copy Python modules recursively
cp -R "$WORKSPACE_ROOT/parsers" "$STAGING_DIR/"
cp -R "$WORKSPACE_ROOT/db" "$STAGING_DIR/"
cp -R "$WORKSPACE_ROOT/engine" "$STAGING_DIR/"

# Copy Standardized .github configurations recursively to a conflict-free folder
mkdir -p "$STAGING_DIR/install_assets"
cp -R "$WORKSPACE_ROOT/.github" "$STAGING_DIR/install_assets/"

# Validate .github was copied correctly (dotfiles can be silently skipped on some systems)
if [ ! -d "$STAGING_DIR/install_assets/.github" ]; then
    echo -e "  ${YELLOW}⚠️  .github was not copied by cp -R (dotfile filtering). Attempting explicit copy...${NC}"
    mkdir -p "$STAGING_DIR/install_assets/.github"
    [ -d "$WORKSPACE_ROOT/.github/agents" ] && cp -R "$WORKSPACE_ROOT/.github/agents" "$STAGING_DIR/install_assets/.github/"
    [ -d "$WORKSPACE_ROOT/.github/skills" ] && cp -R "$WORKSPACE_ROOT/.github/skills" "$STAGING_DIR/install_assets/.github/"
    [ -d "$WORKSPACE_ROOT/.github/hooks" ] && cp -R "$WORKSPACE_ROOT/.github/hooks" "$STAGING_DIR/install_assets/.github/"
    [ -f "$WORKSPACE_ROOT/.github/copilot-instructions.md" ] && cp "$WORKSPACE_ROOT/.github/copilot-instructions.md" "$STAGING_DIR/install_assets/.github/"
fi

# Final validation
if [ ! -f "$STAGING_DIR/install_assets/.github/copilot-instructions.md" ]; then
    echo -e "${RED}❌ CRITICAL: .github configs were not copied into staging. Check source directory.${NC}"
    exit 1
fi

# Copy User Documentation manuals recursively
cp -R "$WORKSPACE_ROOT/docs" "$STAGING_DIR/"

# Prune transient development and system files from staging area
echo -e "  - ${CYAN}🧹 Pruning unwanted transient development files (__pycache__, *.pyc, .DS_Store)...${NC}"
find "$STAGING_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "$STAGING_DIR" -type f -name "*.pyc" -delete 2>/dev/null || true
find "$STAGING_DIR" -type f -name "*.pyo" -delete 2>/dev/null || true
find "$STAGING_DIR" -type f -name ".DS_Store" -delete 2>/dev/null || true

echo -e "  - ${GREEN}✓ Copying assets and pruning staging completed${NC}"

# ------------------------------------------------------------------------------
# Step 4: Zip release files
# ------------------------------------------------------------------------------
echo -e "\n${BOLD}🤐 Step 4: Compressing staging directory into shippable ZIP...${NC}"
rm -f "$ZIP_NAME"

cd "$STAGING_DIR"
zip -r "$WORKSPACE_ROOT/$ZIP_NAME" * > /dev/null
cd "$WORKSPACE_ROOT"

echo -e "${GREEN}✓ Successfully created release ZIP archive${NC}: $ZIP_NAME"

# ------------------------------------------------------------------------------
# Step 5: Cleanup temporary workspace files
# ------------------------------------------------------------------------------
echo -e "\n${BOLD}🧹 Step 5: Cleaning up temporary staging directories...${NC}"
rm -rf "$STAGING_DIR"
rm -f "$VSIX_FILE"
echo -e "${GREEN}✓ Cleanup finished!${NC}"

# ------------------------------------------------------------------------------
# Summary Output
# ------------------------------------------------------------------------------
echo -e "\n${GREEN}${BOLD}==============================================================${NC}"
echo -e "${GREEN}${BOLD}🎉 Release Packaging Succeeded!${NC}"
echo -e "${GREEN}${BOLD}==============================================================${NC}"
echo -e "\nShippable package generated at:"
echo -e "  ${CYAN}${WORKSPACE_ROOT}/${ZIP_NAME}${NC}"
echo -e "\nYou can now transfer this ZIP package to any separate system and run:"
echo -e "  ${BOLD}unzip ${ZIP_NAME} && ./install.sh${NC}\n"
