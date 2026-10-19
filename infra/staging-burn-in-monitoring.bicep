targetScope = 'resourceGroup'

param registryName string
param keyVaultName string
param diagnosticsIdentityName string = 'ibkrv2stg-diagnostics-identity'
param environmentName string = 'ibkrv2stg-env'
param webAppName string = 'ibkrv2stg-web'
param monitorJobName string = 'ibkrv2stg-burn-in-monitor'
param actionGroupName string = 'ibkrv2stg-alerts'
param approvalDatabaseSecretName string = 'database-approval-url'
param imageDigest string
param portalUrl string
param entraTenantId string
param portfolioTenantId string = '00000000-0000-0000-0000-000000000001'
param pilotAccountId string = 'U16088571'

var tags = {
  application: 'ibkr-portfolio-platform'
  environment: 'internal-production'
  owner: 'portfolio-platform'
  costCenter: 'pilot'
  version: '2.0'
  purpose: 'single-user-burn-in'
}

// v1.1's ibkr-v11-daily keeps its own failed-execution alert from infra/job.bicep,
// on v1.1's action group, until v1.1 is deleted.
var monitoredJobNames = [
  'ibkrv2stg-account-sync-daily'
  'ibkrv2stg-benchmark-daily'
  'ibkrv2stg-risk-free-daily'
  'ibkrv2stg-sector-classification'
  'ibkrv2stg-month-end-factsheet'
  'ibkrv2stg-analyst-assurance'
  'ibkrv2stg-autonomous-admin'
  'ibkrv2stg-autonomy-review'
  'ibkrv2stg-derived-daily'
  'ibkrv2stg-risk-snapshots'
  'ibkrv2stg-institutional-daily'
  'ibkrv2stg-flex-discovery'
  monitorJobName
]

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: environmentName
}
resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: registryName
}
resource vault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}
resource diagnosticsIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: diagnosticsIdentityName
}
resource webApp 'Microsoft.App/containerApps@2024-03-01' existing = {
  name: webAppName
}
resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' existing = {
  name: actionGroupName
}
resource databaseSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' existing = {
  parent: vault
  name: approvalDatabaseSecretName
}

resource registryPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, diagnosticsIdentity.id, 'burn-in-acr-pull')
  scope: registry
  properties: {
    principalId: diagnosticsIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '7f951dda-4ed3-4680-a7ca-43fe172d538d'
    )
  }
}

resource vaultSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault.id, diagnosticsIdentity.id, 'burn-in-key-vault-secrets-user')
  scope: vault
  properties: {
    principalId: diagnosticsIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '4633458b-17de-408a-b874-0445c86b69e6'
    )
  }
}

resource monitorJob 'Microsoft.App/jobs@2024-03-01' = {
  name: monitorJobName
  location: resourceGroup().location
  tags: union(tags, { layer: 'ops', component: 'monitoring', hop: 'ops' })
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
      replicaTimeout: 300
      replicaRetryLimit: 0
      scheduleTriggerConfig: {
        cronExpression: '15 9 * * *'
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
          name: 'burn-in-monitor'
          // Keep the acceptance reader on the same immutable application
          // image as the assurance and analytics producers.
          image: '${registry.properties.loginServer}/ibkr-platform@${imageDigest}'
          command: ['python']
          args: ['scripts/run_burn_in_check.py']
          env: [
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            { name: 'PORTAL_URL', value: portalUrl }
            { name: 'ENTRA_TENANT_ID', value: entraTenantId }
            { name: 'PORTFOLIO_TENANT_ID', value: portfolioTenantId }
            { name: 'PILOT_ACCOUNT_ID', value: pilotAccountId }
            { name: 'IBKR_RUN_MIGRATIONS', value: 'false' }
          ]
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
        }
      ]
    }
  }
  dependsOn: [registryPull, vaultSecretsUser]
}

// The Medallion tags step S4a put on each live alert, by job (infra/medallion/applied-tags.json).
// Four of these alerts had no owner tag before S4a, so S4a gave them owner 'ying'.
var medallionAlertTags = {
  'ibkrv2stg-account-sync-daily': { layer: 'ops', component: 'alerts' }
  'ibkrv2stg-benchmark-daily': { layer: 'ops', component: 'alerts' }
  'ibkrv2stg-risk-free-daily': { layer: 'ops', component: 'alerts' }
  'ibkrv2stg-sector-classification': { layer: 'ops', component: 'alerts' }
  'ibkrv2stg-month-end-factsheet': { layer: 'ops', component: 'alerts' }
  'ibkrv2stg-analyst-assurance': { layer: 'ops', component: 'alerts' }
  'ibkrv2stg-autonomous-admin': { layer: 'ops', component: 'alerts' }
  'ibkrv2stg-autonomy-review': { layer: 'ops', component: 'alerts' }
  'ibkrv2stg-derived-daily': { layer: 'ops', component: 'alerts', owner: 'ying' }
  'ibkrv2stg-risk-snapshots': { layer: 'ops', component: 'alerts', owner: 'ying' }
  'ibkrv2stg-institutional-daily': { layer: 'ops', component: 'alerts', owner: 'ying' }
  'ibkrv2stg-flex-discovery': { layer: 'ops', component: 'alerts', owner: 'ying' }
  'ibkrv2stg-burn-in-monitor': { layer: 'ops', component: 'alerts' }
}

resource failedJobAlerts 'Microsoft.Insights/metricAlerts@2018-03-01' = [for jobName in monitoredJobNames: {
  name: '${jobName}-failed-execution'
  location: 'global'
  tags: union(tags, contains(medallionAlertTags, jobName) ? medallionAlertTags[jobName] : { layer: 'ops', component: 'alerts' })
  properties: {
    description: jobName == 'ibkrv2stg-analyst-assurance'
      ? 'The daily portfolio-analyst data, metrics and report assurance job failed.'
      : 'Burn-in alert: a scheduled portfolio-platform job execution failed.'
    severity: 1
    enabled: true
    scopes: [resourceId('Microsoft.App/jobs', jobName)]
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
  dependsOn: jobName == monitorJobName ? [monitorJob] : []
}]

resource webServerErrorAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: '${webAppName}-server-errors'
  location: 'global'
  tags: union(tags, { layer: 'ops', component: 'alerts' })
  properties: {
    description: 'Burn-in alert: the private portfolio portal returned a server error.'
    severity: 1
    enabled: true
    scopes: [webApp.id]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'ServerErrors'
          metricNamespace: 'Microsoft.App/containerApps'
          metricName: 'Requests'
          operator: 'GreaterThan'
          threshold: 0
          timeAggregation: 'Total'
          criterionType: 'StaticThresholdCriterion'
          dimensions: [
            {
              name: 'statusCodeCategory'
              operator: 'Include'
              values: ['5xx']
            }
          ]
        }
      ]
    }
    autoMitigate: true
    actions: [{ actionGroupId: actionGroup.id }]
  }
}

output monitorJobName string = monitorJob.name
output monitorScheduleHkt string = '17:15 HKT daily'
output monitoredScheduledJobs array = monitoredJobNames
