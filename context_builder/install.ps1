# ==============================================================================
# Automated Windows Installer: @build_context Agent
# ==============================================================================

# Set strict mode and error handling
$ErrorActionPreference = "Stop"

# Premium HSL/ANSI colors console simulation (PowerShell Native)
Write-Host "==============================================================" -ForegroundColor Cyan
Write-Host "        🛠️  Installing @build_context Agent & Backend (Windows)" -ForegroundColor Blue
Write-Host "==============================================================" -ForegroundColor Cyan

# Determine script's own directory (where release assets live)
$ScriptDir = $PSScriptRoot

# Define target workspace as the current directory where the script is run
$TargetWorkspace = (Get-Location).Path

# If the script lives inside a release folder, target the parent as the project root.
$baseName = Split-Path $ScriptDir -Leaf
if ($baseName -like "context_builder_release*") {
    $TargetWorkspace = Split-Path -Path $ScriptDir -Parent
    Write-Host "  ℹ️  Detected execution from inside extracted ZIP folder. Targeting parent project root: $TargetWorkspace" -ForegroundColor Yellow
}

# ------------------------------------------------------------------------------
# Step 1: Validate Prerequisites
# ------------------------------------------------------------------------------
Write-Host "`nStep 1: Checking system prerequisites..." -ForegroundColor White

# Check python
$pythonCmd = $null
if (Get-Command "python" -ErrorAction SilentlyContinue) {
    $pythonCmd = "python"
} elseif (Get-Command "py" -ErrorAction SilentlyContinue) {
    $pythonCmd = "py"
}

if ($null -eq $pythonCmd) {
    Write-Host "❌ Python could not be found. Please install Python 3.10+ before running this installer." -ForegroundColor Red
    Exit 1
} else {
    $versionString = & $pythonCmd --version 2>&1
    Write-Host "  - ✓ Python found: $versionString" -ForegroundColor Green
}

# Check pip module
try {
    & $pythonCmd -m pip --version | Out-Null
    Write-Host "  - ✓ pip module found" -ForegroundColor Green
} catch {
    Write-Host "❌ pip module could not be loaded via '$pythonCmd -m pip'. Please ensure pip is installed alongside Python." -ForegroundColor Red
    Exit 1
}

# Check Node.js and npm
if (Get-Command "npm" -ErrorAction SilentlyContinue) {
    $npmVersion = & npm --version
    Write-Host "  - ✓ Node/npm found: v$npmVersion" -ForegroundColor Green
} else {
    Write-Host "⚠️  npm/Node.js could not be found. The pre-compiled VSIX will still install, but local TypeScript compiling will not be available." -ForegroundColor Yellow
}

# Check VS Code 'code' CLI
$codeCliAvailable = $false
if (Get-Command "code" -ErrorAction SilentlyContinue) {
    Write-Host "  - ✓ VS Code 'code' CLI found" -ForegroundColor Green
    $codeCliAvailable = $true
} else {
    Write-Host "⚠️  The VS Code 'code' CLI was not found in your system PATH." -ForegroundColor Yellow
    Write-Host "    The installer requires the 'code' command to automatically register the extension."
    Write-Host "    Please add 'code' to your system PATH and run the installer again, or register the extension manually."
}

# ------------------------------------------------------------------------------
# Step 2: Install Python Dependencies
# ------------------------------------------------------------------------------
Write-Host "`nStep 2: Installing Python backend dependencies..." -ForegroundColor White
if (Test-Path (Join-Path $ScriptDir "requirements.txt")) {
    Write-Host "  Installing requirements listed in requirements.txt..."
    & $pythonCmd -m pip install -r (Join-Path $ScriptDir "requirements.txt")
    Write-Host "  - ✓ Python dependencies successfully installed" -ForegroundColor Green
} else {
    Write-Host "❌ requirements.txt not found in the release directory. Cannot install python dependencies." -ForegroundColor Red
    Exit 1
}

# ------------------------------------------------------------------------------
# Step 3: Install VS Code Extension VSIX
# ------------------------------------------------------------------------------
Write-Host "`nStep 3: Registering VS Code extension..." -ForegroundColor White
$vsixFile = Get-ChildItem -Path $ScriptDir -Filter *.vsix | Select-Object -First 1 -ExpandProperty Name

if ($null -eq $vsixFile) {
    Write-Host "❌ No compiled .vsix extension file found in the release folder." -ForegroundColor Red
    Exit 1
}

if ($codeCliAvailable) {
    Write-Host "  Registering $vsixFile in VS Code..." -ForegroundColor Cyan
    & code --install-extension (Join-Path $ScriptDir $vsixFile)
    Write-Host "  - ✓ Extension registered successfully in VS Code" -ForegroundColor Green
} else {
    Write-Host "⚠️  Please manually install the extension inside VS Code:" -ForegroundColor Yellow
    Write-Host "    1. Open VS Code."
    Write-Host "    2. Press Ctrl+Shift+X to open Extensions."
    Write-Host "    3. Click the ... (More Actions) in the top-right corner."
    Write-Host "    4. Select Install from VSIX... and pick $vsixFile."
}

# ------------------------------------------------------------------------------
# Step 4: Provision Target Workspace Directories & Awesome-Copilot configs
# ------------------------------------------------------------------------------
Write-Host "`nStep 4: Configuring workspace directories and Copilot files..." -ForegroundColor White

# Initialize blank ./ingest folder
Write-Host "  Initializing ./ingest/ folder..." -ForegroundColor Cyan
New-Item -ItemType Directory -Path (Join-Path $TargetWorkspace "ingest") -Force | Out-Null
Write-Host "  - ✓ Ingestion directory initialized" -ForegroundColor Green

# Copy/Merge .github configs if present in release package
$installAssetsGithub = Join-Path $ScriptDir "install_assets\.github"
if (Test-Path $installAssetsGithub) {
    $srcCanonical = (Get-Item $installAssetsGithub).FullName
    $dstPath = Join-Path $TargetWorkspace ".github"
    
    # Resolve dst path if it exists to compare absolute values
    $dstCanonical = $null
    if (Test-Path $dstPath) {
        $dstCanonical = (Get-Item $dstPath).FullName
    }

    if ($srcCanonical -ne $dstCanonical) {
        Write-Host "  Merging standardized Copilot instructions, agents, and skills to .github..." -ForegroundColor Cyan
        
        # Merge agents
        $agentsSource = Join-Path $installAssetsGithub "agents"
        if (Test-Path $agentsSource) {
            New-Item -ItemType Directory -Path (Join-Path $dstPath "agents") -Force | Out-Null
            Copy-Item -Path (Join-Path $agentsSource "*") -Destination (Join-Path $dstPath "agents\") -Force
        }
        
        # Merge skills
        $skillsSource = Join-Path $installAssetsGithub "skills"
        if (Test-Path $skillsSource) {
            New-Item -ItemType Directory -Path (Join-Path $dstPath "skills") -Force | Out-Null
            Copy-Item -Path (Join-Path $skillsSource "*") -Destination (Join-Path $dstPath "skills\") -Recurse -Force
        }

        # Merge hooks
        $hooksSource = Join-Path $installAssetsGithub "hooks"
        if (Test-Path $hooksSource) {
            $hooksDir = Join-Path $dstPath "hooks"
            New-Item -ItemType Directory -Path $hooksDir -Force | Out-Null
            $hookFile = Join-Path $hooksDir "on-save.json"
            if (Test-Path $hookFile) {
                $hookContent = Get-Content $hookFile -Raw
                if ($hookContent -like "*Workspace Ingest Syncer*") {
                    Write-Host "  - ✓ Workspace Ingest Syncer hook already present in .github\hooks\on-save.json" -ForegroundColor Green
                } else {
                    Write-Host "  ⚠️  Existing on-save.json found in .github\hooks\. Backing up to on-save.json.bak" -ForegroundColor Yellow
                    Rename-Item -Path $hookFile -NewName "on-save.json.bak" -Force
                    Copy-Item -Path (Join-Path $hooksSource "on-save.json") -Destination $hooksDir -Force
                }
            } else {
                Copy-Item -Path (Join-Path $hooksSource "on-save.json") -Destination $hooksDir -Force
            }
        }

        # Safely merge copilot-instructions.md instead of overwriting
        $instPath = Join-Path $dstPath "copilot-instructions.md"
        $srcInstPath = Join-Path $installAssetsGithub "copilot-instructions.md"
        if (Test-Path $instPath) {
            $existingContent = Get-Content $instPath -Raw
            if ($existingContent -like "*@build_context*") {
                Write-Host "  - ✓ @build_context instructions already present in .github\copilot-instructions.md" -ForegroundColor Green
            } else {
                Write-Host "  Appending @build_context instructions to existing .github\copilot-instructions.md..." -ForegroundColor Cyan
                $newInstructions = Get-Content $srcInstPath -Raw
                $mergedContent = $existingContent + "`r`n`r`n---`r`n`r`n" + $newInstructions
                Set-Content -Path $instPath -Value $mergedContent -Force
                Write-Host "  - ✓ Instructions successfully merged" -ForegroundColor Green
            }
        } else {
            Copy-Item -Path $srcInstPath -Destination $instPath -Force
            Write-Host "  - ✓ copilot-instructions.md deployed" -ForegroundColor Green
        }
        Write-Host "  - ✓ Standardized .github configs successfully merged into: $TargetWorkspace/.github" -ForegroundColor Green
    } else {
        Write-Host "  - ✓ Standardized .github configs already in place (identical path)" -ForegroundColor Green
    }
} else {
    Write-Host "⚠️  No .github configuration directory found in release package." -ForegroundColor Yellow
}

# ------------------------------------------------------------------------------
# Step 5: Finish
# ------------------------------------------------------------------------------
Write-Host "==============================================================" -ForegroundColor Green
Write-Host "🎉 Installation Completed Successfully!" -ForegroundColor Green
Write-Host "==============================================================" -ForegroundColor Green
Write-Host "`nNext steps to verify your setup:" -ForegroundColor White
Write-Host "  1. Launch VS Code in this directory:"
Write-Host "     code ." -ForegroundColor Cyan
Write-Host "  2. Open the Copilot Chat panel (Cmd+Ctrl+I or Ctrl+Alt+I)."
Write-Host "  3. Verify @build_context is listed by typing '@'."
Write-Host "  4. Place insurance specifications in ./ingest/."
Write-Host "  5. Type @build_context /status to verify the backend database."
Write-Host "`nFor advanced tutorials and workflows, read docs/USER_GUIDE.md.`n" -ForegroundColor Cyan
