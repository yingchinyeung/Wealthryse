"""Isolated legacy recovery probe with existing identity; no Flex token exposure."""
import json,sys,hashlib
from pathlib import Path
from deploy_viewer_private_admin_20261008 import request,rid
from deploy_ms_presentation_worker_release import settings_invariant
from deploy_t_runtime_release_20261009 import idle
from verify_installed_ms_worker_release import corrected_template
from run_specialist_gateway_read_verification import retain
from rehearse_ms_worker_image_rollback import restore_protected
from audit_azure_runtime import az

ROOT=Path(__file__).resolve().parents[1]/'.work/platform-upgrade-20261009-stage10'
OUT=ROOT/'legacy-recovery-probe-v1';PATH=rid('Microsoft.App/jobs','ibkr-v11-daily');API='?api-version=2024-03-01'
SOURCE=Path(__file__).with_name('probe_upgrade_legacy_recovery_20261009.py')
def protected(path):
    value=json.loads(path.read_bytes());remote,_=restore_protected(value['protected_receipt'])
    if remote!={k:v for k,v in value.items() if k!='protected_receipt'}:raise ValueError('Protected evidence differs')
    return value
def save(name,value):
    with (OUT/(name+'.json')).open('x',encoding='utf-8') as f:json.dump({**value,'protected_receipt':retain(value,'upgrade-legacy-recovery-'+name)},f,indent=2)
def main(stage):
    job=request('GET',PATH+API)
    if stage=='start':
        if OUT.exists():raise ValueError('Preserve existing recovery test')
        idle('ibkr-v11-daily')
        proof=protected(ROOT/'legacy-private-build-proof-v1/result.json')
        if not proof['passed'] or not proof['empty_cache_pull_passed'] or proof['base_image']!='ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:bd669adb8265ae75d11644cd98a1dec25fd9ba615281a2abc1536e4625d49429':raise ValueError('Exact qualified diagnostic image required')
        clone=request('GET',rid('Microsoft.DBforPostgreSQL/flexibleServers','ibkrv11-upgrade-recovery-1009')+'?api-version=2024-08-01')
        if clone['properties']['state']!='Ready' or clone['properties']['network']['publicNetworkAccess']!='Disabled':raise ValueError('Private legacy restore not ready')
        environment=request('GET',job['properties']['environmentId']+API)
        workspace=environment['properties']['appLogsConfiguration']['logAnalyticsConfiguration']['customerId']
        import uuid
        uuid.UUID(workspace)
        source=SOURCE.read_text(encoding='utf-8');template=corrected_template(job,source);c=template['containers'][0]
        c['image']=proof['pushed_image'];c['env']=[e for e in c.get('env',[]) if e['name'] in ('DATABASE_URL','PORTFOLIO_TENANT_ID','AZURE_CLIENT_ID')]
        if {e['name'] for e in c['env']}!={'DATABASE_URL','PORTFOLIO_TENANT_ID','AZURE_CLIENT_ID'} or not next(e for e in c['env'] if e['name']=='DATABASE_URL').get('secretRef'):raise ValueError('Secret-reference-only database configuration required')
        OUT.mkdir();save('intent',{'template':template,'job_invariant':settings_invariant(job),'source_sha256':hashlib.sha256(source.encode()).hexdigest(),'log_workspace_id':workspace,'database_writes':0,'broker_token_exposed':False})
        ack=request('POST',PATH+'/start'+API,data=template);save('dispatch',{'execution':ack['name']});print(json.dumps({'execution':ack['name'],'read_only':True}));return
    if stage!='read':raise ValueError('Unknown stage')
    intent=protected(OUT/'intent.json');execution=protected(OUT/'dispatch.json')['execution']
    if settings_invariant(job)!=intent['job_invariant']:raise ValueError('Legacy job changed')
    run=request('GET',PATH+'/executions/'+execution+API);state=run['properties']['status'];print(json.dumps({'execution':execution,'status':state}))
    if state not in ('Succeeded','Failed'):return
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Actual probe differs')
    query="ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+execution+"-' | where Log_s startswith 'LEGACY_RECOVERY_PROOF ' | project TimeGenerated,Result=parse_json(substring(Log_s,22))"
    # Prefix is exactly 22 characters; use its measured length to avoid truncation.
    query=query.replace('substring(Log_s,22)','substring(Log_s,'+str(len('LEGACY_RECOVERY_PROOF '))+')')
    rows=az('monitor','log-analytics','query','-w',intent['log_workspace_id'],'--analytics-query',query)
    if len(rows)!=1:print('Proof not yet available in audit logs');return
    result=json.loads(rows[0]['Result'])
    record={'passed':state=='Succeeded' and result['passed'] and result['database_writes']==0,'execution':execution,'proof':result,'job_settings_preserved':True}
    if not (OUT/'result.json').exists():save('result',record)
    elif {k:v for k,v in protected(OUT/'result.json').items() if k!='protected_receipt'}!=record:raise ValueError('Re-read proof differs')
    print(json.dumps({'passed':record['passed'],'tables':len(result['source']['tables']),'differences':result['table_differences']}))
if __name__=='__main__':main(sys.argv[1])
