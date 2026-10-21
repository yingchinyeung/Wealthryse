targetScope = 'resourceGroup'

param jobName string = 'ibkrv2stg-template-intake'
param environmentName string = 'ibkrv2stg-env'
param registryName string = 'ibkrv2stgauhecwc4itaoo'
param keyVaultName string = 'ibkrv2stg-auhecwc4itaoo'
param identityResourceId string
param imageDigest string
param storageAccountName string = 'ibkrv2stgauhecwc4itaoo'
param tenantId string = '00000000-0000-0000-0000-000000000001'
param subjectId string
param manifestBlob string
param expectedManifestSha256 string

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
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
    purpose: 'verified-account-template-intake'
  }, { layer: 'apps', component: 'onboarding', hop: 'conform', owner: 'ying' })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
  properties: {
    environmentId: environment.id
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 900
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
          keyVaultUrl: '${vault.properties.vaultUri}secrets/database-app-url'
          identity: identity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'template-intake'
          image: '${registry.properties.loginServer}/ibkr-platform@${imageDigest}'
          command: ['python']
          args: ['scripts/register_account_template_intake.py']
          env: [
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            { name: 'DEPLOYMENT_ENVIRONMENT', value: 'staging' }
            { name: 'PORTFOLIO_TENANT_ID', value: tenantId }
            { name: 'TEMPLATE_SUBJECT_ID', value: subjectId }
            { name: 'AZURE_STORAGE_ACCOUNT_URL', value: 'https://${storageAccountName}.blob.${az.environment().suffixes.storage}' }
            { name: 'INTERNAL_REPORT_CONTAINER', value: 'internal-reports' }
            { name: 'TEMPLATE_MANIFEST_BLOB', value: manifestBlob }
            { name: 'EXPECTED_TEMPLATE_MANIFEST_SHA256', value: expectedManifestSha256 }
            { name: 'TEMPLATE_BUNDLE_DIR', value: '/app/templates/${subjectId}/intake' }
            { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId }
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
