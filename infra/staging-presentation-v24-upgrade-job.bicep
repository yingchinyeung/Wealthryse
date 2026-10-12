targetScope = 'resourceGroup'

param jobName string = 'ibkrv2stg-ppt-v24-upgrade'
param environmentName string = 'ibkrv2stg-env'
param registryName string = 'ibkrv2stgauhecwc4itaoo'
param keyVaultName string = 'ibkrv2stg-auhecwc4itaoo'
param identityResourceId string
param imageDigest string
param storageAccountName string = 'ibkrv2stgauhecwc4itaoo'
param tenantId string = '00000000-0000-0000-0000-000000000001'
param approvedBy string = 'anthony.ying@wealthryse.ai'
param expectedPresentationSha256 string = '4393eda47945c9df17167e8cbe5278afe90f20b16f52bd473ff3e7549a759191'
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
  tags: {
    application: 'ibkr-portfolio-platform'
    environment: 'staging'
    purpose: 'one-time-approved-august-presentation-v24-upgrade'
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
      replicaTimeout: 900
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
          name: 'presentation-v24-upgrade'
          image: '${registry.properties.loginServer}/ibkr-portfolio@${imageDigest}'
          command: ['python']
          args: ['scripts/upgrade_august_presentation_v24.py']
          env: [
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            { name: 'DEPLOYMENT_ENVIRONMENT', value: 'staging' }
            { name: 'PORTFOLIO_TENANT_ID', value: tenantId }
            { name: 'PILOT_ACCOUNT_ID', value: 'U16088571' }
            { name: 'REPORT_PERIOD_END', value: '2026-08-31' }
            { name: 'REPORT_REPLACEMENT_APPROVED_BY', value: approvedBy }
            { name: 'EXPECTED_PRESENTATION_SHA256', value: expectedPresentationSha256 }
            { name: 'PRESENTATION_TEMPLATE_VERSION', value: presentationTemplateVersion }
            { name: 'AZURE_STORAGE_ACCOUNT_URL', value: 'https://${storageAccountName}.blob.${az.environment().suffixes.storage}' }
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
