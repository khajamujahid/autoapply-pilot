# ApplyPilot installer for Windows (PowerShell). Run:  powershell -ExecutionPolicy Bypass -File install.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$py = "python"
if (Get-Command py -ErrorAction SilentlyContinue) { $py = "py" }
& $py -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)"
if ($LASTEXITCODE -ne 0) { Write-Error "Python 3.10+ is required: https://www.python.org/downloads/"; exit 1 }
Write-Host "-> Creating virtual environment (.venv)"
& $py -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip -q
Write-Host "-> Installing Python packages"
& .\.venv\Scripts\pip.exe install -r requirements.txt -q
Write-Host "-> Installing Chromium for Playwright"
& .\.venv\Scripts\playwright.exe install chromium
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
New-Item -ItemType Directory -Force -Path data | Out-Null
Write-Host "Installed. Start with: .\start.ps1  (or double-click start.bat), then open http://localhost:8077"
