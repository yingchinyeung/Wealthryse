targetScope = 'resourceGroup'

// Phase 3 data: daily macro series into silver macro_series (docs/PHASE3_DATA_MACRO_13F.md).
// FRED keyless CSV downloads (DGS10, DGS2, DTWEXBGS, DCOILBRENTEU; VIXCLS only once licensed) and
// Yahoo USD/CNH, each raw response captured to bronze-market-data before it is parsed.
param jobName string = 'ibkrv2stg-macro-series'
param environmentName string = 'ibkrv2stg-env'
param registryName string
param keyVaultName string
// The existing ingestion identity (ingestionIdentity in staging.bicep), as on the risk-free job: it
// reads the database-app-url secret, pulls the image and holds Storage Blob Data Contributor on
// bronze-market-data. This file adds no role.
param identityResourceId string
param imageDigest string
param tenantId string = '00000000-0000-0000-0000-000000000001'
// The job does nothing unless this is on. Leave it off until the held migration (macro_series.sql,
// one free day after Wed 25 Nov) has run and the Brent licence check is done.
@allowed(['off', 'on'])
param macroSeriesLoad string = 'off'
// Restricted series the owner has licensed, comma-separated (VIXCLS is Cboe copyright). Empty: none.
param macroSeriesLicenseConfirmed string = ''
@allowed(['false', 'true'])
param lineageRecording string = 'true'
// Raw market-data capture, as on the live market-data jobs. Empty switches capture off.
param rawMarketDataContainer string = 'bronze-market-data'
param rawMarketDataStorageAccountName string = 'ibkrv2stgauhecwc4itaoo'
var rawMarketDataEnv = empty(rawMarketDataContainer) ? [] : [
  { name: 'MARKET_DATA_RAW_CONTAINER', value: rawMarketDataContainer }
  { name: 'AZURE_STORAGE_ACCOUNT_URL', value: 'https://${rawMarketDataStorageAccountName}.blob.${az.environment().suffixes.storage}' }
  { name: 'AZURE_CLIENT_ID', value: jobIdentity.properties.clientId }
  { name: 'MARKET_DATA_RAW_BUDGET_SECONDS', value: '60' }
]
var licenceEnv = empty(macroSeriesLicenseConfirmed) ? [] : [
  { name: 'MACRO_SERIES_LICENSE_CONFIRMED', value: macroSeriesLicenseConfirmed }
]

var tags = {
  application: 'ibkr-portfolio-platform'
  environment: 'staging'
  owner: 'portfolio-platform'
  costCenter: 'pilot'
  version: '2.0'
  dataClassification: 'public-reference-data'
  dataSource: 'fred-and-yahoo-chart'
  dataLicense: 'public-domain-citation-requested;yfinance-internal-only'
  workload: 'macro-series'
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
        // 22:30 UTC daily, after the US close and the day's FRED updates, far from the daily chain
        // (07:50 to 09:25 UTC); the script refuses to start inside that window (exit 75).
        cronExpression: '30 22 * * *'
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
          name: 'macro-series'
          image: '${registry.properties.loginServer}/ibkr-platform@${imageDigest}'
          command: ['python']
          args: ['scripts/load_macro_series.py']
          env: concat([
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            { name: 'PORTFOLIO_TENANT_ID', value: tenantId }
            { name: 'DEPLOYMENT_ENVIRONMENT', value: 'staging' }
            { name: 'MACRO_SERIES_LOAD', value: macroSeriesLoad }
            { name: 'LINEAGE_RECORDING', value: lineageRecording }
            // The app login cannot create tables, so the job must not try migrations.
            { name: 'IBKR_RUN_MIGRATIONS', value: 'false' }
          ], licenceEnv, rawMarketDataEnv)
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
output scheduleUtc string = '22:30 UTC daily'
