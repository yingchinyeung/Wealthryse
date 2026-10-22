// Deploy independently: never redeploy a job merely to extend alert coverage.
targetScope = 'resourceGroup'
param actionGroupName string = 'ibkrv2stg-alerts'
param jobNames array = [
  'ibkrv2stg-derived-daily'
  'ibkrv2stg-risk-snapshots'
  'ibkrv2stg-institutional-daily'
  'ibkrv2stg-flex-discovery'
  'ibkrv2stg-onboard-render'
  'ibkrv2stg-onboard-analytics'
]
resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' existing = {
  name: actionGroupName
}
resource alerts 'Microsoft.Insights/metricAlerts@2018-03-01' = [for jobName in jobNames: {
  name: '${jobName}-failed-execution'
  location: 'global'
  tags: { layer: 'ops', component: 'alerts', owner: 'ying' }
  properties: {
    description: 'Portfolio platform job failed; inspect the execution and its correlated logs.'
    severity: 1
    enabled: true
    scopes: [resourceId('Microsoft.App/jobs', jobName)]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [{
        name: 'FailedExecutions'
        metricNamespace: 'Microsoft.App/jobs'
        metricName: 'Executions'
        operator: 'GreaterThan'
        threshold: 0
        timeAggregation: 'Total'
        criterionType: 'StaticThresholdCriterion'
        dimensions: [{ name: 'state', operator: 'Include', values: ['Failed'] }]
      }]
    }
    autoMitigate: true
    actions: [{ actionGroupId: actionGroup.id }]
  }
}]
