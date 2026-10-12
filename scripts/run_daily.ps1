param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = "Stop"
$runnerLog = Join-Path $ProjectRoot "logs\runner.log"
$runnerLogDirectory = Split-Path -Parent $runnerLog
New-Item -ItemType Directory -Force -Path $runnerLogDirectory | Out-Null

function Write-RunnerLog {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Status,
        [string]$Message = ""
    )
    $timestamp = Get-Date -Format "yyyy-MM-ddTHH:mm:ssK"
    $line = "$timestamp $Status"
    if ($Message) {
        $line += " | $Message"
    }
    Add-Content -LiteralPath $runnerLog -Value $line -Encoding UTF8
}

trap {
    Write-RunnerLog -Status "FAIL" -Message $_.Exception.Message
    exit 1
}

Write-RunnerLog -Status "START"
Set-Location -LiteralPath $ProjectRoot

$venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $venvPython) {
    $pythonExe = $venvPython
} else {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        throw "Python was not found. Run scripts\setup.ps1 after installing Python 3.11 or newer."
    }
    $pythonExe = $pythonCommand.Source
}

$env:PYTHONPATH = Join-Path $ProjectRoot "src"
& $pythonExe -m ibkr_etl.cli daily
$importExitCode = $LASTEXITCODE
if ($importExitCode -ne 0) {
    Write-RunnerLog -Status "FAIL" -Message "import exit code $importExitCode"
    exit $importExitCode
}
Write-RunnerLog -Status "PASS" -Message "IBKR import"

& $pythonExe (Join-Path $ProjectRoot "scripts\health_check.py")
$healthExitCode = $LASTEXITCODE
if ($healthExitCode -ne 0) {
    Write-RunnerLog -Status "FAIL" -Message "health check exit code $healthExitCode"
    exit $healthExitCode
}
Write-RunnerLog -Status "PASS" -Message "health check"

& $pythonExe (Join-Path $ProjectRoot "scripts\refresh_reference_data.py")
$referenceExitCode = $LASTEXITCODE
if ($referenceExitCode -ne 0) {
    Write-RunnerLog -Status "FAIL" -Message "reference data exit code $referenceExitCode"
    exit $referenceExitCode
}
Write-RunnerLog -Status "PASS" -Message "analytics reference data"

& $pythonExe (Join-Path $ProjectRoot "scripts\generate_daily_report.py")
$reportExitCode = $LASTEXITCODE
if ($reportExitCode -ne 0) {
    Write-RunnerLog -Status "FAIL" -Message "dashboard exit code $reportExitCode"
    exit $reportExitCode
}
Write-RunnerLog -Status "PASS" -Message "analytics dashboard"

& $pythonExe (Join-Path $ProjectRoot "scripts\backup_database.py")
$backupExitCode = $LASTEXITCODE
if ($backupExitCode -ne 0) {
    Write-RunnerLog -Status "FAIL" -Message "backup exit code $backupExitCode"
    exit $backupExitCode
}
Write-RunnerLog -Status "PASS" -Message "verified database backup"
Write-RunnerLog -Status "COMPLETE"
exit 0
