targetScope = 'resourceGroup'

param jobName string = 'ibkrv2stg-verify-u16088571'
param environmentName string = 'ibkrv2stg-env'
param registryName string
param keyVaultName string
param identityResourceId string
param imageTag string
param tenantId string = '00000000-0000-0000-0000-000000000001'
param pilotAccountId string = 'U16088571'

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
          name: 'database-url'
          keyVaultUrl: '${vault.properties.vaultUri}secrets/database-app-url'
          identity: jobIdentity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'pilot-verify'
          image: '${registry.properties.loginServer}/ibkr-platform:${imageTag}'
          command: ['python']
          args: ['scripts/verify_pilot_staging.py']
          env: [
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            { name: 'PORTFOLIO_TENANT_ID', value: tenantId }
            { name: 'PILOT_ACCOUNT_ID', value: pilotAccountId }
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

output jobName string = job.name
