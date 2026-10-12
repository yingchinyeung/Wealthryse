"""Protected source-preserving candidate/fallback/installed onboarding probes."""
import hashlib,json,sys
from pathlib import Path
from deploy_viewer_private_admin_20261008 import request,rid
from deploy_ms_presentation_worker_release import settings_invariant
from control_upgrade_readers_20261010 import save,execution_env
from control_upgrade_legacy_recovery_probe_20261009 import protected
from rehearse_ms_worker_image_rollback import restore_protected
from scan_ms_presentation_worker_candidate import verify
from verify_installed_ms_worker_release import corrected_template
from audit_azure_runtime import az
import deploy_t_runtime_release_20261009 as runtime
runtime.request=request
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.work/platform-upgrade-20261010-stage19'
API='?api-version=2026-07-01'
TARGETS={'onboarding':('ibkrv2stg-onboard-analytics','run_self_service_onboarding_analytics.py',
    '46288cdcfaa511e9a84a1403d2d65f9dd5484317e5aaa3ac15d755087ed7e62c')}
PREFIX='UPGRADE_ONBOARDING_RUNTIME '


def candidate(target):
    if target!='onboarding':raise ValueError('Outside scoped job')
    folder=OUT/'onboarding-security-v2';source,_,output=verify(folder)
    image=output['registry']+'/'+output['repository']+'@'+output['digest']
    record=json.loads((folder/'scan-completed.json').read_bytes())
    remote,_=restore_protected(json.loads((folder/'scan-protected-receipt.json').read_bytes()))
    if remote!=record or record['image']!=image or record['assessment']['status']!='pass' or record['assessment']['blocking_findings']:
        raise ValueError('Protected passing exact candidate scan required')
    for name in ('source-intent','native-build-qualified'):
        archived,_=restore_protected(record['protected_artifact_receipts'][name]);raw=(folder/(name+'.json')).read_bytes()
        if archived['image']!=image or archived['sha256']!=hashlib.sha256(raw).hexdigest() or archived['content']!=json.loads(raw):
            raise ValueError('Protected candidate provenance differs')
    native=json.loads((folder/'native-build-qualified.json').read_bytes());proofs={p['stage']:p for p in native['proofs']}
    if set(proofs)!={'baseline','candidate'} or any(proofs['baseline'][k]!=proofs['candidate'][k] for k in ('source_files','source_manifest_sha256','tests')):
        raise ValueError('Native preservation mismatch')
    return image,source,proofs


def accept(proof,expected):
    for key in ('source_files','source_manifest_sha256','python','openssl','tests','entry_points_imported'):
        if proof.get(key)!=expected[key]:raise ValueError('Runtime evidence differs: '+key)
    for key in ('passed','missing_credentials_rejected','verified_tls_readonly_connection'):
        if proof.get(key) is not True:raise ValueError('Missing acceptance: '+key)
    for key in ('financial_rows_read','database_writes','messages_sent','arm_mutations'):
        if type(proof.get(key)) is not int or proof[key]!=0:raise ValueError('Mutation boundary exceeded')
    if proof.get('normal_onboarding_executed') is not False:raise ValueError('Normal onboarding forbidden')


def main(stage,mode='candidate'):
    if mode not in ('candidate','fallback','installed'):raise ValueError('Unknown mode')
    job=TARGETS['onboarding'][0];path=rid('Microsoft.App/jobs',job)
    image,source,proofs=candidate('onboarding');live=request('GET',path+API)
    invariant=source['before_jobs'][job]['settings_invariant_sha256']
    if settings_invariant(live)!=invariant or live['properties']['template']['containers'][0]['image']!=(image if mode=='installed' else source['base_image']):
        raise ValueError('Live settings drift')
    root=OUT/'onboarding-runtime-v1'
    if stage=='freeze':
        if any(s.get('value') for s in live['properties']['configuration'].get('secrets') or []):raise ValueError('Inline credential archive forbidden')
        save(root,'baseline',{'job':live,'invariant':invariant,'image':source['base_image']});print('Protected original onboarding definition');return
    base=protected(root/'baseline.json');folder=root/mode;expected=proofs['baseline' if mode=='fallback' else 'candidate']
    if base['invariant']!=invariant:raise ValueError('Protected baseline differs')
    if stage=='start':
        runtime.safe_window();runtime.idle(job)
        if folder.exists():raise ValueError('Reconcile earlier dispatch')
        tests={p.stem:p.read_text(encoding='utf-8') for p in (OUT/'onboarding-security-v2/context/tests').glob('test_*.py')}
        code='EXPECTED='+repr(expected)+'\nTEST_SOURCES='+repr(tests)+'\n'+(ROOT/'scripts/probe_upgrade_onboarding_runtime_20261010.py').read_text()
        template=corrected_template(live,code);c=template['containers'][0];c['image']=source['base_image'] if mode=='fallback' else image
        c['env']=[e for e in c.get('env',[]) if e['name']=='DATABASE_URL']
        if len(c['env'])!=1 or not c['env'][0].get('secretRef') or c['env'][0].get('value'):raise ValueError('Existing secret reference only')
        save(folder,'intent',{'image':c['image'],'template':template,'expected':expected,'baseline_receipt':base['protected_receipt'],
            'scope':'Synthetic physical onboarding regressions plus SELECT-only TLS; no normal onboarding or financial reads/writes.'})
        ack=request('POST',path+'/start'+API,data=template);save(folder,'dispatch',{'execution':ack['name']})
        print(json.dumps({'mode':mode,'execution':ack['name']}));return
    if stage!='read':raise ValueError('Unknown stage')
    intent=protected(folder/'intent.json');name=protected(folder/'dispatch.json')['execution'];run=request('GET',path+'/executions/'+name+API)
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Execution payload mismatch')
    execution_env(job,actual.get('env') or [],wanted['env']);state=run['properties']['status'];print(json.dumps({'mode':mode,'status':state}))
    if state not in ('Succeeded','Failed'):return
    if state=='Failed':
        if not (folder/'failure.json').exists():save(folder,'failure',{'passed':False,'execution':name})
        raise ValueError('Probe failed; no deployment')
    query="ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+PREFIX+"' | project Result=parse_json(substring(Log_s,"+str(len(PREFIX))+'))'
    rows=az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',query)
    if len(rows)!=1:print('Awaiting proof ingestion');return
    proof=json.loads(rows[0]['Result']);accept(proof,expected)
    newest=request('GET',path+API)
    if settings_invariant(newest)!=invariant or newest['properties']['template']['containers'][0]['image']!=live['properties']['template']['containers'][0]['image']:
        raise ValueError('Runtime drift during probe')
    if not (folder/'result.json').exists():save(folder,'result',{'passed':True,'image':intent['image'],'proof':proof,'execution':name,'settings_preserved':True})
    print(json.dumps({'mode':mode,'passed':True}))


if __name__=='__main__':main(*sys.argv[1:])
