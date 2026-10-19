targetScope = 'resourceGroup'

param jobName string = 'ibkrv2stg-month-end-factsheet'
param environmentName string = 'ibkrv2stg-env'
param registryName string
param keyVaultName string
param storageAccountName string
param identityResourceId string
param imageDigest string
param openAiAccountName string
param tenantId string = '00000000-0000-0000-0000-000000000001'
param pilotAccountId string = 'U16088571'
param factsheetTemplateBlob string = 'templates/U16088571/monthly-factsheet/approved-august-2026-v2.pdf'
param factsheetTemplateSha256 string = '460064f7b7cd0650189e71949f31859e07f1fa7256f266c9138b9b9214ba705d'
param presentationTemplateSha256 string = '4393eda47945c9df17167e8cbe5278afe90f20b16f52bd473ff3e7549a759191'
param modelDeploymentName string = 'gpt-5-6-sol-apac'
param createOpenAiRoleAssignment bool = true

var tags = {
  application: 'ibkr-portfolio-platform'
  environment: 'internal-production'
  owner: 'portfolio-platform'
  costCenter: 'pilot'
  version: '2.0'
  dataClassification: 'confidential-financial'
  reportLayout: 'approved-august-2026-v6'
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
resource openAi 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: openAiAccountName
}
resource jobIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: last(split(identityResourceId, '/'))
}

resource jobAiInferenceRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (createOpenAiRoleAssignment) {
  name: guid(openAi.id, identityResourceId, 'month-end-openai-user')
  scope: openAi
  properties: {
    principalId: jobIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
    )
  }
}

resource job 'Microsoft.App/jobs@2024-03-01' = {
  name: jobName
  location: resourceGroup().location
  tags: union(tags, { layer: 'apps', component: 'reporting', hop: 'analytics+publish' })
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
      replicaTimeout: 3600
      replicaRetryLimit: 0
      scheduleTriggerConfig: {
        // 17:00 HKT every day. A late Flex month-end import or a newly
        // activated subject must still be picked up after the seventh.
        cronExpression: '0 9 * * *'
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
          name: 'month-end-package'
          // Report workers require the locked PDF/PPTX design assets and the
          // Node rendering runtime bundled only in the ibkr-etl image. Keep
          // this repository distinct from the deliberately slim web image.
          image: '${registry.properties.loginServer}/ibkr-etl@${imageDigest}'
          command: ['python']
          args: ['scripts/run_registry_month_end_packages.py']
          env: [
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            { name: 'DEPLOYMENT_ENVIRONMENT', value: 'internal-production' }
            { name: 'IBKR_RUN_MIGRATIONS', value: 'false' }
            { name: 'PORTFOLIO_TENANT_ID', value: tenantId }
            { name: 'PILOT_ACCOUNT_ID', value: pilotAccountId }
            { name: 'REPORTING_PERIOD_START', value: '2025-01-20' }
            { name: 'AZURE_STORAGE_ACCOUNT_URL', value: 'https://${storage.name}.blob.${environment().suffixes.storage}' }
            { name: 'INTERNAL_REPORT_CONTAINER', value: 'internal-reports' }
            { name: 'FACTSHEET_TEMPLATE_BLOB', value: factsheetTemplateBlob }
            { name: 'FACTSHEET_TEMPLATE_SHA256', value: factsheetTemplateSha256 }
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
output scheduleHongKong string = '17:00 HKT on days 1-7 of each month'
output factsheetLayoutVersion string = 'approved-august-2026-v6'
output presentationLayoutVersion string = 'approved-august-2026-20-slide-v24'
output modelRole string = 'gpt-5.6-sol portfolio analyst'
