targetScope = 'resourceGroup'

param jobName string = 'ibkrv2stg-risk-free-daily'
param environmentName string = 'ibkrv2stg-env'
param registryName string
param keyVaultName string
param identityResourceId string
param imageDigest string
param tenantId string = '00000000-0000-0000-0000-000000000001'
param riskFreeStartDate string = '2025-01-20'
// S2 raw market-data capture, on since 18 Oct 2026: the default matches the live job.
// The job's identity (ingestionIdentity in staging.bicep) holds Storage Blob Data
// Contributor on bronze-market-data. Empty switches capture off and drops these settings.
param rawMarketDataContainer string = 'bronze-market-data'
param rawMarketDataStorageAccountName string = 'ibkrv2stgauhecwc4itaoo'
var rawMarketDataEnv = empty(rawMarketDataContainer) ? [] : [
  { name: 'MARKET_DATA_RAW_CONTAINER', value: rawMarketDataContainer }
  { name: 'AZURE_STORAGE_ACCOUNT_URL', value: 'https://${rawMarketDataStorageAccountName}.blob.${az.environment().suffixes.storage}' }
  { name: 'AZURE_CLIENT_ID', value: jobIdentity.properties.clientId }
  // A fetch whose raw copy cannot be stored fails (raw_capture required mode).
  { name: 'MARKET_DATA_RAW_REQUIRED', value: 'true' }
]

// Korea's won risk-free rate (Bank of Korea ECOS, CD 91-day). Empty (the default) keeps the
// job exactly as it is; set to the Key Vault secret holding the ECOS API key to load it too.
param bokEcosApiKeySecretName string = ''
var bokEcosSecrets = empty(bokEcosApiKeySecretName) ? [] : [
  {
    name: 'bok-ecos-api-key'
    keyVaultUrl: '${vault.properties.vaultUri}secrets/${bokEcosApiKeySecretName}'
    identity: jobIdentity.id
  }
]
var bokEcosEnv = empty(bokEcosApiKeySecretName) ? [] : [
  { name: 'BOK_ECOS_API_KEY', secretRef: 'bok-ecos-api-key' }
]
// Without an ECOS key: load the OECD's monthly Korea CD rate from FRED (no key needed).
param koreaCdMonthly bool = false
var koreaCdEnv = concat(bokEcosEnv, koreaCdMonthly ? [ { name: 'KOREA_CD_MONTHLY', value: 'true' } ] : [])

var tags = {
  application: 'ibkr-portfolio-platform'
  environment: 'staging'
  owner: 'portfolio-platform'
  costCenter: 'pilot'
  version: '2.0'
  dataClassification: 'public-reference-data'
  dataSource: 'us-treasury'
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
  tags: union(tags, { layer: 'apps', component: 'daily-pipeline', hop: 'ingest+conform' })
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
        // 07:00 HKT. This independent job never calls IBKR Flex.
        cronExpression: '0 23 * * *'
        parallelism: 1
        replicaCompletionCount: 1
      }
      registries: [
        {
          server: registry.properties.loginServer
          identity: jobIdentity.id
        }
      ]
      secrets: concat([
        {
          name: 'database-url'
          keyVaultUrl: '${vault.properties.vaultUri}secrets/database-app-url'
          identity: jobIdentity.id
        }
      ], bokEcosSecrets)
    }
    template: {
      containers: [
        {
          name: 'official-treasury-risk-free'
          image: '${registry.properties.loginServer}/ibkr-platform@${imageDigest}'
          command: ['python']
          args: ['scripts/refresh_staging_risk_free.py']
          env: concat(concat([
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            { name: 'PORTFOLIO_TENANT_ID', value: tenantId }
            { name: 'RISK_FREE_START_DATE', value: riskFreeStartDate }
            { name: 'DEPLOYMENT_ENVIRONMENT', value: 'staging' }
            // The app login cannot create tables, so the job must not try migrations
            // (live since 18 Oct 2026, as on the institutional daily job).
            { name: 'IBKR_RUN_MIGRATIONS', value: 'false' }
          ], rawMarketDataEnv), koreaCdEnv)
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
        }
      ]
    }
  }
}

output jobName string = job.name
