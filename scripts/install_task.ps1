param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$At = "16:00"
)

$ErrorActionPreference = "Stop"
$runner = Join-Path $ProjectRoot "scripts\run_daily.ps1"
if (-not (Test-Path -LiteralPath $runner)) {
    throw "Daily runner not found: $runner"
}

$time = [datetime]::ParseExact($At, "HH:mm", $null)
$powerShellExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
if (-not (Test-Path -LiteralPath $powerShellExe)) {
    throw "Windows PowerShell was not found: $powerShellExe"
}
$action = New-ScheduledTaskAction `
    -Execute $powerShellExe `
    -Argument "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$runner`"" `
    -WorkingDirectory $ProjectRoot
$trigger = New-ScheduledTaskTrigger -Daily -At $time
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -WakeToRun `
    -RunOnlyIfNetworkAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 30) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName "IBKR Portfolio Daily Import" `
    -Description "Refreshes IBKR Flex data, validates it, generates the dashboard, and backs up the database." `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Force

Write-Host "Scheduled IBKR Portfolio Daily Import at $At daily."
