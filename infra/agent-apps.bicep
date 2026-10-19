targetScope = 'resourceGroup'

// WealthRyse agents, part 2: the agent runtime, the tool server, three
// scheduled jobs and (when deployMonthTeam) the month-end team's job, in the
// existing Container Apps environment. Internal ingress
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
@description('Reasoning mode for website chat (/v1/chat) only: pro works longer on each model call, at the same token rates. Stays standard until the 29 Oct release passes pro (docs/AGENTS.md).')
@allowed(['standard', 'pro'])
param chatReasoningMode string = 'standard'
@description('Per-question limit. Stays under the 240-second Container Apps ingress request timeout.')
@maxValue(220)
param chatTimeoutSeconds int = 210
@description('Output allowance per model call; reasoning tokens count against it.')
param chatMaxOutputTokens int = 32000
param chatMaxToolCalls int = 16
param retentionDays int = 90
@description('The supervisor (docs/AGENTS.md). watch records beside each chat answer what it would have done, in agent.tasks; nobody sees a change. live: the client analysis team answers the people in supervisorUsers, and everyone else is watched. Needs migrations/agent/018 installed.')
@allowed(['off', 'watch', 'live'])
param supervisorMode string = 'off'
@description('With supervisorMode live: the Entra object ids, comma-separated, whose chat the client analysis team answers; * for everyone. Empty: nobody.')
param supervisorUsers string = ''
@description('The event dispatcher\'s job (ibkrv2stg-agent-dispatcher, docs/AGENTS.md): every 5 minutes it checks what the holdings explainer, risk watch and weekly brief are waiting for. Off until approved; needs migrations/agent/018 installed.')
param deployDispatcher bool = false
@description('watch records in agent.tasks which job each new data date would have started; live also runs the jobs in dispatcherJobs. The scheduled jobs stay as a backup either way.')
@allowed(['off', 'watch', 'live'])
param dispatcherMode string = 'watch'
@description('With dispatcherMode live: the jobs it may run, comma-separated (holdings_explainer, risk_watch, weekly_brief). Empty: none.')
param dispatcherJobs string = ''
@description('The market news watch\'s job (ibkrv2stg-agent-market-news, docs/AGENTS.md): hourly it reads world stock market news and keeps the market changes it can confirm. Off until approved; needs migrations/agent/019 installed and the APAC report deployment.')
param deployMarketNews bool = false
@description('watch keeps news items and confirmed market changes and records each run in agent.tasks; live also posts one owner-only inbox card per new change.')
@allowed(['off', 'watch', 'live'])
param marketNewsMode string = 'watch'
@description('Monthly token cap for the market news watch.')
param marketNewsTokenBudget int = 2000000
@description('on shows get_market_events to the agents (the onboarding NAV check, incident diagnosis and chat). Needs migrations/agent/019 installed.')
@allowed(['off', 'on'])
param marketEvents string = 'off'

param agentAppName string = 'ibkrv2stg-agent'
param toolsAppName string = 'ibkrv2stg-agent-tools'

// Month-end team (docs/AGENTS.md, "Month-end team"). Off until Ying approves the
// job and its read access by name; a redeploy for anything else leaves it out.
param deployMonthTeam bool = false
@description('APAC Data Zone deployment for report content. The team refuses any other processing scope.')
param reportModelDeployment string = 'wr-reports-apac'
param reportStorageAccountName string = 'ibkrv2stgauhecwc4itaoo'
param reportContainerName string = 'internal-reports'
@description('On only together with the month-end package job\'s team-commentary setting: the team\'s agreed commentary then goes into the decks, never the factsheets.')
param monthTeamLive bool = false
@description('Tokens per calendar month for the month-end team, separate from the daily chat budget.')
param monthTeamTokenBudget int = 40000000
@description('The onboarding agents\' job (ibkrv2stg-agent-onboarding): drafts a new case\'s account terms from its documents before approval 1, and reviews a new composite\'s historical reports before approval 2. Off until approved; needs the month-end team\'s report reader and the APAC report deployment.')
param deployOnboarding bool = false
@description('The private container the website stores onboarding documents in; the onboarding job may read it.')
param intakeContainerName string = 'onboarding-intake'
@description('Monthly token cap for the onboarding agents, separate from the month-end team\'s.')
param onboardingTokenBudget int = 5000000
@description('The operations agent\'s job (ibkrv2stg-agent-ops): every 5 minutes it diagnoses the autonomous administrator\'s open incidents from Azure telemetry, read-only. Off until approved; needs the APAC report deployment.')
param deployOperations bool = false
@description('The Log Analytics workspace the container apps log to; the operations job may query it.')
param opsLogWorkspaceName string = 'ibkrv2stg-logs'
@description('The VM whose metrics the operations job reads.')
param opsVmName string = 'ibkrv2stg-private-admin'
@description('Monthly token cap for the operations agent.')
param opsTokenBudget int = 5000000
@description('Optional Key Vault secret URI of a GitHub token limited to this repository\'s contents and pull requests. Empty: the agent diagnoses but never opens a draft pull request. The jobs identity needs Key Vault Secrets User on that secret first.')
param opsGithubTokenSecretUri string = ''

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
            { name: 'AGENT_MARKET_EVENTS', value: marketEvents }
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
            { name: 'AGENT_CHAT_REASONING_MODE', value: chatReasoningMode }
            { name: 'AGENT_TIMEOUT_SECONDS', value: string(chatTimeoutSeconds) }
            { name: 'AGENT_MAX_OUTPUT_TOKENS', value: string(chatMaxOutputTokens) }
            { name: 'AGENT_MAX_TOOL_CALLS', value: string(chatMaxToolCalls) }
            { name: 'AGENT_SUPERVISOR', value: supervisorMode }
            { name: 'AGENT_SUPERVISOR_USERS', value: supervisorUsers }
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
  // 09:30 HKT on Saturdays: each granted account's weekly brief, in the website inbox only.
  { name: 'ibkrv2stg-agent-weekly-brief', job: 'weekly_brief', cron: '30 1 * * 6' }
  // Every 15 minutes on weekdays across Hong Kong and US trading hours (the job itself checks the
  // market is open): alerts on big live moves of granted accounts whose live view is on. Reads only
  // what PR 58's collector stored; never the gateway.
  { name: 'ibkrv2stg-agent-live-market', job: 'live_market', cron: '*/15 1-8,13-21 * * 1-5' }
  // 09:15 HKT on weekdays, after the morning account sync: alerts on a VaR breach, a switch into the
  // stressed regime or a volatility jump, and once a month a check of the risk models' backtests.
  { name: 'ibkrv2stg-agent-risk-watch', job: 'risk_watch', cron: '15 1 * * 1-5' }
  // 09:45 HKT daily: an inbox card for each saved export pack whose month, quarter or year has closed
  // and whose data is in. No model and no files: the person builds, checks and releases it on the website.
  { name: 'ibkrv2stg-agent-export-packs', job: 'export_packs', cron: '45 1 * * *' }
  // 10:00 HKT on Mondays: recomputes each granted account's statistics from the Metrics guide's formulas
  // with a separate implementation and posts a card when the website or the analyst shows a figure differently.
  { name: 'ibkrv2stg-agent-guide-check', job: 'guide_check', cron: '0 2 * * 1' }
  // 10:30 HKT on Mondays: checks the deployed code against the Metrics guide and, when a figure or a
  // calculation has moved on, drafts the guide update for the owner's inbox. Reads code only, no client data.
  { name: 'ibkrv2stg-agent-guide-keeper', job: 'guide_keeper', cron: '30 2 * * 1' }
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

// The month-end team: hourly from 15:00 to 20:00 HKT on the 2nd to 7th, so each
// account moves on as soon as its data, package or Ying's decision is ready.
// It shares the jobs identity, reads review files and writes only the agent schema.
resource reportStorage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: reportStorageAccountName
}
resource reportBlobs 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' existing = {
  parent: reportStorage
  name: 'default'
}
resource reportContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = {
  parent: reportBlobs
  name: reportContainerName
}
var storageBlobDataReaderRole = '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
resource monthTeamReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployMonthTeam) {
  name: guid(reportContainer.id, jobsIdentity.id, storageBlobDataReaderRole)
  scope: reportContainer
  properties: {
    principalId: jobsIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataReaderRole)
  }
}

// The intake agent reads the case documents (mandate, fee terms) in the private
// intake container, read-only, and only when the onboarding job is deployed.
resource intakeContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = {
  parent: reportBlobs
  name: intakeContainerName
}
resource onboardingIntakeReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployOnboarding && deployMonthTeam) {
  name: guid(intakeContainer.id, jobsIdentity.id, storageBlobDataReaderRole)
  scope: intakeContainer
  properties: {
    principalId: jobsIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataReaderRole)
  }
}

resource monthTeamJob 'Microsoft.App/jobs@2024-03-01' = if (deployMonthTeam) {
  name: 'ibkrv2stg-agent-month-team'
  location: resourceGroup().location
  tags: tags
  dependsOn: [pulls, tools, monthTeamReader]
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${jobsIdentity.id}': {} }
  }
  properties: {
    environmentId: managedEnvironment.id
    configuration: {
      triggerType: 'Schedule'
      replicaTimeout: 3600
      replicaRetryLimit: 0
      scheduleTriggerConfig: {
        cronExpression: '0 7-12 2-7 * *'
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
          args: ['month_team']
          env: concat(commonEnv, [
            { name: 'AGENT_MODEL_ENDPOINT', value: modelEndpoint }
            { name: 'AGENT_MODEL_DEPLOYMENT', value: reportModelDeployment }
            { name: 'AGENT_MODEL_REGION', value: 'southeastasia' }
            { name: 'AGENT_MODEL_PROCESSING_SCOPE', value: 'apac-data-zone' }
            { name: 'AGENT_TOOL_SERVER_URL', value: 'https://${toolsAppName}.internal.${managedEnvironment.properties.defaultDomain}' }
            { name: 'AGENT_TOOL_SERVER_AUDIENCE', value: toolsAudience }
            { name: 'AGENT_MONTH_TEAM_TOKEN_BUDGET', value: string(monthTeamTokenBudget) }
            { name: 'AGENT_MONTH_TEAM_LIVE', value: monthTeamLive ? 'true' : 'false' }
            { name: 'AZURE_STORAGE_ACCOUNT_URL', value: 'https://${reportStorageAccountName}.blob.${environment().suffixes.storage}' }
            { name: 'INTERNAL_REPORT_CONTAINER', value: reportContainerName }
            { name: 'AZURE_CLIENT_ID', value: jobsIdentity.properties.clientId }
            { name: 'AGENT_DB_USER', value: jobsIdentity.name }
          ])
          resources: { cpu: json('0.5'), memory: '1Gi' }
        }
      ]
    }
  }
}

// The onboarding agents: hourly, they draft the account terms of any case
// waiting for approval 1 from its documents, and review the historical reports
// of any composite case waiting in stage 3. Same jobs identity and report reader
// as the month-end team, plus read access to the intake container; it writes
// only agent.onboarding_term_drafts and agent.onboarding_reviews.
resource onboardingJob 'Microsoft.App/jobs@2024-03-01' = if (deployOnboarding && deployMonthTeam) {
  name: 'ibkrv2stg-agent-onboarding'
  location: resourceGroup().location
  tags: tags
  dependsOn: [pulls, tools, monthTeamReader, onboardingIntakeReader]
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${jobsIdentity.id}': {} }
  }
  properties: {
    environmentId: managedEnvironment.id
    configuration: {
      triggerType: 'Schedule'
      replicaTimeout: 3600
      replicaRetryLimit: 0
      scheduleTriggerConfig: {
        cronExpression: '20 * * * *'
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
          args: ['onboarding']
          env: concat(commonEnv, [
            { name: 'AGENT_MODEL_ENDPOINT', value: modelEndpoint }
            { name: 'AGENT_MODEL_DEPLOYMENT', value: reportModelDeployment }
            { name: 'AGENT_MODEL_REGION', value: 'southeastasia' }
            { name: 'AGENT_MODEL_PROCESSING_SCOPE', value: 'apac-data-zone' }
            { name: 'AGENT_TOOL_SERVER_URL', value: 'https://${toolsAppName}.internal.${managedEnvironment.properties.defaultDomain}' }
            { name: 'AGENT_TOOL_SERVER_AUDIENCE', value: toolsAudience }
            { name: 'AGENT_ONBOARDING_TOKEN_BUDGET', value: string(onboardingTokenBudget) }
            { name: 'AZURE_STORAGE_ACCOUNT_URL', value: 'https://${reportStorageAccountName}.blob.${environment().suffixes.storage}' }
            { name: 'INTERNAL_REPORT_CONTAINER', value: reportContainerName }
            { name: 'ONBOARDING_INTAKE_CONTAINER', value: intakeContainerName }
            { name: 'AZURE_CLIENT_ID', value: jobsIdentity.properties.clientId }
            { name: 'AGENT_DB_USER', value: jobsIdentity.name }
          ])
          resources: { cpu: json('0.5'), memory: '1Gi' }
        }
      ]
    }
  }
}

// The operations agent: every 5 minutes, it reads the administrator's open
// incidents, the recent run records and Azure telemetry (container logs, job
// executions, resource health, fired alerts, VM metrics, cost), and writes a
// diagnosis to agent.ops_investigations, which the administrator posts and
// System health shows. Its Azure roles are read-only: Reader and Cost
// Management Reader on this resource group, Log Analytics Reader on the
// workspace. With a GitHub token it may open draft pull requests on this
// repository only, at most three a day; it never merges.
var readerRole = 'acdd72a7-3385-48ef-bd42-f606fba81ae7'
var costReaderRole = '72fafb9e-0641-4937-9268-a91bfd8191a3'
var logReaderRole = '73c42c96-874c-492b-b04d-ab87d138a893'
resource opsWorkspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: opsLogWorkspaceName
}
resource opsReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for role in [readerRole, costReaderRole]: if (deployOperations) {
  name: guid(resourceGroup().id, jobsIdentity.id, role)
  properties: {
    principalId: jobsIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', role)
  }
}]
resource opsLogReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployOperations) {
  name: guid(opsWorkspace.id, jobsIdentity.id, logReaderRole)
  scope: opsWorkspace
  properties: {
    principalId: jobsIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', logReaderRole)
  }
}
var opsGithubSecrets = empty(opsGithubTokenSecretUri) ? [] : [
  { name: 'ops-github-token', keyVaultUrl: opsGithubTokenSecretUri, identity: jobsIdentity.id }
]
var opsGithubEnv = empty(opsGithubTokenSecretUri) ? [] : [
  { name: 'OPS_GITHUB_TOKEN', secretRef: 'ops-github-token' }
]
resource operationsJob 'Microsoft.App/jobs@2024-03-01' = if (deployOperations) {
  name: 'ibkrv2stg-agent-ops'
  location: resourceGroup().location
  tags: tags
  dependsOn: [pulls, tools, opsReader, opsLogReader]
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${jobsIdentity.id}': {} }
  }
  properties: {
    environmentId: managedEnvironment.id
    configuration: {
      triggerType: 'Schedule'
      replicaTimeout: 600
      replicaRetryLimit: 0
      scheduleTriggerConfig: {
        cronExpression: '*/5 * * * *'
        parallelism: 1
        replicaCompletionCount: 1
      }
      registries: [{ server: registry.properties.loginServer, identity: jobsIdentity.id }]
      secrets: opsGithubSecrets
    }
    template: {
      containers: [
        {
          name: 'job'
          image: image
          command: ['python', '-m', 'wealthryse_agent.jobs']
          args: ['operations']
          env: concat(commonEnv, opsGithubEnv, [
            { name: 'AGENT_MODEL_ENDPOINT', value: modelEndpoint }
            { name: 'AGENT_MODEL_DEPLOYMENT', value: reportModelDeployment }
            { name: 'AGENT_MODEL_REGION', value: 'southeastasia' }
            { name: 'AGENT_MODEL_PROCESSING_SCOPE', value: 'apac-data-zone' }
            { name: 'AGENT_TOOL_SERVER_URL', value: 'https://${toolsAppName}.internal.${managedEnvironment.properties.defaultDomain}' }
            { name: 'AGENT_TOOL_SERVER_AUDIENCE', value: toolsAudience }
            { name: 'AGENT_OPS_TOKEN_BUDGET', value: string(opsTokenBudget) }
            { name: 'AZURE_SUBSCRIPTION_ID', value: subscription().subscriptionId }
            { name: 'OPS_RESOURCE_GROUP', value: resourceGroup().name }
            { name: 'OPS_LOG_WORKSPACE_ID', value: opsWorkspace.properties.customerId }
            { name: 'OPS_VM_NAME', value: opsVmName }
            { name: 'AZURE_CLIENT_ID', value: jobsIdentity.properties.clientId }
            { name: 'AGENT_DB_USER', value: jobsIdentity.name }
          ])
          resources: { cpu: json('0.5'), memory: '1Gi' }
        }
      ]
    }
  }
}

// The event dispatcher: every 5 minutes it asks the tool server, as each scheduled job, whether
// that job's data is in (a new holdings or valuation date, a finished week). In watch mode it only
// records what it would start; in live mode it also runs the named jobs for that account, with
// the jobs identity and each job's own access, as the job's scheduled run would.
resource dispatcherJob 'Microsoft.App/jobs@2024-03-01' = if (deployDispatcher) {
  name: 'ibkrv2stg-agent-dispatcher'
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
      replicaTimeout: 900
      replicaRetryLimit: 0
      scheduleTriggerConfig: {
        cronExpression: '*/5 * * * *'
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
          command: ['python', '-m', 'wealthryse_agent.supervisor.dispatcher']
          env: concat(commonEnv, modelEnv, [
            { name: 'AZURE_CLIENT_ID', value: jobsIdentity.properties.clientId }
            { name: 'AGENT_DB_USER', value: jobsIdentity.name }
            { name: 'AGENT_DISPATCHER', value: dispatcherMode }
            { name: 'AGENT_DISPATCHER_JOBS', value: dispatcherJobs }
          ])
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
        }
      ]
    }
  }
}

// The market news watch: hourly it reads the news feeds of the markets in
// src/wealthryse_agent/supervisor/markets.py (public news only, from those hosts only), asks the
// APAC report deployment which items announce a market change, and keeps those code can confirm.
// No client data goes to the model or to any news site.
resource marketNewsJob 'Microsoft.App/jobs@2024-03-01' = if (deployMarketNews) {
  name: 'ibkrv2stg-agent-market-news'
  location: resourceGroup().location
  tags: tags
  dependsOn: [pulls]
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${jobsIdentity.id}': {} }
  }
  properties: {
    environmentId: managedEnvironment.id
    configuration: {
      triggerType: 'Schedule'
      replicaTimeout: 1200
      replicaRetryLimit: 0
      scheduleTriggerConfig: {
        cronExpression: '7 * * * *'
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
          command: ['python', '-m', 'wealthryse_agent.supervisor.market_news']
          env: concat(commonEnv, [
            { name: 'AGENT_MODEL_ENDPOINT', value: modelEndpoint }
            { name: 'AGENT_MODEL_DEPLOYMENT', value: reportModelDeployment }
            { name: 'AGENT_MODEL_REGION', value: 'southeastasia' }
            { name: 'AGENT_MODEL_PROCESSING_SCOPE', value: 'apac-data-zone' }
            { name: 'AGENT_MARKET_NEWS', value: marketNewsMode }
            { name: 'AGENT_MARKET_NEWS_TOKEN_BUDGET', value: string(marketNewsTokenBudget) }
            { name: 'AZURE_CLIENT_ID', value: jobsIdentity.properties.clientId }
            { name: 'AGENT_DB_USER', value: jobsIdentity.name }
          ])
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
        }
      ]
    }
  }
}

output agentApiUrl string = 'https://${agent.properties.configuration.ingress.fqdn}'
output toolServerUrl string = 'https://${tools.properties.configuration.ingress.fqdn}'
