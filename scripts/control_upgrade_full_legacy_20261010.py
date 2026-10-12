"""Exact four-job security update; all installed app/templates/payloads retained."""
from datetime import datetime,timezone
import copy,hashlib,json,os,re,subprocess,sys
from pathlib import Path
import control_upgrade_legacy_python_20261010 as base
import prepare_upgrade_full_legacy_20261010 as prep

ROOT=prep.ROOT;OUT=prep.OUT;c=base.c;scanner=base.scanner
TARGETS={**prep.TARGETS,'inboxr2':{**prep.TARGETS['inbox'],'job':'ibkrv2stg-inbox-acceptance-r2'}}
ORIGINAL_CANDIDATE=c.candidate;ORIGINAL_ACCEPT=c.accept


def asset(target):
    if target not in TARGETS:raise ValueError('Outside exact four-job scope')
    return 'inbox' if target=='inboxr2' else target


def source_image(target):return prep.source_image(asset(target))


def candidate(target):
    key=asset(target);image,source,proofs=ORIGINAL_CANDIDATE(key);folder=OUT/(key+'-security-v1')
    scan=json.loads((folder/'scan-completed.json').read_bytes())
    if set(scan['assessment']['covered_types'])!={'debian','node-pkg','python-pkg'}:raise ValueError('Full ecosystem coverage required')
    log=folder/'native-build.log';native=json.loads((folder/'native-build-qualified.json').read_bytes())
    if hashlib.sha256(log.read_bytes()).hexdigest()!=native['native_log_sha256']:raise ValueError('Native log changed')
    nodes=[json.loads(line.split('LEGACY_NODE_PROOF ',1)[1]) for line in log.read_text(encoding='utf-8').splitlines() if line.startswith('LEGACY_NODE_PROOF ')]
    if len(nodes)!=2 or {p['stage'] for p in nodes}!={'baseline','candidate'} or any(p.get('renderer_imported') is not True for p in nodes):raise ValueError('Full runtime not qualified')
    for p in nodes:proofs[p['stage']]['node']=p['node']
    if proofs['candidate']['node']!='v24.21.0':raise ValueError('Candidate Node differs')
    return image,source,proofs


def accept(proof,expected):
    ORIGINAL_ACCEPT(proof,expected)
    if proof.get('node')!=expected['node'] or proof.get('renderer_imported') is not True:raise ValueError('Node acceptance missing or changed')


def render_gate(target,image):
    if target!='legacypresentation':return
    evidence=c.protected(OUT/'render-acceptance-v3/result.json')
    proofs=evidence.get('proofs',[])
    if evidence.get('passed') is not True or evidence.get('candidate_image')!=image or evidence.get('original_image')!=source_image(target) or len(proofs)!=2:raise ValueError('Exact native presentation comparison required')
    if {p.get('stage') for p in proofs}!={'baseline','candidate'} or any(p.get('passed') is not True or p.get('slides')!=20 for p in proofs):raise ValueError('Incomplete presentation comparison')
    for key in ('normalized_package_sha256','snapshot_sha256','template_sha256','parts'):
        if proofs[0].get(key)!=proofs[1].get(key) or proofs[0].get(key) is None:raise ValueError('Presentation contents changed')


def deploy(target,stage):
    image,_,_=candidate(target);render_gate(target,image)
    return base.deploy(target,stage)


def probe_source(target,expected):
    folder=OUT/(asset(target)+'-security-v1/context')
    tests={p.stem:p.read_text(encoding='utf-8') for p in (folder/'tests').glob('test_*.py')}
    config=json.loads((folder/'config.json').read_bytes());code=(ROOT/'scripts/probe_upgrade_workflow_runtime_20261010.py').read_text(encoding='utf-8')
    old='    import psycopg\n'
    new='''    import psycopg,subprocess
    result=subprocess.run(['node','--input-type=module','-e',"import {createRequire} from 'node:module';const r=createRequire('/app/scripts/render_monthly_presentation.mjs');const a=await import(r.resolve('@oai/artifact-tool'));if(!a.Presentation||!a.PresentationFile)throw Error('Renderer API missing');console.log(process.version)"],cwd='/app',capture_output=True,text=True,timeout=120,env={'PATH':os.environ.get('PATH','/usr/local/bin:/usr/bin'),'LD_LIBRARY_PATH':os.environ.get('LD_LIBRARY_PATH','/usr/local/lib')})
    if result.returncode or result.stdout.strip()!=EXPECTED['node']:raise ValueError('Full Node runtime differs')
'''
    if code.count(old)!=1:raise ValueError('Probe dependency boundary changed')
    code=code.replace(old,new)
    marker="'entry_points_imported':EXPECTED['entry_points_imported']"
    if code.count(marker)!=1:raise ValueError('Probe acceptance boundary changed')
    code=code.replace(marker,marker+",'node':result.stdout.strip(),'renderer_imported':True")
    # The generic probe reuses the name result for unittest output. Bind the
    # Node version before that scope, not to the final unittest result object.
    code=code.replace("    if result.returncode or result.stdout.strip()!=EXPECTED['node']:raise ValueError('Full Node runtime differs')", "    if result.returncode or result.stdout.strip()!=EXPECTED['node']:raise ValueError('Full Node runtime differs')\n    node_version=result.stdout.strip()")
    code=code.replace("'node':result.stdout.strip()","'node':node_version")
    if asset(target)=='inbox':
        line="        module=runpy.run_path('/app/scripts/'+EXPECTED['entry_points_imported'][0],run_name='qualification_import')"
        if code.count(line)!=1:raise ValueError('Inline import boundary changed')
        code=code.replace(line,"        module={'__name__':'qualification_import'};exec(compile(INLINE_SOURCE,'<preserved-inline>','exec'),module)")
        code=code.replace('except RuntimeError as exc:','except (RuntimeError,ValueError) as exc:')
    return 'EXPECTED='+repr(expected)+'\nTEST_SOURCES='+repr(tests)+'\nMISSING_ERROR='+repr(config['missing_environment_error'])+'\nINLINE_SOURCE='+repr(config.get('inline_source'))+'\n'+code


def fingerprint():
    files=[Path(__file__),Path(base.__file__),Path(prep.__file__),ROOT/'scripts/probe_upgrade_workflow_runtime_20261010.py']
    return hashlib.sha256(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in files},sort_keys=True).encode()).hexdigest()


def configure():
    base.OUT=OUT;base.TARGETS=TARGETS;base.configure();base.r.source_image=source_image
    c.candidate=candidate;c.accept=accept;base.probe_source=probe_source;base.bindings_gate=bindings_gate;base.controller_fingerprint=fingerprint


def freeze():
    prior=c.protected(ROOT/'.work/platform-upgrade-20261010-stage24/closeout.json');old=c.protected(ROOT/'.work/platform-upgrade-20261010-stage24/baseline.json');expected=copy.deepcopy(old['bindings'])
    for item in prior['upgrades']:expected[item['job']]['images']=[item['image']]
    if base.bindings(base.current_resources())!=expected:raise ValueError('Stage24 checkpoint drift')
    c.save(OUT,'baseline',{'checked_at':datetime.now(timezone.utc).isoformat(),'bindings':expected,'prior_receipt':prior['protected_receipt'],'allowed_jobs':[v['job'] for v in TARGETS.values()],
        'scope':'Four manual legacy jobs only, entire app/templates and inline payloads preserved. No normal workflow execution or report replacement.'})


def bindings_gate():
    expected=copy.deepcopy(c.protected(OUT/'baseline.json')['bindings']);now=base.bindings(base.current_resources())
    if set(now)!=set(expected):raise ValueError('Inventory drift')
    for target,spec in TARGETS.items():
        _,_,output=scanner.verify(OUT/(asset(target)+'-security-v1'));image=output['registry']+'/'+output['repository']+'@'+output['digest']
        if now[spec['job']]['images'] not in ([source_image(target)],[image]):raise ValueError('Unreviewed image')
        expected[spec['job']]['images']=now[spec['job']]['images']
    if now!=expected:raise ValueError('Unrelated image or settings drift')


def seal():
    prior=c.protected(ROOT/'.work/platform-upgrade-20261010-stage24/closeout.json');baseline=c.protected(OUT/'baseline.json');expected=copy.deepcopy(baseline['bindings']);upgrades=[]
    for target,spec in TARGETS.items():
        image,source,proofs=candidate(target);render_gate(target,image);folder=OUT/(target+'-runtime-v1');deployment=c.protected(folder/'deployment-v1/verified.json');plan=c.protected(folder/'deployment-v1/plan.json')
        if deployment.get('passed') is not True or deployment['image']!=image or deployment.get('all_nonimage_settings_preserved') is not True or plan['controller_sha256']!=fingerprint():raise ValueError('Deployment incomplete or controller changed')
        receipts={}
        for mode in ('candidate','fallback','installed'):
            result=c.protected(folder/mode/'result.json');base.validate_trial(result,source['base_image'] if mode=='fallback' else image,proofs['baseline' if mode=='fallback' else 'candidate']);receipts[mode]=result['protected_receipt']
        scan=json.loads((OUT/(asset(target)+'-security-v1/scan-completed.json')).read_bytes())
        upgrades.append({'job':spec['job'],'image':image,'old_image':source['base_image'],'native_proofs':proofs,'scan_counts':scan['assessment']['counts'],'runtime_receipts':receipts,'deployment_receipt':deployment['protected_receipt']})
        if expected[spec['job']]['images']!=[source['base_image']]:raise ValueError('Unexpected baseline')
        expected[spec['job']]['images']=[image]
    if base.bindings(base.current_resources())!=expected:raise ValueError('Unrelated live changes')
    remaining=[item for item in prior['remaining_images'] if not set(item['jobs']).intersection(baseline['allowed_jobs'])]
    if remaining:raise ValueError('Legacy image coverage incomplete')
    guards=subprocess.run([sys.executable,'-m','unittest','scripts/test_upgrade_full_legacy_guards_20261010.py','-q'],cwd=ROOT,
        env={**os.environ,'PYTHONPATH':os.pathsep.join(map(str,(ROOT/'src',ROOT/'scripts')))},capture_output=True,text=True,encoding='utf-8',timeout=60)
    if guards.returncode or re.findall(r'Ran (\d+) tests',guards.stderr)!=['20']:raise ValueError('Final deployment guards failed')
    with (OUT/'guard-tests.log').open('x',encoding='utf-8') as f:f.write(guards.stdout+guards.stderr)
    c.save(OUT,'closeout',{'sealed_at':datetime.now(timezone.utc).isoformat(),'upgrades':upgrades,'definitions':len(expected),'remaining_image_families':0,'remaining_images':[],
        'all_nonimage_and_unrelated_workloads_preserved':True,'normal_workflows_executed':False,'financial_data_changed':False,'reports_and_approvals_changed':False,
        'fallback_security_qualified':False,'final_guard_tests':20,'controller_sha256':fingerprint(),'full_visual_render_acceptance':False,'native_presentation_package_equivalence':True,
        'remaining_gates':prior['remaining_gates'],'real_viewer_acceptance':prior['real_viewer_acceptance'],'full_upgrade_complete':False})
    print('Four full-runtime updates sealed; no known legacy image families remain in this checkpoint')


def main(action,*args):
    configure()
    if action=='freeze':return freeze()
    if action=='runtime':return base.runtime(*args)
    if action=='deploy':return deploy(*args)
    if action=='seal':return seal()
    raise ValueError('Unknown stage')


if __name__=='__main__':main(*sys.argv[1:])
