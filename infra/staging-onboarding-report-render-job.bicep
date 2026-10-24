targetScope = 'resourceGroup'

param jobName string = 'ibkrv2stg-onboard-render'
param environmentName string = 'ibkrv2stg-env'
param registryName string
param keyVaultName string
param storageAccountName string
param identityResourceId string
param imageDigest string
param tenantId string = '00000000-0000-0000-0000-000000000001'

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
  tags: union({
    application: 'ibkr-portfolio-platform'
    environment: 'staging'
    owner: 'portfolio-platform'
    costCenter: 'pilot'
    dataClassification: 'confidential-financial'
  }, { layer: 'apps', component: 'onboarding', hop: 'publish' })
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
          // The loader login (Medallion step 3): only the loader jobs may write silver.
          keyVaultUrl: '${vault.properties.vaultUri}secrets/database-loader-url'
          identity: jobIdentity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'onboarding-report-render'
          // Report workers require the locked PDF/PPTX design assets and the
          // Node rendering runtime bundled only in the ibkr-etl image. Keep
          // this repository distinct from the deliberately slim web image.
          image: '${registry.properties.loginServer}/ibkr-etl@${imageDigest}'
          command: ['python']
          args: ['scripts/run_self_service_onboarding_reports.py']
          env: [
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            { name: 'DEPLOYMENT_ENVIRONMENT', value: 'staging' }
            { name: 'PORTFOLIO_TENANT_ID', value: tenantId }
            { name: 'AZURE_CLIENT_ID', value: jobIdentity.properties.clientId }
            { name: 'AZURE_STORAGE_ACCOUNT_URL', value: 'https://${storage.name}.blob.${environment().suffixes.storage}' }
            { name: 'INTERNAL_REPORT_CONTAINER', value: 'internal-reports' }
            { name: 'PLATFORM_PRESENTATION_TEMPLATE_PATH', value: '/app/templates/U16088571/approved-august-2026-20-slide-v24.pptx' }
            { name: 'PLATFORM_PRESENTATION_TEMPLATE_SHA256', value: '4393eda47945c9df17167e8cbe5278afe90f20b16f52bd473ff3e7549a759191' }
            { name: 'NODE_BINARY', value: 'node' }
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
