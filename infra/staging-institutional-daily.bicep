targetScope = 'resourceGroup'

param jobName string = 'ibkrv2stg-institutional-daily'
param environmentName string = 'ibkrv2stg-env'
param registryName string = 'ibkrv2stgauhecwc4itaoo'
param applicationVaultName string = 'ibkrv2stg-auhecwc4itaoo'
param flexTokenVaultName string
param storageAccountName string = 'ibkrv2stgauhecwc4itaoo'
param identityResourceId string
param imageDigest string
param tenantId string = '00000000-0000-0000-0000-000000000001'

var tags = {
  application: 'ibkr-portfolio-platform'
  environment: 'internal-production'
  component: 'institutional-flex-daily'
  dataClassification: 'restricted-credential'
}

resource managedEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: environmentName
}
resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: registryName
}
resource applicationVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: applicationVaultName
}
resource databaseSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' existing = {
  parent: applicationVault
  name: 'database-app-url'
}
resource tokenVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: flexTokenVaultName
}
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}
resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: last(split(identityResourceId, '/'))
}

resource blobContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  // Reuse the exact assignment managed by the discovery worker because both
  // jobs intentionally share the same Flex identity and raw archive scope.
  name: guid(storage.id, identity.id, 'flex-discovery-raw-writer')
  scope: storage
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
  }
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
      replicaTimeout: 3000
      replicaRetryLimit: 0
      scheduleTriggerConfig: {
        // One fail-closed statement generation per active Flex query at 16:00 HKT.
        cronExpression: '0 8 * * *'
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
          keyVaultUrl: databaseSecret.properties.secretUri
          identity: identity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'institutional-daily'
          image: '${registry.properties.loginServer}/ibkr-platform@${imageDigest}'
          command: ['python']
          args: ['scripts/run_institutional_daily_pipeline.py']
          env: [
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            { name: 'DEPLOYMENT_ENVIRONMENT', value: 'internal-production' }
            { name: 'PORTFOLIO_TENANT_ID', value: tenantId }
            { name: 'FLEX_TOKEN_VAULT_URI', value: tokenVault.properties.vaultUri }
            { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId }
            { name: 'AZURE_STORAGE_ACCOUNT_URL', value: 'https://${storageAccountName}.blob.${environment().suffixes.storage}' }
            { name: 'AZURE_RAW_CONTAINER', value: 'ibkr-raw' }
            { name: 'INSTITUTIONAL_FLEX_LOOKBACK_DAYS', value: '14' }
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
    }
  }
  dependsOn: [blobContributor]
}

output jobName string = job.name
output scheduleHongKong string = 'Daily at 16:00 Asia/Hong_Kong (UTC+8)'
