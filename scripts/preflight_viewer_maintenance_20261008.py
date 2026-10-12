"""Read-only maintenance dependency/capacity/backup inventory; no secret values."""
import concurrent.futures, hashlib, json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from audit_azure_runtime import az, RG
from deploy_metadata_monitor import request, BASE, SUB
from run_specialist_gateway_read_verification import retain

FOLDER=Path(__file__).resolve().parents[1]/'.work/viewer-report-investigation-20261008/maintenance-v1'
SERVERS=('ibkrv2stg-pg-auhecwc4itaoo','ibkrv11-pg-5rcc62xxqblay')
API='?api-version=2024-08-01'

def main():
    if az('account','show','--query','id')!=SUB:raise ValueError('Wrong subscription')
    target=FOLDER/'preflight.json'
    if target.exists():raise ValueError('Preserve previous preflight')
    now=datetime.now(timezone.utc)
    def database(name):
        path=BASE+'Microsoft.DBforPostgreSQL/flexibleServers/'+name
        server=request('GET',path+API)
        if server['name']!=name or server['properties']['network']['publicNetworkAccess']!='Disabled':raise ValueError('Unexpected database/private boundary')
        parameters={key:request('GET',path+'/configurations/'+key+API)['properties']['value'] for key in ('max_connections','max_wal_senders','max_replication_slots')}
        metrics=[]
        for metric in ('cpu_percent','memory_percent','cpu_credits_remaining'):
            result=az('monitor','metrics','list','--resource',path,'--metric',metric,'--interval','PT1H','--aggregation','Average','Minimum','Maximum','--start-time',(now-timedelta(days=1)).strftime('%Y-%m-%dT%H:%M:%SZ'),'--end-time',now.strftime('%Y-%m-%dT%H:%M:%SZ'))
            points=[p for m in result.get('value',[]) for s in m.get('timeseries',[]) for p in s.get('data',[])]
            values={k:[p[k] for p in points if p.get(k) is not None] for k in ('average','minimum','maximum')}
            metrics.append({'name':metric,'hourly_average_mean':sum(values['average'])/len(values['average']) if values['average'] else None,'minimum':min(values['minimum']) if values['minimum'] else None,'maximum':max(values['maximum']) if values['maximum'] else None,'populated_hours':len(values['average'])})
        return {'baseline':server,'parameters':parameters,'capacity':metrics}
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:databases=list(pool.map(database,SERVERS))
    networks=az('network','vnet','list','-g',RG,'--query','[].{name:name,id:id,addressSpace:addressSpace.addressPrefixes,subnets:subnets[].{name:name,id:id,prefix:addressPrefix,prefixes:addressPrefixes,delegations:delegations[].serviceName,privateEndpointPolicy:privateEndpointNetworkPolicies,ipConfigurations:ipConfigurations[].id}}')
    environments=az('containerapp','env','list','-g',RG,'--query','[].{name:name,id:id,state:properties.provisioningState,vnet:properties.vnetConfiguration,zoneRedundant:properties.zoneRedundant}')
    jobs=az('containerapp','job','list','-g',RG,'--query','[].{name:name,id:id,trigger:properties.configuration.triggerType,cron:properties.configuration.scheduleTriggerConfig.cronExpression,environment:properties.environmentId,secretBindings:properties.configuration.secrets[].{name:name,keyVaultUrl:keyVaultUrl,identity:identity},images:properties.template.containers[].image}')
    def running(job):
        rows=az('containerapp','job','execution','list','-g',RG,'-n',job['name'],'--query',"[?properties.status=='Running' || properties.status=='Processing' || properties.status=='Pending'].{name:name,status:properties.status,start:properties.startTime}")
        return {'job':job['name'],'executions':rows}
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:active=[r for r in pool.map(running,jobs) if r['executions']]
    registry=az('acr','list','-g',RG,'--query','[].{name:name,id:id,sku:sku.name,public:publicNetworkAccess,admin:adminUserEnabled}')
    web=request('GET',BASE+'Microsoft.App/containerApps/ibkrv2stg-web?api-version=2024-03-01')
    auth=request('GET',web['id']+'/authConfigs/current?api-version=2024-03-01')
    record={'captured_at':now.isoformat(),'maintenance_window':'Owner explicitly selected now on 2026-10-08','databases':databases,'networks':networks,'environments':environments,'jobs':jobs,'active_executions':active,'registries':registry,'website_image':web['properties']['template']['containers'][0]['image'],'website_auth_sha256':hashlib.sha256(json.dumps(auth['properties'],sort_keys=True).encode()).hexdigest(),'secret_values_read':0,'production_mutations':0,'limits':['CPU metrics are hourly aggregates, not a load-test result.','No public network restriction until each caller and administrative recovery path is qualified.','Database changes require isolated application/report recovery and a fresh pre-mutation idle check.']}
    FOLDER.mkdir(parents=True,exist_ok=True)
    receipt=retain(record,'viewer-maintenance-preflight')
    target.write_text(json.dumps({**record,'protected_receipt':receipt},indent=2),encoding='utf-8')
    print(json.dumps({'databases':[{'name':d['baseline']['name'],'state':d['baseline']['properties']['state'],'sku':d['baseline']['sku'],'ha':d['baseline']['properties']['highAvailability'],'backup':d['baseline']['properties']['backup'],'parameters':d['parameters'],'capacity':d['capacity']} for d in databases],'networks':networks,'registries':registry,'active_executions':active,'production_mutations':0},indent=2))

if __name__=='__main__':main()
