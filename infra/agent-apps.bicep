targetScope = 'resourceGroup'

// WealthRyse agents, part 2: the agent runtime, the tool server and three
// scheduled jobs in the existing Container Apps environment. Internal ingress
// only. Deploy after agent-foundation.bicep, the Entra app registrations and
// scripts/install_agent_schema.py (see docs/AGENTS.md), with Ying's approval.

param environmentName string = 'ibkrv2stg-env'
param registryName string
@description('Digest of the agent image built from Dockerfile.agent (sha256:...).')
param imageDigest string

param agentIdentityId string
param toolsIdentityId string
param jobsIdentityId string
@description('Object (principal) ID of the website identity, the only caller of the agent API.')
param webPrincipalId string

@description('Entra app ID URIs used as token audiences. The tenant only accepts URIs that contain its ID.')
param agentApiAudience string = 'api://${subscription().tenantId}/wealthryse-agent'
param toolsAudience string = 'api://${subscription().tenantId}/wealthryse-agent-tools'

param databaseHost string
param databaseName string = 'ibkr'
param tenantId string = '00000000-0000-0000-0000-000000000001'

param modelEndpoint string
param modelDeployment string = 'wr-analyst-astra'
@description('Where the model processes prompts: global (Global Standard) or apac-data-zone (Data Zone Standard).')
@allowed(['global', 'apac-data-zone'])
param modelProcessingScope string = 'global'
param routerDeployment string = ''
param dailyTokenBudget int = 6000000
@description('Reasoning effort for owner chat. The scheduled jobs keep the runtime default (low).')
@allowed(['low', 'medium', 'high', 'xhigh'])
param chatReasoningEffort string = 'xhigh'
@description('Reasoning effort for plain lookups in chat (one figure, no chart or analysis). Analysis questions use chatReasoningEffort.')
@allowed(['low', 'medium', 'high', 'xhigh'])
param chatQuickReasoningEffort string = 'low'
@description('Per-question limit. Stays under the 240-second Container Apps ingress request timeout.')
@maxValue(220)
param chatTimeoutSeconds int = 210
@description('Output allowance per model call; reasoning tokens count against it.')
param chatMaxOutputTokens int = 32000
param chatMaxToolCalls int = 16
param retentionDays int = 90

param agentAppName string = 'ibkrv2stg-agent'
param toolsAppName string = 'ibkrv2stg-agent-tools'

var tags = {
  application: 'wealthryse-agents'
  environment: 'staging'
  owner: 'portfolio-platform'
  dataClassification: 'client-portfolio-results'
}
var acrPullRole = '7f951dda-4ed3-4680-a7ca-43fe172d538d'

resource managedEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: environmentName
}
resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: registryName
}
resource agentIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: last(split(agentIdentityId, '/'))
}
resource toolsIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: last(split(toolsIdentityId, '/'))
}
resource jobsIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: last(split(jobsIdentityId, '/'))
}

resource pulls 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for identityId in [agentIdentityId, toolsIdentityId, jobsIdentityId]: {
  name: guid(registry.id, identityId, acrPullRole)
  scope: registry
  properties: {
    principalId: reference(identityId, '2023-01-31').principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRole)
  }
}]

var image = '${registry.properties.loginServer}/wealthryse-agent@${imageDigest}'
var commonEnv = [
  { name: 'AZURE_TENANT_ID', value: subscription().tenantId }
  { name: 'AGENT_TENANT_ID', value: tenantId }
  { name: 'AGENT_DB_HOST', value: databaseHost }
  { name: 'AGENT_DB_NAME', value: databaseName }
]
var modelEnv = [
  { name: 'AGENT_MODEL_ENDPOINT', value: modelEndpoint }
  { name: 'AGENT_MODEL_DEPLOYMENT', value: modelDeployment }
  { name: 'AGENT_ROUTER_DEPLOYMENT', value: routerDeployment }
  { name: 'AGENT_MODEL_REGION', value: 'southeastasia' }
  { name: 'AGENT_MODEL_PROCESSING_SCOPE', value: modelProcessingScope }
  { name: 'AGENT_TOOL_SERVER_URL', value: 'https://${toolsAppName}.internal.${managedEnvironment.properties.defaultDomain}' }
  { name: 'AGENT_TOOL_SERVER_AUDIENCE', value: toolsAudience }
  { name: 'AGENT_DAILY_TOKEN_BUDGET', value: string(dailyTokenBudget) }
  { name: 'AGENT_RETENTION_DAYS', value: string(retentionDays) }
]
var probes = [
  {
    type: 'Liveness'
    httpGet: { path: '/healthz', port: 8080 }
    periodSeconds: 30
  }
  {
    type: 'Readiness'
    httpGet: { path: '/healthz', port: 8080 }
    periodSeconds: 10
  }
]

resource tools 'Microsoft.App/containerApps@2024-03-01' = {
  name: toolsAppName
  location: resourceGroup().location
  tags: tags
  dependsOn: [pulls]
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${toolsIdentity.id}': {} }
  }
  properties: {
    environmentId: managedEnvironment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: false
        targetPort: 8080
        transport: 'http'
        allowInsecure: false
      }
      registries: [{ server: registry.properties.loginServer, identity: toolsIdentity.id }]
    }
    template: {
      containers: [
        {
          name: 'tools'
          image: image
          command: ['uvicorn']
          args: ['wealthryse_agent.tool_server:app', '--host', '0.0.0.0', '--port', '8080', '--no-access-log']
          env: concat(commonEnv, [
            { name: 'AZURE_CLIENT_ID', value: toolsIdentity.properties.clientId }
            { name: 'AGENT_DB_USER', value: toolsIdentity.name }
            { name: 'AGENT_TOOLS_AUDIENCE', value: toolsAudience }
            { name: 'AGENT_TOOLS_USER_CALLERS', value: agentIdentity.properties.principalId }
            { name: 'AGENT_TOOLS_JOB_CALLERS', value: jobsIdentity.properties.principalId }
          ])
          resources: { cpu: json('0.5'), memory: '1Gi' }
          probes: probes
        }
      ]
      // Kept awake so the first chat question doesn't wait for a cold start.
      scale: { minReplicas: 1, maxReplicas: 2 }
    }
  }
}

resource agent 'Microsoft.App/containerApps@2024-03-01' = {
  name: agentAppName
  location: resourceGroup().location
  tags: tags
  dependsOn: [pulls]
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${agentIdentity.id}': {} }
  }
  properties: {
    environmentId: managedEnvironment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: false
        targetPort: 8080
        transport: 'http'
        allowInsecure: false
      }
      registries: [{ server: registry.properties.loginServer, identity: agentIdentity.id }]
    }
    template: {
      containers: [
        {
          name: 'agent'
          image: image
          command: ['uvicorn']
          args: ['wealthryse_agent.agent_server:app', '--host', '0.0.0.0', '--port', '8080', '--no-access-log']
          env: concat(commonEnv, modelEnv, [
            { name: 'AZURE_CLIENT_ID', value: agentIdentity.properties.clientId }
            { name: 'AGENT_DB_USER', value: agentIdentity.name }
            { name: 'AGENT_API_AUDIENCE', value: agentApiAudience }
            { name: 'AGENT_API_ALLOWED_CALLERS', value: webPrincipalId }
            { name: 'AGENT_REASONING_EFFORT', value: chatReasoningEffort }
            { name: 'AGENT_QUICK_REASONING_EFFORT', value: chatQuickReasoningEffort }
            { name: 'AGENT_TIMEOUT_SECONDS', value: string(chatTimeoutSeconds) }
            { name: 'AGENT_MAX_OUTPUT_TOKENS', value: string(chatMaxOutputTokens) }
            { name: 'AGENT_MAX_TOOL_CALLS', value: string(chatMaxToolCalls) }
          ])
          resources: { cpu: json('0.5'), memory: '1Gi' }
          probes: probes
        }
      ]
      // Kept awake so the first chat question doesn't wait for a cold start.
      scale: { minReplicas: 1, maxReplicas: 2 }
    }
  }
}

// Container Apps job names must be 2 to 32 characters.
var jobs = [
  // 08:30 HKT, after the morning account sync.
  { name: 'ibkrv2stg-agent-holdings', job: 'holdings_explainer', cron: '30 0 * * *' }
  // 10:00 HKT on the 2nd to 7th; skips accounts that already have this month's draft.
  { name: 'ibkrv2stg-agent-month-end', job: 'month_end_drafter', cron: '0 2 2-7 * *' }
  // 09:00 HKT; also deletes chats older than the retention period.
  { name: 'ibkrv2stg-agent-data-quality', job: 'data_quality', cron: '0 1 * * *' }
]

resource agentJobs 'Microsoft.App/jobs@2024-03-01' = [for j in jobs: {
  name: j.name
  location: resourceGroup().location
  tags: tags
  dependsOn: [pulls, tools]
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${jobsIdentity.id}': {} }
  }
  properties: {
    environmentId: managedEnvironment.id
    configuration: {
      triggerType: 'Schedule'
      replicaTimeout: 1800
      replicaRetryLimit: 0
      scheduleTriggerConfig: {
        cronExpression: j.cron
        parallelism: 1
        replicaCompletionCount: 1
      }
      registries: [{ server: registry.properties.loginServer, identity: jobsIdentity.id }]
    }
    template: {
      containers: [
        {
          name: 'job'
          image: image
          command: ['python', '-m', 'wealthryse_agent.jobs']
          args: [j.job]
          env: concat(commonEnv, modelEnv, [
            { name: 'AZURE_CLIENT_ID', value: jobsIdentity.properties.clientId }
            { name: 'AGENT_DB_USER', value: jobsIdentity.name }
          ])
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
        }
      ]
    }
  }
}]

output agentApiUrl string = 'https://${agent.properties.configuration.ingress.fqdn}'
output toolServerUrl string = 'https://${tools.properties.configuration.ingress.fqdn}'
