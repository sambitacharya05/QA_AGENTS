# ==============================================================================
# Automated Installer: Test Case Creator Agent (PowerShell Windows)
# ==============================================================================

$ErrorActionPreference = "Stop"

Write-Host "=============================================================="
Write-Host "        🛠️  Installing Test Case Creator Agent & Backend"
Write-Host "=============================================================="

$WORKSPACE_ROOT = Get-Location

# ------------------------------------------------------------------------------
# Step 1: Validate Prerequisites
# ------------------------------------------------------------------------------
Write-Host "`n🔍 Step 1: Checking system prerequisites..."

# Check python command
$PYTHON_CMD = ""
if (Get-Command "python3" -ErrorAction SilentlyContinue) {
    $PYTHON_CMD = "python3"
} elseif (Get-Command "python" -ErrorAction SilentlyContinue) {
    $PYTHON_CMD = "python"
}

if ($PYTHON_CMD -eq "") {
    Write-Error "❌ Python could not be found. Please install Python 3.10+ before running this installer."
    Exit
} else {
    $PYTHON_VERSION = & $PYTHON_CMD --version
    Write-Host "  - ✓ Python found: $PYTHON_VERSION"
}

# Check Node.js and npm
if (-not (Get-Command "npm" -ErrorAction SilentlyContinue)) {
    Write-Error "❌ npm/Node.js could not be found. TypeScript compilation requires Node.js."
    Exit
} else {
    $NODE_VERSION = node --version
    Write-Host "  - ✓ Node/npm found: $NODE_VERSION"
}

# ------------------------------------------------------------------------------
# Step 2: Install NodeJS packages inside vscode-extension/
# ------------------------------------------------------------------------------
Write-Host "`n📦 Step 2: Installing NodeJS packages..."
Set-Location "$WORKSPACE_ROOT\vscode-extension"
Start-Process "npm" -ArgumentList "install" -NoNewWindow -Wait
Write-Host "  - ✓ NodeJS dependencies successfully installed"

# ------------------------------------------------------------------------------
# Step 3: Configure Python Virtual Environment & Install Dependencies
# ------------------------------------------------------------------------------
Write-Host "`n🐍 Step 3: Provisioning Python virtual environment..."
Set-Location $WORKSPACE_ROOT
if (-not (Test-Path ".venv")) {
    & $PYTHON_CMD -m venv .venv
    Write-Host "  - ✓ Virtual environment created (.venv)"
} else {
    Write-Host "  - ℹ️  Virtual environment already exists. Re-using .venv."
}

# Activate and install
& .venv\Scripts\pip.exe install --upgrade pip
& .venv\Scripts\pip.exe install -r requirements.txt
& .venv\Scripts\pip.exe install -r requirements-dev.txt
Write-Host "  - ✓ Python dependencies successfully installed inside .venv"

# ------------------------------------------------------------------------------
# Step 4: Compiling TypeScript Scaffolding
# ------------------------------------------------------------------------------
Write-Host "`n🔨 Step 4: Compiling TypeScript and bundling assets..."
Set-Location "$WORKSPACE_ROOT\vscode-extension"
Start-Process "npm" -ArgumentList "run compile" -NoNewWindow -Wait
Write-Host "  - ✓ Webpack compilation succeeded!"

# ------------------------------------------------------------------------------
# Step 5: Finish
# ------------------------------------------------------------------------------
Write-Host "`n=============================================================="
Write-Host "🎉 Scaffolding & Setup Completed Successfully!"
Write-Host "=============================================================="
Write-Host "`nVerification and local running commands:"
Write-Host "  1. Run Python unit tests to check the scaffolding:"
Write-Host "     .venv\Scripts\pytest.exe tests\engine\"
Write-Host "  2. Launch VS Code debugging in this workspace to run extension tests."
Write-Host "`nFor advanced tutorials and workflows, read README.md.`n"
Set-Location $WORKSPACE_ROOT
