// Metrics-only first stage. SQL text and secret-access logs need a separately
// reviewed redaction/retention design before export; never enable allLogs here.
targetScope = 'resourceGroup'
param workspaceName string
param postgresNames array = ['ibkrv2stg-pg-auhecwc4itaoo', 'ibkrv11-pg-5rcc62xxqblay']
param vaultNames array = ['ibkrv2stg-auhecwc4itaoo', 'ibkrv2flexauhecwc4ita', 'ibkrv11-5rcc62xxqblay']

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: workspaceName
}
resource postgres 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' existing = [for name in postgresNames: {
  name: name
}]
resource vaults 'Microsoft.KeyVault/vaults@2023-07-01' existing = [for name in vaultNames: {
  name: name
}]
resource pgMetrics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = [for (name, i) in postgresNames: {
  name: 'portfolio-metrics-only'
  scope: postgres[i]
  properties: {
    workspaceId: workspace.id
    metrics: [{ category: 'AllMetrics', enabled: true }]
    logs: []
  }
}]
resource vaultMetrics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = [for (name, i) in vaultNames: {
  name: 'portfolio-metrics-only'
  scope: vaults[i]
  properties: {
    workspaceId: workspace.id
    metrics: [{ category: 'AllMetrics', enabled: true }]
    logs: []
  }
}]
