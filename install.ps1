#Requires -Version 5.1
<#
  hermes-node installer (Windows)
  irm https://raw.githubusercontent.com/uuddhay/hermes-node/main/install.ps1 | iex

  Env vars:
    HERMES_NODE_DRY_RUN=1        print every action, change nothing
    HERMES_NODE_SKILLS_REPO      default https://github.com/uuddhay/hermes-skills.git
    HERMES_NODE_CONFIG_REPO      default https://github.com/uuddhay/hermes-config-bundle.git
    HERMES_NODE_FORCE=1          overwrite existing config bundle files instead of skipping them
#>

$ErrorActionPreference = "Stop"
$DryRun = $env:HERMES_NODE_DRY_RUN -eq "1"
$Force  = $env:HERMES_NODE_FORCE -eq "1"
$SkillsRepo = if ($env:HERMES_NODE_SKILLS_REPO) { $env:HERMES_NODE_SKILLS_REPO } else { "https://github.com/uuddhay/hermes-skills.git" }
$ConfigRepo = if ($env:HERMES_NODE_CONFIG_REPO) { $env:HERMES_NODE_CONFIG_REPO } else { "https://github.com/uuddhay/hermes-config-bundle.git" }
$HermesHome = Join-Path $env:LOCALAPPDATA "hermes"
$Scratch    = Join-Path $env:TEMP "hermes-node-bootstrap"
$NodeRepo   = "https://github.com/uuddhay/hermes-node.git"

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Info($msg) { Write-Host "    $msg" -ForegroundColor DarkGray }
function Act($msg, [scriptblock]$block) {
    if ($DryRun) { Write-Host "    [DRY-RUN] would: $msg" -ForegroundColor Yellow; return }
    Write-Host "    $msg" -ForegroundColor DarkGray
    & $block
}

Write-Host "hermes-node installer  (dry-run=$DryRun)" -ForegroundColor Green

# 1. Python 3.11+
Step "Checking Python 3.11+"
$pyOk = $false
try {
    $v = (& python --version) 2>&1
    if ($v -match "Python (\d+)\.(\d+)") {
        if ([int]$Matches[1] -gt 3 -or ([int]$Matches[1] -eq 3 -and [int]$Matches[2] -ge 11)) { $pyOk = $true }
    }
    Info "found: $v"
} catch { Info "python not found on PATH" }
if (-not $pyOk) {
    Act "winget install -e --id Python.Python.3.12" { winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements }
} else { Info "OK, skipping install" }

# 2. uv
Step "Checking uv"
$uvOk = $false
try { $uvv = (& uv --version) 2>&1; Info "found: $uvv"; $uvOk = $true } catch { Info "uv not found" }
if (-not $uvOk) {
    Act "install uv via astral.sh installer" { irm https://astral.sh/uv/install.ps1 | iex }
} else { Info "OK, skipping install" }

# 3. Tailscale (install only - login is the user's manual step)
Step "Checking Tailscale"
$tsOk = $false
try { & tailscale version | Out-Null; $tsOk = $true; Info "already installed" } catch { Info "not found" }
if (-not $tsOk) {
    Act "winget install -e --id Tailscale.Tailscale" { winget install -e --id Tailscale.Tailscale --accept-source-agreements --accept-package-agreements }
} else { Info "OK, skipping install" }

# 4. GitHub device-code auth (needed to clone private repos)
Step "Checking GitHub auth (gh CLI)"
$ghOk = $false
try { & gh auth status 2>&1 | Out-Null; if ($LASTEXITCODE -eq 0) { $ghOk = $true; Info "already logged in" } } catch {}
if (-not $ghOk) {
    Act "gh auth login --git-protocol https --web=false (device code flow)" { gh auth login --git-protocol https --hostname github.com }
} else { Info "OK, skipping login" }

# 5. Clone private skills + config bundle repos
Step "Cloning private repos (skills + config bundle)"
Act "mkdir $Scratch" { New-Item -ItemType Directory -Force -Path $Scratch | Out-Null }
$skillsDir = Join-Path $Scratch "hermes-skills"
$configDir = Join-Path $Scratch "hermes-config-bundle"
if (-not (Test-Path $skillsDir)) {
    Act "git clone $SkillsRepo -> $skillsDir" { git clone --depth 1 $SkillsRepo $skillsDir }
} else { Info "$skillsDir already present, pulling" ; Act "git -C $skillsDir pull --ff-only" { git -C $skillsDir pull --ff-only } }
if (-not (Test-Path $configDir)) {
    Act "git clone $ConfigRepo -> $configDir" { git clone --depth 1 $ConfigRepo $configDir }
} else { Info "$configDir already present, pulling" ; Act "git -C $configDir pull --ff-only" { git -C $configDir pull --ff-only } }

# 6. Install Hermes (editable git checkout via uv)
Step "Installing Hermes (editable git checkout)"
$hermesAgentDir = Join-Path $HermesHome "hermes-agent"
if (-not (Test-Path $hermesAgentDir)) {
    Act "git clone https://github.com/NousResearch/hermes-agent.git -> $hermesAgentDir" { git clone https://github.com/NousResearch/hermes-agent.git $hermesAgentDir }
    Act "uv venv + uv pip install -e (editable) in $hermesAgentDir" {
        Push-Location $hermesAgentDir
        uv venv
        uv pip install -e .
        Pop-Location
    }
} else { Info "$hermesAgentDir already exists, skipping clone+install (re-run scripts/apply_core_patches.py manually if you need to re-apply)" }

# 7. Apply carried core patches deliberately (never blind-overwrite)
Step "Applying core patches from config bundle"
$patchScript = Join-Path $configDir "scripts\apply_core_patches.py"
if (Test-Path $patchScript) {
    Act "python $patchScript --hermes-agent-dir $hermesAgentDir" { python $patchScript --hermes-agent-dir $hermesAgentDir --backup }
} else { Info "no apply_core_patches.py in config bundle, skipping" }

# 8. Restore config bundle (profiles, cron, SOULs) without clobbering
Step "Restoring config bundle into $HermesHome"
$restoreScript = Join-Path $configDir "scripts\restore_config_bundle.py"
if (Test-Path $restoreScript) {
    $forceFlag = if ($Force) { "--force" } else { "" }
    Act "python $restoreScript --src $configDir --dest $HermesHome $forceFlag" { python $restoreScript --src $configDir --dest $HermesHome @(if ($Force) { "--force" }) }
} else { Info "no restore_config_bundle.py in config bundle, skipping" }

# 9. Skill sync fan-out
Step "Running skill_sync.py --apply"
$syncScript = Join-Path $HermesHome "scripts\skill_sync.py"
if (Test-Path $syncScript) {
    Act "python $syncScript --apply" { python $syncScript --apply }
} else { Info "skill_sync.py not present yet at $syncScript, skipping" }

# 10. Gateway install + start
Step "Installing and starting the Hermes gateway"
Act "hermes gateway install" { hermes gateway install }
Act "hermes gateway start" { hermes gateway start }

# 11. Enroll: tailnet discovery + hub registration
Step "Enrolling with your Hermes mesh"
Info "You'll be asked to log in to Tailscale (browser) and to paste a one-time"
Info "enrollment token generated on your hub by hub-scripts/generate_enrollment_token.py"
Act "tailscale up" { tailscale up }
$enrollScript = Join-Path $Scratch "hermes-node\scripts\enroll_node.py"
if (-not (Test-Path $enrollScript)) {
    Act "git clone $NodeRepo -> $Scratch\hermes-node" { git clone --depth 1 $NodeRepo (Join-Path $Scratch "hermes-node") }
}
if ($DryRun) {
    Write-Host "    [DRY-RUN] would: python $enrollScript" -ForegroundColor Yellow
} else {
    python $enrollScript
}

Write-Host "`nDone. Re-run this same command any time - every step is idempotent." -ForegroundColor Green
