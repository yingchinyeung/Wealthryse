"""Dependency-ordered, previously rehearsed live daily analytics catch-up.

No saved job setting changes. Broker ingestion, report generation and approval
actions are not invoked. Full risk history uses the isolated-qualified loop.
"""
import base64,copy,gzip,hashlib,json,re,sys
from pathlib import Path
from datetime import datetime,timezone
from urllib.parse import urlsplit
from deploy_viewer_private_admin_20261008 import request
from deploy_metadata_monitor import BASE
from deploy_ms_presentation_worker_release import settings_invariant
from deploy_t_runtime_release_20261009 import idle,safe_window
from verify_installed_ms_worker_release import corrected_template
from rehearse_ms_worker_image_rollback import restore_protected
from run_specialist_gateway_read_verification import retain
from audit_azure_runtime import az
from deploy_upgrade_inception_20261009 import FOLDER as DEPLOYMENT,REHEARSAL,accept

ROOT=Path(__file__).resolve().parents[1]/'.work/platform-upgrade-20261009-stage9'
OUT=ROOT/'live-catchup-v2';API='?api-version=2024-03-01'
TARGETS={
 'benchmark':('ibkrv2stg-benchmark-daily','8ccec008cd81a36ffcda2ba342827532889e66ee69321d72d3f56b8905e5cea6','scripts/refresh_provisional_benchmark.py'),
 'derived':('ibkrv2stg-derived-daily','3e7caaed1be1d5caf64cd027547c0f79f983f29c0d9b7850f6ad472344b4725a','scripts/run_self_service_derived_refresh.py'),
 'risk':('ibkrv2stg-risk-snapshots','bd669adb8265ae75d11644cd98a1dec25fd9ba615281a2abc1536e4625d49429','scripts/build_risk_snapshots.py')}

def protected(path):
    value=json.loads(path.read_bytes());remote,_=restore_protected(value['protected_receipt'])
    if remote!={k:v for k,v in value.items() if k!='protected_receipt'}:raise ValueError('Protected evidence differs')
    return value

def save(name,value):
    with (OUT/(name+'.json')).open('x',encoding='utf-8') as f:json.dump({**value,'protected_receipt':retain(value,'upgrade-live-catchup-'+name)},f,indent=2)

def live(kind):
    name,digest,entry=TARGETS[kind];path=BASE+'Microsoft.App/jobs/'+name
    job=request('GET',path+API);c=job['properties']['template']['containers'][0]
    if c['image']!='ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:'+digest or c.get('command')!=['python'] or c.get('args')!=[entry]:raise ValueError('Qualified image or normal entrypoint changed')
    if any(s.get('value') for s in job['properties']['configuration'].get('secrets',[])):raise ValueError('Inline secret archiving prohibited')
    return job,path

def normal_template(job):
    # Match the previously accepted Azure start-request shape; GET materializes
    # optional null fields which the execution POST need not accept.
    template={k:copy.deepcopy(v) for k,v in job['properties']['template'].items() if v is not None}
    if len(template['containers'])!=1:raise ValueError('Exactly one qualified container required')
    return template

def rejected_attempt():
    previous=protected(ROOT/'live-catchup-v1/benchmark-intent.json')
    started=datetime.fromisoformat(previous['started_at'])
    path=BASE+'Microsoft.App/jobs/ibkrv2stg-benchmark-daily/executions';url=path+API;seen=[]
    for _ in range(20):
        page=request('GET',url)
        for row in page.get('value',[]):
            stamp=row['properties'].get('startTime')
            if not stamp:raise ValueError('Execution start is unavailable')
            if datetime.fromisoformat(stamp.replace('Z','+00:00'))>=started:raise ValueError('Reconcile execution after rejected attempt before proceeding')
            seen.append({'name':row['name'],'start':stamp,'status':row['properties']['status']})
        link=page.get('nextLink')
        if not link:return {'prior_intent':previous['protected_receipt'],'no_execution_since_intent':True,'reviewed_executions':len(seen),'reason':'Prior request rejected HTTP 400; optional null template fields now omitted, matching accepted rehearsal request shape'}
        parsed=urlsplit(link)
        if parsed.scheme!='https' or parsed.netloc!='management.azure.com' or parsed.path.lower()!=path.lower():raise ValueError('Unexpected execution pagination')
        url=parsed.path+'?'+parsed.query
    raise ValueError('Execution inventory exceeds bound')

def risk_code(intent):
    packed=intent['template']['containers'][0]['args'][1]
    match=re.fullmatch("import base64,gzip;exec\\(compile\\(gzip.decompress\\(base64.b64decode\\('([A-Za-z0-9+/=]+)'\\)\\),'ms-installed-worker-identity-check','exec'\\)\\)",packed)
    if not match:raise ValueError('Unexpected isolated qualification encoding')
    tested=gzip.decompress(base64.b64decode(match[1])).decode()
    if hashlib.sha256(tested.encode()).hexdigest()!=intent['source_sha256']:raise ValueError('Tested source hash differs')
    start=tested.index('from ibkr_etl.db import connect as portfolio_connect\n');end=tested.index("status=0\ntry:runpy.run_path('/app/'+ENTRY,run_name='__main__')",start)
    checks=tested[start:end]
    # Same tested loop, but truthfully identify this as a live cache refresh.
    if checks.count("'live_database_writes':0")!=1:raise ValueError('Tested loop summary differs')
    checks=checks.replace("'live_database_writes':0","'live_cache_refresh':True")
    header="""import os,sys,json,runpy
from urllib.parse import urlsplit
expected_host='ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com'
if urlsplit(os.environ['DATABASE_URL']).hostname!=expected_host:raise ValueError('Unexpected live database host')
if os.environ['PORTFOLIO_TENANT_ID']!='00000000-0000-0000-0000-000000000001':raise ValueError('Unexpected tenant')
os.environ['IBKR_RUN_MIGRATIONS']='false'
import psycopg
from psycopg.conninfo import conninfo_to_dict
original_connect=psycopg.connect
def only_source(conninfo='',**kwargs):
    if conninfo_to_dict(conninfo,**kwargs).get('host')!=expected_host:raise ValueError('Unexpected secondary database')
    return original_connect(conninfo,**kwargs)
psycopg.connect=only_source
sys.path[:0]=['/app/src','/app/scripts']
"""
    footer="""status=0
sys.argv=['/app/scripts/build_risk_snapshots.py']
try:runpy.run_path(sys.argv[0],run_name='__main__')
except SystemExit as result:status=result.code or 0
print('UPGRADE_LIVE_CYCLE '+json.dumps({'kind':'risk','exit_code':status,'database_host':expected_host,'live_cache_refresh':True}),flush=True)
if status:raise SystemExit(status)
"""
    return header+checks+footer

def main(stage,kind=None):
    if stage=='plan':
        if OUT.exists():raise ValueError('Preserve existing live plan')
        verified=protected(DEPLOYMENT/'verified.json');proof=protected(REHEARSAL/'result.json');accept(proof,verified['image'])
        if not verified['passed'] or not verified['settings_preserved']:raise ValueError('Risk deployment not qualified')
        preflight=protected(ROOT/'live-cycle-preflight-v4/result.json')
        if preflight['status']!='Succeeded' or len(preflight['records'])!=1:raise ValueError('Read-only live preflight required')
        checked=datetime.fromisoformat(preflight['records'][0]['TimeGenerated'].replace('Z','+00:00'))
        if not 0<=(datetime.now(timezone.utc)-checked).total_seconds()<=1800:raise ValueError('Fresh live preflight within thirty minutes required')
        summary=json.loads(preflight['records'][0]['Result'])
        if not summary['passed'] or summary['database_writes']!=0:raise ValueError('Preflight failed')
        reconciliation=rejected_attempt()
        templates={};invariants={};images={}
        for key in TARGETS:
            job,_=live(key);invariants[key]=settings_invariant(job);images[key]=job['properties']['template']['containers'][0]['image']
            template=normal_template(job)
            if key=='risk':template=corrected_template(job,risk_code(protected(REHEARSAL/'intent.json')))
            for c in template['containers']:
                for e in c.get('env',[]):
                    if e.get('secretRef') and e.get('value')=='':e.pop('value')
            templates[key]=template
        OUT.mkdir()
        save('plan',{'templates':templates,'job_invariants':invariants,'images':images,'preflight':summary,'prior_attempt_reconciliation':reconciliation,'created_at':datetime.now(timezone.utc).isoformat(),'controller_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'scope':'Normal benchmark then derived refresh; qualified risk cache catch-up and normal run. No ingestion, report-generation, approval, schedule, identity or saved job changes.','risk_qualification':proof['protected_receipt'],'authorization':'Owner requested completion of the full upgrade and approved the hypothetical SPY identity correction','recovery':'Fresh private 18:00:19 recovery copy qualified; actual broker/report/approval inputs preserved and checked independently after refresh; disposable analytics can be rebuilt. No automatic full-database rollback.'})
        print('Protected dependency-ordered live catch-up plan; no jobs started');return
    if kind not in TARGETS or stage not in ('start','read'):raise ValueError('Unknown catch-up operation')
    plan=protected(OUT/'plan.json');job,path=live(kind)
    if settings_invariant(job)!=plan['job_invariants'][kind] or hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=plan['controller_sha256']:raise ValueError('Frozen live catch-up boundary changed')
    if stage=='start':
        safe_window()
        for name,_,_ in TARGETS.values():idle(name)
        if kind!='benchmark':
            previous='benchmark' if kind=='derived' else 'derived'
            if not protected(OUT/(previous+'-result.json'))['passed']:raise ValueError('Previous dependency did not pass')
        if (OUT/(kind+'-intent.json')).exists():raise ValueError('Reconcile existing execution; never redispatch')
        save(kind+'-intent',{'plan_receipt':plan['protected_receipt'],'started_at':datetime.now(timezone.utc).isoformat(),'template':plan['templates'][kind]})
        ack=request('POST',path+'/start'+API,data=plan['templates'][kind]);save(kind+'-dispatch',{'execution':ack['name']})
        print(json.dumps({'kind':kind,'execution':ack['name'],'saved_job_changed':False}));return
    execution=protected(OUT/(kind+'-dispatch.json'))['execution'];run=request('GET',path+'/executions/'+execution+API);state=run['properties']['status']
    print(json.dumps({'kind':kind,'execution':execution,'status':state}))
    if state not in ('Succeeded','Failed'):return
    actual=run['properties']['template']['containers'][0];wanted=plan['templates'][kind]['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Executed program differs')
    history=None;passed=state=='Succeeded'
    if kind=='risk' and passed:
        prefix='UPGRADE_HISTORY_CATCHUP '
        query="ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+execution+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+"))"
        rows=az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',query)
        if len(rows)!=1:raise ValueError('Full live history evidence not yet available')
        history=json.loads(rows[0]['Result'])
        passed=history.get('passed') and history.get('live_cache_refresh') is True and history.get('expected')==history.get('unique_checked') and 1<=history.get('expected',0)<=3000
    save(kind+'-result',{'passed':passed,'execution':execution,'status':state,'history_coverage':history,'job_settings_preserved':True})
    print(json.dumps({'passed':passed,'history':history}))

if __name__=='__main__':main(*sys.argv[1:])
