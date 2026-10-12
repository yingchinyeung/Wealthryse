targetScope = 'resourceGroup'

// Retired with owner approval on 11 October 2026. Restore only through an
// explicitly reviewed recovery plan; normal deployments must not recreate it.
param restoreRetiredHistoricalJob bool = false

param jobName string = 'ibkrv2stg-historical-reports'
param environmentName string = 'ibkrv2stg-env'
param registryName string
param keyVaultName string
param storageAccountName string
param identityResourceId string
param imageDigest string
param tenantId string = '00000000-0000-0000-0000-000000000001'
param pilotAccountId string = 'U16088571'
param reportingPeriodStart string = '2025-01-20'
param historicalReportStart string = '2025-01-20'
param historicalReportEnd string = '2026-08-31'
param historicalYahooTransmissionApproved bool = false
param factsheetTemplateId string = 'U16088571-monthly-factsheet'
param factsheetTemplateVersion string = 'sha256:460064f7b7cd0650189e71949f31859e07f1fa7256f266c9138b9b9214ba705d+layout:approved-august-2026-v6'
param presentationTemplateId string = 'U16088571-monthly-presentation'
param presentationTemplateVersion string = 'sha256:4393eda47945c9df17167e8cbe5278afe90f20b16f52bd473ff3e7549a759191+layout:approved-august-2026-20-slide-v24'

var tags = {
  application: 'ibkr-portfolio-platform'
  environment: 'staging'
  owner: 'portfolio-platform'
  costCenter: 'pilot'
  version: '2.0'
  dataClassification: 'confidential-financial'
  dataLicense: 'unlicensed-development-only'
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

resource job 'Microsoft.App/jobs@2024-03-01' = if (restoreRetiredHistoricalJob) {
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
      replicaTimeout: 7200
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
          name: 'historical-reports'
          // Report workers require the locked PDF/PPTX design assets and the
          // Node rendering runtime bundled only in the ibkr-etl image. Keep
          // this repository distinct from the deliberately slim web image.
          image: '${registry.properties.loginServer}/ibkr-etl@${imageDigest}'
          command: ['python']
          args: ['scripts/generate_historical_month_end_batch.py']
          env: [
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            { name: 'DEPLOYMENT_ENVIRONMENT', value: 'staging' }
            { name: 'PORTFOLIO_TENANT_ID', value: tenantId }
            { name: 'PILOT_ACCOUNT_ID', value: pilotAccountId }
            { name: 'REPORTING_PERIOD_START', value: reportingPeriodStart }
            { name: 'HISTORICAL_REPORT_START', value: historicalReportStart }
            { name: 'HISTORICAL_REPORT_END', value: historicalReportEnd }
            { name: 'REPORTING_CURRENCY', value: 'USD' }
            { name: 'ALLOW_UNLICENSED_BENCHMARK', value: 'true' }
            { name: 'YAHOO_HISTORICAL_HOLDINGS_APPROVED', value: historicalYahooTransmissionApproved ? 'true' : 'false' }
            { name: 'FACTSHEET_TEMPLATE_ID', value: factsheetTemplateId }
            { name: 'FACTSHEET_TEMPLATE_VERSION', value: factsheetTemplateVersion }
            { name: 'PRESENTATION_TEMPLATE_ID', value: presentationTemplateId }
            { name: 'PRESENTATION_TEMPLATE_VERSION', value: presentationTemplateVersion }
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

output jobName string = restoreRetiredHistoricalJob ? job!.name : ''
