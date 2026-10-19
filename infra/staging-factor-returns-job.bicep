targetScope = 'resourceGroup'

// Kenneth French daily factor returns into silver.factor_returns (docs/PHASE3_DATA.md). Each file
// is stored as received in bronze-market-data (feed kenneth-french) before it is parsed.
param jobName string = 'ibkrv2stg-factor-returns'
param environmentName string = 'ibkrv2stg-env'
param registryName string
param keyVaultName string
// The ingestion identity (staging.bicep), as the market-data jobs use: it reads the
// database-app-url secret and holds Storage Blob Data Contributor on bronze-market-data, where
// the raw copies go. No new role assignment.
param identityResourceId string
param imageDigest string
param tenantId string = '00000000-0000-0000-0000-000000000001'
// The raw copies, as the other market-data jobs make them. Empty switches capture off and drops
// these settings (the load still runs, but its rows could not be rebuilt from bronze).
param rawMarketDataContainer string = 'bronze-market-data'
param rawMarketDataStorageAccountName string = 'ibkrv2stgauhecwc4itaoo'
var rawMarketDataEnv = empty(rawMarketDataContainer) ? [] : [
  { name: 'MARKET_DATA_RAW_CONTAINER', value: rawMarketDataContainer }
  { name: 'AZURE_STORAGE_ACCOUNT_URL', value: 'https://${rawMarketDataStorageAccountName}.blob.${az.environment().suffixes.storage}' }
  // Six files of up to a few MB each: more than the default 30 seconds.
  { name: 'MARKET_DATA_RAW_BUDGET_SECONDS', value: '60' }
]
// The load does nothing unless this is on. Leave it off until the held migration has run and one
// run by hand has been checked (docs/PHASE3_DATA.md, switching on).
@allowed(['off', 'on'])
param factorReturnsLoad string = 'off'
// Lineage records each run, with the raw copies it stored (U4).
@allowed(['false', 'true'])
param lineageRecording string = 'true'

var tags = {
  application: 'ibkr-portfolio-platform'
  environment: 'internal-production'
  owner: 'portfolio-platform'
  costCenter: 'pilot'
  version: '2.0'
  dataClassification: 'public-reference-data'
  dataSource: 'kenneth-french-data-library'
  // Free to download; the terms for commercial use are not yet checked: internal use only.
  dataLicense: 'kenneth-french-terms-unchecked-internal-only'
  workload: 'factor-returns'
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
      // Twenty minutes at most: the first load writes about 250,000 rows, later ones a few thousand.
      replicaTimeout: 1200
      replicaRetryLimit: 0
      scheduleTriggerConfig: {
        // Saturdays 06:00 UTC: the library publishes about once a month, and a weekend run is far
        // from the 07:50 to 09:25 UTC daily window.
        cronExpression: '0 6 * * 6'
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
          name: 'factor-returns'
          image: '${registry.properties.loginServer}/ibkr-platform@${imageDigest}'
          command: ['python']
          args: ['scripts/load_factor_returns.py']
          env: concat([
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            { name: 'PORTFOLIO_TENANT_ID', value: tenantId }
            { name: 'DEPLOYMENT_ENVIRONMENT', value: 'internal-production' }
            { name: 'AZURE_CLIENT_ID', value: jobIdentity.properties.clientId }
            { name: 'FACTOR_RETURNS_LOAD', value: factorReturnsLoad }
            { name: 'LINEAGE_RECORDING', value: lineageRecording }
            // The app login cannot create tables, so the job must not try migrations.
            { name: 'IBKR_RUN_MIGRATIONS', value: 'false' }
          ], rawMarketDataEnv)
          resources: {
            cpu: json('0.25')
            memory: '1Gi'
          }
        }
      ]
    }
  }
}

output jobName string = job.name
output scheduleUtc string = 'Saturdays 06:00 UTC'
