targetScope = 'resourceGroup'

param jobName string = 'ibkrv2stg-analyst-assurance'
param environmentName string = 'ibkrv2stg-env'
param registryName string
param keyVaultName string
param openAiAccountName string
param identityResourceId string
param imageDigest string
param actionGroupName string = 'ibkrv2stg-alerts'
param databaseSecretName string = 'database-app-url'
param modelDeploymentName string = 'gpt-5-6-sol-apac'
param tenantId string = '00000000-0000-0000-0000-000000000001'
param pilotAccountId string = 'U16088571'

var tags = {
  application: 'wealthryse-portfolio-data-platform'
  environment: 'internal-production'
  owner: 'portfolio-platform'
  costCenter: 'pilot'
  version: '2.0'
  purpose: 'portfolio-analyst-assurance'
  policyVersion: 'portfolio-analyst-assurance-v1'
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
  name: databaseSecretName
}
resource openAi 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: openAiAccountName
}
resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: last(split(identityResourceId, '/'))
}
resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' existing = {
  name: actionGroupName
}

// The publisher identity's AcrPull, secret-read and inference-only roles are
// owned by the staging baseline. This job deliberately reuses those existing
// assignments instead of creating duplicate or broader permissions.
resource job 'Microsoft.App/jobs@2024-03-01' = {
  name: jobName
  location: resourceGroup().location
  tags: union(tags, { layer: 'apps', component: 'reporting', hop: 'quality-gate' })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
  properties: {
    environmentId: environment.id
    configuration: {
      triggerType: 'Schedule'
      replicaTimeout: 600
      replicaRetryLimit: 0
      scheduleTriggerConfig: {
        // Runs after the daily account, benchmark and risk-free updates and
        // before the formal Stage 4 acceptance check.
        cronExpression: '10 9 * * *'
        parallelism: 1
        replicaCompletionCount: 1
      }
      registries: [
        {
          server: registry.properties.loginServer
          identity: identity.id
        }
      ]
      secrets: [
        {
          name: 'database-url'
          keyVaultUrl: databaseSecret.properties.secretUriWithVersion
          identity: identity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'portfolio-analyst-assurance'
          // Use the same immutable application image as the benchmark and web
          // workloads so analytics readers cannot drift from the calculation
          // version that produced the stored records.
          image: '${registry.properties.loginServer}/ibkr-platform@${imageDigest}'
          command: ['python']
          args: ['scripts/run_registry_portfolio_assurance.py']
          env: [
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            { name: 'DEPLOYMENT_ENVIRONMENT', value: 'internal-production' }
            { name: 'PORTFOLIO_TENANT_ID', value: tenantId }
            { name: 'PILOT_ACCOUNT_ID', value: pilotAccountId }
            { name: 'AZURE_AI_ENDPOINT', value: openAi.properties.endpoint }
            { name: 'AZURE_AI_DEPLOYMENT', value: modelDeploymentName }
            { name: 'AZURE_AI_REGION', value: 'southeastasia' }
            { name: 'AZURE_AI_PROCESSING_SCOPE', value: 'apac-data-zone' }
            { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId }
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
    }
  }
}

resource failureAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: '${jobName}-failed-execution'
  location: 'global'
  tags: union(tags, { layer: 'ops', component: 'alerts' })
  properties: {
    description: 'The daily portfolio-analyst data, metrics and report assurance job failed.'
    severity: 1
    enabled: true
    scopes: [job.id]
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

output jobName string = job.name
output scheduleHongKong string = '17:10 HKT daily'
output policyVersion string = 'portfolio-analyst-assurance-v1'
