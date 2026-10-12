targetScope = 'resourceGroup'

param productionStorageAccountName string = 'ibkrv115rcc62xxqblay'
param productionExportContainerName string = 'analytics-reports'
param identityResourceId string

resource productionStorage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: productionStorageAccountName
}
resource productionBlobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' existing = {
  parent: productionStorage
  name: 'default'
}
resource productionExportContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = {
  parent: productionBlobService
  name: productionExportContainerName
}
resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: last(split(identityResourceId, '/'))
}

var storageBlobReaderRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
)

resource productionExportReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(productionExportContainer.id, identity.id, storageBlobReaderRoleId)
  scope: productionExportContainer
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: storageBlobReaderRoleId
  }
}

output scope string = productionExportContainer.id
output principalId string = identity.properties.principalId
