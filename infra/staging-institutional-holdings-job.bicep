targetScope = 'resourceGroup'

// Phase 3 data: SEC EDGAR 13F-HR holdings into silver institutional_holdings
// (docs/PHASE3_DATA_MACRO_13F.md). Each raw filing is captured to bronze-market-data before it is parsed.
param jobName string = 'ibkrv2stg-institutional-holdings'
param environmentName string = 'ibkrv2stg-env'
param registryName string
param keyVaultName string
// The existing ingestion identity (ingestionIdentity in staging.bicep), as on the risk-free job: it
// reads the database-app-url secret, pulls the image and holds Storage Blob Data Contributor on
// bronze-market-data. This file adds no role.
param identityResourceId string
param imageDigest string
param tenantId string = '00000000-0000-0000-0000-000000000001'
// The job does nothing unless this is on. Leave it off until the held migration
// (institutional_holdings.sql) has run and the filer list is reviewed.
@allowed(['off', 'on'])
param institutionalHoldingsLoad string = 'off'
// SEC fair access: the operator's name and contact email address, for example
// 'Company Name contact(at)company.example' written with a real at sign. Given at deploy time, never
// kept in this file. Empty leaves the setting out, and the job then refuses to run (exit 78).
param secUserAgent string = ''
@allowed(['false', 'true'])
param lineageRecording string = 'true'
// Raw market-data capture, as on the live market-data jobs. Empty switches capture off. Information
// tables of the largest filers run to tens of megabytes, so the capture budget is larger than the
// default 30 seconds.
param rawMarketDataContainer string = 'bronze-market-data'
param rawMarketDataStorageAccountName string = 'ibkrv2stgauhecwc4itaoo'
var rawMarketDataEnv = empty(rawMarketDataContainer) ? [] : [
  { name: 'MARKET_DATA_RAW_CONTAINER', value: rawMarketDataContainer }
  { name: 'AZURE_STORAGE_ACCOUNT_URL', value: 'https://${rawMarketDataStorageAccountName}.blob.${az.environment().suffixes.storage}' }
  { name: 'AZURE_CLIENT_ID', value: jobIdentity.properties.clientId }
  { name: 'MARKET_DATA_RAW_BUDGET_SECONDS', value: '900' }
]
var userAgentEnv = empty(secUserAgent) ? [] : [
  { name: 'SEC_USER_AGENT', value: secUserAgent }
]

var tags = {
  application: 'ibkr-portfolio-platform'
  environment: 'staging'
  owner: 'portfolio-platform'
  costCenter: 'pilot'
  version: '2.0'
  dataClassification: 'public-reference-data'
  dataSource: 'sec-edgar'
  dataLicense: 'sec-edgar-public-filing'
  workload: 'institutional-holdings'
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
      // An hour at most. The loader starts no new filer or filing after 45 minutes and reports what
      // it left; the next run (or a manual start) picks it up, skipping accessions already stored.
      replicaTimeout: 3600
      replicaRetryLimit: 0
      scheduleTriggerConfig: {
        // 22:45 UTC on the 20th of Feb, May, Aug and Nov: after the 45-day 13F deadline of the
        // quarter before, far from the daily chain (07:50 to 09:25 UTC).
        cronExpression: '45 22 20 2,5,8,11 *'
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
          name: 'institutional-holdings'
          image: '${registry.properties.loginServer}/ibkr-platform@${imageDigest}'
          command: ['python']
          args: ['scripts/load_institutional_holdings.py']
          env: concat([
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            { name: 'PORTFOLIO_TENANT_ID', value: tenantId }
            { name: 'DEPLOYMENT_ENVIRONMENT', value: 'staging' }
            { name: 'INSTITUTIONAL_HOLDINGS_LOAD', value: institutionalHoldingsLoad }
            { name: 'LINEAGE_RECORDING', value: lineageRecording }
            // The app login cannot create tables, so the job must not try migrations.
            { name: 'IBKR_RUN_MIGRATIONS', value: 'false' }
          ], userAgentEnv, rawMarketDataEnv)
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
output scheduleUtc string = '22:45 UTC on the 20th of Feb, May, Aug and Nov'
