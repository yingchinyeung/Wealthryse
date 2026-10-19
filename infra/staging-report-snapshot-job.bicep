targetScope = 'resourceGroup'

param jobName string = 'ibkrv2stg-report-snapshot'
param environmentName string = 'ibkrv2stg-env'
param registryName string
param keyVaultName string
param storageAccountName string
param identityResourceId string
param imageDigest string
param tenantId string = '00000000-0000-0000-0000-000000000001'
param pilotAccountId string = 'U16088571'
param reportingPeriodStart string = '2025-01-20'
param reportingPeriodEnd string

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
resource vault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}
resource jobIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: last(split(identityResourceId, '/'))
}

resource job 'Microsoft.App/jobs@2024-03-01' = {
  name: jobName
  location: resourceGroup().location
  tags: union(tags, { layer: 'apps', component: 'reporting', hop: 'analytics' })
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
      replicaTimeout: 1800
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
          name: 'report-snapshot'
          image: '${registry.properties.loginServer}/ibkr-platform@${imageDigest}'
          command: ['python']
          args: ['scripts/freeze_month_end_snapshot.py']
          env: [
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            { name: 'DEPLOYMENT_ENVIRONMENT', value: 'staging' }
            { name: 'PORTFOLIO_TENANT_ID', value: tenantId }
            { name: 'PILOT_ACCOUNT_ID', value: pilotAccountId }
            { name: 'REPORTING_PERIOD_START', value: reportingPeriodStart }
            { name: 'REPORTING_PERIOD_END', value: reportingPeriodEnd }
            { name: 'REPORTING_CURRENCY', value: 'USD' }
            { name: 'AZURE_CLIENT_ID', value: jobIdentity.properties.clientId }
            { name: 'AZURE_STORAGE_ACCOUNT_URL', value: 'https://${storage.name}.blob.${environment().suffixes.storage}' }
            { name: 'REPORT_DRAFT_CONTAINER', value: 'report-drafts' }
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
