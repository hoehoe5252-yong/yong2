param(
    [string]$StartUrl = "https://www.i-boss.co.kr/ab-7214",
    [int]$Limit = 50,
    [string]$OutFile = "data/iboss_manual.json",
    [string]$Branch = "main"
)

$ErrorActionPreference = "Stop"

function Require-Command([string]$Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command not found: $Name"
    }
}

function Invoke-External([scriptblock]$Command, [string]$StepName) {
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$StepName failed with exit code $LASTEXITCODE"
    }
}

Require-Command "python"
Require-Command "git"

Write-Host "[1/4] Export i-boss articles to JSON..."
Invoke-External { python scripts/export_iboss_manual.py --start-url $StartUrl --out $OutFile --limit $Limit } "Export"

if (-not (Test-Path $OutFile)) {
    throw "Output file not found: $OutFile"
}

Write-Host "[2/4] Stage output file..."
Invoke-External { git add $OutFile } "Git add"

$diff = git diff --cached --name-only
if (-not $diff) {
    Write-Host "No changes detected. Skip commit/push."
    exit 0
}

$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$message = "Update manual i-boss seed ($timestamp)"

Write-Host "[3/4] Commit..."
Invoke-External { git commit -m $message } "Git commit"

Write-Host "[4/4] Push to origin/$Branch..."
Invoke-External { git push origin $Branch } "Git push"

Write-Host "Done. Railway auto-deploy should start from latest push."
