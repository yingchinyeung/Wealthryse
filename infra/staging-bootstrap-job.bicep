targetScope = 'resourceGroup'

param jobName string = 'ibkrv2stg-bootstrap'
param environmentName string = 'ibkrv2stg-env'
param registryName string
param keyVaultName string
param identityResourceId string
param imageTag string
param tenantId string = '00000000-0000-0000-0000-000000000001'
param tenantName string = 'WealthRyse staging pilot'

var tags = {
  application: 'ibkr-portfolio-platform'
  environment: 'staging'
  owner: 'portfolio-platform'
  costCenter: 'pilot'
  version: '2.0'
  dataClassification: 'confidential-financial'
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
  tags: union(tags, { layer: 'ops', component: 'database-admin', hop: 'ops' })
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
      replicaTimeout: 1800
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
          name: 'database-admin-url'
          keyVaultUrl: '${vault.properties.vaultUri}secrets/database-admin-url'
          identity: jobIdentity.id
        }
        {
          name: 'database-app-password'
          keyVaultUrl: '${vault.properties.vaultUri}secrets/database-app-password'
          identity: jobIdentity.id
        }
        {
          name: 'database-web-password'
          keyVaultUrl: '${vault.properties.vaultUri}secrets/database-web-password'
          identity: jobIdentity.id
        }
        {
          name: 'database-approval-password'
          keyVaultUrl: '${vault.properties.vaultUri}secrets/database-approval-password'
          identity: jobIdentity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'postgres-bootstrap'
          image: '${registry.properties.loginServer}/ibkr-platform:${imageTag}'
          command: ['python']
          args: ['scripts/bootstrap_postgres.py']
          env: [
            { name: 'DATABASE_ADMIN_URL', secretRef: 'database-admin-url' }
            { name: 'DATABASE_APP_PASSWORD', secretRef: 'database-app-password' }
            { name: 'DATABASE_APP_ROLE', value: 'ibkr_stg_app' }
            { name: 'DATABASE_WEB_PASSWORD', secretRef: 'database-web-password' }
            { name: 'DATABASE_WEB_ROLE', value: 'ibkr_stg_web' }
            { name: 'DATABASE_APPROVAL_PASSWORD', secretRef: 'database-approval-password' }
            { name: 'DATABASE_APPROVAL_ROLE', value: 'ibkr_stg_approval' }
            { name: 'PORTFOLIO_TENANT_ID', value: tenantId }
            { name: 'PORTFOLIO_TENANT_NAME', value: tenantName }
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
