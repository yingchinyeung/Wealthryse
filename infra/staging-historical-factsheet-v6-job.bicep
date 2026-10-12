targetScope = 'resourceGroup'

param jobName string = 'ibkrv2stg-factsheet-v6-hist'
param environmentName string = 'ibkrv2stg-env'
param registryName string = 'ibkrv2stgauhecwc4itaoo'
param keyVaultName string = 'ibkrv2stg-auhecwc4itaoo'
param identityResourceId string
param imageDigest string
param storageAccountName string = 'ibkrv2stgauhecwc4itaoo'
param tenantId string = '00000000-0000-0000-0000-000000000001'
param approvedBy string = 'anthony.ying@wealthryse.ai'
param upgradeMode string
param baseTemplateSha256 string = '460064f7b7cd0650189e71949f31859e07f1fa7256f266c9138b9b9214ba705d'
param factsheetTemplateVersion string = 'sha256:460064f7b7cd0650189e71949f31859e07f1fa7256f266c9138b9b9214ba705d+layout:approved-august-2026-v6'

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
    purpose: 'owner-approved-historical-factsheet-v6-upgrade'
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
          name: 'historical-factsheet-v6'
          image: '${registry.properties.loginServer}/ibkr-portfolio@${imageDigest}'
          command: ['python']
          args: ['scripts/upgrade_historical_factsheets_v6.py']
          env: [
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            { name: 'DEPLOYMENT_ENVIRONMENT', value: 'staging' }
            { name: 'UPGRADE_MODE', value: upgradeMode }
            { name: 'PORTFOLIO_TENANT_ID', value: tenantId }
            { name: 'REPORT_REPLACEMENT_APPROVED_BY', value: approvedBy }
            { name: 'FACTSHEET_BASE_TEMPLATE_SHA256', value: baseTemplateSha256 }
            { name: 'FACTSHEET_TEMPLATE_VERSION', value: factsheetTemplateVersion }
            { name: 'QA_MANIFEST_BLOB', value: 'qa-v6/U16088571/historical-factsheet-v6-manifest.json' }
            { name: 'AZURE_STORAGE_ACCOUNT_URL', value: 'https://${storageAccountName}.blob.${az.environment().suffixes.storage}' }
            { name: 'INTERNAL_REPORT_CONTAINER', value: 'internal-reports' }
            { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId }
          ]
          resources: {
            cpu: json('1.0')
            memory: '2Gi'
          }
        }
      ]
    }
  }
}

output jobName string = job.name
