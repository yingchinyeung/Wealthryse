# Sets up the rebalancing website for Aiden in rg-ibkr-database-v11.
#
# Creates, without touching the current website, jobs or data:
#   - Container App ibkrv2stg-rebalance in ibkrv2stg-env (placeholder page)
#   - Entra sign-in for it, limited to Aiden and the person running this
#   - Postgres login ibkr_rebalance_reader: SELECT only, no write rights
#   - Registry token that can push/pull only the "rebalance" image
#   - Aiden's Azure rights: his app only, plus "join" on the environment
#
# Secrets are never printed. Aiden's registry login is written to
# $HOME\Desktop\rebalance-for-aiden.txt for Ying to pass on.
# Safe to re-run: every step checks before it creates.

param(
    [string]$AidenUpn = 'aiden.yu@wealthryse.ai',
    [string]$ResourceGroup = 'rg-ibkr-database-v11',
    [string]$Subscription = '3956e821-cee4-403f-8708-e84e2ed490eb',
    [string]$Environment = 'ibkrv2stg-env',
    [string]$Registry = 'ibkrv2stgauhecwc4itaoo',
    [string]$PostgresServer = 'ibkrv2stg-pg-auhecwc4itaoo',
    [string]$Database = 'ibkr_staging',
    [string]$AppName = 'ibkrv2stg-rebalance',
    [string]$BootstrapJob = 'ibkrv2stg-bootstrap',
    [string]$SetupJob = 'ibkrv2stg-rebalance-db-setup'
)

$ErrorActionPreference = 'Stop'
$work = Join-Path $env:TEMP 'wr-rebalance-setup'
New-Item -ItemType Directory -Force -Path $work | Out-Null

# Windows PowerShell treats az warnings on stderr as errors under 'Stop'.
function AzRun {
    $ErrorActionPreference = 'Continue'
    $out = & az @args 2>&1
    if ($LASTEXITCODE -ne 0) { throw "az $($args -join ' ') failed: $out" }
    return ($out | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] }) -join "`n"
}
function AzOk { $ErrorActionPreference = 'Continue'; & az @args 2>$null | Out-Null; return ($LASTEXITCODE -eq 0) }
function Step($text) { Write-Host "== $text" }
function NewPassword($length) {
    $chars = [char[]]'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789'
    $bytes = New-Object byte[] $length
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    return -join ($bytes | ForEach-Object { $chars[$_ % $chars.Length] })
}

AzRun account set --subscription $Subscription | Out-Null
$tenantId = AzRun account show --query tenantId -o tsv
$rgId = AzRun group show -n $ResourceGroup --query id -o tsv

Step 'Checking Aiden and you in Entra'
$aidenId = AzRun ad user show --id $AidenUpn --query id -o tsv
$meId = AzRun ad signed-in-user show --query id -o tsv
$envId = AzRun containerapp env show -n $Environment -g $ResourceGroup --query id -o tsv
$registryId = AzRun acr show -n $Registry -g $ResourceGroup --query id -o tsv
$loginServer = AzRun acr show -n $Registry -g $ResourceGroup --query loginServer -o tsv
$pgHost = AzRun postgres flexible-server show -n $PostgresServer -g $ResourceGroup --query fullyQualifiedDomainName -o tsv

# --- 1. Read-only database login ------------------------------------------
Step 'Creating the read-only database login (one-off job inside the environment)'
$readerPassword = NewPassword 40
$boot = AzRun containerapp job show -n $BootstrapJob -g $ResourceGroup -o json | ConvertFrom-Json
$bootImage = $boot.properties.template.containers[0].image
$bootIdentity = @($boot.identity.userAssignedIdentities.PSObject.Properties.Name)[0]
$adminSecret = $boot.properties.configuration.secrets | Where-Object { $_.name -eq 'database-admin-url' }
if (-not $adminSecret.keyVaultUrl) { throw 'Bootstrap job has no database-admin-url Key Vault reference.' }
$bootRegistry = $boot.properties.configuration.registries[0]

$code = [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'create_reader_role.py'))
$codeB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($code))
$jobYaml = @"
location: $((AzRun containerapp env show -n $Environment -g $ResourceGroup --query location -o tsv))
identity:
  type: UserAssigned
  userAssignedIdentities:
    '$bootIdentity': {}
properties:
  environmentId: $envId
  configuration:
    triggerType: Manual
    replicaTimeout: 600
    replicaRetryLimit: 0
    manualTriggerConfig: { parallelism: 1, replicaCompletionCount: 1 }
    registries:
      - server: $($bootRegistry.server)
        identity: '$($bootRegistry.identity)'
    secrets:
      - name: database-admin-url
        keyVaultUrl: $($adminSecret.keyVaultUrl)
        identity: '$($adminSecret.identity)'
      - name: reader-password
        value: '$readerPassword'
  template:
    containers:
      - name: setup
        image: $bootImage
        command: ['python']
        args: ['-c', 'import base64;exec(base64.b64decode("$codeB64"))']
        resources: { cpu: 0.5, memory: 1Gi }
        env:
          - { name: DATABASE_ADMIN_URL, secretRef: database-admin-url }
          - { name: READER_PASSWORD, secretRef: reader-password }
          - { name: EXPECTED_DATABASE, value: $Database }
"@
$jobFile = Join-Path $work 'job.yaml'
[IO.File]::WriteAllText($jobFile, $jobYaml)
try {
    if (AzOk containerapp job show -n $SetupJob -g $ResourceGroup) {
        AzRun containerapp job delete -n $SetupJob -g $ResourceGroup --yes | Out-Null
    }
    AzRun containerapp job create -n $SetupJob -g $ResourceGroup --yaml $jobFile | Out-Null
    $execution = AzRun containerapp job start -n $SetupJob -g $ResourceGroup --query name -o tsv
    $status = 'Running'
    for ($i = 0; $i -lt 60 -and $status -in @('Running', 'Processing', 'Unknown', ''); $i++) {
        Start-Sleep -Seconds 10
        $status = AzRun containerapp job execution show -n $SetupJob -g $ResourceGroup --job-execution-name $execution --query properties.status -o tsv
    }
    Write-Host "Database setup job: $status"
    try {
        AzRun containerapp job logs show -n $SetupJob -g $ResourceGroup --execution $execution --container setup --format text 2>$null |
            Select-String 'rebalance_reader' | ForEach-Object { Write-Host $_.Line }
    } catch { Write-Host '(Job log not available yet; status above is authoritative.)' }
    if ($status -ne 'Succeeded') { throw "Database setup job ended with status $status. Nothing else was created." }
}
finally {
    Remove-Item $jobFile -Force -ErrorAction SilentlyContinue
    if (AzOk containerapp job show -n $SetupJob -g $ResourceGroup) {
        AzRun containerapp job delete -n $SetupJob -g $ResourceGroup --yes | Out-Null
    }
}
$readerUrl = "postgresql://ibkr_rebalance_reader:$readerPassword@${pgHost}:5432/${Database}?sslmode=require"

# --- 2. Registry tokens limited to the "rebalance" image -------------------
Step 'Creating registry access limited to the rebalance image'
if (-not (AzOk acr scope-map show -n rebalance-push -r $Registry)) {
    AzRun acr scope-map create -n rebalance-push -r $Registry --repository rebalance content/write content/read metadata/read --description 'Rebalancing site: push and pull the rebalance image only' | Out-Null
}
if (-not (AzOk acr scope-map show -n rebalance-pull -r $Registry)) {
    AzRun acr scope-map create -n rebalance-pull -r $Registry --repository rebalance content/read metadata/read --description 'Rebalancing site: pull the rebalance image only' | Out-Null
}
foreach ($t in @(@('aiden-rebalance-push', 'rebalance-push'), @('rebalance-app-pull', 'rebalance-pull'))) {
    if (-not (AzOk acr token show -n $t[0] -r $Registry)) {
        AzRun acr token create -n $t[0] -r $Registry --scope-map $t[1] --no-passwords | Out-Null
    }
}
$aidenRegistryPassword = AzRun acr token credential generate -n aiden-rebalance-push -r $Registry --password1 --expiration-in-days 365 --query 'passwords[0].value' -o tsv
$appRegistryPassword = AzRun acr token credential generate -n rebalance-app-pull -r $Registry --password1 --expiration-in-days 365 --query 'passwords[0].value' -o tsv

# --- 3. The app (placeholder page until Aiden deploys) ---------------------
Step "Creating $AppName"
if (-not (AzOk containerapp show -n $AppName -g $ResourceGroup)) {
    AzRun containerapp create -n $AppName -g $ResourceGroup --environment $Environment `
        --image mcr.microsoft.com/k8se/quickstart:latest --target-port 80 --ingress external `
        --min-replicas 0 --max-replicas 2 --cpu 0.5 --memory 1Gi `
        --tags application=wealthryse-rebalance owner=aiden.yu environment=staging dataClassification=confidential-financial | Out-Null
}
AzRun containerapp secret set -n $AppName -g $ResourceGroup --secrets "database-url=$readerUrl" "registry-password=$appRegistryPassword" | Out-Null
AzRun containerapp registry set -n $AppName -g $ResourceGroup --server $loginServer --username rebalance-app-pull --password secretref:registry-password | Out-Null
AzRun containerapp update -n $AppName -g $ResourceGroup --set-env-vars DATABASE_URL=secretref:database-url PORTFOLIO_TENANT_ID=00000000-0000-0000-0000-000000000001 | Out-Null
$fqdn = AzRun containerapp show -n $AppName -g $ResourceGroup --query properties.configuration.ingress.fqdn -o tsv
$appId = AzRun containerapp show -n $AppName -g $ResourceGroup --query id -o tsv

# --- 4. Entra sign-in: only assigned people get in -------------------------
Step 'Setting up sign-in (Aiden and you only)'
$entraAppId = AzRun ad app list --display-name 'WealthRyse Rebalance' --query '[0].appId' -o tsv
if (-not $entraAppId) {
    $entraAppId = AzRun ad app create --display-name 'WealthRyse Rebalance' --sign-in-audience AzureADMyOrg `
        --web-redirect-uris "https://$fqdn/.auth/login/aad/callback" --enable-id-token-issuance true --query appId -o tsv
}
$spId = AzRun ad sp list --filter "appId eq '$entraAppId'" --query '[0].id' -o tsv
if (-not $spId) { $spId = AzRun ad sp create --id $entraAppId --query id -o tsv }
AzRun ad sp update --id $spId --set appRoleAssignmentRequired=true | Out-Null
$assigned = AzRun rest --method GET --url "https://graph.microsoft.com/v1.0/servicePrincipals/$spId/appRoleAssignedTo" --query 'value[].principalId' -o tsv
foreach ($user in @($aidenId, $meId)) {
    if ($assigned -notmatch $user) {
        $bodyFile = Join-Path $work 'assign.json'
        [IO.File]::WriteAllText($bodyFile, (@{ principalId = $user; resourceId = $spId; appRoleId = '00000000-0000-0000-0000-000000000000' } | ConvertTo-Json))
        AzRun rest --method POST --url "https://graph.microsoft.com/v1.0/servicePrincipals/$spId/appRoleAssignedTo" --headers 'Content-Type=application/json' --body "@$bodyFile" | Out-Null
        Remove-Item $bodyFile -Force
    }
}
$clientSecret = AzRun ad app credential reset --id $entraAppId --display-name rebalance-container-app --years 1 --append --query password -o tsv
AzRun containerapp auth microsoft update -n $AppName -g $ResourceGroup --client-id $entraAppId --client-secret $clientSecret `
    --tenant-id $tenantId --issuer "https://login.microsoftonline.com/$tenantId/v2.0" --yes | Out-Null
AzRun containerapp auth update -n $AppName -g $ResourceGroup --enabled true --unauthenticated-client-action RedirectToLoginPage --redirect-provider azureactivedirectory | Out-Null

# --- 5. Aiden's Azure rights: his app only ---------------------------------
Step "Giving Aiden access to $AppName only"
$joinRole = 'WealthRyse rebalance environment join'
if (-not (AzRun role definition list --name $joinRole --scope $envId --query '[0].name' -o tsv)) {
    $roleFile = Join-Path $work 'role.json'
    [IO.File]::WriteAllText($roleFile, (@{
        Name = $joinRole
        Description = 'Lets the rebalancing site developer keep his app in ibkrv2stg-env. Cannot change the environment or other apps.'
        Actions = @('Microsoft.App/managedEnvironments/read', 'Microsoft.App/managedEnvironments/join/action')
        AssignableScopes = @($envId)
    } | ConvertTo-Json))
    AzRun role definition create --role-definition "@$roleFile" | Out-Null
    Remove-Item $roleFile -Force
    Start-Sleep -Seconds 30
}
foreach ($a in @(@('Container Apps Contributor', $appId), @($joinRole, $envId))) {
    $granted = $false
    for ($i = 0; $i -lt 6 -and -not $granted; $i++) {
        $granted = AzOk role assignment create --assignee-object-id $aidenId --assignee-principal-type User --role $a[0] --scope $a[1]
        if (-not $granted) { Start-Sleep -Seconds 20 }
    }
    if (-not $granted) { throw "Could not give Aiden '$($a[0])' on $($a[1])." }
}

# --- 6. Hand-over note for Aiden (not printed) -----------------------------
$note = Join-Path ([Environment]::GetFolderPath('Desktop')) 'rebalance-for-aiden.txt'
@"
Rebalancing website: what's set up for you (Aiden)

Site:            https://$fqdn   (sign in with $AidenUpn)
Container app:   $AppName  (resource group $ResourceGroup)
Your rights:     update, restart and view logs of this one app only.

Image registry:  $loginServer   (push only to the "rebalance" image)
  docker login $loginServer -u aiden-rebalance-push -p '$aidenRegistryPassword'
  docker push $loginServer/rebalance:<tag>
  az containerapp update -n $AppName -g $ResourceGroup --image $loginServer/rebalance:<tag>
  (set --target-port on ingress if your app does not listen on port 80)

Database:        read-only. Your app gets DATABASE_URL already; the login
                 ibkr_rebalance_reader can SELECT but cannot change any data.
                 Sign-in, user and broker-credential tables are hidden.
                 Keep connections low (limit 10); queries stop after 60s.
                 The database is private: it is reachable from the app, not
                 from your laptop.
Registry login expires in 365 days.
"@ | Set-Content -Path $note -Encoding UTF8

Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
Write-Host ''
Write-Host "DONE  site=https://$fqdn  aiden=$aidenId  entraApp=$entraAppId"
Write-Host "Aiden's hand-over note (contains his registry login): $note"
