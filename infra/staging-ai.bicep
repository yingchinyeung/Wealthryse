targetScope = 'resourceGroup'

@description('Globally unique Azure OpenAI account name for the isolated staging analyst.')
param accountName string

@description('Approved inference resource region. Do not change without explicit data-region approval.')
param location string = 'southeastasia'

@description('Object ID of the staging web managed identity.')
param webPrincipalId string

@description('Approved APAC Data Zone model deployment name.')
param modelDeploymentName string = 'gpt-5-6-sol-apac'

@description('Explicitly approved model and pinned version.')
param modelName string = 'gpt-5.6-sol'
param modelVersion string = '2026-07-09'

@minValue(1)
@maxValue(100)
@description('Pay-as-you-go Data Zone capacity in thousands of tokens per minute.')
param modelCapacity int = 10

var tags = {
  application: 'ibkr-portfolio-platform'
  environment: 'staging'
  owner: 'portfolio-platform'
  costCenter: 'pilot'
  version: '2.0'
  dataClassification: 'approved-structured-portfolio-results-only'
  approvedSubject: 'U16088571'
  inferenceRegion: location
}

resource account 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: accountName
  location: location
  tags: tags
  kind: 'OpenAI'
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: accountName
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
    }
  }
}

// Inference only: the website can call deployed models but cannot create or
// modify deployments, retrieve account keys, or administer the resource.
resource webInferenceRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, webPrincipalId, 'cognitive-services-openai-user')
  scope: account
  properties: {
    principalId: webPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
    )
  }
}

resource modelDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: account
  name: modelDeploymentName
  sku: {
    name: 'DataZoneStandard'
    capacity: modelCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: modelName
      version: modelVersion
    }
    versionUpgradeOption: 'NoAutoUpgrade'
  }
}

output accountId string = account.id
output accountName string = account.name
output endpoint string = account.properties.endpoint
output disableLocalAuth bool = account.properties.disableLocalAuth
output location string = account.location
output modelDeploymentName string = modelDeployment.name
output modelName string = modelDeployment.properties.model.name
output modelVersion string = modelDeployment.properties.model.version
output modelSku string = modelDeployment.sku.name
