targetScope = 'resourceGroup'

param jobName string = 'ibkrv2stg-benchmark-daily'
param environmentName string = 'ibkrv2stg-env'
param registryName string
param keyVaultName string
param identityResourceId string
param imageDigest string
param tenantId string = '00000000-0000-0000-0000-000000000001'
param benchmarkAccountIds string = ''
param benchmarkStartDate string = '2025-01-20'
// S2 raw market-data capture. Empty keeps capture off and leaves this job's
// environment exactly as before. To switch on, set it to 'bronze-market-data' after
// granting this job's identity Storage Blob Data Contributor on that container.
param rawMarketDataContainer string = ''
param rawMarketDataStorageAccountName string = 'ibkrv2stgauhecwc4itaoo'
var rawMarketDataEnv = empty(rawMarketDataContainer) ? [] : [
  { name: 'MARKET_DATA_RAW_CONTAINER', value: rawMarketDataContainer }
  { name: 'AZURE_STORAGE_ACCOUNT_URL', value: 'https://${rawMarketDataStorageAccountName}.blob.${az.environment().suffixes.storage}' }
  { name: 'AZURE_CLIENT_ID', value: jobIdentity.properties.clientId }
]

var tags = {
  application: 'ibkr-portfolio-platform'
  environment: 'internal-production'
  owner: 'portfolio-platform'
  costCenter: 'pilot'
  version: '2.0'
  dataClassification: 'confidential-financial'
  dataLicense: 'yfinance-internal-only'
}

resource managedEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: environmentName
}
resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: registryName
}
resource vault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}
resource jobIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: last(split(identityResourceId, '/'))
}

resource job 'Microsoft.App/jobs@2024-03-01' = {
  name: jobName
  location: resourceGroup().location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${jobIdentity.id}': {}
    }
  }
  properties: {
    environmentId: managedEnvironment.id
    configuration: {
      triggerType: 'Schedule'
      replicaTimeout: 1200
      replicaRetryLimit: 0
      scheduleTriggerConfig: {
        // 16:30 HKT, after the 16:10 account-only synchronization.
        cronExpression: '30 8 * * *'
        parallelism: 1
        replicaCompletionCount: 1
      }
      registries: [
        {
          server: registry.properties.loginServer
          identity: jobIdentity.id
        }
      ]
      secrets: [
        {
          name: 'database-url'
          keyVaultUrl: '${vault.properties.vaultUri}secrets/database-app-url'
          identity: jobIdentity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'provisional-benchmark'
          image: '${registry.properties.loginServer}/ibkr-platform@${imageDigest}'
          command: ['python']
          args: ['scripts/refresh_provisional_benchmark.py']
          env: concat([
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            { name: 'PORTFOLIO_TENANT_ID', value: tenantId }
            { name: 'BENCHMARK_ACCOUNT_IDS', value: benchmarkAccountIds }
            { name: 'BENCHMARK_START_DATE', value: benchmarkStartDate }
            { name: 'DEPLOYMENT_ENVIRONMENT', value: 'internal-production' }
            { name: 'ALLOW_UNLICENSED_BENCHMARK', value: 'true' }
          ], rawMarketDataEnv)
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
    }
  }
}

output jobName string = job.name
