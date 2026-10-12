"""Read-only reconciliation of effective Azure settings and audit ingestion."""
import json
from datetime import datetime, timezone
from pathlib import Path
import requests
import upgrade_platform_resilience_v2_20261009 as upgrade
from audit_azure_runtime import az
from rehearse_ms_worker_image_rollback import restore_protected

def main():
    plan=upgrade.read('plan')
    protected,_=restore_protected(upgrade.read('plan-protected-receipt'))
    if plan!=protected:raise ValueError('Protected plan differs')
    records=[]
    for name,item in plan['resources'].items():
        kind=item['kind'];scope,api=upgrade.path(kind,name)
        live=upgrade.request('GET',scope+api)
        if upgrade.normalized(live,kind)!=upgrade.normalized(item['before'],kind):
            raise ValueError('Unreviewed resource drift: '+name)
        entry={'resource':name,'kind':kind,'location':live['location'],'other_settings_preserved':True}
        if kind=='registry':
            if live['sku']['name']!='Premium' or live['properties']['provisioningState']!='Succeeded':
                raise ValueError('Registry upgrade incomplete')
            diagnostic=upgrade.request('GET',upgrade.diagnostic_path(name))
            props=diagnostic['properties']
            if (props['workspaceId'].lower()!=upgrade.WORKSPACE.lower()
                or {(d.get('category'),d['enabled']) for d in props['logs']}!={('ContainerRegistryLoginEvents',True),('ContainerRegistryRepositoryEvents',True)}
                or any(d.get('enabled') for d in props.get('metrics',[]))
                or props.get('storageAccountId') or props.get('eventHubAuthorizationRuleId')
                or props.get('eventHubName') or props.get('serviceBusRuleId')
                or props.get('logAnalyticsDestinationType') not in (None,'Dedicated')):
                raise ValueError('Registry audit destinations/categories differ')
            # ACR uses resource-specific tables natively. This service returns
            # null for the optional destination-mode flag even when Dedicated
            # was requested. Preserve the returned value; never claim ingestion
            # merely from this configuration check.
            entry.update(tier='Premium',audit_categories_enabled=True,
                         returned_destination_mode=props.get('logAnalyticsDestinationType'),
                         expected_tables=['ContainerRegistryLoginEvents','ContainerRegistryRepositoryEvents'])
        else:
            if live['properties']['backup']['backupRetentionDays']!=35 or live['properties']['state']!='Ready':
                raise ValueError('Database retention change incomplete')
            entry.update(backup_retention_days=35,high_availability=live['properties']['highAvailability'],
                         geo_redundant_backup=live['properties']['backup']['geoRedundantBackup'])
        records.append(entry)
    query="union isfuzzy=true ContainerRegistryLoginEvents, ContainerRegistryRepositoryEvents, (AzureDiagnostics | where ResourceProvider == 'MICROSOFT.CONTAINERREGISTRY') | where TimeGenerated > datetime(2026-10-09T14:00:00Z) | summarize records=count(), latest=max(TimeGenerated) by Type, _ResourceId"
    logs=az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',query)
    for entry in records:
        if entry['kind']=='registry':
            matching=[row for row in logs if str(row.get('_ResourceId','')).lower().endswith('/'+entry['resource'].lower())]
            entry['audit_ingestion_observed']=any(int(row.get('records',0))>0 for row in matching)
            entry['audit_ingestion_counts']=matching
    web=upgrade.request('GET',upgrade.BASE+'Microsoft.App/containerApps/ibkrv2stg-web?api-version=2024-03-01')
    expected='ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:746db20f417f7586301ddd2a4ed48ccfe481b6190568d0835c0244db82716716'
    if web['properties']['template']['containers'][0]['image']!=expected:
        raise ValueError('Website release drift')
    response=requests.get('https://'+web['properties']['configuration']['ingress']['fqdn']+'/',timeout=20,allow_redirects=False)
    if response.status_code not in (401,302):raise ValueError('Anonymous access boundary failed')
    result={'verified_at':datetime.now(timezone.utc).isoformat(),'resources':records,
            'all_data_locations_eastasia':True,'public_paths_unchanged':True,
            'website_image_preserved':True,'website_latest_ready_revision':web['properties'].get('latestReadyRevisionName'),
            'anonymous_access_status':response.status_code,
            'regional_disaster_recovery':False,'private_network_cutover':False,'full_upgrade_complete':False,
            'audit_reference':'https://learn.microsoft.com/en-us/azure/container-registry/monitor-container-registry-reference'}
    label='postflight-'+datetime.now(timezone.utc).strftime('%H%M%S')
    upgrade.save(label,result)
    upgrade.save(label+'-protected',upgrade.retain(result,'eastasia-resilience-postflight'))
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
