// v2's own alert destination. Until 18 Oct 2026 every v2 alert used v1.1's
// `ibkr-v11-daily-alerts` group (created by infra/job.bicep), so retiring v1.1
// would have silenced them. This group has the same single owner email receiver.
// Deploy on its own; the alert modules only reference it by name.
targetScope = 'resourceGroup'

param actionGroupName string = 'ibkrv2stg-alerts'
// The owner address that ibkr-v11-daily-alerts uses; passed at deploy time.
param alertEmail string

resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: actionGroupName
  location: 'global'
  tags: {
    application: 'ibkr-portfolio-platform'
    environment: 'internal-production'
    version: '2.0'
    layer: 'ops'
    component: 'alerts'
    owner: 'ying'
  }
  properties: {
    groupShortName: 'ibkrv2'
    enabled: true
    emailReceivers: [
      {
        name: 'operations-email'
        emailAddress: alertEmail
        useCommonAlertSchema: true
      }
    ]
  }
}

output actionGroupId string = actionGroup.id
