[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SubscriptionId,

    [string]$ResourceGroup = 'rg-ibkr-database-v11',
    [string]$Location = 'eastasia',
    [string]$Prefix = 'ibkrv11',
    [string]$AdministratorLogin = 'ibkradmin',
    [string]$AlertEmail = '',
    [string]$ImageTag = (Get-Date -Format 'yyyyMMdd-HHmmss'),
    [string]$TenantId = '00000000-0000-0000-0000-000000000001',
    [string]$TenantName = 'Own Portfolio',
    [switch]$RunTest,
    [switch]$ConfirmAzureCosts
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# This script deploys v1.1 (infra/main.bicep, bootstrap-job.bicep and job.bicep).
# v1.1 was deleted on 21 Oct 2026, so running it would build a new copy of the
# retired stack. Kept as a record only; v2 has its own Bicep files in infra/.
throw 'v1.1 was deleted on 21 Oct 2026. This script would create it again, so it no longer runs.'

if (-not $ConfirmAzureCosts) {
    throw 'Azure resources incur ongoing charges. Re-run with -ConfirmAzureCosts after reviewing the selected region and SKUs.'
}

if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    throw 'Azure CLI is not installed. Install it from https://learn.microsoft.com/cli/azure/install-azure-cli-windows and run az login.'
}

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$MainTemplate = Join-Path $ProjectRoot 'infra\main.bicep'
$BootstrapTemplate = Join-Path $ProjectRoot 'infra\bootstrap-job.bicep'
$JobTemplate = Join-Path $ProjectRoot 'infra\job.bicep'
$TemporaryFiles = [System.Collections.Generic.List[string]]::new()

function New-ProtectedTemporaryFile {
    param([Parameter(Mandatory = $true)][string]$Content)
    $path = [System.IO.Path]::GetTempFileName()
    [System.IO.File]::WriteAllText($path, $Content, [System.Text.UTF8Encoding]::new($false))
    $TemporaryFiles.Add($path)
    return $path
}

function Convert-SecureStringToText {
    param([Parameter(Mandatory = $true)][Security.SecureString]$SecureValue)
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureValue)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
}

try {
    & az account show --output none
    if ($LASTEXITCODE -ne 0) { throw 'Azure CLI is not logged in. Run az login first.' }

    & az account set --subscription $SubscriptionId
    if ($LASTEXITCODE -ne 0) { throw 'Unable to select the requested Azure subscription.' }

    & az bicep version --output none
    if ($LASTEXITCODE -ne 0) {
        & az bicep install
        if ($LASTEXITCODE -ne 0) { throw 'Unable to install the Azure Bicep CLI.' }
    }
    foreach ($Template in @($MainTemplate, $BootstrapTemplate, $JobTemplate)) {
        & az bicep build --file $Template --stdout | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Bicep compilation failed: $Template" }
    }

    & az config set extension.use_dynamic_install=yes_without_prompt --output none
    & az provider register --namespace Microsoft.App --wait --output none
    & az provider register --namespace Microsoft.OperationalInsights --wait --output none
    & az provider register --namespace Microsoft.DBforPostgreSQL --wait --output none
    & az provider register --namespace Microsoft.ContainerRegistry --wait --output none

    & az group create --name $ResourceGroup --location $Location --output none
    if ($LASTEXITCODE -ne 0) { throw 'Resource group creation failed.' }

    $AdminPasswordSecure = Read-Host 'New PostgreSQL administrator password' -AsSecureString
    $AdminPassword = Convert-SecureStringToText $AdminPasswordSecure
    if ($AdminPassword.Length -lt 12) { throw 'Use a PostgreSQL password of at least 12 characters.' }
    $ApplicationPassword = [Convert]::ToBase64String(
        [Security.Cryptography.RandomNumberGenerator]::GetBytes(32)
    )
    $FlexTokenSecure = Read-Host 'IBKR Flex token (stored only in Azure Key Vault)' -AsSecureString
    $FlexToken = Convert-SecureStringToText $FlexTokenSecure
    $FlexQueryId = Read-Host 'IBKR Activity Flex Query ID'
    if ([string]::IsNullOrWhiteSpace($FlexToken) -or [string]::IsNullOrWhiteSpace($FlexQueryId)) {
        throw 'Both the IBKR Flex token and Activity Flex Query ID are required.'
    }

    $ParameterObject = @{
        '$schema' = 'https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#'
        contentVersion = '1.0.0.0'
        parameters = @{
            prefix = @{ value = $Prefix }
            location = @{ value = $Location }
            administratorLogin = @{ value = $AdministratorLogin }
            administratorLoginPassword = @{ value = $AdminPassword }
            applicationLoginPassword = @{ value = $ApplicationPassword }
            ibkrFlexToken = @{ value = $FlexToken }
            ibkrFlexQueryId = @{ value = $FlexQueryId }
        }
    }
    $ParameterFile = New-ProtectedTemporaryFile ($ParameterObject | ConvertTo-Json -Depth 8)
    $AdminPassword = $null
    $ApplicationPassword = $null
    $FlexToken = $null
    $FlexQueryId = $null
    $AdminPasswordSecure.Dispose()
    $FlexTokenSecure.Dispose()

    Write-Host 'Deploying the private network, PostgreSQL, storage, Key Vault, registry, identity, and monitoring workspace...'
    & az deployment group what-if `
        --resource-group $ResourceGroup `
        --template-file $MainTemplate `
        --parameters "@$ParameterFile" `
        --result-format ResourceIdOnly `
        --output table
    if ($LASTEXITCODE -ne 0) { throw 'Azure what-if validation failed.' }
    $DeploymentOutputJson = & az deployment group create `
        --resource-group $ResourceGroup `
        --name "ibkr-v11-base-$ImageTag" `
        --template-file $MainTemplate `
        --parameters "@$ParameterFile" `
        --query properties.outputs `
        --output json
    if ($LASTEXITCODE -ne 0) { throw 'Base infrastructure deployment failed.' }
    $Outputs = $DeploymentOutputJson | ConvertFrom-Json

    $RegistryName = $Outputs.registryName.value
    $VaultName = $Outputs.keyVaultName.value
    $EnvironmentName = $Outputs.environmentName.value
    $StorageAccountName = $Outputs.storageAccountName.value
    $IdentityResourceId = $Outputs.identityResourceId.value

    Write-Host "Building image ibkr-etl:$ImageTag in Azure Container Registry..."
    & az acr build `
        --registry $RegistryName `
        --image "ibkr-etl:$ImageTag" `
        --file (Join-Path $ProjectRoot 'Dockerfile.azure') `
        $ProjectRoot
    if ($LASTEXITCODE -ne 0) { throw 'Container image build failed.' }

    $ImageDigest = & az acr manifest show-metadata `
        --registry $RegistryName `
        --name "ibkr-etl:$ImageTag" `
        --query digest `
        --output tsv
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($ImageDigest)) {
        throw 'Unable to resolve the built container image digest.'
    }
    foreach ($ImageReference in @("ibkr-etl:$ImageTag", "ibkr-etl@$ImageDigest")) {
        & az acr repository update `
            --name $RegistryName `
            --image $ImageReference `
            --write-enabled false `
            --delete-enabled false `
            --read-enabled true `
            --list-enabled true `
            --output none
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to lock production image reference: $ImageReference"
        }
    }
    Write-Host "Production image is immutable and deletion-protected: ibkr-etl:$ImageTag"

    Write-Host 'Deploying and running the one-time PostgreSQL bootstrap job...'
    & az deployment group create `
        --resource-group $ResourceGroup `
        --name "ibkr-v11-bootstrap-$ImageTag" `
        --template-file $BootstrapTemplate `
        --parameters `
            environmentName=$EnvironmentName `
            registryName=$RegistryName `
            keyVaultName=$VaultName `
            identityResourceId=$IdentityResourceId `
            imageTag=$ImageTag `
            tenantId=$TenantId `
            tenantName=$TenantName `
        --output none
    if ($LASTEXITCODE -ne 0) { throw 'Bootstrap job deployment failed.' }

    $BootstrapExecution = & az containerapp job start `
        --name ibkr-v11-bootstrap `
        --resource-group $ResourceGroup `
        --query name `
        --output tsv
    if ($LASTEXITCODE -ne 0) { throw 'Unable to start the PostgreSQL bootstrap job.' }
    $BootstrapStatus = ''
    for ($attempt = 0; $attempt -lt 90; $attempt++) {
        Start-Sleep -Seconds 10
        $BootstrapStatus = & az containerapp job execution show `
            --name ibkr-v11-bootstrap `
            --job-execution-name $BootstrapExecution `
            --resource-group $ResourceGroup `
            --query properties.status `
            --output tsv
        if ($BootstrapStatus -in @('Succeeded', 'Failed')) { break }
    }
    if ($BootstrapStatus -ne 'Succeeded') {
        throw "PostgreSQL bootstrap did not succeed. Final status: $BootstrapStatus"
    }
    Write-Host 'Least-privilege PostgreSQL application role is ready.'

    Write-Host 'Deploying the daily 16:00 HKT Container Apps job and failure alert...'
    & az deployment group create `
        --resource-group $ResourceGroup `
        --name "ibkr-v11-job-$ImageTag" `
        --template-file $JobTemplate `
        --parameters `
            environmentName=$EnvironmentName `
            registryName=$RegistryName `
            storageAccountName=$StorageAccountName `
            keyVaultName=$VaultName `
            identityResourceId=$IdentityResourceId `
            imageTag=$ImageTag `
            tenantId=$TenantId `
            tenantName=$TenantName `
            alertEmail=$AlertEmail `
        --output none
    if ($LASTEXITCODE -ne 0) { throw 'Container Apps job deployment failed.' }

    if ($RunTest) {
        Write-Host 'Starting one end-to-end test execution...'
        $ExecutionName = & az containerapp job start --name ibkr-v11-daily --resource-group $ResourceGroup --query name --output tsv
        if ($LASTEXITCODE -ne 0) { throw 'Unable to start the test execution.' }
        Write-Host "Test execution started: $ExecutionName"
        Write-Host 'Use scripts\check_azure_status.ps1 to inspect completion, data recency, and logs.'
    }

    Write-Host 'Azure deployment completed. Schedule: 0 8 * * * UTC, which is 16:00 HKT every day.'
}
finally {
    foreach ($TemporaryFile in $TemporaryFiles) {
        if (Test-Path -LiteralPath $TemporaryFile) {
            [System.IO.File]::WriteAllText($TemporaryFile, '')
            Remove-Item -LiteralPath $TemporaryFile -Force
        }
    }
}
