targetScope = 'resourceGroup'

// Daily traded volumes into silver.daily_volumes (docs/PHASE3_DATA.md). No new fetch: the job
// reads the Yahoo chart responses already kept in bronze-market-data (feed yahoo-chart).
param jobName string = 'ibkrv2stg-daily-volumes'
param environmentName string = 'ibkrv2stg-env'
param registryName string
param keyVaultName string
// The ingestion identity (staging.bicep), as the market-data jobs use: it reads the
// database-app-url secret and holds Storage Blob Data Contributor on bronze-market-data, which
// covers reading the captures. No new role assignment.
param identityResourceId string
param imageDigest string
param tenantId string = '00000000-0000-0000-0000-000000000001'
param storageAccountName string = 'ibkrv2stgauhecwc4itaoo'
param bronzeContainer string = 'bronze-market-data'
// The load does nothing unless this is on. Leave it off until the held migration has run and one
// backfill has been run by hand and checked (docs/PHASE3_DATA.md, switching on).
@allowed(['off', 'on'])
param dailyVolumesLoad string = 'off'
// Lineage records each run, with the captures it read (U4).
@allowed(['false', 'true'])
param lineageRecording string = 'true'

var tags = {
  application: 'ibkr-portfolio-platform'
  environment: 'internal-production'
  owner: 'portfolio-platform'
  costCenter: 'pilot'
  version: '2.0'
  dataClassification: 'public-reference-data'
  dataSource: 'yahoo-finance'
  dataLicense: 'yfinance-internal-only'
  workload: 'daily-volumes'
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
      // Fifteen minutes at most, so a run started at 06:30 is over long before 07:50.
      replicaTimeout: 900
      replicaRetryLimit: 0
      scheduleTriggerConfig: {
        // 06:30 UTC, after the overnight prices and outside the 07:50 to 09:25 UTC daily window.
        cronExpression: '30 6 * * *'
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
          name: 'daily-volumes'
          image: '${registry.properties.loginServer}/ibkr-platform@${imageDigest}'
          command: ['python']
          args: ['scripts/load_daily_volumes.py']
          env: [
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            { name: 'PORTFOLIO_TENANT_ID', value: tenantId }
            { name: 'DEPLOYMENT_ENVIRONMENT', value: 'internal-production' }
            { name: 'AZURE_CLIENT_ID', value: jobIdentity.properties.clientId }
            { name: 'AZURE_STORAGE_ACCOUNT_URL', value: 'https://${storageAccountName}.blob.${environment().suffixes.storage}' }
            { name: 'DAILY_VOLUMES_CONTAINER', value: bronzeContainer }
            { name: 'DAILY_VOLUMES_LOAD', value: dailyVolumesLoad }
            { name: 'LINEAGE_RECORDING', value: lineageRecording }
            // The app login cannot create tables, so the job must not try migrations.
            { name: 'IBKR_RUN_MIGRATIONS', value: 'false' }
          ]
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
output scheduleUtc string = '06:30 UTC daily'
