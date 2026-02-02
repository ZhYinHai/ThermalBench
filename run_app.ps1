# Runs ThermalBench using the workspace venv Python to avoid missing-dependency issues
# Usage:
#   .\run_app.ps1

$ErrorActionPreference = 'Stop'

$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'

if (-not (Test-Path $venvPython)) {
  Write-Host "Venv not found at: $venvPython" -ForegroundColor Yellow
  Write-Host "Create it and install deps:" -ForegroundColor Yellow
  Write-Host "  python -m venv .venv" -ForegroundColor Yellow
  Write-Host "  .\.venv\Scripts\Activate.ps1" -ForegroundColor Yellow
  Write-Host "  pip install -r requirements.txt" -ForegroundColor Yellow
  exit 1
}

& $venvPython (Join-Path $PSScriptRoot 'app.py')
exit $LASTEXITCODE
