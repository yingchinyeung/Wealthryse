// Deploy independently. One log alert for the deposits and withdrawals switch (FLOWS_SOURCE,
// docs/MEDALLION_ARCHITECTURE.md): it fires when a reader of return figures kept bronze because
// silver differed, could not be read or had no transfers table, so the difference is looked at
// the day it shows rather than found at a month-end. The lines it counts carry counts and masked
// subjects only, and so does the alert: the job or app, and the reason.
targetScope = 'resourceGroup'
param actionGroupName string = 'ibkrv2stg-alerts'
param workspaceName string = 'ibkrv2stg-logs'
param enabled bool = true

resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' existing = {
  name: actionGroupName
}

resource keptBronze 'Microsoft.Insights/scheduledQueryRules@2023-12-01' = {
  name: 'ibkrv2stg-flows-source-kept-bronze'
  location: 'eastasia'
  kind: 'LogAlert'
  tags: { layer: 'ops', component: 'alerts', owner: 'ying' }
  properties: {
    displayName: 'ibkrv2stg-flows-source-kept-bronze'
    description: 'A return-figure reader used bronze deposits and withdrawals because silver differed, could not be read or had no transfers table (FLOWS_SOURCE). Figures stay as bronze gives them. Run python -m ibkr_etl.flows_source compare, read-only, to count which kinds differ, and keep FLOWS_SOURCE where it is until they are explained.'
    enabled: enabled
    severity: 2
    scopes: [resourceId('Microsoft.OperationalInsights/workspaces', workspaceName)]
    evaluationFrequency: 'PT15M'
    windowSize: 'PT15M'
    autoMitigate: true
    skipQueryValidation: false
    criteria: {
      allOf: [{
        query: '''
ContainerAppConsoleLogs_CL
| where Log_s has 'FLOWS_SOURCE {'
| extend Line = parse_json(substring(Log_s, indexof(Log_s, '{')))
| where tostring(Line.decision) == 'bronze'
| extend Source = iff(isnotempty(ContainerJobName_s), ContainerJobName_s, ContainerAppName_s), Reason = tostring(Line.reason)
| project TimeGenerated, Source, Reason
'''
        timeAggregation: 'Count'
        operator: 'GreaterThan'
        threshold: 0
        dimensions: [
          { name: 'Source', operator: 'Include', values: ['*'] }
          { name: 'Reason', operator: 'Include', values: ['*'] }
        ]
        failingPeriods: {
          numberOfEvaluationPeriods: 1
          minFailingPeriodsToAlert: 1
        }
      }]
    }
    actions: {
      actionGroups: [actionGroup.id]
    }
  }
}
