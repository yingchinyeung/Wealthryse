# Gives Aiden and the person running this full control of the rebalancing
# website: Owner on the ibkrv2stg-rebalance container app, owner of its
# "WealthRyse Rebalance" sign-in app, and sign-in to the site. The database
# login stays read-only. Safe to re-run. Prints what each person has at the end.

param(
    [string]$AidenUpn = 'aiden.yu@wealthryse.ai',
    [string]$ResourceGroup = 'rg-ibkr-database-v11',
    [string]$Subscription = '3956e821-cee4-403f-8708-e84e2ed490eb',
    [string]$AppName = 'ibkrv2stg-rebalance',
    [string]$EntraAppName = 'WealthRyse Rebalance'
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
function GraphPost($url, $body) {
    $file = Join-Path $env:TEMP 'wr-rebalance-graph.json'
    [IO.File]::WriteAllText($file, ($body | ConvertTo-Json))
    try { AzRun rest --method POST --url $url --headers 'Content-Type=application/json' --body "@$file" | Out-Null }
    finally { Remove-Item $file -Force -ErrorAction SilentlyContinue }
}

if ((AzRun account show --query id -o tsv) -ne $Subscription) { AzRun account set --subscription $Subscription | Out-Null }
$people = [ordered]@{
    'Aiden' = AzRun ad user show --id $AidenUpn --query id -o tsv
    'You'   = AzRun ad signed-in-user show --query id -o tsv
}
$appId = AzRun containerapp show -n $AppName -g $ResourceGroup --query id -o tsv
$entraAppId = AzRun ad app list --display-name $EntraAppName --query '[0].appId' -o tsv
$entraObjectId = AzRun ad app show --id $entraAppId --query id -o tsv
$spId = AzRun ad sp list --filter "appId eq '$entraAppId'" --query '[0].id' -o tsv

foreach ($name in $people.Keys) {
    $id = $people[$name]
    Write-Host "== $name"
    # Azure: Owner on the website's container app only.
    $has = AzRun role assignment list --assignee $id --scope $appId --role Owner --query '[0].id' -o tsv
    if (-not $has) {
        AzRun role assignment create --assignee-object-id $id --assignee-principal-type User --role Owner --scope $appId | Out-Null
    }
    # Sign-in settings: owner of the app registration and its enterprise app.
    if ((AzRun ad app owner list --id $entraAppId --query '[].id' -o tsv) -notmatch $id) {
        AzRun ad app owner add --id $entraAppId --owner-object-id $id | Out-Null
    }
    $spOwners = AzRun rest --method GET --url "https://graph.microsoft.com/v1.0/servicePrincipals/$spId/owners" --query 'value[].id' -o tsv
    if ($spOwners -notmatch $id) {
        GraphPost "https://graph.microsoft.com/v1.0/servicePrincipals/$spId/owners/`$ref" @{ '@odata.id' = "https://graph.microsoft.com/v1.0/directoryObjects/$id" }
    }
    # Site sign-in.
    $assigned = AzRun rest --method GET --url "https://graph.microsoft.com/v1.0/servicePrincipals/$spId/appRoleAssignedTo" --query 'value[].principalId' -o tsv
    if ($assigned -notmatch $id) {
        GraphPost "https://graph.microsoft.com/v1.0/servicePrincipals/$spId/appRoleAssignedTo" @{ principalId = $id; resourceId = $spId; appRoleId = '00000000-0000-0000-0000-000000000000' }
    }
}

Write-Host ''
Write-Host '== Check'
$assigned = AzRun rest --method GET --url "https://graph.microsoft.com/v1.0/servicePrincipals/$spId/appRoleAssignedTo" --query 'value[].principalId' -o tsv
$appOwners = AzRun ad app owner list --id $entraAppId --query '[].id' -o tsv
foreach ($name in $people.Keys) {
    $id = $people[$name]
    $roles = (AzRun role assignment list --assignee $id --scope $appId --include-inherited --query '[].roleDefinitionName' -o tsv) -split "`n" | Sort-Object -Unique
    Write-Host ("{0}: app roles = {1}; sign-in = {2}; sign-in app owner = {3}" -f $name, ($roles -join ', '), [bool]($assigned -match $id), [bool]($appOwners -match $id))
}
$site = AzRun containerapp show -n $AppName -g $ResourceGroup --query properties.configuration.ingress.fqdn -o tsv
$auth = AzRun containerapp auth show -n $AppName -g $ResourceGroup --query 'platform.enabled' -o tsv
Write-Host "Site https://$site  sign-in required: $auth"
