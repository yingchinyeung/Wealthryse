targetScope = 'resourceGroup'

param storageAccountName string = 'ibkrv2stgauhecwc4itaoo'
param publisherIdentityName string = 'ibkrv2stg-publisher-identity'

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' existing = {
  parent: storage
  name: 'default'
}

resource internalContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'internal-reports'
  properties: {
    publicAccess: 'None'
  }
}

resource publisherIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: publisherIdentityName
}

var storageBlobContributorRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
)

// The name is the assignment made on 18 Oct 2026 (medallion S4a), so a redeploy updates it
// instead of failing on a duplicate.
resource publisherInternalReportsContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: '852bd9ae-29b3-44b4-9266-abee52b3bf58'
  scope: internalContainer
  properties: {
    principalId: publisherIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: storageBlobContributorRoleId
  }
}

output internalReportContainer string = internalContainer.name
