"""Reuse exact scanned current app image; qualify unchanged assurance policy."""
import hashlib,json,sys
from pathlib import Path
from release_upgrade_derived_tail_20261009 import OUT as DAILY
from deploy_viewer_private_admin_20261008 import request,rid
from scan_ms_presentation_worker_candidate import verify
from rehearse_ms_worker_image_rollback import restore_protected
from control_upgrade_legacy_recovery_probe_20261009 import protected
from deploy_ms_presentation_worker_release import settings_invariant
from verify_installed_ms_worker_release import corrected_template
import deploy_t_runtime_release_20261009 as runtime
from run_specialist_gateway_read_verification import retain
from audit_azure_runtime import az
runtime.request=request
ROOT=Path(__file__).resolve().parents[1];OUT=DAILY.parent/'assurance-current-runtime-v1';FOLDER=OUT/'read-only-v1'
BUILD=ROOT/'.work/platform-upgrade-20261009-stage7/app-release-v1'
BASE=ROOT/'.work/alpha-t-download-names-20261009-v2/web/local-runtime';CURRENT=ROOT/'.work/platform-upgrade-20261009-stage7/runtime'
FILES=('scripts/run_registry_portfolio_assurance.py','scripts/run_portfolio_analyst_assurance.py','src/ibkr_etl/portfolio_assurance.py')
JOB='ibkrv2stg-analyst-assurance';PATH=rid('Microsoft.App/jobs',JOB);API='?api-version=2024-03-01'
OLD='ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:1226d2e0ef97739f2b9f380e8eb153848e98c7cf1b3a1ec81353c3e9fc25eb90'
def candidate():
    _,_,output=verify(BUILD);image=output['registry']+'/'+output['repository']+'@'+output['digest']
    scan=json.loads((BUILD/'scan-completed.json').read_bytes());remote,_=restore_protected(json.loads((BUILD/'scan-protected-receipt.json').read_bytes()))
    if remote!=scan or scan['image']!=image or scan['assessment']['status']!='pass':raise ValueError('Exact protected scan required')
    expected={}
    for name in FILES:
        if (BASE/name).read_bytes()!=(CURRENT/name).read_bytes():raise ValueError('Assurance policy/entrypoint must not change')
        expected[name]=hashlib.sha256((CURRENT/name).read_bytes()).hexdigest()
    return image,expected
def save(name,value):
    with (FOLDER/(name+'.json')).open('x',encoding='utf-8') as f:json.dump({**value,'protected_receipt':retain(value,'upgrade-assurance-candidate-'+name)},f,indent=2)
def main(stage):
    image,expected=candidate();job=request('GET',PATH+API)
    if job['properties']['template']['containers'][0]['image']!=OLD:raise ValueError('Assurance baseline changed')
    if stage=='start':
        if FOLDER.exists():raise ValueError('Reconcile existing qualification')
        runtime.idle(JOB)
        if protected(DAILY/'assurance-cycle-v1/result.json')['passed']:raise ValueError('Expected retained benchmark-reader failure')
        code='EXPECTED_FILES='+repr(expected)+'\n'+Path(__file__).with_name('probe_upgrade_assurance_candidate_20261009.py').read_text(encoding='utf-8')
        compile(code,'qualified-assurance-readonly','exec');template=corrected_template(job,code);c=template['containers'][0];c['image']=image;c['env']=[e for e in c.get('env',[]) if e['name'] in ('DATABASE_URL','PORTFOLIO_TENANT_ID','AZURE_CLIENT_ID')]
        FOLDER.mkdir(parents=True);save('intent',{'image':image,'template':template,'invariant':settings_invariant(job),'verified_assurance_files':expected,'database_writes':0,'model_calls':0})
        ack=request('POST',PATH+'/start'+API,data=template);save('dispatch',{'execution':ack['name']});print(json.dumps({'execution':ack['name']}));return
    if stage!='read':raise ValueError('Unknown stage')
    intent=protected(FOLDER/'intent.json');execution=protected(FOLDER/'dispatch.json')['execution']
    if settings_invariant(job)!=intent['invariant']:raise ValueError('Saved assurance settings changed')
    run=request('GET',PATH+'/executions/'+execution+API);status=run['properties']['status'];a=run['properties']['template']['containers'][0];w=intent['template']['containers'][0]
    if any(a.get(k)!=w.get(k) for k in ('image','command','args')):raise ValueError('Executed candidate differs')
    print(json.dumps({'execution':execution,'status':status}))
    if status not in ('Succeeded','Failed'):return
    prefix='ASSURANCE_CANDIDATE_PROOF '
    q="ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+execution+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+"))"
    rows=az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',q)
    if len(rows)!=1:print('Final proof not yet available');return
    proof=json.loads(rows[0]['Result']);result={'passed':status=='Succeeded' and proof['passed'],'image':image,'execution':execution,'proof':proof,'saved_settings_preserved':True}
    if not (FOLDER/'result.json').exists():save('result',result)
    print(json.dumps(result))
if __name__=='__main__':
    if sys.argv[2:]:
        if sys.argv[2:]!=['period-v2']:raise ValueError('Unknown read-only attempt')
        if protected(FOLDER/'result.json')['passed']:raise ValueError('Existing candidate unexpectedly passed')
        FOLDER=OUT/'period-diagnostic-v2'
    main(sys.argv[1])
