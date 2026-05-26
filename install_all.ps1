# ==============================================================================
# Combined Installer: Context Builder + Test Case Creator (Windows PowerShell)
# ==============================================================================
#
# Usage: cd C:\path\to\your-project
#        Set-ExecutionPolicy Bypass -Scope Process
#        & "C:\path\to\agents\install_all.ps1"
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

$ErrorActionPreference = "Stop"

function Write-OK   { param($msg) Write-Host "  v $msg" -ForegroundColor Green }
function Write-Warn { param($msg) Write-Host "  WARNING: $msg" -ForegroundColor Yellow }
function Write-Err  { param($msg) Write-Host "  ERROR: $msg" -ForegroundColor Red }
function Write-Step { param($msg) Write-Host "`n$msg" -ForegroundColor Cyan }

Write-Host "`n==============================================================" -ForegroundColor Cyan
Write-Host "  Combined Installer: Context Builder + Test Case Creator" -ForegroundColor Cyan
Write-Host "==============================================================`n" -ForegroundColor Cyan

# ── Step 0: Resolve Directories ────────────────────────────────────────────────
Write-Step "Step 0: Resolving directories..."

$ScriptDir       = Split-Path -Parent $MyInvocation.MyCommand.Path
$AgentsDir       = $ScriptDir
$TargetWorkspace = Get-Location | Select-Object -ExpandProperty Path

if ($TargetWorkspace -eq $AgentsDir) {
    Write-Err "Do not run install_all.ps1 from inside the agents/ directory."
    Write-Host "  Please cd to your target project first, then run this script."
    exit 1
}

Write-Host "  AGENTS_DIR:        $AgentsDir"
Write-Host "  TARGET_WORKSPACE:  $TargetWorkspace"

$CbDir  = Join-Path $AgentsDir "context_builder"
$TccDir = Join-Path $AgentsDir "Test_case_creator"

if (!(Test-Path $CbDir) -or !(Test-Path $TccDir)) {
    Write-Err "Expected context_builder/ and Test_case_creator/ inside: $AgentsDir"
    exit 1
}

# ── Step 1: Prerequisites ──────────────────────────────────────────────────────
Write-Step "Step 1: Checking system prerequisites..."

$PythonCmd = $null
foreach ($candidate in @("python3", "python")) {
    if (Get-Command $candidate -ErrorAction SilentlyContinue) {
        $PythonCmd = $candidate
        break
    }
}

if (!$PythonCmd) {
    Write-Err "Python not found. Install Python 3.10+ and retry."
    exit 1
}

$PythonMajor = (& $PythonCmd -c "import sys; print(sys.version_info.major)").Trim()
$PythonMinor = (& $PythonCmd -c "import sys; print(sys.version_info.minor)").Trim()

if ([int]$PythonMajor -lt 3 -or ([int]$PythonMajor -eq 3 -and [int]$PythonMinor -lt 10)) {
    Write-Err "Python 3.10+ required. Found: $PythonMajor.$PythonMinor"
    exit 1
}
Write-OK "Python $PythonMajor.$PythonMinor found"

if (!(& $PythonCmd -m pip --version 2>&1 | Select-String -Quiet "pip")) {
    Write-Err "pip is not available. Install pip alongside Python and retry."
    exit 1
}
Write-OK "pip available"

if (!(Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Err "npm/Node.js not found. Install Node.js 18+ and retry."
    exit 1
}
Write-OK "Node/npm found: $(node --version)"

$CodeCliAvailable = $true
if (!(Get-Command code -ErrorAction SilentlyContinue)) {
    Write-Warn "VS Code 'code' CLI not found. Extensions will be built but not auto-installed."
    Write-Warn "After the script completes, install manually via: Extensions panel -> ... -> Install from VSIX"
    $CodeCliAvailable = $false
} else {
    Write-OK "VS Code 'code' CLI found"
}

# ── Step 2: context_builder venv ──────────────────────────────────────────────
Write-Step "Step 2: Setting up context_builder Python environment..."

$CbVenv   = Join-Path $CbDir ".venv"
$CbPython = Join-Path $CbVenv "Scripts\python.exe"
$CbPip    = Join-Path $CbVenv "Scripts\pip.exe"

if (!(Test-Path $CbVenv)) {
    Write-Host "  Creating .venv in $CbVenv ..."
    & $PythonCmd -m venv $CbVenv
    Write-OK "Virtual environment created"
} else {
    Write-OK "Virtual environment already exists - reusing"
}

& $CbPip install --quiet --upgrade pip
& $CbPip install --quiet -r (Join-Path $CbDir "requirements.txt")
Write-OK "context_builder Python dependencies installed"

# ── Step 3: Test_case_creator venv ────────────────────────────────────────────
Write-Step "Step 3: Setting up Test_case_creator Python environment..."

$TccVenv   = Join-Path $TccDir ".venv"
$TccPython = Join-Path $TccVenv "Scripts\python.exe"
$TccPip    = Join-Path $TccVenv "Scripts\pip.exe"

if (!(Test-Path $TccVenv)) {
    & $PythonCmd -m venv $TccVenv
    Write-OK "Virtual environment created"
} else {
    Write-OK "Virtual environment already exists - reusing"
}

& $TccPip install --quiet --upgrade pip
& $TccPip install --quiet -r (Join-Path $TccDir "requirements.txt")
& $TccPip install --quiet -r (Join-Path $TccDir "requirements-dev.txt")
Write-OK "Test_case_creator Python dependencies installed"

# ── Steps 4 & 5: Write config BEFORE any VSIX install ─────────────────────────
# Installing a VSIX triggers VS Code extension activation immediately.
# If settings haven't been written yet the extension reads empty config and
# starts the MCP server from the wrong directory.  Write first, install second.

# Python merge helper (accepts target_path and json_data as CLI args)
$MergeScript = @'
import json, os, sys

def deep_update(base, updates):
    for k, v in updates.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            deep_update(base[k], v)
        else:
            base[k] = v
    return base

target_path = sys.argv[1]
merge_data  = json.loads(sys.argv[2])
existing = {}
if os.path.exists(target_path):
    try:
        with open(target_path, encoding='utf-8') as f:
            existing = json.load(f)
    except Exception:
        pass
deep_update(existing, merge_data)
with open(target_path, 'w', encoding='utf-8') as f:
    json.dump(existing, f, indent=2)
    f.write('\n')
print('OK')
'@

# Escape backslashes in paths for JSON string values
$CbDirJson  = $CbDir     -replace '\\', '\\\\'
$TccDirJson = $TccDir    -replace '\\', '\\\\'
$CbPyJson   = $CbPython  -replace '\\', '\\\\'
$TccPyJson  = $TccPython -replace '\\', '\\\\'

$VscodeDir = Join-Path $TargetWorkspace ".vscode"
New-Item -ItemType Directory -Force -Path $VscodeDir | Out-Null

Write-Step "Step 4: Registering MCP servers in .vscode/mcp.json..."
$McpData = "{`"servers`":{`"context-builder`":{`"type`":`"stdio`",`"command`":`"$CbPyJson`",`"args`":[`"main.py`"],`"cwd`":`"$CbDirJson`"},`"test-case-creator`":{`"type`":`"stdio`",`"command`":`"$TccPyJson`",`"args`":[`"-m`",`"engine.mcp_server`"],`"cwd`":`"$TccDirJson`"}}}"
& $PythonCmd -c $MergeScript (Join-Path $VscodeDir "mcp.json") $McpData
Write-OK ".vscode/mcp.json written with context-builder and test-case-creator servers"

Write-Step "Step 5: Writing VS Code settings to .vscode/settings.json..."
$SettingsData = "{`"contextBuilder.serverDirectory`":`"$CbDirJson`",`"testCaseCreator.engineDirectory`":`"$TccDirJson`"}"
& $PythonCmd -c $MergeScript (Join-Path $VscodeDir "settings.json") $SettingsData
Write-OK ".vscode/settings.json updated with contextBuilder.serverDirectory and testCaseCreator.engineDirectory"

# ── Steps 6 & 7: Build & Install VSIXes (after config is on disk) ──────────────
function Install-Vsix {
    param($ExtDir, $Label)
    Write-Step "Step: Building and installing $Label VS Code extension..."
    Push-Location $ExtDir
    try {
        npm install --silent
        npm run compile
        # vsce package failure is non-fatal
        try {
            npx -y @vscode/vsce package 2>&1 | Out-Null
        } catch {
            Write-Warn "$Label VSIX packaging failed: $_. Continuing..."
            Pop-Location
            return
        }
        $vsix = Get-ChildItem -Path $ExtDir -Filter "*.vsix" -ErrorAction SilentlyContinue | Select-Object -First 1
        if (!$vsix) {
            Write-Warn "Could not locate .vsix for $Label"
            return
        }
        if ($CodeCliAvailable) {
            code --install-extension $vsix.FullName --force
            Write-OK "$Label extension installed: $($vsix.Name)"
        } else {
            Write-Warn "Manually install: $($vsix.FullName)"
        }
        Remove-Item $vsix.FullName -Force
        Write-OK "Cleaned up .vsix file"
    } catch {
        Write-Warn "$Label VSIX build failed: $_. Continuing..."
    } finally {
        Pop-Location
    }
}

Install-Vsix (Join-Path $CbDir "vscode-extension")  "context_builder"
Install-Vsix (Join-Path $TccDir "vscode-extension") "Test_case_creator"

# ── Step 8: Merge .github/ ────────────────────────────────────────────────────
Write-Step "Step 8: Merging .github/ configuration files into target project..."

$GhDir = Join-Path $TargetWorkspace ".github"
@("agents", "skills", "hooks") | ForEach-Object {
    New-Item -ItemType Directory -Force -Path (Join-Path $GhDir $_) | Out-Null
}

# Copy context_builder agents
$CbAgentsDir = Join-Path $CbDir ".github\agents"
if (Test-Path $CbAgentsDir) {
    Get-ChildItem $CbAgentsDir -Filter "*.agent.md" | ForEach-Object {
        Copy-Item $_.FullName (Join-Path $GhDir "agents\$($_.Name)") -Force
    }
    Write-OK "context_builder agent files copied"
}

# Copy Test_case_creator agents (source-of-truth is src/agents/)
$TccAgentsDir = Join-Path $TccDir "vscode-extension\src\agents"
if (Test-Path $TccAgentsDir) {
    Get-ChildItem $TccAgentsDir -Filter "*.agent.md" | ForEach-Object {
        Copy-Item $_.FullName (Join-Path $GhDir "agents\$($_.Name)") -Force
    }
    Write-OK "Test_case_creator agent files copied"
}

# Copy context_builder skills
$CbSkillsDir = Join-Path $CbDir ".github\skills"
if (Test-Path $CbSkillsDir) {
    Copy-Item (Join-Path $CbSkillsDir "*") (Join-Path $GhDir "skills") -Recurse -Force
    Write-OK "context_builder skill files copied"
}

# Merge hooks/on-save.json
$OnSaveSource = Join-Path $CbDir ".github\hooks\on-save.json"
$OnSaveTarget = Join-Path $GhDir "hooks\on-save.json"
if (Test-Path $OnSaveSource) {
    if (Test-Path $OnSaveTarget) {
        $existingHook = Get-Content $OnSaveTarget -Raw
        if ($existingHook -match "Workspace Ingest Syncer") {
            Write-OK "on-save.json hook already up to date"
        } else {
            Copy-Item $OnSaveTarget "$OnSaveTarget.bak" -Force
            Copy-Item $OnSaveSource $OnSaveTarget -Force
            Write-OK "on-save.json replaced (backup saved as on-save.json.bak)"
        }
    } else {
        Copy-Item $OnSaveSource $OnSaveTarget -Force
        Write-OK "on-save.json deployed"
    }
}

# Merge copilot-instructions.md (safe append, never duplicate)
$InstructionsTarget = Join-Path $GhDir "copilot-instructions.md"

function Append-InstructionIfMissing {
    param($Marker, $SourceFile, $Label)
    if (Test-Path $SourceFile) {
        if (Test-Path $InstructionsTarget) {
            $existing = Get-Content $InstructionsTarget -Raw -ErrorAction SilentlyContinue
            if ($existing -match [regex]::Escape($Marker)) {
                Write-OK "$Label instructions already present in copilot-instructions.md"
            } else {
                Add-Content $InstructionsTarget "`n`n---`n"
                Get-Content $SourceFile | Add-Content $InstructionsTarget
                Write-OK "$Label instructions appended to copilot-instructions.md"
            }
        } else {
            Copy-Item $SourceFile $InstructionsTarget -Force
            Write-OK "$Label copilot-instructions.md deployed"
        }
    }
}

Append-InstructionIfMissing "@build_context" (Join-Path $CbDir ".github\copilot-instructions.md") "context_builder"
Append-InstructionIfMissing "@create_tests"  (Join-Path $TccDir ".github\copilot-instructions.md") "Test_case_creator"

# ── Step 9: Initialize ingest/ ────────────────────────────────────────────────
Write-Step "Step 9: Initializing ingest/ folder..."

$IngestDir = Join-Path $TargetWorkspace "ingest"
if (!(Test-Path $IngestDir)) {
    New-Item -ItemType Directory -Path $IngestDir | Out-Null
    Write-OK "ingest/ directory created"
} else {
    Write-OK "ingest/ directory already exists"
}

# ── Summary ───────────────────────────────────────────────────────────────────
Write-Host "`n==============================================================" -ForegroundColor Green
Write-Host "  Installation Completed Successfully!" -ForegroundColor Green
Write-Host "==============================================================`n" -ForegroundColor Green
Write-Host "What was installed:" -ForegroundColor White
Write-Host "  * context_builder Python environment: $CbVenv"
Write-Host "  * Test_case_creator Python environment: $TccVenv"
Write-Host "  * .github/agents/ populated with agent prompt files"
Write-Host "  * .vscode/mcp.json registered both MCP servers"
Write-Host "  * .vscode/settings.json configured with server directories"
Write-Host ""
Write-Host "Next steps:" -ForegroundColor White
Write-Host "  1. Reload VS Code: Ctrl+Shift+P -> Developer: Reload Window"
Write-Host "  2. Open Copilot Chat and verify @build_context is listed"
Write-Host "  3. Place specification files in: .\ingest\"
Write-Host "  4. Run @build_context /ingest to build the semantic context graph"
Write-Host "  5. Run @create_tests /generate to generate Azure DevOps test cases"
Write-Host ""
Write-Host "MCP server config: $TargetWorkspace\.vscode\mcp.json"
Write-Host "Settings config:   $TargetWorkspace\.vscode\settings.json"
Write-Host ""
