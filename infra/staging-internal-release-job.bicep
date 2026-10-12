targetScope = 'resourceGroup'

param jobName string = 'ibkrv2stg-internal-release'
param environmentName string = 'ibkrv2stg-env'
param registryName string = 'ibkrv2stgauhecwc4itaoo'
param keyVaultName string = 'ibkrv2stg-auhecwc4itaoo'
param identityResourceId string
param imageDigest string
param storageAccountName string = 'ibkrv2stgauhecwc4itaoo'
param tenantId string = '00000000-0000-0000-0000-000000000001'
param snapshotId string = '751c2b5a-c9cd-54fb-808e-ff98edbd8d67'
param approvedBy string = 'anthony.ying@wealthryse.ai'

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
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
  tags: {
    application: 'ibkr-portfolio-platform'
    environment: 'staging'
    releaseChannel: 'internal-production'
  }
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
  properties: {
    environmentId: environment.id
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
          name: 'internal-release'
          image: '${registry.properties.loginServer}/ibkr-platform@${imageDigest}'
          command: ['python']
          args: ['scripts/release_internal_reports.py']
          env: [
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            { name: 'DEPLOYMENT_ENVIRONMENT', value: 'staging' }
            { name: 'PORTFOLIO_TENANT_ID', value: tenantId }
            { name: 'REPORTING_SNAPSHOT_ID', value: snapshotId }
            { name: 'INTERNAL_RELEASE_APPROVED_BY', value: approvedBy }
            { name: 'AZURE_STORAGE_ACCOUNT_URL', value: 'https://${storageAccountName}.blob.core.windows.net' }
            { name: 'REPORT_DRAFT_CONTAINER', value: 'report-drafts' }
            { name: 'INTERNAL_REPORT_CONTAINER', value: 'internal-reports' }
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
