targetScope = 'resourceGroup'

param jobName string = 'ibkrv2stg-derived-daily'
param environmentName string = 'ibkrv2stg-env'
param registryName string
param keyVaultName string
param storageAccountName string
// 'database' (since the switch on 17 Oct 2026) builds reference_data.json from v2's own
// instrument_classifications and risk_free_rates tables. 'blob' reads a published file
// from reportContainer only: v1.1's analytics-reports copy is no longer read.
@allowed(['blob', 'database'])
param referenceDataSource string = 'database'
param identityResourceId string
param imageDigest string
param tenantId string = '00000000-0000-0000-0000-000000000001'
param reportContainer string = 'ibkr-reports'

var tags = {
  application: 'ibkr-portfolio-platform'
  environment: 'internal-production'
  owner: 'portfolio-platform'
  costCenter: 'pilot'
  version: '2.0'
  dataClassification: 'confidential-financial'
  sourceScope: 'self-service-derived-portfolios'
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
resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: last(split(identityResourceId, '/'))
}

resource job 'Microsoft.App/jobs@2024-03-01' = {
  name: jobName
  location: resourceGroup().location
  tags: union(tags, { layer: 'apps', component: 'daily-pipeline', hop: 'conform' })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
  properties: {
    environmentId: managedEnvironment.id
    configuration: {
      triggerType: 'Schedule'
      replicaTimeout: 3600
      replicaRetryLimit: 0
      scheduleTriggerConfig: {
        // 16:50 HKT, after physical-account, benchmark and sector refreshes,
        // and before month-end generation and analyst assurance.
        cronExpression: '50 8 * * *'
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
          // The loader login (Medallion step 3): only the loader jobs may write silver.
          keyVaultUrl: '${vault.properties.vaultUri}secrets/database-loader-url'
          identity: identity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'derived-refresh'
          image: '${registry.properties.loginServer}/ibkr-platform@${imageDigest}'
          command: ['python']
          args: ['scripts/run_self_service_derived_refresh.py']
          env: [
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            { name: 'DEPLOYMENT_ENVIRONMENT', value: 'internal-production' }
            { name: 'PORTFOLIO_TENANT_ID', value: tenantId }
            { name: 'AZURE_STORAGE_ACCOUNT_URL', value: 'https://${storageAccountName}.blob.${environment().suffixes.storage}' }
            { name: 'AZURE_REPORT_CONTAINER', value: reportContainer }
            { name: 'REFERENCE_DATA_SOURCE', value: referenceDataSource }
            { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId }
            { name: 'ALLOW_UNLICENSED_BENCHMARK', value: 'true' }
            { name: 'IBKR_RUN_MIGRATIONS', value: 'false' }
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
output scheduleHongKong string = '16:50 HKT daily'
