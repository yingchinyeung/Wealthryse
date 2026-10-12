"""Allow-listed, read-only resource-group inventory and runtime metadata."""
import concurrent.futures
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from audit_azure_runtime import az, RG

FOLDER = Path(__file__).resolve().parents[1] / '.work/viewer-report-investigation-20261008'

def main():
    assert az('account','show','--query','id') == '3956e821-cee4-403f-8708-e84e2ed490eb'
    resources = az('resource','list','-g',RG,'--query','[].{name:name,type:type,id:id,location:location}')
    jobs = az('containerapp','job','list','-g',RG,'--query',
        '[].{name:name,trigger:properties.configuration.triggerType,cron:properties.configuration.scheduleTriggerConfig.cronExpression,retry:properties.configuration.replicaRetryLimit,timeout:properties.configuration.replicaTimeout,images:properties.template.containers[].image}')
    def recent(job):
        try:
            job['executions'] = az('containerapp','job','execution','list','-g',RG,'-n',job['name'],'--query','[:3].{name:name,status:properties.status,start:properties.startTime,end:properties.endTime}')
        except Exception as error:
            job['error_type'] = type(error).__name__
        return job
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        jobs = list(pool.map(recent,jobs))
    queries = {
        'apps': ('containerapp','list','-g',RG,'--query','[].{name:name,state:properties.provisioningState,revision:properties.latestReadyRevisionName,images:properties.template.containers[].image,min:properties.template.scale.minReplicas,max:properties.template.scale.maxReplicas,traffic:properties.configuration.ingress.traffic}'),
        'databases': ('postgres','flexible-server','list','-g',RG,'--query','[].{name:name,state:state,version:version,ha:highAvailability,backup:backup,network:network,sku:sku,storage:storage}'),
        'storage': ('storage','account','list','-g',RG,'--query','[].{name:name,state:provisioningState,public:publicNetworkAccess,https:enableHttpsTrafficOnly,tls:minimumTlsVersion,allowBlobPublic:allowBlobPublicAccess,network:networkRuleSet}'),
        'vaults': ('keyvault','list','-g',RG,'--query','[].{name:name,state:properties.provisioningState,public:properties.publicNetworkAccess,rbac:properties.enableRbacAuthorization,purge:properties.enablePurgeProtection,network:properties.networkAcls}'),
        'registries': ('acr','list','-g',RG,'--query','[].{name:name,state:provisioningState,admin:adminUserEnabled,public:publicNetworkAccess,network:networkRuleSet}'),
        'metric_alerts': ('monitor','metrics','alert','list','-g',RG,'--query','[].{name:name,enabled:enabled,severity:severity,scopes:scopes}'),
        'query_alerts': ('monitor','scheduled-query','list','-g',RG,'--query','[].{name:name,enabled:enabled,severity:severity,scopes:scopes}'),
    }
    result = {'captured_at':datetime.now(timezone.utc).isoformat(),'resource_group':RG,'resources':resources,'jobs':jobs,'mode':'read_only_no_secrets'}
    def query(item):
        key,args=item
        try: return key,az(*args)
        except Exception as error: return key,{'error_type':type(error).__name__}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        result.update(dict(pool.map(query,queries.items())))
    FOLDER.mkdir(parents=True,exist_ok=True)
    (FOLDER/'resource-group-inventory.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({'resources':len(resources),'types':dict(Counter(r['type'] for r in resources)),'jobs':len(jobs),'recent_scheduled_jobs':[{k:j.get(k) for k in ('name','cron','executions','error_type')} for j in jobs if j['trigger']=='Schedule'],'databases':result['databases'],'read_failures':[k for k,v in result.items() if isinstance(v,dict) and 'error_type' in v]},indent=2))

if __name__ == '__main__': main()
