# Lets only Aiden and the person running this sign in to the rebalancing site.
# Sets allowedPrincipals on ibkrv2stg-rebalance's own sign-in settings; the
# main website and its sign-in app are not changed. Safe to re-run.

param(
    [string]$AidenUpn = 'aiden.yu@wealthryse.ai',
    [string]$ResourceGroup = 'rg-ibkr-database-v11',
    [string]$Subscription = '3956e821-cee4-403f-8708-e84e2ed490eb',
    [string]$AppName = 'ibkrv2stg-rebalance'
)

$ErrorActionPreference = 'Stop'
$azExe = (Get-Command az -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
function AzRun {
    $ErrorActionPreference = 'Continue'
    $global:LASTEXITCODE = 0
    $out = @(& $azExe @args 2>&1)
    $code = $LASTEXITCODE
    if ($code -ne 0) { throw "az $($args -join ' ') failed (exit $code): $(($out | ForEach-Object { "$_" }) -join "`n")" }
    return ($out | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] } | ForEach-Object { "$_" }) -join "`n"
}

if ((AzRun account show --query id -o tsv) -ne $Subscription) { AzRun account set --subscription $Subscription | Out-Null }
[string[]]$ids = @(
    (AzRun ad user show --id $AidenUpn --query id -o tsv),
    (AzRun ad signed-in-user show --query id -o tsv)
)
$url = "https://management.azure.com/subscriptions/$Subscription/resourceGroups/$ResourceGroup/providers/Microsoft.App/containerApps/$AppName/authConfigs/current?api-version=2024-03-01"

Write-Host '== Limiting sign-in to Aiden and you'
$config = (AzRun rest --method GET --url $url) | ConvertFrom-Json
$aad = $config.properties.identityProviders.azureActiveDirectory
if (-not $aad.validation) { $aad | Add-Member -NotePropertyName validation -NotePropertyValue ([pscustomobject]@{}) }
$aad.validation | Add-Member -Force -NotePropertyName defaultAuthorizationPolicy -NotePropertyValue ([pscustomobject]@{
    allowedPrincipals = [pscustomobject]@{ identities = $ids }
})
$body = @{ properties = $config.properties } | ConvertTo-Json -Depth 20
$file = Join-Path $env:TEMP 'wr-rebalance-auth.json'
[IO.File]::WriteAllText($file, $body)
try { AzRun rest --method PUT --url $url --headers 'Content-Type=application/json' --body "@$file" | Out-Null }
finally { Remove-Item $file -Force -ErrorAction SilentlyContinue }

$now = AzRun rest --method GET --url $url --query 'properties.identityProviders.azureActiveDirectory.validation.defaultAuthorizationPolicy.allowedPrincipals.identities' -o tsv
$enabled = AzRun rest --method GET --url $url --query 'properties.platform.enabled' -o tsv
Write-Host ''
Write-Host "DONE  sign-in required: $enabled  allowed people: $((($now -split "`r?`n") | Where-Object { $_ }).Count)  (Aiden and you: $([bool](($now -match $ids[0]) -and ($now -match $ids[1]))))"
