param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $ProjectRoot

$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCommand) {
    throw "Python 3.11 or newer is required. Install it from python.org or with: winget install Python.Python.3.12"
}

& $pythonCommand.Source --version
& $pythonCommand.Source -m venv .venv
$venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath ".env")) {
    Copy-Item -LiteralPath ".env.example" -Destination ".env"
}

& $venvPython -m ibkr_etl.cli init-db
Write-Host "Setup complete. Add the Flex token and query ID to .env."
