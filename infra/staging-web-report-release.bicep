targetScope = 'resourceGroup'

param storageAccountName string = 'ibkrv2stgauhecwc4itaoo'
param webIdentityName string = 'ibkrv2stg-web-identity'
param internalReportContainer string = 'internal-reports'

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' existing = {
  parent: storage
  name: 'default'
}

resource reports 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = {
  parent: blobService
  name: internalReportContainer
}

resource webIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: webIdentityName
}

var storageBlobDataContributorRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
)

resource webReportsReleaseWriter 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(reports.id, webIdentity.id, storageBlobDataContributorRoleId)
  scope: reports
  properties: {
    principalId: webIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: storageBlobDataContributorRoleId
  }
}

output containerScope string = reports.id
output principalId string = webIdentity.properties.principalId
