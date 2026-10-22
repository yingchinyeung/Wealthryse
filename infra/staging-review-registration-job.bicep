targetScope = 'resourceGroup'

param jobName string = 'ibkrv2stg-review-registration'
param environmentName string = 'ibkrv2stg-env'
param registryName string = 'ibkrv2stgauhecwc4itaoo'
param keyVaultName string = 'ibkrv2stg-auhecwc4itaoo'
param identityResourceId string
param imageDigest string
param storageAccountName string = 'ibkrv2stgauhecwc4itaoo'
param tenantId string = '00000000-0000-0000-0000-000000000001'
param pilotAccountId string = 'U16088571'
param reviewManifestBlob string = 'review/U16088571/historical-batch-manifest.json'
param factsheetTemplateVersion string = 'sha256:460064f7b7cd0650189e71949f31859e07f1fa7256f266c9138b9b9214ba705d+layout:approved-august-2026-v6'
param presentationTemplateVersion string = 'sha256:4393eda47945c9df17167e8cbe5278afe90f20b16f52bd473ff3e7549a759191+layout:approved-august-2026-20-slide-v24'

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
  tags: union({
    application: 'ibkr-portfolio-platform'
    environment: 'staging'
    purpose: 'historical-report-owner-review'
  }, { layer: 'apps', component: 'onboarding', hop: 'publish', owner: 'ying' })
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
          name: 'review-registration'
          image: '${registry.properties.loginServer}/ibkr-platform@${imageDigest}'
          command: ['python']
          args: ['scripts/register_historical_review_batch.py']
          env: [
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            { name: 'DEPLOYMENT_ENVIRONMENT', value: 'staging' }
            { name: 'PORTFOLIO_TENANT_ID', value: tenantId }
            { name: 'PILOT_ACCOUNT_ID', value: pilotAccountId }
            { name: 'AZURE_STORAGE_ACCOUNT_URL', value: 'https://${storageAccountName}.blob.${az.environment().suffixes.storage}' }
            { name: 'INTERNAL_REPORT_CONTAINER', value: 'internal-reports' }
            { name: 'REVIEW_MANIFEST_BLOB', value: reviewManifestBlob }
            { name: 'FACTSHEET_TEMPLATE_VERSION', value: factsheetTemplateVersion }
            { name: 'PRESENTATION_TEMPLATE_VERSION', value: presentationTemplateVersion }
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
