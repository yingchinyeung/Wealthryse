"""Execute installed daily jobs against the exact private recovery host only."""
import copy,json,sys,hashlib
from datetime import datetime,timezone
from pathlib import Path
from deploy_viewer_private_admin_20261008 import request
from deploy_metadata_monitor import BASE
from deploy_t_runtime_release_20261009 import idle
from deploy_ms_presentation_worker_release import settings_invariant
from verify_installed_ms_worker_release import corrected_template
from audit_azure_runtime import az
from run_specialist_gateway_read_verification import retain

ROOT=Path(__file__).resolve().parents[1]/'.work/platform-upgrade-20261009-stage8'
TARGETS={
 'derived':('ibkrv2stg-derived-daily','3e7caaed1be1d5caf64cd027547c0f79f983f29c0d9b7850f6ad472344b4725a','scripts/run_self_service_derived_refresh.py'),
 'benchmark':('ibkrv2stg-benchmark-daily','8ccec008cd81a36ffcda2ba342827532889e66ee69321d72d3f56b8905e5cea6','scripts/refresh_provisional_benchmark.py'),
 'risk':('ibkrv2stg-risk-snapshots','b95e81e28dcb8429563143cf5a7001cd1af4c2acad5a660cbf807ff85c18815f','scripts/build_risk_snapshots.py'),
}
def save(folder,name,value):
    with (folder/(name+'.json')).open('x',encoding='utf-8') as f:json.dump({**value,'protected_receipt':retain(value,'upgrade-clone-cycle-'+name)},f,indent=2)
def main(kind,stage,attempt='v1'):
    if attempt not in ('v1','v2','v3'):raise ValueError('Unknown bounded rehearsal attempt')
    name,digest,entry=TARGETS[kind];folder=ROOT/('clone-'+kind+'-'+attempt);path=BASE+'Microsoft.App/jobs/'+name;api='?api-version=2024-03-01'
    job=request('GET',path+api);container=job['properties']['template']['containers'][0]
    if container['image']!='ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:'+digest or container.get('command')!=['python'] or container.get('args')!=[entry]:raise ValueError('Qualified worker identity/entrypoint differs')
    if stage=='start':
        if folder.exists():raise ValueError('Preserve existing dispatch')
        idle(name)
        clone=request('GET',BASE+'Microsoft.DBforPostgreSQL/flexibleServers/ibkrv2stg-upgrade-recovery-1009?api-version=2024-08-01')
        if clone['properties']['state']!='Ready' or clone['properties']['network']['publicNetworkAccess']!='Disabled':raise ValueError('Private recovery must be ready')
        code='''import os,sys,json,runpy
from urllib.parse import urlsplit,urlunsplit
source='ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com'
target='ibkrv2stg-upgrade-recovery-1009.postgres.database.azure.com'
p=urlsplit(os.environ['DATABASE_URL'])
if p.hostname!=source:raise ValueError('Unexpected original database host')
user,sep,_=p.netloc.rpartition('@')
if not sep:raise ValueError('Existing credential authority required')
os.environ['DATABASE_URL']=urlunsplit((p.scheme,user+'@'+target+(':'+str(p.port) if p.port else ''),p.path,p.query,p.fragment))
os.environ['IBKR_RUN_MIGRATIONS']='false'
import psycopg
from psycopg.conninfo import conninfo_to_dict
connect=psycopg.connect
def only_clone(conninfo='',**kwargs):
    options=conninfo_to_dict(conninfo,**kwargs)
    if options.get('host')!=target:raise ValueError('Blocked connection outside isolated recovery')
    return connect(conninfo,**kwargs)
psycopg.connect=only_clone
sys.path[:0]=['/app/src','/app/scripts']
status=0
try:runpy.run_path('/app/'+ENTRY,run_name='__main__')
except SystemExit as result:status=result.code or 0
print('UPGRADE_CLONE_CYCLE '+json.dumps({'kind':KIND,'exit_code':status,'database_host':target,'live_database_writes':0}),flush=True)
if status:raise SystemExit(status)
'''
        code='ENTRY='+repr(entry)+'\nKIND='+repr(kind)+'\n'+code
        template=corrected_template(job,code);folder.mkdir()
        save(folder,'intent',{'template':template,'job_invariant':settings_invariant(job),'source_sha256':hashlib.sha256(code.encode()).hexdigest(),'dispatched_at':datetime.now(timezone.utc).isoformat(),'live_database_writes':0})
        ack=request('POST',path+'/start'+api,data=template);save(folder,'dispatch',{'execution':ack['name']})
        print(json.dumps({'kind':kind,'execution':ack['name'],'database':'isolated-recovery'}));return
    if stage!='read':raise ValueError('Unknown stage')
    intent=json.loads((folder/'intent.json').read_bytes());execution=json.loads((folder/'dispatch.json').read_bytes())['execution']
    if settings_invariant(job)!=intent['job_invariant']:raise ValueError('Saved job changed')
    run=request('GET',path+'/executions/'+execution+api);status=run['properties']['status']
    print(json.dumps({'kind':kind,'execution':execution,'status':status}))
    if status not in ('Succeeded','Failed'):return
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Executed source differs')
    query="ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+execution+"-' | where Log_s startswith 'UPGRADE_CLONE_CYCLE ' | project Result=parse_json(substring(Log_s,20))"
    records=az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',query)
    if records and not (folder/'result.json').exists():save(folder,'result',{'status':status,'execution':execution,'records':records,'job_settings_preserved':True})
    print(json.dumps(records))
if __name__=='__main__':main(*sys.argv[1:])
