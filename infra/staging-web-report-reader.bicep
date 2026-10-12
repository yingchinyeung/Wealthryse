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

var storageBlobDataReaderRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
)

resource webReportsReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(reports.id, webIdentity.id, storageBlobDataReaderRoleId)
  scope: reports
  properties: {
    principalId: webIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: storageBlobDataReaderRoleId
  }
}

output containerScope string = reports.id
output principalId string = webIdentity.properties.principalId
