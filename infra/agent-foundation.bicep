targetScope = 'resourceGroup'

// WealthRyse agents, part 1: model resource, private endpoint, DNS and identities.
// Nothing here is deployed without Ying's typed approval naming each resource.
// The private endpoint and DNS zones sit in ibkrv2stg-vnet, which the private
// network thread owns: deploy this after that thread's current change and with
// its agreement. That thread owns the privatelink zones; pass their IDs in
// privateDnsZoneIds (createPrivateDns = true is only for a standalone setup).

@description('Region for identities and the private endpoint (the VNet region).')
param location string = resourceGroup().location

@description('Region of the model resource. Southeast Asia with APAC Data Zone processing is the approved boundary.')
@allowed(['southeastasia'])
param aiLocation string = 'southeastasia'

@description('Globally unique name and subdomain of the Foundry resource.')
param foundryName string

param projectName string = 'wealthryse-agents'
param vnetName string = 'ibkrv2stg-vnet'

@description('Existing subnet in the VNet that holds private endpoints.')
param privateEndpointSubnetName string

@description('Create the three private DNS zones and link them to the VNet. Leave false when the network owner provides them.')
param createPrivateDns bool = false

@description('Resource IDs of the existing privatelink zones for cognitiveservices, openai and services.ai, in that order. Leave empty to create the endpoint without a zone group; the zone owner then adds the records.')
param privateDnsZoneIds array = []

@description('Main analyst model (pinned version, Data Zone Standard).')
param modelName string = 'gpt-5.6-sol'
param modelVersion string = '2026-07-09'
param modelDeploymentName string = 'wr-analyst'
@minValue(1)
@maxValue(200)
param modelCapacity int = 20

@description('Optional small model used to screen out-of-scope questions. Leave empty to skip.')
param routerModelName string = ''
param routerModelVersion string = ''
param routerDeploymentName string = 'wr-router'
param routerCapacity int = 20

var tags = {
  application: 'wealthryse-agents'
  environment: 'staging'
  owner: 'portfolio-platform'
  dataClassification: 'client-portfolio-results'
  inferenceRegion: aiLocation
}
var openAiUserRole = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd' // Cognitive Services OpenAI User
var dnsZones = [
  'privatelink.cognitiveservices.azure.com'
  'privatelink.openai.azure.com'
  'privatelink.services.ai.azure.com'
]

resource vnet 'Microsoft.Network/virtualNetworks@2023-11-01' existing = {
  name: vnetName
}
resource peSubnet 'Microsoft.Network/virtualNetworks/subnets@2023-11-01' existing = {
  parent: vnet
  name: privateEndpointSubnetName
}

// Identities: the runtime calls the model and the tool server; the tool server
// reads the database; the jobs call the model and the tool server.
resource agentIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-wr-agent'
  location: location
  tags: tags
}
resource toolsIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-wr-agent-tools'
  location: location
  tags: tags
}
resource jobsIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-wr-agent-jobs'
  location: location
  tags: tags
}

resource foundry 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: foundryName
  location: aiLocation
  tags: tags
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    customSubDomainName: foundryName
    allowProjectManagement: true
    disableLocalAuth: true
    publicNetworkAccess: 'Disabled'
    networkAcls: {
      defaultAction: 'Deny'
      ipRules: []
      virtualNetworkRules: []
    }
  }
}

// The account accepts one change at a time: create the project and the
// endpoint only after the model deployment, when the account has settled.
resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: foundry
  name: projectName
  dependsOn: [analystDeployment]
  location: aiLocation
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    displayName: 'WealthRyse agents'
    description: 'Evaluations and monitoring for the WealthRyse portfolio agents.'
  }
}

resource analystDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: foundry
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

resource routerDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = if (!empty(routerModelName)) {
  parent: foundry
  name: routerDeploymentName
  dependsOn: [project]
  sku: {
    name: 'DataZoneStandard'
    capacity: routerCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: routerModelName
      version: routerModelVersion
    }
    versionUpgradeOption: 'NoAutoUpgrade'
  }
}

// Inference only: no keys, no deployment changes, no administration.
resource agentInference 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundry.id, agentIdentity.id, openAiUserRole)
  scope: foundry
  properties: {
    principalId: agentIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', openAiUserRole)
  }
}
resource jobsInference 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundry.id, jobsIdentity.id, openAiUserRole)
  scope: foundry
  properties: {
    principalId: jobsIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', openAiUserRole)
  }
}

resource privateEndpoint 'Microsoft.Network/privateEndpoints@2023-11-01' = {
  name: '${foundryName}-pe'
  location: location
  dependsOn: [project]
  tags: tags
  properties: {
    subnet: {
      id: peSubnet.id
    }
    privateLinkServiceConnections: [
      {
        name: '${foundryName}-account'
        properties: {
          privateLinkServiceId: foundry.id
          groupIds: ['account']
        }
      }
    ]
  }
}

resource zones 'Microsoft.Network/privateDnsZones@2020-06-01' = [for zone in dnsZones: if (createPrivateDns) {
  name: zone
  location: 'global'
  tags: tags
}]

resource zoneLinks 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = [for (zone, i) in dnsZones: if (createPrivateDns) {
  parent: zones[i]
  name: '${vnetName}-link'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnet.id
    }
  }
}]

resource zoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = if (createPrivateDns || !empty(privateDnsZoneIds)) {
  parent: privateEndpoint
  name: 'default'
  dependsOn: [zones]
  properties: {
    privateDnsZoneConfigs: [for (zone, i) in dnsZones: {
      name: replace(zone, '.', '-')
      properties: {
        privateDnsZoneId: createPrivateDns ? resourceId('Microsoft.Network/privateDnsZones', zone) : privateDnsZoneIds[?i]
      }
    }]
  }
}

output foundryEndpoint string = 'https://${foundryName}.openai.azure.com'
output analystDeployment string = analystDeployment.name
output routerDeployment string = empty(routerModelName) ? '' : routerDeploymentName
output agentIdentityId string = agentIdentity.id
output agentPrincipalId string = agentIdentity.properties.principalId
output toolsIdentityId string = toolsIdentity.id
output toolsPrincipalId string = toolsIdentity.properties.principalId
output jobsIdentityId string = jobsIdentity.id
output jobsPrincipalId string = jobsIdentity.properties.principalId
