targetScope = 'resourceGroup'

// The research zone (infra/medallion/catalog.json: layer and hop "research"; docs/RESEARCH_ZONE.md).
// Deployed on its own into rg-ibkr-database-v11; it changes no existing resource:
//   - its own small Postgres Flexible Server, stopped when idle, never the live server;
//   - the containers bronze-vendor-data, research-features and research-experiments as child
//     resources of the existing storage account (the account itself is not redeployed);
//   - its own user-assigned identity, with roles on single containers, single secrets, the
//     registry and this one server only;
//   - the job ibkrv2stg-research (manual unless a schedule is given) and a nightly idle stop.
// Nothing here grants the web, agent or any other existing identity anything, and nothing
// touches the storage roles Medallion narrows on Thu 22 Oct.
//
// Network choice (written down as the task asks): the research server joins the live server's
// pattern exactly. It is deployed into the same delegated subnet ('postgres', 10.50.4.0/28; a
// /28 has 11 usable addresses and the live server, without HA, uses one) and registers in the
// same private DNS zone, which is already linked to the VNet the Container Apps environment
// runs in. Public network access is off. A separate subnet would mean redeploying the VNet
// that staging.bicep declares with inline subnets, so that is left to a parameter change
// (postgresSubnetName) if the shared subnet ever runs short.
//
// Two deployments: without imageDigest it creates the server, containers, identity and roles;
// with imageDigest (after the image is built and the roles are in) it adds the jobs. The jobs'
// Key Vault references need the identity's secret roles to have taken effect first.

@description('Location of the research resources (the resource group location).')
param location string = resourceGroup().location

// Existing shared resources, referenced only.
param environmentName string = 'ibkrv2stg-env'
param registryName string = 'ibkrv2stgauhecwc4itaoo'
param storageAccountName string = 'ibkrv2stgauhecwc4itaoo'
param keyVaultName string = 'ibkrv2stg-auhecwc4itaoo'
param vnetName string = 'ibkrv2stg-vnet'
@description('Delegated subnet for Postgres Flexible Servers; shared with the live server (see the network choice above).')
param postgresSubnetName string = 'postgres'
param privateDnsZoneName string = 'ibkrv2stg.postgres.database.azure.com'

// The research server.
param serverName string = 'ibkrv2stg-research-pg'
param administratorLogin string = 'researchadmin'
@secure()
@description('From Key Vault only (secret research-db-admin-password); the switch-on helper reads it there and passes it in.')
param administratorLoginPassword string
param databaseName string = 'research'
@description('Burstable size; Standard_B1ms (1 vCore, 2 GiB) by default.')
@allowed(['Standard_B1ms', 'Standard_B2s', 'Standard_B2ms'])
param postgresSkuName string = 'Standard_B1ms'
param postgresVersion string = '16'
@minValue(32)
param storageSizeGB int = 32
@description('Minimal backups: 7 days, no geo-redundant copy.')
@minValue(7)
@maxValue(35)
param backupRetentionDays int = 7
@description('Password sign-in as on the live server. Entra sign-in can be added later; password sign-in stays on.')
@allowed(['Disabled', 'Enabled'])
param entraAuthentication string = 'Disabled'

// The identity and its secrets (created before this deployment by the switch-on helper).
param identityName string = 'ibkrv2stg-research-identity'
param researchDatabaseUrlSecretName string = 'research-db-url'
param liveReaderUrlSecretName string = 'research-live-reader-url'
@description('Key Vault secrets for each data vendor (plan.md); the identity may read exactly these. Each must exist first.')
param vendorSecretNames array = []

// The jobs.
param jobName string = 'ibkrv2stg-research'
param idleStopJobName string = 'ibkrv2stg-research-idle-stop'
@description('The research image digest (sha256:...). Empty: the jobs are not deployed yet.')
param imageDigest string = ''
param imageRepository string = 'ibkr-research'
@description('The source commit the image was built from, recorded with every trial.')
param codeVersion string = 'unknown'
param tenantId string = '00000000-0000-0000-0000-000000000001'
@description('More than the daily jobs (they use 0.5 to 1 vCPU and 1 to 2 GiB).')
param cpu string = '2.0'
param memory string = '4Gi'
@description('Time limit of one run, in seconds; also how long a run marked running keeps the server on.')
@minValue(600)
@maxValue(28800)
param replicaTimeoutSeconds int = 14400
@description('UTC hour of a scheduled run, or -1 for manual runs only (the default). Never inside 07:50 to 09:25 UTC.')
@minValue(-1)
@maxValue(23)
param scheduleHourUtc int = -1
@minValue(0)
@maxValue(59)
param scheduleMinuteUtc int = 0
@description('Cron day-of-week field of a scheduled run, for example 6 for Saturdays.')
param scheduleDaysOfWeek string = '6'
@description('What a scheduled run does (a manual run gives its own arguments with az containerapp job start --args).')
param jobArgs array = ['check']
@description('UTC hour of the nightly idle stop, or -1 for none. Azure starts a stopped server again after 7 days; this stops it.')
@minValue(-1)
@maxValue(23)
param idleStopHourUtc int = 21

var tags = {
  application: 'ibkr-portfolio-platform'
  environment: 'internal-production'
  owner: 'portfolio-platform'
  costCenter: 'pilot'
  version: '2.0'
  dataClassification: 'confidential-financial'
  layer: 'research'
  hop: 'research'
}

var storageBlobDataReaderRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1')
var storageBlobDataContributorRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
var acrPullRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
var keyVaultSecretsUserRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
var serverStartStopRoleName = guid(resourceGroup().id, 'research-server-start-stop')

// 07:50 to 09:25 UTC is the daily chain's window (minutes 470 to 565 of the day). A schedule
// inside it becomes an invalid cron expression, so the deployment fails instead of scheduling it.
var deployJobs = !empty(imageDigest)
var scheduled = scheduleHourUtc >= 0
var scheduleMinuteOfDay = scheduleHourUtc * 60 + scheduleMinuteUtc
var scheduleInsideWindow = scheduled && scheduleMinuteOfDay >= 470 && scheduleMinuteOfDay <= 565
var cronExpression = scheduleInsideWindow ? 'refused: inside the 07:50 to 09:25 UTC daily window' : '${scheduleMinuteUtc} ${scheduleHourUtc} * * ${scheduleDaysOfWeek}'
var idleStopScheduled = idleStopHourUtc >= 0
var idleStopInsideWindow = idleStopHourUtc == 8 || idleStopHourUtc == 9
var idleStopCron = idleStopInsideWindow ? 'refused: inside the 07:50 to 09:25 UTC daily window' : '15 ${idleStopHourUtc} * * *'

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' existing = {
  name: vnetName
}
resource postgresSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' existing = {
  parent: vnet
  name: postgresSubnetName
}
resource privateDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' existing = {
  name: privateDnsZoneName
}
resource managedEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: environmentName
}
resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: registryName
}
resource vault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}
resource researchDatabaseUrlSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' existing = {
  parent: vault
  name: researchDatabaseUrlSecretName
}
resource liveReaderUrlSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' existing = {
  parent: vault
  name: liveReaderUrlSecretName
}
resource vendorSecrets 'Microsoft.KeyVault/vaults/secrets@2023-07-01' existing = [for name in vendorSecretNames: {
  parent: vault
  name: name
}]
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}
resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' existing = {
  parent: storage
  name: 'default'
}
// Bronze containers the research job reads; they belong to the platform and are not changed.
resource rawStatements 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = {
  parent: blobService
  name: 'ibkr-raw'
}
resource marketData 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = {
  parent: blobService
  name: 'bronze-market-data'
}

// New containers. No immutability or legal hold here: those are set and cleared only by the
// separately authorised administrator, after each vendor's licence check.
resource vendorData 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'bronze-vendor-data'
  properties: {
    publicAccess: 'None'
    metadata: {
      dataLayer: 'bronze'
    }
  }
}
resource researchFeatures 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'research-features'
  properties: {
    publicAccess: 'None'
    metadata: {
      dataLayer: 'research'
    }
  }
}
resource researchExperiments 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'research-experiments'
  properties: {
    publicAccess: 'None'
    metadata: {
      dataLayer: 'research'
    }
  }
}

resource researchIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: location
  tags: union(tags, { component: 'research-identity' })
}

resource researchServer 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = {
  name: serverName
  location: location
  tags: union(tags, { component: 'research-database', dataLayer: 'research' })
  sku: {
    name: postgresSkuName
    tier: 'Burstable'
  }
  properties: {
    administratorLogin: administratorLogin
    administratorLoginPassword: administratorLoginPassword
    version: postgresVersion
    authConfig: {
      activeDirectoryAuth: entraAuthentication
      passwordAuth: 'Enabled'
    }
    backup: {
      backupRetentionDays: backupRetentionDays
      geoRedundantBackup: 'Disabled'
    }
    highAvailability: {
      mode: 'Disabled'
    }
    network: {
      delegatedSubnetResourceId: postgresSubnet.id
      privateDnsZoneArmResourceId: privateDnsZone.id
      publicNetworkAccess: 'Disabled'
    }
    storage: {
      autoGrow: 'Disabled'
      storageSizeGB: storageSizeGB
    }
  }
}

resource researchDatabase 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-08-01' = {
  parent: researchServer
  name: databaseName
  properties: {
    charset: 'UTF8'
    collation: 'en_US.utf8'
  }
}

// Read, start and stop of the research server, nothing else. A custom role can only be made
// assignable at a resource group (or wider), so the definition names this resource group and
// the one assignment below is scoped to the research server alone.
resource serverStartStopRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: serverStartStopRoleName
  properties: {
    roleName: 'Research server start and stop (${resourceGroup().name})'
    description: 'Read, start and stop one PostgreSQL flexible server. Assigned only on the research server, to the research identity.'
    type: 'CustomRole'
    permissions: [
      {
        actions: [
          'Microsoft.DBforPostgreSQL/flexibleServers/read'
          'Microsoft.DBforPostgreSQL/flexibleServers/start/action'
          'Microsoft.DBforPostgreSQL/flexibleServers/stop/action'
        ]
        notActions: []
        dataActions: []
        notDataActions: []
      }
    ]
    assignableScopes: [
      resourceGroup().id
    ]
  }
}

// Every role below is for the research identity alone, each on one container, one secret,
// the registry or the research server.
resource readRawStatements 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(rawStatements.id, researchIdentity.id, storageBlobDataReaderRoleId)
  scope: rawStatements
  properties: {
    principalId: researchIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: storageBlobDataReaderRoleId
  }
}
resource readMarketData 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(marketData.id, researchIdentity.id, storageBlobDataReaderRoleId)
  scope: marketData
  properties: {
    principalId: researchIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: storageBlobDataReaderRoleId
  }
}
resource readVendorData 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vendorData.id, researchIdentity.id, storageBlobDataReaderRoleId)
  scope: vendorData
  properties: {
    principalId: researchIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: storageBlobDataReaderRoleId
  }
}
resource writeResearchFeatures 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(researchFeatures.id, researchIdentity.id, storageBlobDataContributorRoleId)
  scope: researchFeatures
  properties: {
    principalId: researchIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: storageBlobDataContributorRoleId
  }
}
resource writeResearchExperiments 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(researchExperiments.id, researchIdentity.id, storageBlobDataContributorRoleId)
  scope: researchExperiments
  properties: {
    principalId: researchIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: storageBlobDataContributorRoleId
  }
}
resource pullImages 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, researchIdentity.id, acrPullRoleId)
  scope: registry
  properties: {
    principalId: researchIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPullRoleId
  }
}
resource readResearchDatabaseUrl 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(researchDatabaseUrlSecret.id, researchIdentity.id, keyVaultSecretsUserRoleId)
  scope: researchDatabaseUrlSecret
  properties: {
    principalId: researchIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: keyVaultSecretsUserRoleId
  }
}
resource readLiveReaderUrl 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(liveReaderUrlSecret.id, researchIdentity.id, keyVaultSecretsUserRoleId)
  scope: liveReaderUrlSecret
  properties: {
    principalId: researchIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: keyVaultSecretsUserRoleId
  }
}
resource readVendorSecrets 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for i in range(0, length(vendorSecretNames)): {
  name: guid(vendorSecrets[i].id, researchIdentity.id, keyVaultSecretsUserRoleId)
  scope: vendorSecrets[i]
  properties: {
    principalId: researchIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: keyVaultSecretsUserRoleId
  }
}]
resource startStopServer 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(researchServer.id, researchIdentity.id, serverStartStopRoleName)
  scope: researchServer
  properties: {
    principalId: researchIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', serverStartStopRole.name)
  }
}

var jobSecrets = [
  {
    name: 'research-database-url'
    keyVaultUrl: '${vault.properties.vaultUri}secrets/${researchDatabaseUrlSecretName}'
    identity: researchIdentity.id
  }
  {
    name: 'research-live-reader-url'
    keyVaultUrl: '${vault.properties.vaultUri}secrets/${liveReaderUrlSecretName}'
    identity: researchIdentity.id
  }
]
var jobEnvironment = [
  { name: 'RESEARCH_DATABASE_URL', secretRef: 'research-database-url' }
  { name: 'RESEARCH_LIVE_READER_URL', secretRef: 'research-live-reader-url' }
  { name: 'RESEARCH_SERVER_RESOURCE_ID', value: researchServer.id }
  { name: 'RESEARCH_MAX_RUN_SECONDS', value: string(replicaTimeoutSeconds) }
  { name: 'RESEARCH_CODE_VERSION', value: codeVersion }
  { name: 'AZURE_CLIENT_ID', value: researchIdentity.properties.clientId }
  { name: 'AZURE_STORAGE_ACCOUNT_URL', value: 'https://${storageAccountName}.blob.${environment().suffixes.storage}' }
  { name: 'PORTFOLIO_TENANT_ID', value: tenantId }
  { name: 'DEPLOYMENT_ENVIRONMENT', value: 'internal-production' }
  { name: 'IBKR_RUN_MIGRATIONS', value: 'false' }
]

resource researchJob 'Microsoft.App/jobs@2024-03-01' = if (deployJobs) {
  name: jobName
  location: location
  tags: union(tags, { component: 'research-job', dataLicense: 'yfinance-internal-only' })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${researchIdentity.id}': {}
    }
  }
  properties: {
    environmentId: managedEnvironment.id
    configuration: {
      triggerType: scheduled ? 'Schedule' : 'Manual'
      replicaTimeout: replicaTimeoutSeconds
      replicaRetryLimit: 0
      manualTriggerConfig: scheduled ? null : {
        parallelism: 1
        replicaCompletionCount: 1
      }
      scheduleTriggerConfig: scheduled ? {
        cronExpression: cronExpression
        parallelism: 1
        replicaCompletionCount: 1
      } : null
      registries: [
        {
          server: registry.properties.loginServer
          identity: researchIdentity.id
        }
      ]
      secrets: jobSecrets
    }
    template: {
      containers: [
        {
          name: 'research'
          image: '${registry.properties.loginServer}/${imageRepository}@${imageDigest}'
          command: ['python', 'scripts/research/run_research_job.py']
          args: jobArgs
          env: jobEnvironment
          resources: {
            cpu: json(cpu)
            memory: memory
          }
        }
      ]
    }
  }
  dependsOn: [
    readResearchDatabaseUrl
    readLiveReaderUrl
    pullImages
    startStopServer
  ]
}

// Stops the research server at 21:15 UTC (05:15 HKT) unless a run recorded in research.job_runs
// is still going. Without it a server Azure restarted after 7 days stopped would stay on.
resource idleStopJob 'Microsoft.App/jobs@2024-03-01' = if (deployJobs && idleStopScheduled) {
  name: idleStopJobName
  location: location
  tags: union(tags, { component: 'research-idle-stop' })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${researchIdentity.id}': {}
    }
  }
  properties: {
    environmentId: managedEnvironment.id
    configuration: {
      triggerType: 'Schedule'
      replicaTimeout: 1800
      replicaRetryLimit: 0
      scheduleTriggerConfig: {
        cronExpression: idleStopCron
        parallelism: 1
        replicaCompletionCount: 1
      }
      registries: [
        {
          server: registry.properties.loginServer
          identity: researchIdentity.id
        }
      ]
      secrets: [
        jobSecrets[0]
      ]
    }
    template: {
      containers: [
        {
          name: 'research-idle-stop'
          image: '${registry.properties.loginServer}/${imageRepository}@${imageDigest}'
          command: ['python', 'scripts/research/run_research_job.py']
          args: ['stop-server', '--if-idle']
          env: [
            { name: 'RESEARCH_DATABASE_URL', secretRef: 'research-database-url' }
            { name: 'RESEARCH_SERVER_RESOURCE_ID', value: researchServer.id }
            { name: 'RESEARCH_MAX_RUN_SECONDS', value: string(replicaTimeoutSeconds) }
            { name: 'AZURE_CLIENT_ID', value: researchIdentity.properties.clientId }
            { name: 'PORTFOLIO_TENANT_ID', value: tenantId }
            { name: 'DEPLOYMENT_ENVIRONMENT', value: 'internal-production' }
          ]
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
        }
      ]
    }
  }
  dependsOn: [
    readResearchDatabaseUrl
    pullImages
    startStopServer
  ]
}

output serverResourceId string = researchServer.id
output serverFqdn string = researchServer.properties.fullyQualifiedDomainName
output databaseName string = researchDatabase.name
output identityPrincipalId string = researchIdentity.properties.principalId
output identityClientId string = researchIdentity.properties.clientId
output containers array = [vendorData.name, researchFeatures.name, researchExperiments.name]
output jobsDeployed bool = deployJobs
output schedule string = scheduled ? '${cronExpression} (UTC)' : 'manual'
