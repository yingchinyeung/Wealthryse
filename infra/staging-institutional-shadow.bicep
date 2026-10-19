targetScope = 'resourceGroup'

param jobName string = 'ibkrv2stg-institutional-shadow'
param registryName string
param keyVaultName string
param diagnosticsIdentityName string = 'ibkrv2stg-diagnostics-identity'
param environmentName string = 'ibkrv2stg-env'
param approvalDatabaseSecretName string = 'database-approval-url'
param imageDigest string
param authoritativeJobManifestJson string
param portfolioTenantId string = '00000000-0000-0000-0000-000000000001'

var tags = {
  application: 'ibkr-portfolio-platform'
  environment: 'internal-production'
  owner: 'portfolio-platform'
  costCenter: 'pilot'
  version: '2.0'
  purpose: 'institutional-shadow-reconciliation'
}

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: environmentName
}
resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: registryName
}
resource vault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}
resource diagnosticsIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: diagnosticsIdentityName
}
resource databaseSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' existing = {
  parent: vault
  name: approvalDatabaseSecretName
}

resource shadowJob 'Microsoft.App/jobs@2024-03-01' = {
  name: jobName
  location: resourceGroup().location
  tags: union(tags, { layer: 'apps', component: 'daily-pipeline', hop: 'ops' })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${diagnosticsIdentity.id}': {}
    }
  }
  properties: {
    environmentId: environment.id
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 300
      replicaRetryLimit: 0
      manualTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
      }
      registries: [
        {
          server: registry.properties.loginServer
          identity: diagnosticsIdentity.id
        }
      ]
      secrets: [
        {
          name: 'database-url'
          keyVaultUrl: databaseSecret.properties.secretUriWithVersion
          identity: diagnosticsIdentity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'institutional-shadow'
          image: '${registry.properties.loginServer}/ibkr-platform@${imageDigest}'
          command: ['python']
          args: ['scripts/reconcile_institutional_shadow.py']
          env: [
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            { name: 'PORTFOLIO_TENANT_ID', value: portfolioTenantId }
            { name: 'DEPLOYMENT_ENVIRONMENT', value: 'internal-production' }
            { name: 'AUTHORITATIVE_JOB_MANIFEST_JSON', value: authoritativeJobManifestJson }
          ]
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
        }
      ]
    }
  }
}

output jobName string = shadowJob.name
