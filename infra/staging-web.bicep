targetScope = 'resourceGroup'

param appName string = 'ibkrv2stg-web'
param environmentName string = 'ibkrv2stg-env'
param registryName string
param keyVaultName string
param identityResourceId string
param imageTag string
param allowedEmail string = 'anthony.ying@wealthryse.ai'
param allowedObjectId string
@description('Comma-separated Viewer email allow-list. Viewers can browse account data, download approved reports and exports, and use the AI analyst, but cannot administer the platform.')
param viewerEmails string = ''
@description('Comma-separated Entra object IDs corresponding to the Viewer email allow-list.')
param viewerObjectIds string = ''
@description('Comma-separated onboarding-operator emails. Operators may use onboarding workflows but cannot access system administration.')
param onboardingOperatorEmails string = ''
@description('Comma-separated Entra object IDs corresponding to onboarding operators.')
param onboardingOperatorObjectIds string = ''
@allowed([
  'legacy'
  'hybrid'
  'database'
])
@description('Production uses database-only roles and entitlements. Legacy/hybrid are explicit recovery/migration overrides, not normal deployment defaults.')
param authorizationMode string = 'database'
param tenantId string = '00000000-0000-0000-0000-000000000001'
param pilotAccountId string = 'U16088571'
param pilotAccountDisplayName string = 'U16088571-U.S. Technology and Biotechnology'
@description('Main-vault secret holding the Entra application client secret used by Easy Auth.')
param entraClientSecretName string = 'website-entra-client-secret'
param aiEndpoint string = ''
param aiDeployment string = ''
param aiRegion string = 'southeastasia'
param aiProcessingScope string = 'disabled'
param aiAnalystEnabled bool = false
param approvalDatabaseSecretName string = 'database-approval-url'
param azureSubscriptionId string
param azureResourceGroup string = resourceGroup().name
param adminActionsEnabled bool = false
param derivedRefreshJobName string = 'ibkrv2stg-derived-daily'
param onboardingReportJobName string = 'ibkrv2stg-onboard-render'
param onboardingAnalyticsJobName string = 'ibkrv2stg-onboard-analytics'
param storageAccountUrl string = 'https://ibkrv2stgauhecwc4itaoo.blob.${environment().suffixes.storage}'
param internalReportContainer string = 'internal-reports'
param onboardingIntakeContainer string = 'onboarding-intake'
param credentialBrokerUrl string = ''
@description('Main-vault secret holding the shared bearer secret for the credential broker.')
param credentialBrokerAuthSecretName string = 'credential-broker-auth'
// The pilot portal is externally reachable only through HTTPS and is protected by
// the separately managed Container Apps Entra auth configuration plus the
// application allow-list. Set this parameter to false for emergency isolation.
param externalIngress bool = true

var tags = {
  application: 'ibkr-portfolio-platform'
  environment: 'staging'
  owner: 'portfolio-platform'
  costCenter: 'pilot'
  version: '2.0'
  dataClassification: 'confidential-financial'
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
resource webIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: last(split(identityResourceId, '/'))
}

resource web 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: resourceGroup().location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${webIdentity.id}': {}
    }
  }
  properties: {
    environmentId: managedEnvironment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: externalIngress
        targetPort: 8000
        allowInsecure: false
        transport: 'auto'
      }
      registries: [
        {
          server: registry.properties.loginServer
          identity: webIdentity.id
        }
      ]
      secrets: [
        {
          name: 'database-web-url'
          keyVaultUrl: '${vault.properties.vaultUri}secrets/database-web-url'
          identity: webIdentity.id
        }
        {
          name: 'microsoft-provider-authentication-secret'
          keyVaultUrl: '${vault.properties.vaultUri}secrets/${entraClientSecretName}'
          identity: webIdentity.id
        }
        {
          name: 'database-approval-url'
          keyVaultUrl: '${vault.properties.vaultUri}secrets/${approvalDatabaseSecretName}'
          identity: webIdentity.id
        }
        {
          name: 'credential-broker-auth'
          keyVaultUrl: '${vault.properties.vaultUri}secrets/${credentialBrokerAuthSecretName}'
          identity: webIdentity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'web'
          image: '${registry.properties.loginServer}/ibkr-platform:${imageTag}'
          command: ['python']
          args: ['-m', 'uvicorn', 'ibkr_etl.web:app', '--host', '0.0.0.0', '--port', '8000', '--no-access-log']
          env: [
            { name: 'DATABASE_URL', secretRef: 'database-web-url' }
            { name: 'APPROVAL_DATABASE_URL', secretRef: 'database-approval-url' }
            { name: 'PORTFOLIO_TENANT_ID', value: tenantId }
            { name: 'PILOT_ACCOUNT_ID', value: pilotAccountId }
            { name: 'PILOT_ACCOUNT_DISPLAY_NAME', value: pilotAccountDisplayName }
            { name: 'ALLOWED_EMAILS', value: allowedEmail }
            { name: 'ALLOWED_ENTRA_OBJECT_IDS', value: allowedObjectId }
            { name: 'VIEWER_EMAILS', value: viewerEmails }
            { name: 'VIEWER_ENTRA_OBJECT_IDS', value: viewerObjectIds }
            { name: 'ONBOARDING_OPERATOR_EMAILS', value: onboardingOperatorEmails }
            { name: 'ONBOARDING_OPERATOR_ENTRA_OBJECT_IDS', value: onboardingOperatorObjectIds }
            { name: 'AUTHORIZATION_MODE', value: authorizationMode }
            { name: 'AZURE_CLIENT_ID', value: webIdentity.properties.clientId }
            { name: 'AZURE_AI_ENDPOINT', value: aiEndpoint }
            { name: 'AZURE_AI_DEPLOYMENT', value: aiDeployment }
            { name: 'AZURE_AI_REGION', value: aiRegion }
            { name: 'AZURE_AI_PROCESSING_SCOPE', value: aiProcessingScope }
            { name: 'AI_ANALYST_ENABLED', value: aiAnalystEnabled ? 'true' : 'false' }
            { name: 'DEPLOYMENT_ENVIRONMENT', value: 'staging' }
            { name: 'RELEASE_CHANNEL', value: 'internal-production' }
            { name: 'AZURE_SUBSCRIPTION_ID', value: azureSubscriptionId }
            { name: 'AZURE_RESOURCE_GROUP', value: azureResourceGroup }
            { name: 'ADMIN_ACTIONS_ENABLED', value: adminActionsEnabled ? 'true' : 'false' }
            { name: 'DERIVED_REFRESH_JOB_NAME', value: derivedRefreshJobName }
            { name: 'ONBOARDING_REPORT_JOB_NAME', value: onboardingReportJobName }
            { name: 'ONBOARDING_ANALYTICS_JOB_NAME', value: onboardingAnalyticsJobName }
            { name: 'AZURE_STORAGE_ACCOUNT_URL', value: storageAccountUrl }
            { name: 'INTERNAL_REPORT_CONTAINER', value: internalReportContainer }
            { name: 'ONBOARDING_INTAKE_CONTAINER', value: onboardingIntakeContainer }
            { name: 'CREDENTIAL_BROKER_URL', value: credentialBrokerUrl }
            { name: 'CREDENTIAL_BROKER_AUTH', secretRef: 'credential-broker-auth' }
          ]
          resources: {
            cpu: json('1.0')
            memory: '2Gi'
          }
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 2
        rules: [
          {
            name: 'http'
            http: {
              metadata: {
                concurrentRequests: '20'
              }
            }
          }
        ]
      }
    }
  }
}

output appName string = web.name
output fqdn string = web.properties.configuration.ingress.fqdn
