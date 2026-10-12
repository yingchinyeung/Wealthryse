"""Candidate, fallback and installed execution gates for two manual jobs only."""
import hashlib,json,sys
from pathlib import Path
from release_upgrade_workflow_security_20261010 import ROOT,OUT,TARGETS,API
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
PREFIX='UPGRADE_WORKFLOW_RUNTIME '


def candidate(target):
    if target not in TARGETS:raise ValueError('Outside two-workflow scope')
    folder=OUT/(target+'-security-v1');source,_,output=verify(folder)
    image=output['registry']+'/'+output['repository']+'@'+output['digest']
    record=json.loads((folder/'scan-completed.json').read_bytes())
    remote,_=restore_protected(json.loads((folder/'scan-protected-receipt.json').read_bytes()))
    if record!=remote or record['image']!=image or record['assessment']['status']!='pass' or record['assessment']['blocking_findings']:raise ValueError('Protected passing exact-image scan required')
    for name in ('source-intent','native-build-qualified'):
        archived,_=restore_protected(record['protected_artifact_receipts'][name]);raw=(folder/(name+'.json')).read_bytes()
        if archived['image']!=image or archived['sha256']!=hashlib.sha256(raw).hexdigest() or archived['content']!=json.loads(raw):raise ValueError('Candidate provenance differs')
    native=json.loads((folder/'native-build-qualified.json').read_bytes());proofs={p['stage']:p for p in native['proofs']}
    if set(proofs)!={'baseline','candidate'} or any(proofs['baseline'][k]!=proofs['candidate'][k] for k in ('source_files','source_manifest_sha256','tests')):raise ValueError('Source preservation differs')
    if proofs['candidate']['tests']!=TARGETS[target]['tests']:raise ValueError('Test count changed')
    return image,source,proofs


def accept(proof,expected):
    for key in ('source_files','source_manifest_sha256','python','openssl','tests','entry_points_imported'):
        if proof.get(key)!=expected[key]:raise ValueError('Runtime identity differs: '+key)
    for key in ('passed','missing_credentials_rejected','verified_tls_readonly_connection'):
        if proof.get(key) is not True:raise ValueError('Missing runtime acceptance: '+key)
    for key in ('financial_rows_read','database_writes','messages_sent','arm_mutations'):
        if type(proof.get(key)) is not int or proof[key]!=0:raise ValueError('Read-only boundary exceeded')
    if proof.get('normal_workflow_executed') is not False:raise ValueError('Normal workflow execution forbidden')


def main(target,stage,mode='candidate'):
    if target not in TARGETS or mode not in ('candidate','fallback','installed'):raise ValueError('Outside scope')
    spec=TARGETS[target];job=spec['job'];path=rid('Microsoft.App/jobs',job)
    image,source,proofs=candidate(target);live=request('GET',path+API);invariant=source['before_jobs'][job]['settings_invariant_sha256']
    if settings_invariant(live)!=invariant or live['properties']['template']['containers'][0]['image']!=(image if mode=='installed' else source['base_image']):raise ValueError('Live settings drift')
    root=OUT/(target+'-runtime-v1')
    if stage=='freeze':
        if any(s.get('value') for s in live['properties']['configuration'].get('secrets') or []):raise ValueError('Inline credential archive forbidden')
        save(root,'baseline',{'job':live,'invariant':invariant,'image':source['base_image']});print('Protected '+target+' baseline');return
    base=protected(root/'baseline.json');folder=root/mode;expected=proofs['baseline' if mode=='fallback' else 'candidate']
    if base['invariant']!=invariant:raise ValueError('Baseline changed')
    if stage=='start':
        runtime.safe_window();runtime.idle(job)
        if folder.exists():raise ValueError('Reconcile previous dispatch')
        tests={p.stem:p.read_text(encoding='utf-8') for p in (OUT/(target+'-security-v1/context/tests')).glob('test_*.py')}
        code='EXPECTED='+repr(expected)+'\nTEST_SOURCES='+repr(tests)+'\nMISSING_ERROR='+repr(spec['missing_environment_error'])+'\n'+(ROOT/'scripts/probe_upgrade_workflow_runtime_20261010.py').read_text(encoding='utf-8')
        template=corrected_template(live,code);c=template['containers'][0];c['image']=source['base_image'] if mode=='fallback' else image
        c['env']=[e for e in c.get('env',[]) if e['name']=='DATABASE_URL']
        if len(c['env'])!=1 or not c['env'][0].get('secretRef') or c['env'][0].get('value'):raise ValueError('Existing database reference required')
        save(folder,'intent',{'image':c['image'],'template':template,'expected':expected,'baseline_receipt':base['protected_receipt'],
            'scope':'Synthetic workflow contracts plus SELECT-only verified TLS; no normal workflow, financial reads, storage writes or approvals.'})
        ack=request('POST',path+'/start'+API,data=template);save(folder,'dispatch',{'execution':ack['name']})
        print(json.dumps({'target':target,'mode':mode,'execution':ack['name']}));return
    if stage!='read':raise ValueError('Unknown stage')
    intent=protected(folder/'intent.json');name=protected(folder/'dispatch.json')['execution'];run=request('GET',path+'/executions/'+name+API)
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Execution identity mismatch')
    execution_env(job,actual.get('env') or [],wanted['env']);state=run['properties']['status'];print(json.dumps({'target':target,'mode':mode,'status':state}))
    if state not in ('Succeeded','Failed'):return
    if state=='Failed':
        if not (folder/'failure.json').exists():save(folder,'failure',{'passed':False,'execution':name})
        raise ValueError('Probe failed; no deployment')
    query="ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+PREFIX+"' | project Result=parse_json(substring(Log_s,"+str(len(PREFIX))+'))'
    rows=az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',query)
    if len(rows)!=1:print('Awaiting proof ingestion');return
    proof=json.loads(rows[0]['Result']);accept(proof,expected)
    newest=request('GET',path+API)
    if settings_invariant(newest)!=invariant or newest['properties']['template']['containers'][0]['image']!=live['properties']['template']['containers'][0]['image']:raise ValueError('Settings changed during trial')
    if not (folder/'result.json').exists():save(folder,'result',{'passed':True,'image':intent['image'],'proof':proof,'execution':name,'settings_preserved':True})
    print(json.dumps({'target':target,'mode':mode,'passed':True}))


if __name__=='__main__':main(*sys.argv[1:])
