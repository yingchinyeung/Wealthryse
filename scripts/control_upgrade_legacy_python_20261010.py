"""Stage24: three source-preserving legacy Python upgrades.

Two live declarations are tags. Resolve and verify their frozen digest every
time, build/rehearse only immutable images, and retain the real original tag
in the evidence. Rollback is pinned, never an unverified mutable-tag pull.
"""
from datetime import datetime,timezone
import copy,hashlib,json,os,re,subprocess,sys
from pathlib import Path
import release_upgrade_workflow_security_20261010 as r
import control_upgrade_workflow_runtime_20261010 as c
import scan_ms_presentation_worker_candidate as scanner
from deploy_upgrade_inception_20261009 import patch
from retire_upgrade_additional_diagnostics_20261010 import current_resources,bindings
from ibkr_etl.container_scan import assess_container_scan

ROOT=r.ROOT;OUT=ROOT/'.work/platform-upgrade-20261010-stage24';API=c.API
TARGETS={
 'internalrelease':{'job':'ibkrv2stg-internal-release','digest':'4e14e5f4aa57b1e7e5f0b911a9b812774776c1ca0f430e08f41177080fa92294',
    'entry':'release_internal_reports.py','tests':20,'test_files':['scripts/test_upgrade_release_contract_20261010.py'],
    'missing_environment_error':'The internal-release job may run only in the isolated environment'},
 'pilotimport':{'job':'ibkrv2stg-import-u16088571','digest':'e429166fd29694339e6afc961f4913128c578fce3d6ec2c305678a93ce478dd3','tag':'789e7d4',
    'entry':'import_pilot_snapshot.py','tests':12,'test_files':['scripts/test_upgrade_import_contract_20261010.py'],
    'missing_environment_error':'Missing required environment variable: DATABASE_URL'},
 'pilotverify':{'job':'ibkrv2stg-verify-u16088571','digest':'b5b9eca7de40d948dca38df118236ae17a61ba31b7c3d40fb6c78356e2c8c86d','tag':'4731ccf',
    'entry':'verify_pilot_staging.py','tests':14,'test_files':['scripts/test_upgrade_verify_contract_20261010.py'],
    'missing_environment_error':'DATABASE_URL must be a PostgreSQL URL'}}


def configure():
    r.OUT=c.OUT=OUT;r.TARGETS=c.TARGETS=TARGETS;r.snapshot=snapshot


def original(target):
    spec=TARGETS[target]
    return ('ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform:'+spec['tag']) if spec.get('tag') else r.source_image(target)


def verify_tag(target):
    spec=TARGETS[target]
    if spec.get('tag'):
        value=c.az('acr','repository','show','-n','ibkrv2stgauhecwc4itaoo','--image','ibkr-platform:'+spec['tag'])
        if value.get('digest')!='sha256:'+spec['digest']:raise ValueError('Mutable reference changed; do not build, deploy or roll back')


def snapshot(target):
    spec=TARGETS[target];verify_tag(target);job=c.request('GET',c.rid('Microsoft.App/jobs',spec['job'])+API)
    container=job['properties']['template']['containers'][0]
    if container['image']!=original(target) or container.get('command')!=['python'] or container.get('args')!=['scripts/'+spec['entry']] or job['properties']['configuration']['triggerType']!='Manual':raise ValueError('Installed legacy workflow changed')
    return {spec['job']:{'image':container['image'],'settings_invariant_sha256':c.settings_invariant(job)}}


def frozen_bindings():
    prior=c.protected(ROOT/'.work/platform-upgrade-20261010-stage23/closeout.json')
    previous=c.protected(ROOT/'.work/platform-upgrade-20261010-stage23/baseline.json')
    expected=copy.deepcopy(previous['bindings'])
    for item in prior['upgrades']:expected[item['job']]['images']=[item['image']]
    if bindings(current_resources())!=expected:raise ValueError('Stage23 checkpoint drift')
    c.save(OUT,'baseline',{'checked_at':datetime.now(timezone.utc).isoformat(),'bindings':expected,
        'prior_receipt':prior['protected_receipt'],'allowed_jobs':[s['job'] for s in TARGETS.values()],
        'scope':'Source-preserving Python/OS security; original mutable references resolved independently, fallback digest pinned.'})
    print('Protected unchanged 37-definition checkpoint')


def bindings_gate():
    expected=copy.deepcopy(c.protected(OUT/'baseline.json')['bindings']);now=bindings(current_resources())
    if set(now)!=set(expected):raise ValueError('Workload inventory drift')
    for target,spec in TARGETS.items():
        _,_,out=scanner.verify(OUT/(target+'-security-v1'));image=out['registry']+'/'+out['repository']+'@'+out['digest']
        if now[spec['job']]['images'] not in ([original(target)],[image]):raise ValueError('Unreviewed runtime image')
        expected[spec['job']]['images']=now[spec['job']]['images']
    if now!=expected:raise ValueError('Unrelated image or non-image configuration changed')


def probe_source(target,expected):
    tests={p.stem:p.read_text(encoding='utf-8') for p in (OUT/(target+'-security-v1/context/tests')).glob('test_*.py')}
    return 'EXPECTED='+repr(expected)+'\nTEST_SOURCES='+repr(tests)+'\nMISSING_ERROR='+repr(TARGETS[target]['missing_environment_error'])+'\n'+(ROOT/'scripts/probe_upgrade_workflow_runtime_20261010.py').read_text(encoding='utf-8')


def runtime(target,stage,mode='candidate'):
    if target not in TARGETS or mode not in ('candidate','fallback','installed'):raise ValueError('Outside scope')
    spec=TARGETS[target];job=spec['job'];path=c.rid('Microsoft.App/jobs',job)
    image,source,proofs=c.candidate(target);live=c.request('GET',path+API);invariant=source['before_jobs'][job]['settings_invariant_sha256']
    expected_live=image if mode=='installed' else original(target)
    if c.settings_invariant(live)!=invariant or live['properties']['template']['containers'][0]['image']!=expected_live:raise ValueError('Live settings/image changed')
    if mode!='installed':verify_tag(target)
    root=OUT/(target+'-runtime-v1')
    if stage=='freeze':
        if any(s.get('value') for s in live['properties']['configuration'].get('secrets') or []):raise ValueError('Inline secret archive forbidden')
        c.save(root,'baseline',{'job':live,'image':expected_live,'immutable_fallback':source['base_image'],'invariant':invariant});return
    base=c.protected(root/'baseline.json');folder=root/mode;expected=proofs['baseline' if mode=='fallback' else 'candidate']
    if base['invariant']!=invariant or base['immutable_fallback']!=source['base_image']:raise ValueError('Baseline changed')
    if stage=='start':
        c.runtime.safe_window();c.runtime.idle(job)
        if folder.exists():raise ValueError('Reconcile prior dispatch; do not repeat')
        code=probe_source(target,expected)
        template=c.corrected_template(live,code);container=template['containers'][0]
        container['image']=source['base_image'] if mode=='fallback' else image
        container['env']=[e for e in container.get('env',[]) if e['name']=='DATABASE_URL']
        if len(container['env'])!=1 or not container['env'][0].get('secretRef') or container['env'][0].get('value'):raise ValueError('Existing referenced DB credential required')
        c.save(folder,'intent',{'image':container['image'],'template':template,'expected':expected,'baseline_receipt':base['protected_receipt'],
            'scope':'Synthetic workflow plus read-only TLS; no normal release/import/verification, source reads, uploads or approvals.'})
        ack=c.request('POST',path+'/start'+API,data=template);c.save(folder,'dispatch',{'execution':ack['name']});print(json.dumps({'target':target,'mode':mode,'execution':ack['name']}));return
    if stage!='read':raise ValueError('Unknown runtime stage')
    intent=c.protected(folder/'intent.json');name=c.protected(folder/'dispatch.json')['execution'];run=c.request('GET',path+'/executions/'+name+API)
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Execution differs')
    c.execution_env(job,actual.get('env') or [],wanted['env']);state=run['properties']['status'];print(json.dumps({'target':target,'mode':mode,'status':state}))
    if state not in ('Succeeded','Failed'):return
    if state=='Failed':
        if not (folder/'failure.json').exists():c.save(folder,'failure',{'passed':False,'execution':name})
        raise ValueError('Probe failed')
    query="ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+c.PREFIX+"' | project Result=parse_json(substring(Log_s,"+str(len(c.PREFIX))+'))'
    rows=c.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',query)
    if len(rows)!=1:print('Awaiting proof ingestion');return
    proof=json.loads(rows[0]['Result']);c.accept(proof,expected)
    newest=c.request('GET',path+API)
    if c.settings_invariant(newest)!=invariant or newest['properties']['template']['containers'][0]['image']!=expected_live:raise ValueError('Live changed during trial')
    if not (folder/'result.json').exists():c.save(folder,'result',{'passed':True,'image':intent['image'],'proof':proof,'execution':name,'settings_preserved':True})
    print(json.dumps({'target':target,'mode':mode,'passed':True}))


def deploy(target,stage):
    if target not in TARGETS or stage not in ('plan','apply','verify'):raise ValueError('Outside deployment scope')
    spec=TARGETS[target];job=spec['job'];path=c.rid('Microsoft.App/jobs',job)
    image,source,proofs=c.candidate(target);root=OUT/(target+'-runtime-v1');folder=root/'deployment-v1';base=c.protected(root/'baseline.json')
    trials={}
    for mode,wanted in [('candidate',image),('fallback',source['base_image'])]:
        result=c.protected(root/mode/'result.json')
        validate_trial(result,wanted,proofs['baseline' if mode=='fallback' else 'candidate']);trials[mode]=result['protected_receipt']
    live=c.request('GET',path+API);current=live['properties']['template']['containers'][0]['image'];fingerprint=controller_fingerprint()
    if c.settings_invariant(live)!=base['invariant']:raise ValueError('Non-image drift')
    if stage=='plan':
        bindings_gate();verify_tag(target)
        if folder.exists() or current!=original(target):raise ValueError('Existing deployment or image drift')
        body=patch(live,image);projected=copy.deepcopy(live);projected['properties']['template']=body['properties']['template']
        if c.settings_invariant(projected)!=base['invariant']:raise ValueError('Image-only boundary failed')
        c.save(folder,'plan',{'image':image,'before_image':current,'immutable_fallback':source['base_image'],'apply_body':body,
            'rollback_body':patch(live,source['base_image']),'invariant':base['invariant'],'trial_receipts':trials,
            'controller_sha256':fingerprint,'fallback_security_qualified':False,'live_rollback_interruption_performed':False});return
    plan=c.protected(folder/'plan.json')
    if plan['controller_sha256']!=fingerprint or plan['image']!=image:raise ValueError('Plan changed')
    if stage=='apply':
        c.runtime.safe_window();c.runtime.idle(job);bindings_gate();verify_tag(target)
        if current!=original(target) or (folder/'apply-intent.json').exists():raise ValueError('Reconcile previous mutation')
        c.save(folder,'apply-intent',{'started_at':datetime.now(timezone.utc).isoformat(),'plan_receipt':plan['protected_receipt']})
        c.runtime.idle(job);newest=c.request('GET',path+API)
        if c.settings_invariant(newest)!=base['invariant'] or newest['properties']['template']['containers'][0]['image']!=original(target):raise ValueError('Immediate live drift')
        ack=c.request('PATCH',path+API,data=plan['apply_body']);c.save(folder,'apply-ack',{'state':ack.get('properties',{}).get('provisioningState')});print('Submitted scoped immutable image update');return
    if stage!='verify':raise ValueError('Unknown deployment stage')
    if current!=image or live['properties'].get('provisioningState')!='Succeeded':print('Deployment not ready');return
    if not (folder/'verified.json').exists():c.save(folder,'verified',{'passed':True,'image':image,'all_nonimage_settings_preserved':True,'normal_job_started':False,'reports_or_approvals_changed':False})
    print('Installed immutable image and preserved settings verified')


def scan(target):
    if target not in TARGETS:raise ValueError('Outside scan scope')
    folder=OUT/(target+'-security-v1')
    def unchanged(path):
        if path!=folder.resolve() or snapshot(target)!=json.loads((folder/'source-intent.json').read_bytes())['before_jobs']:raise ValueError('Scan/live binding drift')
    scanner.jobs_unchanged=unchanged
    scanner.assess_container_scan=lambda **kw:assess_container_scan(**kw,required_types=frozenset(('debian','python-pkg')))
    sys.argv=[sys.argv[0],'--folder',str(folder)];scanner.main()


def validate_trial(result,image,expected):
    if result.get('passed') is not True or result.get('image')!=image or result.get('settings_preserved') is not True:raise ValueError('Incomplete exact-image trial')
    c.accept(result['proof'],expected)


def controller_fingerprint():return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def seal():
    prior=c.protected(ROOT/'.work/platform-upgrade-20261010-stage23/closeout.json')
    base=c.protected(OUT/'baseline.json');expected=copy.deepcopy(base['bindings']);upgrades=[]
    for target,spec in TARGETS.items():
        image,source,proofs=c.candidate(target);folder=OUT/(target+'-runtime-v1')
        deployment=c.protected(folder/'deployment-v1/verified.json')
        if deployment.get('passed') is not True or deployment['image']!=image or deployment.get('all_nonimage_settings_preserved') is not True:raise ValueError('Deployment incomplete')
        trials={}
        for mode in ('candidate','fallback','installed'):
            result=c.protected(folder/mode/'result.json')
            validate_trial(result,source['base_image'] if mode=='fallback' else image,proofs['baseline' if mode=='fallback' else 'candidate'])
            trials[mode]=result['protected_receipt']
        assessment=json.loads((OUT/(target+'-security-v1/scan-completed.json')).read_bytes())
        upgrades.append({'job':spec['job'],'image':image,'old_declared_image':original(target),'immutable_fallback':source['base_image'],
            'native_proofs':proofs,'scan_counts':assessment['assessment']['counts'],'runtime_receipts':trials,'deployment_receipt':deployment['protected_receipt']})
        if expected[spec['job']]['images']!=[original(target)]:raise ValueError('Baseline differs')
        expected[spec['job']]['images']=[image]
    if bindings(current_resources())!=expected:raise ValueError('Unrelated workload drift')
    remaining=[item for item in prior['remaining_images'] if not set(item['jobs']).intersection(base['allowed_jobs'])]
    if len(remaining)!=3:raise ValueError('Unexpected remaining count')
    guards=subprocess.run([sys.executable,'-m','unittest','scripts/test_upgrade_legacy_python_guards_20261010.py','-q'],cwd=ROOT,
        env={**os.environ,'PYTHONPATH':os.pathsep.join(map(str,(ROOT/'src',ROOT/'scripts'))),'PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,text=True,encoding='utf-8',timeout=60)
    if guards.returncode or re.findall(r'Ran (\d+) tests',guards.stderr)!=['25']:raise ValueError('Final deployment guards failed')
    with (OUT/'guard-tests.log').open('x',encoding='utf-8') as stream:stream.write(guards.stdout+guards.stderr)
    c.save(OUT,'closeout',{'sealed_at':datetime.now(timezone.utc).isoformat(),'upgrades':upgrades,'definitions':len(expected),
        'remaining_image_families':len(remaining),'remaining_images':remaining,'all_nonimage_and_unrelated_workloads_preserved':True,
        'normal_workflows_executed':False,'financial_data_changed':False,'reports_and_approvals_changed':False,
        'fallback_security_qualified':False,'final_guard_tests':25,'controller_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'remaining_gates':prior['remaining_gates'],'real_viewer_acceptance':prior['real_viewer_acceptance'],'full_upgrade_complete':False})
    print('Three exact-image upgrades sealed; three image families remain')


def main(action,*args):
    configure()
    if action=='freeze':return frozen_bindings()
    if action=='release':return r.main(*args)
    if action=='runtime':return runtime(*args)
    if action=='deploy':return deploy(*args)
    if action=='scan':return scan(*args)
    if action=='seal':return seal()
    raise ValueError('Unknown stage')


if __name__=='__main__':main(*sys.argv[1:])
