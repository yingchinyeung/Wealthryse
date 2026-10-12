targetScope = 'resourceGroup'

param jobName string = 'ibkr-v11-history-backfill'
param environmentName string
param registryName string
param storageAccountName string
param keyVaultName string
param identityResourceId string
param imageTag string
param tenantId string = '00000000-0000-0000-0000-000000000001'
param tenantName string = 'Own Portfolio'
param historyFrom string = '2025-01-20'

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

resource historyJob 'Microsoft.App/jobs@2024-03-01' = {
  name: jobName
  location: resourceGroup().location
  tags: {
    application: 'ibkr-portfolio-database'
    version: '1.1'
    purpose: 'one-time-exact-daily-position-backfill'
  }
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${jobIdentity.id}': {}
    }
  }
  properties: {
    environmentId: managedEnvironment.id
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 21600
      replicaRetryLimit: 0
      manualTriggerConfig: {
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
          name: 'ibkr-history-backfill'
          image: '${registry.properties.loginServer}/ibkr-etl:${imageTag}'
          command: [
            'python'
          ]
          args: [
            'scripts/run_cloud_historical_backfill.py'
            '--from'
            historyFrom
          ]
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

output jobName string = historyJob.name
output historyFrom string = historyFrom
