#!/bin/bash
# ==============================================================================
# Development Packaging Script: Test Case Creator Agent
# ==============================================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color
BOLD='\033[1m'

echo -e "${CYAN}${BOLD}==============================================================${NC}"
echo -e "${BLUE}${BOLD}        📦 Packaging Test Case Creator Agent release .vsix${NC}"
echo -e "${CYAN}${BOLD}==============================================================${NC}"

WORKSPACE_ROOT="$(pwd)"
EXTENSION_DIR="$WORKSPACE_ROOT/vscode-extension"

# ------------------------------------------------------------------------------
# Step 1: Production Webpack build
# ------------------------------------------------------------------------------
echo -e "\n${BOLD}🔨 Step 1: Compiling production bundle...${NC}"
cd "$EXTENSION_DIR"
npm run compile
echo -e "${GREEN}✓ Production compilation succeeded!${NC}"

# ------------------------------------------------------------------------------
# Step 2: Package into VSIX
# ------------------------------------------------------------------------------
echo -e "\n${BOLD}🔌 Step 2: Packaging extension using VS Code Extension CLI (vsce)...${NC}"
npx -y @vscode/vsce package --out "$WORKSPACE_ROOT"
cd "$WORKSPACE_ROOT"

VSIX_FILE=$(ls *.vsix 2>/dev/null | head -n 1)
if [ -z "$VSIX_FILE" ]; then
    echo -e "${RED}❌ Failed to locate compiled .vsix extension file after packaging.${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Successfully generated extension VSIX${NC}: $VSIX_FILE"

# ------------------------------------------------------------------------------
# Step 3: Bundle Filtering Verification
# ------------------------------------------------------------------------------
echo -e "\n${BOLD}🔍 Step 3: Verifying packager filtering (.vscodeignore)...${NC}"
if command -v unzip &> /dev/null; then
    echo -e "  Listing VSIX contents to confirm exclusions..."
    VSIX_CONTENTS=$(unzip -l "$VSIX_FILE")
    
    if echo "$VSIX_CONTENTS" | grep -q -E "(engine/|specs/|tests/|\.sh|\.ps1)"; then
        echo -e "${RED}❌ Verification failed: Excluded source directories or shell scripts found in VSIX bundle!${NC}"
        exit 1
    else
        echo -e "  - ${GREEN}✓ Verification succeeded: specs/, tests/, engine/, and helper scripts successfully excluded.${NC}"
    fi
else
    echo -e "  - ${YELLOW}⚠️  unzip utility not available. Skipping manual exclusion checks.${NC}"
fi

echo -e "\n${GREEN}${BOLD}==============================================================${NC}"
echo -e "${GREEN}${BOLD}🎉 Release Packaging Succeeded!${NC}"
echo -e "${GREEN}${BOLD}==============================================================${NC}"
echo -e "\nFinal extension installer package located at:"
echo -e "  ${CYAN}${WORKSPACE_ROOT}/${VSIX_FILE}${NC}\n"
