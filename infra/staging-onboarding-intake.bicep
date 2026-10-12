targetScope = 'resourceGroup'

param storageAccountName string = 'ibkrv2stgauhecwc4itaoo'
param webIdentityName string = 'ibkrv2stg-web-identity'
param intakeContainerName string = 'onboarding-intake'

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' existing = {
  parent: storage
  name: 'default'
}

resource intake 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: intakeContainerName
  properties: {
    publicAccess: 'None'
  }
}

resource webIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: webIdentityName
}

var storageBlobDataContributorRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
)

// The web identity may write only to this private intake container. It does not
// receive write access to released reports or any IBKR source-data container.
resource webIntakeContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(intake.id, webIdentity.id, storageBlobDataContributorRoleId)
  scope: intake
  properties: {
    principalId: webIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: storageBlobDataContributorRoleId
  }
}

output containerName string = intake.name
output containerScope string = intake.id
output principalId string = webIdentity.properties.principalId
