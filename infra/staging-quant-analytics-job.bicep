targetScope = 'resourceGroup'

param jobName string = 'ibkrv2stg-quant-analytics'
param environmentName string = 'ibkrv2stg-env'
param registryName string
param keyVaultName string
// The same identity as the risk-snapshot job: it reads the database-app-url secret.
param identityResourceId string
param imageDigest string
param tenantId string = '00000000-0000-0000-0000-000000000001'
// The build does nothing unless this is on (docs/QUANT_ANALYTICS.md). Leave it off until the
// held migration (076) has run and one subject has been built by hand and checked.
@allowed(['off', 'on'])
param quantAnalyticsBuild string = 'off'
// Lineage records each run and stamps the rows it writes (U4).
@allowed(['false', 'true'])
param lineageRecording string = 'true'

var tags = {
  application: 'ibkr-portfolio-platform'
  environment: 'internal-production'
  owner: 'portfolio-platform'
  costCenter: 'pilot'
  version: '2.0'
  dataClassification: 'confidential-financial'
  workload: 'quant-analytics-snapshots'
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
resource jobIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: last(split(identityResourceId, '/'))
}

resource job 'Microsoft.App/jobs@2024-03-01' = {
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
      triggerType: 'Schedule'
      // Ten minutes at most, so a run started at 09:30 is over by 09:40, before the second
      // certified-figures run at 09:45. The build itself stops starting new subject-dates after
      // QUANT_ANALYTICS_MINUTES (8) and reports what it skipped; the next run picks them up.
      replicaTimeout: 600
      replicaRetryLimit: 0
      scheduleTriggerConfig: {
        // 09:30 UTC, after the risk-snapshot (08:55) and crisis (09:20) steps.
        cronExpression: '30 9 * * *'
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
          name: 'quant-analytics'
          image: '${registry.properties.loginServer}/ibkr-platform@${imageDigest}'
          command: ['python']
          args: ['scripts/build_quant_analytics.py']
          env: [
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            { name: 'PORTFOLIO_TENANT_ID', value: tenantId }
            { name: 'DEPLOYMENT_ENVIRONMENT', value: 'internal-production' }
            { name: 'AZURE_CLIENT_ID', value: jobIdentity.properties.clientId }
            { name: 'QUANT_ANALYTICS_BUILD', value: quantAnalyticsBuild }
            { name: 'QUANT_ANALYTICS_MINUTES', value: '8' }
            { name: 'LINEAGE_RECORDING', value: lineageRecording }
            // The register and contracts, as the image carries them.
            { name: 'MEDALLION_DIR', value: '/app/medallion' }
            // The app login cannot create tables, so the job must not try migrations.
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
output scheduleUtc string = '09:30 UTC daily'
