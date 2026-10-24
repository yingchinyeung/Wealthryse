targetScope = 'resourceGroup'

param jobName string = 'ibkrv2stg-onboard-analytics'
param environmentName string = 'ibkrv2stg-env'
param registryName string
param keyVaultName string
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
resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: last(split(identityResourceId, '/'))
}

resource job 'Microsoft.App/jobs@2024-03-01' = {
  name: jobName
  location: resourceGroup().location
  tags: union({
    application: 'ibkr-portfolio-platform'
    environment: 'staging'
    component: 'self-service-onboarding-analytics'
    dataClassification: 'confidential-financial'
  }, { layer: 'apps', hop: 'analytics', owner: 'ying' })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
  properties: {
    environmentId: managedEnvironment.id
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 3600
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
          // The loader login (Medallion step 3): only the loader jobs may write silver.
          keyVaultUrl: '${vault.properties.vaultUri}secrets/database-loader-url'
          identity: identity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'onboarding-analytics'
          image: '${registry.properties.loginServer}/ibkr-platform@${imageDigest}'
          command: ['python']
          args: ['scripts/run_self_service_onboarding_analytics.py']
          env: [
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            { name: 'DEPLOYMENT_ENVIRONMENT', value: 'staging' }
            { name: 'PORTFOLIO_TENANT_ID', value: tenantId }
            { name: 'SECTOR_CLASSIFICATION_WORKERS', value: '4' }
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
