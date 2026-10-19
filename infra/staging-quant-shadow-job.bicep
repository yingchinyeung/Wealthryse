// Temporary job for the shadow run of the gold quant build on a restored copy (Fri 30 Oct 2026).
// docs/QUANT_ANALYTICS.md, "Shadow run on a restored copy (Fri 30 Oct)", has the plan. Deployed
// that day, started by hand once, and deleted the same day: it has no schedule and grants no
// roles. It runs scripts/quant_shadow_run.py preflight, apply-held, run and review against the
// copy whose admin URL is in the Key Vault secret named below, never the live server (the
// script's guard refuses any other host before connecting) and never through the bootstrap job.
//
// Network: the job runs in ibkrv2stg-env (container-apps subnet 10.50.0.0/23). A copy restored
// into the upgrade-recovery subnet (10.50.11.0/27) is reachable from there: that subnet's NSG
// allows 5432 from 10.50.0.0/23, and the copy keeps the private DNS zone linked to the VNet. Jobs
// in this environment reached the recovery copy at 10.50.11.4 on 10 Oct. The admin VM path in
// the docs is the fallback if the execution cannot connect.
targetScope = 'resourceGroup'

param jobName string = 'ibkrv2stg-quant-shadow'
param environmentName string = 'ibkrv2stg-env'
param registryName string
param keyVaultName string
// An existing identity that already reads secrets in keyVaultName and pulls from registryName
// (ibkrv2stg-ingestion-identity). No role is assigned here.
param identityResourceId string
// The image built for the day (ibkr-platform:quant-shadow-<commit>). Required: no default.
@minLength(1)
param imageTag string
// The secret holding the copy's admin URL (postgresql://...@<copy host>:5432/...). Set that day,
// deleted the same day.
@minLength(1)
param copyUrlSecretName string
// The copy's host name. The script refuses a URL naming any other host.
@minLength(1)
param copyHost string
// The copy's host must resolve inside this subnet (upgrade-recovery).
param copySubnet string = '10.50.11.0/27'
// Live servers the script must refuse, on top of the two it knows.
param liveDatabaseHosts string = 'ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com,ibkrv2stg.postgres.database.azure.com,ibkrv11-pg-5rcc62xxqblay.postgres.database.azure.com'
// Long enough for the latest-date pass and a year of history. The run command stops starting
// subject-dates 30 minutes before this, leaving time for the review.
@minValue(3600)
@maxValue(28800)
param replicaTimeoutSeconds int = 14400
param tenantId string = '00000000-0000-0000-0000-000000000001'

var runMinutes = (replicaTimeoutSeconds / 60) - 30

var tags = {
  application: 'ibkr-portfolio-platform'
  environment: 'internal-production'
  owner: 'portfolio-platform'
  costCenter: 'pilot'
  version: '2.0'
  dataClassification: 'confidential-financial'
  workload: 'quant-shadow'
  temporary: 'delete-same-day'
}

resource managedEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: environmentName
}
resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: registryName
}
resource vault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}
resource jobIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: last(split(identityResourceId, '/'))
}

resource job 'Microsoft.App/jobs@2024-03-01' = {
  name: jobName
  location: resourceGroup().location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${jobIdentity.id}': {}
    }
  }
  properties: {
    environmentId: managedEnvironment.id
    configuration: {
      // Started by hand once (az containerapp job start); never on a schedule.
      triggerType: 'Manual'
      replicaTimeout: replicaTimeoutSeconds
      replicaRetryLimit: 0
      manualTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
      }
      registries: [
        {
          server: registry.properties.loginServer
          identity: jobIdentity.id
        }
      ]
      secrets: [
        {
          name: 'copy-admin-url'
          keyVaultUrl: '${vault.properties.vaultUri}secrets/${copyUrlSecretName}'
          identity: jobIdentity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'quant-shadow'
          image: '${registry.properties.loginServer}/ibkr-platform:${imageTag}'
          command: ['/bin/sh', '-c']
          // preflight (read-only), apply-held, run, then review, whose output is the job's last
          // log lines. The review runs whatever the run's outcome, so a failure is reported too.
          args: [
            '''
set -u
s=scripts/quant_shadow_run.py
c="--copy-host $QUANT_SHADOW_COPY_HOST --copy-subnet $QUANT_SHADOW_COPY_SUBNET"
python $s preflight $c || exit 2
if python $s apply-held $c; then
  python $s run $c --runner job --max-minutes $QUANT_SHADOW_MAX_MINUTES
  status=$?
else
  status=1
fi
python $s review $c --out /tmp/quant-shadow-review || exit 1
exit $status
'''
          ]
          env: [
            { name: 'DATABASE_ADMIN_URL', secretRef: 'copy-admin-url' }
            { name: 'PORTFOLIO_TENANT_ID', value: tenantId }
            { name: 'LIVE_DATABASE_HOSTS', value: liveDatabaseHosts }
            { name: 'QUANT_SHADOW_COPY_HOST', value: copyHost }
            { name: 'QUANT_SHADOW_COPY_SUBNET', value: copySubnet }
            { name: 'QUANT_SHADOW_MAX_MINUTES', value: string(runMinutes) }
            // The daily job's budget, so the latest-date pass is timed as the job would run it.
            { name: 'QUANT_ANALYTICS_MINUTES', value: '8' }
            { name: 'MEDALLION_DIR', value: '/app/medallion' }
            { name: 'IBKR_RUN_MIGRATIONS', value: 'false' }
          ]
          // The daily job's size, so the timing is the job's.
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
    }
  }
}

output jobName string = job.name
output runMinutes int = runMinutes
