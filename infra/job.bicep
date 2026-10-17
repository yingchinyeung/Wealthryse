targetScope = 'resourceGroup'

param jobName string = 'ibkr-v11-daily'
param environmentName string
param registryName string
param storageAccountName string
param keyVaultName string
param identityResourceId string
param imageTag string
param tenantId string = '00000000-0000-0000-0000-000000000001'
param tenantName string = 'Own Portfolio'
param scheduleUtc string = '0 8 * * *'
// false once v2 makes its own Flex pull: the job stays, but only starts by hand.
param scheduleEnabled bool = true
param alertEmail string = ''
param tags object = {
  application: 'ibkr-portfolio-database'
  version: '1.1'
  dataClassification: 'confidential-financial'
}

resource managedEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: environmentName
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: registryName
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
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
      triggerType: scheduleEnabled ? 'Schedule' : 'Manual'
      replicaTimeout: 3600
      replicaRetryLimit: 0
      scheduleTriggerConfig: scheduleEnabled ? {
        cronExpression: scheduleUtc
        parallelism: 1
        replicaCompletionCount: 1
      } : null
      manualTriggerConfig: scheduleEnabled ? null : {
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
          keyVaultUrl: '${vault.properties.vaultUri}secrets/database-url'
          identity: jobIdentity.id
        }
        {
          name: 'ibkr-flex-token'
          keyVaultUrl: '${vault.properties.vaultUri}secrets/ibkr-flex-token'
          identity: jobIdentity.id
        }
        {
          name: 'ibkr-flex-query-id'
          keyVaultUrl: '${vault.properties.vaultUri}secrets/ibkr-flex-query-id'
          identity: jobIdentity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'ibkr-etl'
          image: '${registry.properties.loginServer}/ibkr-etl:${imageTag}'
          env: [
            {
              name: 'DATABASE_URL'
              secretRef: 'database-url'
            }
            {
              name: 'IBKR_FLEX_TOKEN'
              secretRef: 'ibkr-flex-token'
            }
            {
              name: 'IBKR_FLEX_QUERY_ID'
              secretRef: 'ibkr-flex-query-id'
            }
            {
              name: 'PORTFOLIO_TENANT_ID'
              value: tenantId
            }
            {
              name: 'PORTFOLIO_TENANT_NAME'
              value: tenantName
            }
            {
              name: 'AZURE_STORAGE_ACCOUNT_URL'
              value: 'https://${storage.name}.blob.${environment().suffixes.storage}'
            }
            {
              name: 'AZURE_CLIENT_ID'
              value: jobIdentity.properties.clientId
            }
            {
              name: 'AZURE_RAW_CONTAINER'
              value: 'raw-statements'
            }
            {
              name: 'AZURE_REPORT_CONTAINER'
              value: 'analytics-reports'
            }
            {
              name: 'TIMEZONE'
              value: 'Asia/Hong_Kong'
            }
            {
              name: 'IBKR_RUN_MIGRATIONS'
              value: 'false'
            }
            {
              name: 'FLEX_MIN_SEND_INTERVAL_SECONDS'
              value: '10'
            }
            {
              name: 'FLEX_TRANSIENT_COOLDOWN_SECONDS'
              value: '1800'
            }
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

resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: '${jobName}-alerts'
  location: 'global'
  tags: tags
  properties: {
    groupShortName: 'ibkrjob'
    enabled: true
    emailReceivers: empty(alertEmail) ? [] : [
      {
        name: 'operations-email'
        emailAddress: alertEmail
        useCommonAlertSchema: true
      }
    ]
  }
}

resource failedExecutionAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: '${jobName}-failed-execution'
  location: 'global'
  tags: tags
  properties: {
    description: 'Alerts when an IBKR daily job execution fails.'
    severity: 1
    enabled: true
    scopes: [
      job.id
    ]
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
              values: [
                'Failed'
              ]
            }
          ]
        }
      ]
    }
    autoMitigate: true
    actions: [
      {
        actionGroupId: actionGroup.id
      }
    ]
  }
}

output jobName string = job.name
output scheduleUtc string = scheduleUtc
output scheduleHongKong string = 'Daily at 16:00 Asia/Hong_Kong (UTC+8)'
