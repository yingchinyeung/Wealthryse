// Private additive containers only. Do not change shared account networking,
// retention, identities or production schedules in this foundation deployment.
targetScope = 'resourceGroup'
param storageAccountName string

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}
resource blobs 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' existing = {
  parent: storage
  name: 'default'
}
resource containers 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = [for name in ['release-sources', 'release-evidence']: {
  parent: blobs
  name: name
  properties: {
    publicAccess: 'None'
    metadata: {
      purpose: 'azure-native-release-foundation'
      deploymentAuthority: 'none'
    }
  }
}]
