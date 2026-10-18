targetScope = 'resourceGroup'

param jobName string = 'ibkrv2stg-sector-classification'
param environmentName string = 'ibkrv2stg-env'
param registryName string
param keyVaultName string
param identityResourceId string
param imageDigest string
param tenantId string = '00000000-0000-0000-0000-000000000001'
param classificationAccountIds string = ''
param classificationAsOf string = ''
param actionGroupName string = 'ibkr-v11-daily-alerts'
// S2 raw market-data capture, on since 18 Oct 2026: the default matches the live job.
// The job's identity (ingestionIdentity in staging.bicep) holds Storage Blob Data
// Contributor on bronze-market-data. Empty switches capture off and drops these settings.
param rawMarketDataContainer string = 'bronze-market-data'
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
resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' existing = {
  name: actionGroupName
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
      replicaTimeout: 1800
      replicaRetryLimit: 0
      scheduleTriggerConfig: {
        // 16:45 HKT, after account and benchmark refreshes and before the
        // 17:10 HKT analyst-assurance gate.
        cronExpression: '45 8 * * *'
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
          name: 'sector-classification'
          image: '${registry.properties.loginServer}/ibkr-platform@${imageDigest}'
          command: ['python']
          args: ['scripts/refresh_staging_sector_classifications.py']
          env: concat([
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            { name: 'DEPLOYMENT_ENVIRONMENT', value: 'internal-production' }
            { name: 'PORTFOLIO_TENANT_ID', value: tenantId }
            { name: 'SECTOR_CLASSIFICATION_ACCOUNT_IDS', value: classificationAccountIds }
            { name: 'SECTOR_CLASSIFICATION_AS_OF', value: classificationAsOf }
            { name: 'SECTOR_CLASSIFICATION_WORKERS', value: '4' }
            // The app login cannot create tables, so the job must not try migrations
            // (live since 18 Oct 2026, as on the institutional daily job).
            { name: 'IBKR_RUN_MIGRATIONS', value: 'false' }
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

resource failureAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: '${jobName}-failed-execution'
  location: 'global'
  tags: tags
  properties: {
    description: 'The daily current-holdings sector-classification job failed.'
    severity: 1
    enabled: true
    scopes: [job.id]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'FailedExecutions'
          metricNamespace: 'Microsoft.App/jobs'
          metricName: 'Executions'
          operator: 'GreaterThan'
          threshold: 0
          timeAggregation: 'Total'
          criterionType: 'StaticThresholdCriterion'
          dimensions: [
            {
              name: 'state'
              operator: 'Include'
              values: ['Failed']
            }
          ]
        }
      ]
    }
    autoMitigate: true
    actions: [{ actionGroupId: actionGroup.id }]
  }
}

output jobName string = job.name
output scheduleHongKong string = '16:45 HKT daily'
