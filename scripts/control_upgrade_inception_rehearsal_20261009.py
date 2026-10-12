"""Exact-image normal risk-worker rehearsal, isolated recovery database only."""
import sys,json,base64,gzip,re,hashlib
from datetime import datetime,timezone
import release_upgrade_inception_20261009 as release
from deploy_viewer_private_admin_20261008 import request
from deploy_t_runtime_release_20261009 import idle
from verify_installed_ms_worker_release import corrected_template
from deploy_ms_presentation_worker_release import settings_invariant
from scan_ms_presentation_worker_candidate import verify
from rehearse_ms_worker_image_rollback import restore_protected
from run_specialist_gateway_read_verification import retain
from audit_azure_runtime import az

ROOT=release.FOLDER.parent;FOLDER=ROOT/'clone-risk-candidate-v1'
PATH=release.PATH.split('?')[0];API='?api-version=2024-03-01'

def save(name,value):
    with (FOLDER/(name+'.json')).open('x',encoding='utf-8') as f:json.dump({**value,'protected_receipt':retain(value,'upgrade-inception-rehearsal-'+name)},f,indent=2)

def candidate():
    _,_,out=verify(release.FOLDER);image=out['registry']+'/'+out['repository']+'@'+out['digest']
    scan=release.r.read(release.FOLDER/'scan-completed.json')
    archived,_=restore_protected(release.r.read(release.FOLDER/'scan-protected-receipt.json'))
    if scan!=archived or scan['image']!=image or scan['assessment']['status']!='pass':raise ValueError('Protected exact-image scan required')
    return image

def main(stage,attempt='v1'):
    global FOLDER
    if attempt not in ('v1','history1','history2','history3','history-all','final'):raise ValueError('Unknown bounded rehearsal attempt')
    if attempt!='v1':FOLDER=ROOT/('clone-risk-candidate-'+attempt)
    image=candidate();job=request('GET',PATH+API)
    if job['properties']['template']['containers'][0]['image']!=release.IMAGE:raise ValueError('Saved worker changed')
    if stage=='start':
        if FOLDER.exists():raise ValueError('Preserve existing rehearsal')
        idle(release.JOB)
        original=release.r.read(ROOT/'clone-risk-v1/intent.json')
        archived,_=restore_protected(original['protected_receipt'])
        if archived!={k:v for k,v in original.items() if k!='protected_receipt'} or original['job_invariant']!=settings_invariant(job):raise ValueError('Protected baseline differs')
        packed=re.fullmatch("import base64,gzip;exec\\(compile\\(gzip.decompress\\(base64.b64decode\\('([A-Za-z0-9+/=]+)'\\)\\),'ms-installed-worker-identity-check','exec'\\)\\)",original['template']['containers'][0]['args'][1])
        if not packed:raise ValueError('Unexpected frozen execution encoding')
        source=gzip.decompress(base64.b64decode(packed[1])).decode()
        if hashlib.sha256(source.encode()).hexdigest()!=original['source_sha256']:raise ValueError('Frozen isolation source differs')
        marker="status=0\ntry:runpy.run_path('/app/'+ENTRY,run_name='__main__')"
        if source.count(marker)!=1:raise ValueError('Expected isolated normal entrypoint')
        checks="""from ibkr_etl.db import connect as portfolio_connect
from ibkr_etl.risk_refresh import inception_without_comparisons
from pathlib import Path
db=portfolio_connect(Path('/tmp/unused.sqlite3'),os.environ['DATABASE_URL'],os.environ['PORTFOLIO_TENANT_ID'])
try:
    for day,expected in [('2025-01-20',True),('2025-01-21',True),('2025-01-22',False),('2026-10-08',False)]:
        if inception_without_comparisons(db,tenant_id=os.environ['PORTFOLIO_TENANT_ID'],subject_id='alpha-world-ms',as_of=day) is not expected:raise ValueError('Inception boundary differs on actual inputs')
finally:db.rollback();db.close()
"""
        if attempt=='history-all':
            checks+='''import time
from ibkr_etl.risk_refresh import audit_risk_history
subjects=['U16088571','U16088606','hong-kong-china','alpha-world-ms','alpha-world-t']
db=portfolio_connect(Path('/tmp/unused.sqlite3'),os.environ['DATABASE_URL'],os.environ['PORTFOLIO_TENANT_ID'])
try:
    total=db.execute("SELECT COUNT(*) AS n FROM risk_analytics_snapshots WHERE tenant_id=? AND subject_id=ANY(?::text[]) AND analytics_version='risk-analytics-v2'",(os.environ['PORTFOLIO_TENANT_ID'],subjects)).fetchone()['n']
    db.commit()
    if not 1<=total<=3000:raise ValueError('Historical cache inventory outside bounded scope')
    seen=set();started=time.monotonic();rebuilt=0;unavailable=0
    for wave in range(32):
        if time.monotonic()-started>2900:raise ValueError('Bounded catch-up time exhausted; preserve progress')
        result=audit_risk_history(db,tenant_id=os.environ['PORTFOLIO_TENANT_ID'],subjects=subjects,audit_limit=100,rebuild_limit=100)
        seen.update((r['subject_id'],r['as_of']) for r in result['items']);rebuilt+=result['rebuilt'];unavailable+=result['unavailable']
        print('UPGRADE_HISTORY_PROGRESS '+json.dumps({'wave':wave+1,'unique_checked':len(seen),'expected':total,'rebuilt':rebuilt,'blocked':result['blocked'],'pending':result['pending'],'busy':result['busy'],'unavailable':unavailable}),flush=True)
        if result['blocked'] or result['pending'] or result['busy']:raise ValueError('Historical inputs remain unresolved')
        if len(seen)>=total:break
    if len(seen)!=total:raise ValueError('Historical coverage incomplete')
    print('UPGRADE_HISTORY_CATCHUP '+json.dumps({'passed':True,'unique_checked':len(seen),'expected':total,'rebuilt':rebuilt,'unavailable':unavailable,'live_database_writes':0}),flush=True)
finally:db.rollback();db.close()
'''
        elif attempt.startswith('history'):
            checks+="sys.argv=['/app/scripts/build_risk_snapshots.py','--history-audit-limit','100','--history-rebuild-limit','100']\n"
        source=source.replace(marker,checks+marker)
        template=corrected_template(job,source);template['containers'][0]['image']=image
        FOLDER.mkdir()
        save('intent',{'image':image,'template':template,'job_invariant':settings_invariant(job),'source_sha256':hashlib.sha256(source.encode()).hexdigest(),'live_database_writes':0,'started_at':datetime.now(timezone.utc).isoformat()})
        ack=request('POST',PATH+'/start'+API,data=template);save('dispatch',{'execution':ack['name']});print(json.dumps({'execution':ack['name'],'live_changes':False}));return
    if stage!='read':raise ValueError('Unknown stage')
    intent=release.r.read(FOLDER/'intent.json');execution=release.r.read(FOLDER/'dispatch.json')['execution']
    archived,_=restore_protected(intent['protected_receipt'])
    if archived!={k:v for k,v in intent.items() if k!='protected_receipt'}:raise ValueError('Protected rehearsal intent differs')
    if settings_invariant(job)!=intent['job_invariant']:raise ValueError('Job settings changed')
    run=request('GET',PATH+'/executions/'+execution+API);state=run['properties']['status']
    print(json.dumps({'execution':execution,'status':state}))
    if state not in ('Succeeded','Failed'):return
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Execution identity differs')
    query="ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+execution+"-' | where Log_s startswith 'UPGRADE_CLONE_CYCLE ' | project TimeGenerated,Result=parse_json(substring(Log_s,20))"
    records=az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',query)
    if len(records)==1:
        summary=json.loads(records[0]['Result']);passed=state=='Succeeded' and summary['exit_code']==0 and summary['live_database_writes']==0 and summary['database_host']=='ibkrv2stg-upgrade-recovery-1009.postgres.database.azure.com'
        history=None
        if attempt=='history-all':
            prefix='UPGRADE_HISTORY_CATCHUP '
            query="ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+execution+"-' | where Log_s startswith '"+prefix+"' | project TimeGenerated,Result=parse_json(substring(Log_s,"+str(len(prefix))+"))"
            history_records=az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',query)
            if len(history_records)!=1:raise ValueError('Exactly one complete historical coverage proof required')
            history=json.loads(history_records[0]['Result'])
            if not history.get('passed') or history.get('live_database_writes')!=0 or not 1<=history.get('expected',0)<=3000 or history.get('unique_checked')!=history['expected']:raise ValueError('Full historical acceptance incomplete')
        save('result',{'passed':passed,'execution':execution,'image':image,'status':state,'records':records,'history_coverage':history,'job_settings_preserved':True,'actual_inception_boundaries_checked':True})
    print(json.dumps(records))

if __name__=='__main__':main(*sys.argv[1:])
