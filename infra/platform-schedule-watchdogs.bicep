// Infrastructure-only monitoring. No application images, identities or timers.
targetScope = 'resourceGroup'
param rules array
param actionGroupName string = 'ibkrv2stg-alerts'
param enabled bool = false

resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' existing = {
  name: actionGroupName
}
resource alerts 'Microsoft.Insights/scheduledQueryRules@2023-12-01' = [for rule in rules: {
  name: rule.name
  location: 'eastasia'
  kind: 'LogAlert'
  // layer, component and owner: the resource group's Medallion tag rule refuses a rule without them.
  tags: {
    application: 'ibkr-portfolio-platform'
    purpose: 'schedule-telemetry-watchdog'
    layer: 'ops'
    component: 'alerts'
    owner: 'ying'
  }
  properties: {
    displayName: rule.name
    description: 'Expected scheduled execution telemetry is missing. Check Azure execution history and log delivery; this is not proof of stale portfolio data. Existing failed-execution alerts remain separate. No automatic retries.'
    enabled: enabled
    severity: 2
    scopes: [resourceId('Microsoft.OperationalInsights/workspaces', rule.workspaceName)]
    evaluationFrequency: 'PT15M'
    windowSize: 'PT15M'
    overrideQueryTimeRange: 'P2D'
    autoMitigate: true
    skipQueryValidation: false
    criteria: {
      allOf: [{
        query: rule.query
        timeAggregation: 'Count'
        operator: 'GreaterThan'
        threshold: 0
        dimensions: [{ name: 'JobName', operator: 'Include', values: ['*'] }]
        failingPeriods: {
          // Absence queries intentionally synthesize rows without event time.
          // Azure requires one evaluation period for this shape. Debounce is
          // enforced by the query's explicit grace/runtime windows instead.
          numberOfEvaluationPeriods: 1
          minFailingPeriodsToAlert: 1
        }
      }]
    }
    actions: {
      actionGroups: [actionGroup.id]
    }
  }
}]
