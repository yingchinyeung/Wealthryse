targetScope = 'resourceGroup'

param appName string = 'ibkrv2stg-credential-broker'
param environmentName string = 'ibkrv2stg-env'
param registryName string
param keyVaultName string
param imageTag string
@secure()
param brokerAuth string

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: environmentName
}
resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: registryName
}
resource vault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${appName}-identity'
  location: resourceGroup().location
}

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, identity.id, 'acrpull')
  scope: registry
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
  }
}

resource secretWriterRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(resourceGroup().id, 'ibkr-flex-secret-set-only')
  properties: {
    roleName: 'IBKR Flex Secret Set Only'
    description: 'Can create or replace secrets but cannot read, list, delete or recover them.'
    type: 'CustomRole'
    assignableScopes: [resourceGroup().id]
    permissions: [
      {
        actions: []
        notActions: []
        dataActions: ['Microsoft.KeyVault/vaults/secrets/setSecret/action']
        notDataActions: []
      }
    ]
  }
}

resource secretWriterAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault.id, identity.id, secretWriterRole.id)
  scope: vault
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: secretWriterRole.id
  }
}

resource broker 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: resourceGroup().location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {'${identity.id}': {}}
  }
  properties: {
    environmentId: environment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: false
        targetPort: 8000
        allowInsecure: false
        transport: 'auto'
      }
      registries: [
        {
          server: registry.properties.loginServer
          identity: identity.id
        }
      ]
      secrets: [{name: 'broker-auth', value: brokerAuth}]
    }
    template: {
      containers: [
        {
          name: 'broker'
          image: '${registry.properties.loginServer}/ibkr-platform:${imageTag}'
          command: ['python']
          args: ['-m', 'uvicorn', 'ibkr_etl.credential_broker:app', '--host', '0.0.0.0', '--port', '8000', '--no-access-log']
          env: [
            {name: 'KEY_VAULT_URI', value: vault.properties.vaultUri}
            {name: 'AZURE_CLIENT_ID', value: identity.properties.clientId}
            {name: 'BROKER_AUTH', secretRef: 'broker-auth'}
          ]
          resources: {cpu: json('0.25'), memory: '0.5Gi'}
        }
      ]
      scale: {minReplicas: 0, maxReplicas: 1}
    }
  }
  dependsOn: [acrPull, secretWriterAssignment]
}

output fqdn string = broker.properties.configuration.ingress.fqdn
output identityId string = identity.id
