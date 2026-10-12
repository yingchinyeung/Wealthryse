targetScope = 'resourceGroup'

param jobName string = 'ibkrv2stg-account-sync-daily'
param environmentName string = 'ibkrv2stg-env'
param registryName string = 'ibkrv2stgauhecwc4itaoo'
param keyVaultName string = 'ibkrv2stg-auhecwc4itaoo'
param stagingStorageAccountName string = 'ibkrv2stgauhecwc4itaoo'
param productionStorageAccountName string = 'ibkrv115rcc62xxqblay'
param identityResourceId string
param imageDigest string
param tenantId string = '00000000-0000-0000-0000-000000000001'
param pilotAccountId string = 'U16088571'
param pilotStartDate string = '2025-01-20'
param wave0AccountId string = 'U16088606'
param accountSyncTargets array = [
  {
    account_id: 'U16088571'
    start_date: '2025-01-20'
  }
  {
    account_id: 'U16088606'
    start_date: '2025-01-20'
  }
]

var tags = {
  application: 'ibkr-portfolio-platform'
  environment: 'internal-production'
  owner: 'portfolio-platform'
  costCenter: 'pilot'
  version: '2.0'
  dataClassification: 'confidential-financial'
  sourceScope: 'physical-account-wave-0'
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
resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: last(split(identityResourceId, '/'))
}

resource job 'Microsoft.App/jobs@2024-03-01' = {
  name: jobName
  location: resourceGroup().location
  tags: tags
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
        // 16:10 HKT, ten minutes after the production IBKR export starts.
        cronExpression: '10 8 * * *'
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
          keyVaultUrl: '${vault.properties.vaultUri}secrets/database-app-url'
          identity: identity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'account-sync'
          image: '${registry.properties.loginServer}/ibkr-platform@${imageDigest}'
          command: ['python']
          args: ['scripts/sync_daily_pilot_account.py']
          env: [
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            { name: 'DEPLOYMENT_ENVIRONMENT', value: 'internal-production' }
            { name: 'PORTFOLIO_TENANT_ID', value: tenantId }
            { name: 'PILOT_ACCOUNT_ID', value: pilotAccountId }
            { name: 'PILOT_START_DATE', value: pilotStartDate }
            { name: 'ONBOARDING_WAVE0_ACCOUNT_ID', value: wave0AccountId }
            { name: 'ACCOUNT_SYNC_TARGETS_JSON', value: string(accountSyncTargets) }
            { name: 'PRODUCTION_STORAGE_ACCOUNT_URL', value: 'https://${productionStorageAccountName}.blob.${environment().suffixes.storage}' }
            { name: 'PRODUCTION_EXPORT_CONTAINER', value: 'analytics-reports' }
            { name: 'PRODUCTION_EXPORT_PREFIX', value: '${tenantId}/latest' }
            { name: 'AZURE_STORAGE_ACCOUNT_URL', value: 'https://${stagingStorageAccountName}.blob.${environment().suffixes.storage}' }
            { name: 'PILOT_SNAPSHOT_CONTAINER', value: 'input-snapshots' }
            { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId }
          ]
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
output scheduleHongKong string = 'Daily at 16:10 Asia/Hong_Kong (UTC+8)'
