targetScope = 'resourceGroup'

param jobName string = 'ibkrv2stg-certified-figures'
param environmentName string = 'ibkrv2stg-env'
param registryName string
param keyVaultName string
param identityResourceId string
param imageDigest string
param tenantId string = '00000000-0000-0000-0000-000000000001'
// The build does nothing unless this is on (docs/MEDALLION_CERTIFIED_FIGURES.md).
@allowed(['off', 'on'])
param certifiedFiguresBuild string = 'off'
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
  dataLicense: 'yfinance-internal-only'
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
      // The one-off --backfill (a manual start with the args changed) reads every NAV date.
      replicaTimeout: 3600
      replicaRetryLimit: 0
      scheduleTriggerConfig: {
        // Twice a day: 09:05 UTC, after the derived (08:50) and risk-snapshot (08:55) steps, for
        // the NAV, holdings, cash and statistics figures; 09:45 UTC, after the crisis step (09:20),
        // for the risk and benchmark figures. Each run builds only what has come in.
        cronExpression: '5,45 9 * * *'
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
          name: 'certified-figures'
          image: '${registry.properties.loginServer}/ibkr-platform@${imageDigest}'
          command: ['python']
          args: ['scripts/build_certified_figures.py']
          env: [
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            { name: 'PORTFOLIO_TENANT_ID', value: tenantId }
            { name: 'DEPLOYMENT_ENVIRONMENT', value: 'internal-production' }
            { name: 'CERTIFIED_FIGURES_BUILD', value: certifiedFiguresBuild }
            { name: 'LINEAGE_RECORDING', value: lineageRecording }
            // The build calls the website's page code, so the settings that change what a page
            // shows must match the website's (crisis scenarios are shown there).
            { name: 'CRISIS_SCENARIOS_ENABLED', value: 'true' }
            // The register and contracts, as the image carries them.
            { name: 'MEDALLION_DIR', value: '/app/medallion' }
            // The app login cannot create tables, so the job must not try migrations.
            { name: 'IBKR_RUN_MIGRATIONS', value: 'false' }
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
