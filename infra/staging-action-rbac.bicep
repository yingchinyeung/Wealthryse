targetScope = 'resourceGroup'

param webIdentityResourceId string
param benchmarkJobName string = 'ibkrv2stg-benchmark-daily'
param riskFreeJobName string = 'ibkrv2stg-risk-free-daily'
param derivedRefreshJobName string = 'ibkrv2stg-derived-daily'
param onboardingReportJobName string = 'ibkrv2stg-onboard-render'
param onboardingAnalyticsJobName string = 'ibkrv2stg-onboard-analytics'
param keyVaultName string
param approvalDatabaseSecretName string = 'database-approval-url'

resource webIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: last(split(webIdentityResourceId, '/'))
}

resource benchmarkJob 'Microsoft.App/jobs@2024-03-01' existing = {
  name: benchmarkJobName
}

resource riskFreeJob 'Microsoft.App/jobs@2024-03-01' existing = {
  name: riskFreeJobName
}

resource derivedRefreshJob 'Microsoft.App/jobs@2024-03-01' existing = {
  name: derivedRefreshJobName
}

resource onboardingReportJob 'Microsoft.App/jobs@2024-03-01' existing = {
  name: onboardingReportJobName
}

resource onboardingAnalyticsJob 'Microsoft.App/jobs@2024-03-01' existing = {
  name: onboardingAnalyticsJobName
}

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource approvalDatabaseSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' existing = {
  parent: vault
  name: approvalDatabaseSecretName
}

// Built-in Container Apps Jobs Operator. Assignment is scoped to each exact
// staging job, so the web identity cannot start, modify or inspect production jobs.
resource jobsOperatorRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' existing = {
  scope: subscription()
  name: 'b9a307c4-5aa3-4b52-ba60-2b17c136cd7b'
}

resource keyVaultSecretsUserRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' existing = {
  scope: subscription()
  name: '4633458b-17de-408a-b874-0445c86b69e6'
}

resource benchmarkOperator 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(benchmarkJob.id, webIdentity.id, jobsOperatorRole.id)
  scope: benchmarkJob
  properties: {
    principalId: webIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: jobsOperatorRole.id
  }
}

resource riskFreeOperator 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(riskFreeJob.id, webIdentity.id, jobsOperatorRole.id)
  scope: riskFreeJob
  properties: {
    principalId: webIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: jobsOperatorRole.id
  }
}

resource derivedRefreshOperator 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(derivedRefreshJob.id, webIdentity.id, jobsOperatorRole.id)
  scope: derivedRefreshJob
  properties: {
    principalId: webIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: jobsOperatorRole.id
  }
}

resource onboardingReportOperator 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(onboardingReportJob.id, webIdentity.id, jobsOperatorRole.id)
  scope: onboardingReportJob
  properties: {
    principalId: webIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: jobsOperatorRole.id
  }
}

resource onboardingAnalyticsOperator 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(onboardingAnalyticsJob.id, webIdentity.id, jobsOperatorRole.id)
  scope: onboardingAnalyticsJob
  properties: {
    principalId: webIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: jobsOperatorRole.id
  }
}

resource approvalSecretReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(approvalDatabaseSecret.id, webIdentity.id, keyVaultSecretsUserRole.id)
  scope: approvalDatabaseSecret
  properties: {
    principalId: webIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: keyVaultSecretsUserRole.id
  }
}

output benchmarkJobId string = benchmarkJob.id
output riskFreeJobId string = riskFreeJob.id
output derivedRefreshJobId string = derivedRefreshJob.id
output onboardingReportJobId string = onboardingReportJob.id
output onboardingAnalyticsJobId string = onboardingAnalyticsJob.id
output approvalDatabaseSecretId string = approvalDatabaseSecret.id
