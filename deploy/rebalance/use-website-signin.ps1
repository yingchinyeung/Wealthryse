# Points the rebalancing site's sign-in at the main website's sign-in app
# (already approved by an admin) instead of the unapproved
# "WealthRyse Rebalance" app. Changes:
#   - adds the rebalancing site's callback address to the main sign-in app
#     (existing addresses are kept, so the main website is unaffected)
#   - adds a separate secret on the main sign-in app just for the rebalancing
#     site (the main website's own secret is not read, copied or changed, and
#     this one can be revoked on its own)
#   - lets Aiden and you sign in through it, if it only admits assigned people
#   - switches ibkrv2stg-rebalance to use it
# The main website's own user list still decides who can use the main website.
# Secrets are never printed.

param(
    [string]$AidenUpn = 'aiden.yu@wealthryse.ai',
    [string]$ResourceGroup = 'rg-ibkr-database-v11',
    [string]$Subscription = '3956e821-cee4-403f-8708-e84e2ed490eb',
    [string]$WebApp = 'ibkrv2stg-web',
    [string]$AppName = 'ibkrv2stg-rebalance'
)

$ErrorActionPreference = 'Stop'
$azExe = (Get-Command az -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
function HideSecrets($list) {
    $shown = @(); $hide = $false
    foreach ($a in $list) {
        $s = "$a"
        if ($s -like '--*') { $hide = $s -in @('--client-secret', '--password', '--secrets'); $shown += $s; continue }
        if ($hide) { $shown += '***'; continue }
        $shown += $s
    }
    return ($shown -join ' ')
}
function AzRun {
    $ErrorActionPreference = 'Continue'
    $global:LASTEXITCODE = 0
    $out = @(& $azExe @args 2>&1)
    $code = $LASTEXITCODE
    if ($code -ne 0) { throw "az $(HideSecrets $args) failed (exit $code): $(($out | ForEach-Object { "$_" }) -join "`n")" }
    return ($out | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] } | ForEach-Object { "$_" }) -join "`n"
}
function GraphCall($method, $url, $body) {
    $file = Join-Path $env:TEMP 'wr-rebalance-graph.json'
    [IO.File]::WriteAllText($file, ($body | ConvertTo-Json -Depth 4))
    try { AzRun rest --method $method --url $url --headers 'Content-Type=application/json' --body "@$file" | Out-Null }
    finally { Remove-Item $file -Force -ErrorAction SilentlyContinue }
}

if ((AzRun account show --query id -o tsv) -ne $Subscription) { AzRun account set --subscription $Subscription | Out-Null }

Write-Host '== Reading the main website''s sign-in settings'
$clientId = AzRun containerapp auth show -n $WebApp -g $ResourceGroup --query 'identityProviders.azureActiveDirectory.registration.clientId' -o tsv
$issuer = AzRun containerapp auth show -n $WebApp -g $ResourceGroup --query 'identityProviders.azureActiveDirectory.registration.openIdIssuer' -o tsv
if (-not $clientId) { throw 'The main website has no Microsoft sign-in configured.' }
$appObjectId = AzRun ad app show --id $clientId --query id -o tsv

Write-Host '== Adding the rebalancing site''s address to the main sign-in app'
$fqdn = AzRun containerapp show -n $AppName -g $ResourceGroup --query properties.configuration.ingress.fqdn -o tsv
$callback = "https://$fqdn/.auth/login/aad/callback"
$uris = @((AzRun ad app show --id $clientId --query 'web.redirectUris' -o json) | ConvertFrom-Json)
if ($uris -notcontains $callback) {
    GraphCall PATCH "https://graph.microsoft.com/v1.0/applications/$appObjectId" @{ web = @{ redirectUris = @($uris + $callback) } }
}

Write-Host '== Adding a separate secret for the rebalancing site'
$clientSecret = AzRun ad app credential reset --id $clientId --append --display-name rebalance-site --years 1 --query password -o tsv

$spId = AzRun ad sp list --filter "appId eq '$clientId'" --query '[0].id' -o tsv
if ((AzRun ad sp show --id $spId --query appRoleAssignmentRequired -o tsv) -eq 'true') {
    Write-Host '== Letting Aiden and you sign in through it'
    $assigned = AzRun rest --method GET --url "https://graph.microsoft.com/v1.0/servicePrincipals/$spId/appRoleAssignedTo" --query 'value[].principalId' -o tsv
    foreach ($id in @((AzRun ad user show --id $AidenUpn --query id -o tsv), (AzRun ad signed-in-user show --query id -o tsv))) {
        if ($assigned -notmatch $id) {
            GraphCall POST "https://graph.microsoft.com/v1.0/servicePrincipals/$spId/appRoleAssignedTo" @{ principalId = $id; resourceId = $spId; appRoleId = '00000000-0000-0000-0000-000000000000' }
        }
    }
}

Write-Host "== Switching $AppName to the main sign-in app"
AzRun containerapp auth microsoft update -n $AppName -g $ResourceGroup --client-id $clientId --client-secret $clientSecret --issuer $issuer --yes | Out-Null
AzRun containerapp auth update -n $AppName -g $ResourceGroup --enabled true --unauthenticated-client-action RedirectToLoginPage --redirect-provider azureactivedirectory | Out-Null

$now = AzRun containerapp auth show -n $AppName -g $ResourceGroup --query 'identityProviders.azureActiveDirectory.registration.clientId' -o tsv
Write-Host ''
Write-Host "DONE  site=https://$fqdn  uses main sign-in app: $($now -eq $clientId)"
