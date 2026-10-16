targetScope = 'resourceGroup'

param registryName string
param keyVaultName string
param aiAccountName string
param diagnosticsIdentityName string = 'ibkrv2stg-diagnostics-identity'
param environmentName string = 'ibkrv2stg-env'
param jobName string = 'ibkrv2stg-autonomous-admin'
param reviewJobName string = 'ibkrv2stg-autonomy-review'
param webAppName string = 'ibkrv2stg-web'
param actionGroupName string = 'ibkr-v11-daily-alerts'
param benchmarkJobName string = 'ibkrv2stg-benchmark-daily'
param riskFreeJobName string = 'ibkrv2stg-risk-free-daily'
param approvalDatabaseSecretName string = 'database-approval-url'
param imageDigest string
param lastKnownGoodRevision string
param lastKnownGoodImageDigest string
param portalUrl string
param entraTenantId string
param aiEndpoint string
param aiDeployment string = 'gpt-5-6-sol-apac'
param aiRegion string = 'southeastasia'
param aiProcessingScope string = 'apac-data-zone'
param azureSubscriptionId string
param portfolioTenantId string = '00000000-0000-0000-0000-000000000001'
param pilotAccountId string = 'U16088571'
// Medallion layer freshness (src/ibkr_etl/layer_freshness.py). Off by default, which
// leaves the supervisor's environment exactly as before. The bronze-market-data
// checks also need the container name and Storage Blob Data Reader on it.
param layerFreshnessEnabled bool = false
param rawMarketDataContainer string = ''
param rawMarketDataStorageAccountName string = 'ibkrv2stgauhecwc4itaoo'
var layerFreshnessEnv = !layerFreshnessEnabled ? [] : concat([
  { name: 'LAYER_FRESHNESS_ENABLED', value: 'true' }
], empty(rawMarketDataContainer) ? [] : [
  { name: 'MARKET_DATA_RAW_CONTAINER', value: rawMarketDataContainer }
  { name: 'AZURE_STORAGE_ACCOUNT_URL', value: 'https://${rawMarketDataStorageAccountName}.blob.${az.environment().suffixes.storage}' }
])

var tags = {
  application: 'wealthryse-portfolio-data-platform'
  environment: 'internal-production'
  owner: 'portfolio-platform'
  costCenter: 'pilot'
  version: '2.0'
  purpose: 'controlled-autonomy'
  policyVersion: 'controlled-autonomy-v1'
}

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: environmentName
}
resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: registryName
}
resource vault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}
resource databaseSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' existing = {
  parent: vault
  name: approvalDatabaseSecretName
}
resource diagnosticsIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: diagnosticsIdentityName
}
resource benchmarkJob 'Microsoft.App/jobs@2024-03-01' existing = {
  name: benchmarkJobName
}
resource riskFreeJob 'Microsoft.App/jobs@2024-03-01' existing = {
  name: riskFreeJobName
}
resource webApp 'Microsoft.App/containerApps@2024-03-01' existing = {
  name: webAppName
}
resource aiAccount 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: aiAccountName
}
resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' existing = {
  name: actionGroupName
}

resource keyVaultSecretsUserRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' existing = {
  scope: subscription()
  name: '4633458b-17de-408a-b874-0445c86b69e6'
}
resource jobsOperatorRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' existing = {
  scope: subscription()
  name: 'b9a307c4-5aa3-4b52-ba60-2b17c136cd7b'
}

resource exactRevisionRecoveryRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(resourceGroup().id, 'wealthryse-exact-container-app-revision-recovery')
  properties: {
    roleName: 'WealthRyse Exact Container App Revision Recovery'
    description: 'Read Container App revisions and activate one exact pre-registered revision. No deployment, write, delete, secret or role permission.'
    type: 'CustomRole'
    permissions: [
      {
        actions: [
          'Microsoft.App/containerApps/read'
          'Microsoft.App/containerApps/revisions/read'
          'Microsoft.App/containerApps/revisions/activate/action'
        ]
        notActions: []
        dataActions: []
        notDataActions: []
      }
    ]
    assignableScopes: [resourceGroup().id]
  }
}

resource databaseSecretReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(databaseSecret.id, diagnosticsIdentity.id, 'autonomy-database-secret-reader')
  scope: databaseSecret
  properties: {
    principalId: diagnosticsIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: keyVaultSecretsUserRole.id
  }
}

resource benchmarkOperator 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(benchmarkJob.id, diagnosticsIdentity.id, 'autonomy-jobs-operator')
  scope: benchmarkJob
  properties: {
    principalId: diagnosticsIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: jobsOperatorRole.id
  }
}

resource riskFreeOperator 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(riskFreeJob.id, diagnosticsIdentity.id, 'autonomy-jobs-operator')
  scope: riskFreeJob
  properties: {
    principalId: diagnosticsIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: jobsOperatorRole.id
  }
}

resource aiInferenceRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(aiAccount.id, diagnosticsIdentity.id, 'autonomy-openai-user')
  scope: aiAccount
  properties: {
    principalId: diagnosticsIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
    )
  }
}

resource exactRevisionRecoveryAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(webApp.id, diagnosticsIdentity.id, 'exact-revision-recovery')
  scope: webApp
  properties: {
    principalId: diagnosticsIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: exactRevisionRecoveryRole.id
  }
}

resource supervisor 'Microsoft.App/jobs@2024-03-01' = {
  name: jobName
  location: resourceGroup().location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${diagnosticsIdentity.id}': {}
    }
  }
  properties: {
    environmentId: environment.id
    configuration: {
      triggerType: 'Schedule'
      replicaTimeout: 240
      replicaRetryLimit: 0
      scheduleTriggerConfig: {
        cronExpression: '*/5 * * * *'
        parallelism: 1
        replicaCompletionCount: 1
      }
      registries: [
        {
          server: registry.properties.loginServer
          identity: diagnosticsIdentity.id
        }
      ]
      secrets: [
        {
          name: 'database-url'
          keyVaultUrl: databaseSecret.properties.secretUriWithVersion
          identity: diagnosticsIdentity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'controlled-autonomy-supervisor'
          image: '${registry.properties.loginServer}/ibkr-portfolio@${imageDigest}'
          command: ['python']
          args: ['scripts/run_autonomous_administrator.py']
          env: concat([
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            { name: 'PORTAL_URL', value: portalUrl }
            { name: 'ENTRA_TENANT_ID', value: entraTenantId }
            { name: 'PORTFOLIO_TENANT_ID', value: portfolioTenantId }
            { name: 'PILOT_ACCOUNT_ID', value: pilotAccountId }
            { name: 'AZURE_CLIENT_ID', value: diagnosticsIdentity.properties.clientId }
            { name: 'AZURE_SUBSCRIPTION_ID', value: azureSubscriptionId }
            { name: 'AZURE_RESOURCE_GROUP', value: resourceGroup().name }
            { name: 'WEB_APP_NAME', value: webAppName }
            { name: 'LAST_KNOWN_GOOD_REVISION', value: lastKnownGoodRevision }
            { name: 'LAST_KNOWN_GOOD_IMAGE_DIGEST', value: lastKnownGoodImageDigest }
            { name: 'AZURE_AI_ENDPOINT', value: aiEndpoint }
            { name: 'AZURE_AI_DEPLOYMENT', value: aiDeployment }
            { name: 'AZURE_AI_REGION', value: aiRegion }
            { name: 'AZURE_AI_PROCESSING_SCOPE', value: aiProcessingScope }
            { name: 'AI_ADMIN_ENABLED', value: 'true' }
          ], layerFreshnessEnv)
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
        }
      ]
    }
  }
  dependsOn: [
    databaseSecretReader
    benchmarkOperator
    riskFreeOperator
    aiInferenceRole
    exactRevisionRecoveryAssignment
  ]
}

resource monthlyAuthorityReview 'Microsoft.App/jobs@2024-03-01' = {
  name: reviewJobName
  location: resourceGroup().location
  tags: union(tags, { purpose: 'monthly-authority-review' })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${diagnosticsIdentity.id}': {}
    }
  }
  properties: {
    environmentId: environment.id
    configuration: {
      triggerType: 'Schedule'
      replicaTimeout: 180
      replicaRetryLimit: 0
      scheduleTriggerConfig: {
        cronExpression: '0 1 1 * *'
        parallelism: 1
        replicaCompletionCount: 1
      }
      registries: [
        {
          server: registry.properties.loginServer
          identity: diagnosticsIdentity.id
        }
      ]
      secrets: [
        {
          name: 'database-url'
          keyVaultUrl: databaseSecret.properties.secretUriWithVersion
          identity: diagnosticsIdentity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'monthly-authority-review'
          image: '${registry.properties.loginServer}/ibkr-portfolio@${imageDigest}'
          command: ['python']
          args: ['scripts/run_monthly_authority_review.py']
          env: [
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            { name: 'PORTFOLIO_TENANT_ID', value: portfolioTenantId }
            { name: 'AZURE_CLIENT_ID', value: diagnosticsIdentity.properties.clientId }
          ]
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
        }
      ]
    }
  }
  dependsOn: [databaseSecretReader]
}

resource supervisorFailureAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: '${jobName}-failed-execution'
  location: 'global'
  tags: tags
  properties: {
    description: 'Controlled-autonomy alert: the five-minute administrator supervisor failed.'
    severity: 1
    enabled: true
    scopes: [supervisor.id]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'FailedExecutions'
          metricNamespace: 'Microsoft.App/jobs'
          metricName: 'Executions'
          operator: 'GreaterThan'
          threshold: 0
          timeAggregation: 'Total'
          criterionType: 'StaticThresholdCriterion'
          dimensions: [
            {
              name: 'state'
              operator: 'Include'
              values: ['Failed']
            }
          ]
        }
      ]
    }
    autoMitigate: true
    actions: [{ actionGroupId: actionGroup.id }]
  }
}

resource monthlyReviewFailureAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: '${reviewJobName}-failed-execution'
  location: 'global'
  tags: union(tags, { purpose: 'monthly-authority-review' })
  properties: {
    description: 'Controlled-autonomy alert: the monthly owner-authority review job failed.'
    severity: 1
    enabled: true
    scopes: [monthlyAuthorityReview.id]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'FailedExecutions'
          metricNamespace: 'Microsoft.App/jobs'
          metricName: 'Executions'
          operator: 'GreaterThan'
          threshold: 0
          timeAggregation: 'Total'
          criterionType: 'StaticThresholdCriterion'
          dimensions: [
            {
              name: 'state'
              operator: 'Include'
              values: ['Failed']
            }
          ]
        }
      ]
    }
    autoMitigate: true
    actions: [{ actionGroupId: actionGroup.id }]
  }
}

output jobName string = supervisor.name
output schedule string = 'Every five minutes, 24/7'
output policyVersion string = 'controlled-autonomy-v1'
output reviewJobName string = monthlyAuthorityReview.name
output reviewSchedule string = '09:00 HKT on the first day of each month'
