targetScope = 'resourceGroup'

@description('Short lowercase prefix used in Azure resource names.')
@minLength(3)
@maxLength(12)
param prefix string = 'ibkrv11'

param location string = resourceGroup().location
param administratorLogin string = 'ibkradmin'

@secure()
param administratorLoginPassword string

@secure()
param applicationLoginPassword string

param applicationLogin string = 'ibkr_app'

@secure()
param ibkrFlexToken string

@secure()
param ibkrFlexQueryId string

param databaseName string = 'ibkr'
param postgresSkuName string = 'Standard_B1ms'
param postgresSkuTier string = 'Burstable'
param postgresVersion string = '16'
param tags object = {
  application: 'ibkr-portfolio-database'
  version: '1.1'
  dataClassification: 'confidential-financial'
}

var suffix = uniqueString(subscription().subscriptionId, resourceGroup().id, prefix)
var compactPrefix = toLower(replace(prefix, '-', ''))
var workspaceName = '${prefix}-logs'
var environmentName = '${prefix}-env'
var registryName = take('${compactPrefix}${suffix}', 50)
var storageName = take('${compactPrefix}${suffix}', 24)
var vaultName = take('${prefix}-${suffix}', 24)
var identityName = '${prefix}-job-identity'
var postgresName = take('${prefix}-pg-${suffix}', 63)
var vnetName = '${prefix}-vnet'
var privateDnsZoneName = 'privatelink.postgres.database.azure.com'

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: workspaceName
  location: location
  tags: tags
  properties: {
    retentionInDays: 30
    features: {
      disableLocalAuth: false
      enableLogAccessUsingOnlyResourcePermissions: true
    }
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
    sku: {
      name: 'PerGB2018'
    }
  }
}

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: vnetName
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [
        '10.40.0.0/16'
      ]
    }
    subnets: [
      {
        name: 'container-apps'
        properties: {
          addressPrefix: '10.40.0.0/23'
          delegations: [
            {
              name: 'container-apps-delegation'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
        }
      }
      {
        name: 'postgres'
        properties: {
          addressPrefix: '10.40.4.0/28'
          delegations: [
            {
              name: 'postgres-delegation'
              properties: {
                serviceName: 'Microsoft.DBforPostgreSQL/flexibleServers'
              }
            }
          ]
        }
      }
    ]
  }
}

resource privateDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: privateDnsZoneName
  location: 'global'
  tags: tags
}

resource privateDnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: privateDnsZone
  name: '${prefix}-dns-link'
  location: 'global'
  tags: tags
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnet.id
    }
  }
}

resource managedEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: environmentName
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: workspace.properties.customerId
        sharedKey: workspace.listKeys().primarySharedKey
      }
    }
    vnetConfiguration: {
      infrastructureSubnetId: resourceId('Microsoft.Network/virtualNetworks/subnets', vnet.name, 'container-apps')
      internal: false
    }
    zoneRedundant: false
  }
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: registryName
  location: location
  tags: tags
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: 'Enabled'
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  tags: tags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    defaultToOAuthAuthentication: true
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    publicNetworkAccess: 'Enabled'
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    isVersioningEnabled: true
    deleteRetentionPolicy: {
      enabled: true
      days: 30
    }
    containerDeleteRetentionPolicy: {
      enabled: true
      days: 30
    }
  }
}

resource storageLifecycle 'Microsoft.Storage/storageAccounts/managementPolicies@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    policy: {
      rules: [
        {
          enabled: true
          name: 'delete-previous-versions-after-30-days'
          type: 'Lifecycle'
          definition: {
            actions: {
              version: {
                delete: {
                  daysAfterCreationGreaterThan: 30
                }
              }
            }
            filters: {
              blobTypes: [
                'blockBlob'
              ]
            }
          }
        }
      ]
    }
  }
}

resource rawContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'raw-statements'
  properties: {
    publicAccess: 'None'
  }
}

resource reportContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'analytics-reports'
  properties: {
    publicAccess: 'None'
  }
}

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: vaultName
  location: location
  tags: tags
  properties: {
    tenantId: tenant().tenantId
    enableRbacAuthorization: true
    enablePurgeProtection: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 90
    publicNetworkAccess: 'Enabled'
    sku: {
      family: 'A'
      name: 'standard'
    }
  }
}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: location
  tags: tags
}

resource postgres 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = {
  name: postgresName
  location: location
  tags: tags
  sku: {
    name: postgresSkuName
    tier: postgresSkuTier
  }
  properties: {
    administratorLogin: administratorLogin
    administratorLoginPassword: administratorLoginPassword
    version: postgresVersion
    authConfig: {
      activeDirectoryAuth: 'Disabled'
      passwordAuth: 'Enabled'
    }
    backup: {
      backupRetentionDays: 14
      geoRedundantBackup: 'Disabled'
    }
    highAvailability: {
      mode: 'Disabled'
    }
    network: {
      delegatedSubnetResourceId: resourceId('Microsoft.Network/virtualNetworks/subnets', vnet.name, 'postgres')
      privateDnsZoneArmResourceId: privateDnsZone.id
      publicNetworkAccess: 'Disabled'
    }
    storage: {
      autoGrow: 'Enabled'
      storageSizeGB: 32
    }
  }
  dependsOn: [
    privateDnsLink
  ]
}

resource database 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-08-01' = {
  parent: postgres
  name: databaseName
  properties: {
    charset: 'UTF8'
    collation: 'en_US.utf8'
  }
}

var acrPullRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
var keyVaultSecretsUserRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
var storageBlobContributorRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, identity.id, acrPullRoleId)
  scope: registry
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPullRoleId
  }
}

resource secretReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault.id, identity.id, keyVaultSecretsUserRoleId)
  scope: vault
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: keyVaultSecretsUserRoleId
  }
}

resource blobContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, identity.id, storageBlobContributorRoleId)
  scope: storage
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: storageBlobContributorRoleId
  }
}

resource databaseUrlSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: vault
  name: 'database-url'
  properties: {
    value: 'postgresql://${applicationLogin}:${uriComponent(applicationLoginPassword)}@${postgres.properties.fullyQualifiedDomainName}:5432/${databaseName}?sslmode=require'
  }
}

resource databaseAdminUrlSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: vault
  name: 'database-admin-url'
  properties: {
    value: 'postgresql://${administratorLogin}:${uriComponent(administratorLoginPassword)}@${postgres.properties.fullyQualifiedDomainName}:5432/${databaseName}?sslmode=require'
  }
}

resource databaseAppPasswordSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: vault
  name: 'database-app-password'
  properties: {
    value: applicationLoginPassword
  }
}

resource flexTokenSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: vault
  name: 'ibkr-flex-token'
  properties: {
    value: ibkrFlexToken
  }
}

resource flexQueryIdSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: vault
  name: 'ibkr-flex-query-id'
  properties: {
    value: ibkrFlexQueryId
  }
}

output registryName string = registry.name
output registryLoginServer string = registry.properties.loginServer
output storageAccountName string = storage.name
output storageAccountUrl string = 'https://${storage.name}.blob.${environment().suffixes.storage}'
output keyVaultName string = vault.name
output identityResourceId string = identity.id
output environmentName string = managedEnvironment.name
output postgresServerName string = postgres.name
output databaseName string = database.name
