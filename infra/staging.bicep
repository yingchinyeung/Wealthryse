targetScope = 'resourceGroup'

@description('Short lowercase prefix used in staging Azure resource names.')
@minLength(3)
@maxLength(12)
param prefix string = 'ibkrv2stg'

param location string = resourceGroup().location
param administratorLogin string = 'ibkrstgadmin'

@secure()
param administratorLoginPassword string

param applicationLogin string = 'ibkr_stg_app'

@secure()
param applicationLoginPassword string

param databaseName string = 'ibkr_staging'
param postgresSkuName string = 'Standard_B1ms'
param postgresSkuTier string = 'Burstable'
param postgresVersion string = '16'

param tags object = {
  application: 'ibkr-portfolio-platform'
  environment: 'staging'
  owner: 'portfolio-platform'
  costCenter: 'pilot'
  version: '2.0'
  dataClassification: 'confidential-financial'
}

var suffix = uniqueString(subscription().subscriptionId, resourceGroup().id, prefix)
var compactPrefix = toLower(replace(prefix, '-', ''))
var workspaceName = '${prefix}-logs'
var environmentName = '${prefix}-env'
var registryName = take('${compactPrefix}${suffix}', 50)
var storageName = take('${compactPrefix}${suffix}', 24)
var vaultName = take('${prefix}-${suffix}', 24)
var postgresName = take('${prefix}-pg-${suffix}', 63)
var vnetName = '${prefix}-vnet'
var privateDnsZoneName = '${prefix}.postgres.database.azure.com'

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: workspaceName
  location: location
  tags: union(tags, { layer: 'foundation', component: 'logging' })
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
  tags: union(tags, { layer: 'foundation', component: 'network' })
  properties: {
    addressSpace: {
      addressPrefixes: [
        '10.50.0.0/16'
      ]
    }
    subnets: [
      {
        name: 'container-apps'
        properties: {
          addressPrefix: '10.50.0.0/23'
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
          addressPrefix: '10.50.4.0/28'
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
  tags: union(tags, { layer: 'foundation', component: 'network' })
}

resource privateDnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: privateDnsZone
  name: '${prefix}-dns-link'
  location: 'global'
  tags: union(tags, { layer: 'foundation', component: 'network' })
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
  tags: union(tags, { layer: 'foundation', component: 'container-apps' })
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
  tags: union(tags, { layer: 'foundation', component: 'registry' })
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
  tags: union(tags, { layer: 'data', component: 'storage', dataLayer: 'bronze+gold+ops' })
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

resource inputContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'input-snapshots'
  properties: {
    publicAccess: 'None'
  }
}

resource draftContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'report-drafts'
  properties: {
    publicAccess: 'None'
  }
}

resource approvedContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'approved-reports'
  properties: {
    publicAccess: 'None'
  }
}

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: vaultName
  location: location
  tags: union(tags, { layer: 'foundation', component: 'secrets' })
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

resource webIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-web-identity'
  location: location
  tags: union(tags, { layer: 'foundation', component: 'identity' })
}

resource ingestionIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-ingestion-identity'
  location: location
  tags: union(tags, { layer: 'foundation', component: 'identity' })
}

resource publisherIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-publisher-identity'
  location: location
  tags: union(tags, { layer: 'foundation', component: 'identity' })
}

resource diagnosticsIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-diagnostics-identity'
  location: location
  tags: union(tags, { layer: 'foundation', component: 'identity' })
}

resource approvedActionsIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-approved-actions-identity'
  location: location
  tags: union(tags, { layer: 'foundation', component: 'identity' })
}

resource postgres 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = {
  name: postgresName
  location: location
  tags: union(tags, { layer: 'data', component: 'database', dataLayer: 'bronze+silver+gold+ops' })
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
var storageBlobReaderRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1')
var storageBlobContributorRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')

resource webAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, webIdentity.id, acrPullRoleId)
  scope: registry
  properties: {
    principalId: webIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPullRoleId
  }
}

resource ingestionAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, ingestionIdentity.id, acrPullRoleId)
  scope: registry
  properties: {
    principalId: ingestionIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPullRoleId
  }
}

resource publisherAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, publisherIdentity.id, acrPullRoleId)
  scope: registry
  properties: {
    principalId: publisherIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPullRoleId
  }
}

resource webSecretReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault.id, webIdentity.id, keyVaultSecretsUserRoleId)
  scope: vault
  properties: {
    principalId: webIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: keyVaultSecretsUserRoleId
  }
}

resource ingestionSecretReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault.id, ingestionIdentity.id, keyVaultSecretsUserRoleId)
  scope: vault
  properties: {
    principalId: ingestionIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: keyVaultSecretsUserRoleId
  }
}

resource publisherSecretReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault.id, publisherIdentity.id, keyVaultSecretsUserRoleId)
  scope: vault
  properties: {
    principalId: publisherIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: keyVaultSecretsUserRoleId
  }
}

// The web identity's account-wide Storage Blob Data Reader came off on 18 Oct 2026; it reads
// only through its container roles (staging-web-report-reader.bicep, staging-onboarding-intake.bicep).

// The ingestion and publisher identities' account-wide Storage Blob Data Contributor came off on
// 21 Oct 2026 (medallion S4a storage narrowing); each keeps only the container roles below and in
// staging-internal-release.bicep. A deployment leaves an assignment it no longer declares in place, so
// the removal was a separate step (storage_narrow.py), and its undo puts both back under their earlier
// names, guid(storage.id, <identity>.id, storageBlobContributorRoleId).

// Containers made outside this file (onboarding-intake by staging-onboarding-intake.bicep,
// bronze-market-data by the medallion S2 step), referenced for the container roles below.
resource onboardingIntakeContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = {
  parent: blobService
  name: 'onboarding-intake'
}
resource bronzeMarketDataContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = {
  parent: blobService
  name: 'bronze-market-data'
}
// Made by staging-internal-release.bicep, which also gives the publisher identity its role there.
resource internalReportsContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = {
  parent: blobService
  name: 'internal-reports'
}
// Made by staging-flex-discovery.bicep; the Flex identity writes the raw statements there.
resource rawStatementsContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = {
  parent: blobService
  name: 'ibkr-raw'
}

// Container-scope storage roles (medallion S4a). Each name is the assignment made on
// 18 Oct 2026, so a redeploy updates it instead of failing on a duplicate.
resource publisherDraftsContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: '1650cae9-0811-4165-bdba-24be733c4a08'
  scope: draftContainer
  properties: {
    principalId: publisherIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: storageBlobContributorRoleId
  }
}

resource publisherIntakeReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: '8b7c44bf-33fd-4a1d-a66c-1a9a8621dee9'
  scope: onboardingIntakeContainer
  properties: {
    principalId: publisherIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: storageBlobReaderRoleId
  }
}

resource ingestionInputContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: 'e49deab7-7272-4de6-995d-6e7e54fa48a6'
  scope: inputContainer
  properties: {
    principalId: ingestionIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: storageBlobContributorRoleId
  }
}

// The bootstrap job reads the raw statements as the ingestion identity: the corrections history
// backfill and check, the FX step and the rehearsals on restored copies. Read only; the container is
// under legal hold. The name is the assignment the narrowing step made.
resource ingestionRawReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: 'f2cbbd24-51eb-4cf6-b159-3e8ec4d55785'
  scope: rawStatementsContainer
  properties: {
    principalId: ingestionIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: storageBlobReaderRoleId
  }
}

// The onboarding render job (as the ingestion identity) writes rendered factsheets to internal-reports
// and reads the onboarding uploads. The names are the assignments the narrowing step made.
resource ingestionInternalReportsContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: 'f2ab1682-51fe-44d9-a209-9426919e0f40'
  scope: internalReportsContainer
  properties: {
    principalId: ingestionIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: storageBlobContributorRoleId
  }
}

resource ingestionIntakeReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: '943026b5-f7a1-4087-b4a7-68803110ede1'
  scope: onboardingIntakeContainer
  properties: {
    principalId: ingestionIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: storageBlobReaderRoleId
  }
}

// The four market-data jobs write their raw copies here as the ingestion identity.
resource ingestionBronzeMarketDataContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: 'ce61af0c-490d-4e11-8956-8f4b08b376a6'
  scope: bronzeMarketDataContainer
  properties: {
    principalId: ingestionIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: storageBlobContributorRoleId
  }
}

// For the supervisor's bronze-market-data file checks (still off, see staging-autonomous-admin.bicep).
resource diagnosticsBronzeMarketDataReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: '9f644dc9-64c7-4ae0-90db-f760b639048b'
  scope: bronzeMarketDataContainer
  properties: {
    principalId: diagnosticsIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: storageBlobReaderRoleId
  }
}

// Delete locks placed on 18 Oct 2026 (medallion S4a). A lock blocks deletes, not changes, but
// removing a storage role needs the storage lock lifted briefly. No lock on the key vault yet:
// role removal there was refused under a lock, so it waits for the credential work (R11, R12).
resource postgresDeleteLock 'Microsoft.Authorization/locks@2020-05-01' = {
  name: 'medallion-no-delete'
  scope: postgres
  properties: {
    level: 'CanNotDelete'
    notes: 'Medallion S4a: delete only with Ying approval'
  }
}

resource storageDeleteLock 'Microsoft.Authorization/locks@2020-05-01' = {
  name: 'medallion-no-delete'
  scope: storage
  properties: {
    level: 'CanNotDelete'
    notes: 'Medallion S4a: delete only with Ying approval; lift briefly to remove a storage role'
  }
}

resource databaseAdminUrlSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: vault
  name: 'database-admin-url'
  properties: {
    value: 'postgresql://${administratorLogin}:${uriComponent(administratorLoginPassword)}@${postgres.properties.fullyQualifiedDomainName}:5432/${databaseName}?sslmode=require'
  }
}

resource databaseAppUrlSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: vault
  name: 'database-app-url'
  properties: {
    value: 'postgresql://${applicationLogin}:${uriComponent(applicationLoginPassword)}@${postgres.properties.fullyQualifiedDomainName}:5432/${databaseName}?sslmode=require'
  }
}

resource databaseAppPasswordSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: vault
  name: 'database-app-password'
  properties: {
    value: applicationLoginPassword
  }
}

output registryName string = registry.name
output registryLoginServer string = registry.properties.loginServer
output storageAccountName string = storage.name
output storageAccountUrl string = 'https://${storage.name}.blob.${environment().suffixes.storage}'
output keyVaultName string = vault.name
output environmentName string = managedEnvironment.name
output postgresServerName string = postgres.name
output databaseName string = database.name
output webIdentityResourceId string = webIdentity.id
output ingestionIdentityResourceId string = ingestionIdentity.id
output publisherIdentityResourceId string = publisherIdentity.id
output diagnosticsIdentityResourceId string = diagnosticsIdentity.id
output approvedActionsIdentityResourceId string = approvedActionsIdentity.id
