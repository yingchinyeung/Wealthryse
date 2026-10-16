targetScope = 'resourceGroup'

// Dedicated worker for owner-requested report design updates (migration 062).
// It is separate from the month-end scheduler so a long historical batch never
// blocks normal monthly generation. Deploy disabled first; enable only after the
// staging checks in REPORT_DESIGN_UPDATES_2026-10-07.md have passed.

param jobName string = 'ibkrv2stg-report-design-worker'
param environmentName string = 'ibkrv2stg-env'
param registryName string
param keyVaultName string
param storageAccountName string
param identityResourceId string
@description('Digest of the qualified ibkr-etl reporting image, for example sha256:abc...')
param imageDigest string
param openAiAccountName string
param tenantId string = '00000000-0000-0000-0000-000000000001'
param presentationTemplateSha256 string = '4393eda47945c9df17167e8cbe5278afe90f20b16f52bd473ff3e7549a759191'
param modelDeploymentName string = 'gpt-5-6-sol-apac'
@description('Keep false until the scoped staging acceptance has passed.')
param regenerationEnabled bool = false

var rendererImageSha256 = replace(imageDigest, 'sha256:', '')

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
resource openAi 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: openAiAccountName
}
resource jobIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: last(split(identityResourceId, '/'))
}

resource job 'Microsoft.App/jobs@2024-03-01' = {
  name: jobName
  location: resourceGroup().location
  tags: {
    application: 'ibkr-portfolio-platform'
    environment: 'internal-production'
    owner: 'portfolio-platform'
    purpose: 'report-design-regeneration'
    dataClassification: 'confidential-financial'
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
      triggerType: 'Schedule'
      // Each month renders under a 20-minute ceiling and renews a 30-minute
      // lease, so an interrupted batch is resumed by a later execution.
      replicaTimeout: 7200
      replicaRetryLimit: 0
      scheduleTriggerConfig: {
        cronExpression: '*/30 * * * *'
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
          name: 'report-design-worker'
          image: '${registry.properties.loginServer}/ibkr-etl@${imageDigest}'
          command: ['python']
          args: ['scripts/run_report_design_updates.py']
          env: [
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            { name: 'DEPLOYMENT_ENVIRONMENT', value: 'internal-production' }
            { name: 'PORTFOLIO_TENANT_ID', value: tenantId }
            { name: 'REPORT_DESIGN_UPDATES_ENABLED', value: regenerationEnabled ? 'true' : 'false' }
            { name: 'REPORT_RENDERER_IMAGE_SHA256', value: rendererImageSha256 }
            { name: 'REPORTING_PERIOD_START', value: '2025-01-20' }
            { name: 'AZURE_STORAGE_ACCOUNT_URL', value: 'https://${storage.name}.blob.${environment().suffixes.storage}' }
            { name: 'INTERNAL_REPORT_CONTAINER', value: 'internal-reports' }
            { name: 'PRESENTATION_TEMPLATE_PATH', value: '/app/templates/U16088571/approved-august-2026-20-slide-v24.pptx' }
            { name: 'PRESENTATION_TEMPLATE_SHA256', value: presentationTemplateSha256 }
            { name: 'AZURE_OPENAI_ENDPOINT', value: openAi.properties.endpoint }
            { name: 'AZURE_OPENAI_DEPLOYMENT', value: modelDeploymentName }
            { name: 'AI_RESOURCE_REGION', value: 'southeastasia' }
            { name: 'AI_PROCESSING_SCOPE', value: 'apac-data-zone' }
            { name: 'AZURE_CLIENT_ID', value: jobIdentity.properties.clientId }
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
output regenerationEnabled bool = regenerationEnabled
