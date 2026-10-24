targetScope = 'resourceGroup'

// Self-service onboarding (docs/SELF_SERVICE_ONBOARDING.md): the job that does the platform's part
// of each open onboarding case, so no case needs a backend run. Every 20 minutes, and started by the
// website (Run now on a case, Check now on a Kiwoom connection). It skips the daily chain
// (07:20-09:45 UTC) and asks Kiwoom for past days only outside Kiwoom's rebuild hours.
//
// A loader job (Medallion step 3): the loader login, Kiwoom pages stored in bronze before silver is
// written from them, as the institutional daily job does. It runs as the institutional daily job's
// identity, which already reads the Kiwoom keys in the Flex token vault; the one right added here is
// writing review copies of the historical reports to the internal-reports container.

param jobName string = 'ibkrv2stg-onboard-worker'
param environmentName string = 'ibkrv2stg-env'
param registryName string = 'ibkrv2stgauhecwc4itaoo'
param applicationVaultName string = 'ibkrv2stg-auhecwc4itaoo'
param flexTokenVaultName string
param storageAccountName string = 'ibkrv2stgauhecwc4itaoo'
param identityResourceId string
param imageDigest string
param tenantId string = '00000000-0000-0000-0000-000000000001'
param scheduleUtc string = '*/20 * * * *'
// No Kiwoom call at all unless this is on.
param kiwoomEnabled bool = true
@description('The default for a case that does not say whether to read the Kiwoom U.S. account.')
param kiwoomIncludeUs bool = true

var tags = {
  application: 'ibkr-portfolio-platform'
  environment: 'internal-production'
  owner: 'portfolio-platform'
  dataClassification: 'restricted-credential'
}

resource managedEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: environmentName
}
resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: registryName
}
resource applicationVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: applicationVaultName
}
resource databaseSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' existing = {
  parent: applicationVault
  // The loader login (Medallion step 3): only the loader jobs may write silver.
  name: 'database-loader-url'
}
resource tokenVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: flexTokenVaultName
}
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}
resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' existing = {
  parent: storage
  name: 'default'
}
resource reportContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = {
  parent: blobService
  name: 'internal-reports'
}
resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: last(split(identityResourceId, '/'))
}

// Storage Blob Data Contributor on internal-reports only: the historical reports' review copies.
resource reportWriter 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(reportContainer.id, identity.id, 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
  scope: reportContainer
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
  }
}

resource job 'Microsoft.App/jobs@2024-03-01' = {
  name: jobName
  location: resourceGroup().location
  tags: union(tags, { layer: 'data', component: 'onboarding', hop: 'ingest+conform' })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
  properties: {
    environmentId: managedEnvironment.id
    configuration: {
      triggerType: 'Schedule'
      replicaTimeout: 1800
      replicaRetryLimit: 0
      scheduleTriggerConfig: {
        cronExpression: scheduleUtc
        parallelism: 1
        replicaCompletionCount: 1
      }
      registries: [
        {
          server: registry.properties.loginServer
          identity: identity.id
        }
      ]
      secrets: [
        {
          name: 'database-url'
          keyVaultUrl: databaseSecret.properties.secretUri
          identity: identity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'onboarding-worker'
          image: '${registry.properties.loginServer}/ibkr-platform@${imageDigest}'
          command: ['python']
          args: ['scripts/run_onboarding_worker.py']
          env: [
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            { name: 'DEPLOYMENT_ENVIRONMENT', value: 'internal-production' }
            { name: 'PORTFOLIO_TENANT_ID', value: tenantId }
            { name: 'FLEX_TOKEN_VAULT_URI', value: tokenVault.properties.vaultUri }
            { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId }
            { name: 'AZURE_STORAGE_ACCOUNT_URL', value: 'https://${storageAccountName}.blob.${environment().suffixes.storage}' }
            { name: 'INTERNAL_REPORT_CONTAINER', value: 'internal-reports' }
            { name: 'KIWOOM_COLLECTOR_ENABLED', value: kiwoomEnabled ? 'true' : 'false' }
            { name: 'KIWOOM_INCLUDE_US', value: kiwoomIncludeUs ? 'true' : 'false' }
            // Every Kiwoom page in bronze first, and silver computed from bronze's copy.
            { name: 'KIWOOM_BRONZE', value: 'true' }
            { name: 'KIWOOM_BRONZE_REQUIRED', value: 'true' }
            { name: 'KIWOOM_SILVER_FROM_BRONZE', value: 'true' }
            { name: 'IBKR_RUN_MIGRATIONS', value: 'false' }
            { name: 'LINEAGE_RECORDING', value: 'true' }
          ]
          resources: {
            cpu: json('1.0')
            memory: '2Gi'
          }
        }
      ]
    }
  }
  dependsOn: [reportWriter]
}

output jobName string = job.name
output scheduleUtc string = scheduleUtc
