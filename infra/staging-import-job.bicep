targetScope = 'resourceGroup'

param jobName string = 'ibkrv2stg-import-u16088571'
param environmentName string = 'ibkrv2stg-env'
param registryName string
param storageAccountName string
param keyVaultName string
param identityResourceId string
param imageTag string
param snapshotBlobName string
param tenantId string = '00000000-0000-0000-0000-000000000001'
param pilotAccountId string = 'U16088571'
param pilotStartDate string = '2025-01-20'

var tags = {
  application: 'ibkr-portfolio-platform'
  environment: 'staging'
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
      triggerType: 'Manual'
      replicaTimeout: 3600
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
          keyVaultUrl: '${vault.properties.vaultUri}secrets/database-app-url'
          identity: jobIdentity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'pilot-import'
          image: '${registry.properties.loginServer}/ibkr-platform:${imageTag}'
          command: ['python']
          args: ['scripts/import_pilot_snapshot.py']
          env: [
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            { name: 'PORTFOLIO_TENANT_ID', value: tenantId }
            { name: 'PILOT_ACCOUNT_ID', value: pilotAccountId }
            { name: 'PILOT_START_DATE', value: pilotStartDate }
            { name: 'AZURE_STORAGE_ACCOUNT_URL', value: 'https://${storage.name}.blob.${environment().suffixes.storage}' }
            { name: 'AZURE_CLIENT_ID', value: jobIdentity.properties.clientId }
            { name: 'PILOT_SNAPSHOT_CONTAINER', value: 'input-snapshots' }
            { name: 'PILOT_SNAPSHOT_BLOB', value: snapshotBlobName }
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
