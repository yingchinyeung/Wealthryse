"""Image-only assurance update after full private deterministic qualification."""
import copy,hashlib,json,sys
from datetime import datetime,timezone
from pathlib import Path
import deploy_reporting_sector_web_20261011 as w
from deploy_ms_presentation_worker_release import settings_invariant
from deploy_upgrade_inception_20261009 import patch

b=w.b;protected=w.protected;save=w.save
JOB='ibkrv2stg-analyst-assurance';PATH=b.rid('Microsoft.App/jobs',JOB);OUT=w.STAGE/'assurance-deployment-v1'

def expected_bindings():
    web=protected(w.STAGE/'web-boundary-v1/result.json')
    if not web['passed']:raise ValueError('Verified website correction required')
    return copy.deepcopy(web['boundary']['workloads'])

def only_assurance(actual,expected):
    if set(actual)!=set(expected) or {k:v for k,v in actual.items() if k!=JOB}!={k:v for k,v in expected.items() if k!=JOB}:raise ValueError('Unrelated workload drift')

def full_accept(value,image):
    p=value.get('proof',{});rows=p.get('subjects',[])
    if not value.get('passed') or value.get('image')!=image or p.get('image')!=image or p.get('passed') is not True:raise ValueError('Exact full assurance runtime required')
    if len(rows)!=5 or {r['subject'] for r in rows}!=w.SUBJECTS or any(r['checks_failed']!=0 or r['status']=='fail' or r['passed'] is not True for r in rows):raise ValueError('All five deterministic checks must pass')
    if type(p.get('database_writes')) is not int or p['database_writes']!=0 or p.get('model_calls')!=0 or p.get('production_connection_used') is not False:raise ValueError('Private deterministic boundary required')

def sources():
    return {n:hashlib.sha256((w.ROOT/'scripts'/n).read_bytes()).hexdigest() for n in (Path(__file__).name,'test_reporting_assurance_deploy_20261011.py','deploy_reporting_sector_web_20261011.py')}

def main(stage):
    if stage not in ('plan','apply','verify','rollback'):raise ValueError('Unknown operation')
    live=b.request('GET',PATH+b.API)
    if stage=='rollback':
        plan=protected(OUT/'plan.json')
        if settings_invariant(live)!=plan['invariant'] or live['properties']['template']['containers'][0]['image']!=plan['image']:raise ValueError('Rollback target drift')
        only_assurance(b.bindings(b.current_resources()),plan['bindings']);b.runtime.idle(JOB)
        if (OUT/'rollback-intent.json').exists():raise ValueError('Reconcile earlier rollback')
        save(OUT,'rollback-intent',{'at':datetime.now(timezone.utc).isoformat(),'image':plan['before_image']})
        ack=b.request('PATCH',PATH+b.API,data=plan['rollback_body']);save(OUT,'rollback-ack',{'acknowledgement_received':True,'state':ack.get('properties',{}).get('provisioningState')});return
    image,source,tested=w.candidate('assurance')
    full=protected(w.STAGE/'consumer-assurance-full-v2/result.json');full_accept(full,image)
    expected=expected_bindings();before=source['base_image']
    if stage=='plan':
        if OUT.exists() or b.bindings(b.current_resources())!=expected or live['properties']['template']['containers'][0]['image']!=before:raise ValueError('Existing plan or baseline drift')
        if any(s.get('value') for s in live['properties']['configuration'].get('secrets',[])):raise ValueError('No inline secret retention')
        template=live['properties']['template'];container=template['containers'][0]
        if len(template['containers'])!=1 or template.get('initContainers') or template.get('volumes') or container.get('command')!=['python'] or container.get('args')!=['scripts/run_registry_portfolio_assurance.py']:raise ValueError('Reviewed entrypoint required')
        apply=patch(live,image);trial=copy.deepcopy(live);trial['properties']['template']=apply['properties']['template']
        if settings_invariant(trial)!=settings_invariant(live):raise ValueError('Image-only boundary failed')
        import os,re,subprocess
        run=subprocess.run([sys.executable,'-m','unittest','scripts.test_reporting_assurance_deploy_20261011','-q'],cwd=w.ROOT,env={**os.environ,'PYTHONPATH':'src'+os.pathsep+'scripts'},capture_output=True,text=True,timeout=60)
        if run.returncode or re.findall(r'Ran (\d+) tests',run.stderr)!=['8']:raise ValueError('Deployment guards failed')
        save(OUT,'plan',{'image':image,'before_image':before,'before':live,'invariant':settings_invariant(live),'bindings':expected,'sources':sources(),'apply_body':apply,'rollback_body':patch(live,before),
            'runtime_proof':tested['protected_receipt'],'full_assurance_proof':full['protected_receipt'],'guard_tests':8,
            'scope':'Existing assurance worker image only. Source-preserved worker/policy, model configuration, schedules, credentials and financial data unchanged.',
            'rollback_limitation':'Exact previous image retained; known sector currency defect remains in fallback. No fresh live rollback or full scheduled cycle claimed.'})
        print('Protected assurance image-only plan prepared');return
    plan=protected(OUT/'plan.json')
    if sources()!=plan['sources'] or image!=plan['image'] or settings_invariant(live)!=plan['invariant']:raise ValueError('Assurance configuration/source drift')
    only_assurance(b.bindings(b.current_resources()),plan['bindings'])
    if stage=='apply':
        b.runtime.safe_window();b.runtime.idle(JOB)
        if (OUT/'apply-intent.json').exists() or live['properties']['template']['containers'][0]['image']!=before:raise ValueError('Reconcile previous mutation')
        save(OUT,'apply-intent',{'at':datetime.now(timezone.utc).isoformat(),'plan_receipt':plan['protected_receipt']})
        latest=b.request('GET',PATH+b.API)
        if settings_invariant(latest)!=plan['invariant'] or latest['properties']['template']['containers'][0]['image']!=before:raise ValueError('Immediate target drift')
        ack=b.request('PATCH',PATH+b.API,data=plan['apply_body']);save(OUT,'apply-ack',{'acknowledgement_received':True,'state':ack.get('properties',{}).get('provisioningState')});print('Assurance image-only update submitted');return
    if live['properties']['provisioningState']!='Succeeded' or live['properties']['template']['containers'][0]['image']!=image:print('Not ready; not verified');return
    if not (OUT/'verified.json').exists():save(OUT,'verified',{'passed':True,'image':image,'job':JOB,'saved_settings_preserved':True,'unrelated_workloads_preserved':True,'normal_cycle_verified':False,'reports_and_approvals_changed':False,'database_writes':0,'real_viewer_sign_in':'UNVERIFIED','fourteen_day_observation':'UNVERIFIED','full_upgrade_complete':False})
    print('Assurance image and unchanged saved settings verified; scheduled-cycle acceptance remains unverified')

if __name__=='__main__':main(*sys.argv[1:])
