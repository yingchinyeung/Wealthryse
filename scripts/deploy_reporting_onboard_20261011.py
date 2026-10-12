"""Guarded image-only onboarding renderer update; never launch onboarding work."""
import copy,hashlib,json,sys
from datetime import datetime,timezone
from pathlib import Path
import control_reporting_onboard_qa_20261011 as qa
import release_reporting_onboard_20261011 as release
import scan_ms_presentation_worker_candidate as scan
from deploy_ms_presentation_worker_release import settings_invariant
from deploy_upgrade_inception_20261009 import patch
from control_upgrade_legacy_recovery_probe_20261009 import protected
from control_upgrade_readers_20261010 import save
b=qa.b;JOB='ibkrv2stg-onboard-render';PATH=b.rid('Microsoft.App/jobs',JOB);OUT=qa.STAGE/'onboard-deployment-v1'

def require(ok,message):
    if not ok:raise ValueError(message)

def only_target(actual,expected):
    require(set(actual)==set(expected) and {k:v for k,v in actual.items() if k!=JOB}=={k:v for k,v in expected.items() if k!=JOB},'Unrelated workload drift')

def scope(job):
    p=job['properties'];t=p['template'];cfg=p['configuration']
    require(job['name']==JOB and job['id'].casefold()==PATH.casefold(),'Exact onboarding target required')
    require(len(t['containers'])==1 and not t.get('initContainers') and not t.get('volumes'),'Reviewed single-container topology required')
    c=t['containers'][0]
    require(c.get('command')==['python'] and c.get('args')==['scripts/run_self_service_onboarding_reports.py'],'Reviewed onboarding entrypoint required')
    require(cfg.get('triggerType')=='Manual' and not cfg.get('scheduleTriggerConfig') and not cfg.get('eventTriggerConfig'),'Manual trigger required')
    require(not any(s.get('value') for s in cfg.get('secrets',[])),'Do not retain inline secrets')

def accept(runtime,outputs,image):
    require(runtime.get('passed') is True and runtime.get('image')==image,'Exact runtime qualification required');qa.accept(runtime['proofs'])
    require(outputs.get('passed') is True and outputs.get('image')==image,'Exact output acceptance required')
    for k,v in {'pdfs_byte_identical':10,'presentation_packages_compared':10,'slides_compared':200,'financial_charts_verified':60}.items():
        require(type(outputs.get(k)) is int and outputs[k]==v,'Incomplete output acceptance: '+k)
    require(len(outputs.get('artifacts',[]))==20 and all(r.get('content_identical') is True for r in outputs['artifacts']),'All outputs must match')

def sources():return {n:hashlib.sha256((qa.ROOT/'scripts'/n).read_bytes()).hexdigest() for n in (Path(__file__).name,'test_reporting_onboard_deploy_20261011.py')}

def main(stage):
    require(stage in ('plan','apply','verify','rollback'),'Unknown operation')
    live=b.request('GET',PATH+b.API);scope(live)
    if stage=='rollback':
        plan=protected(OUT/'plan.json');require(settings_invariant(live)==plan['invariant'] and live['properties']['template']['containers'][0]['image']==plan['image'],'Rollback target drift')
        only_target(b.bindings(b.current_resources()),plan['bindings']);b.runtime.safe_window();b.runtime.idle(JOB)
        require(not (OUT/'rollback-intent.json').exists(),'Reconcile earlier rollback')
        save(OUT,'rollback-intent',{'at':datetime.now(timezone.utc).isoformat(),'image':plan['before_image']})
        ack=b.request('PATCH',PATH+b.API,data=plan['rollback_body']);save(OUT,'rollback-ack',{'acknowledgement_received':True,'state':ack.get('properties',{}).get('provisioningState')});return
    candidate=protected(qa.STAGE/'candidate-checkpoint.json');source,_,build=scan.verify(release.OUT)
    image=build['registry']+'/'+build['repository']+'@'+build['digest']
    require(candidate['passed'] and candidate['image']==image and candidate['native_tests']==104 and not any(candidate['scan_counts'][k] for k in ('CRITICAL','HIGH','UNKNOWN')),'Native qualification and scan required')
    runtime=protected(qa.OUT/'result.json');outputs=protected(qa.STAGE/'onboard-output-review-v1/result.json');accept(runtime,outputs,image)
    for r in outputs['artifacts']:require(hashlib.sha256(Path(r['file']).read_bytes()).hexdigest()==r['sha256'],'Accepted file changed')
    before=source['base_image'];expected=protected(qa.STAGE/'resume-v1/result.json')['boundary']['workloads']
    if stage=='plan':
        require(not OUT.exists() and b.bindings(b.current_resources())==expected and live['properties']['template']['containers'][0]['image']==before,'Existing plan or baseline drift')
        apply=patch(live,image);trial=copy.deepcopy(live);trial['properties']['template']=apply['properties']['template']
        require(settings_invariant(trial)==settings_invariant(live),'Image-only invariant failed')
        restored=copy.deepcopy(trial);restored['properties']['template']=patch(trial,before)['properties']['template']
        require(settings_invariant(restored)==settings_invariant(live) and restored['properties']['template']['containers'][0]['image']==before,'Rollback payload does not restore baseline')
        import os,re,subprocess
        run=subprocess.run([sys.executable,'-m','unittest','scripts.test_reporting_onboard_deploy_20261011','scripts.test_reporting_onboard_qa_20261011','-q'],cwd=qa.ROOT,env={**os.environ,'PYTHONPATH':'src'+os.pathsep+'scripts'},capture_output=True,text=True,timeout=60)
        require(not run.returncode and re.findall(r'Ran (\d+) tests',run.stderr)==['18'],'Deployment safeguards failed')
        save(OUT,'plan',{'image':image,'before_image':before,'before':live,'invariant':settings_invariant(live),'bindings':expected,'sources':sources(),'apply_body':apply,'rollback_body':patch(live,before),'guard_tests':18,'runtime_receipt':runtime['protected_receipt'],'output_receipt':outputs['protected_receipt'],'scope':'Onboarding renderer saved image only. No normal job execution, registrations, approvals, account activation, credentials or financial data changes.','rollback_payload_roundtrip_verified':True,'live_rollback_execution':'NOT_PERFORMED','rollback_limitation':'Exact former image retained; restoring it restores known reporting defects.'})
        print('Protected image-only onboarding plan prepared');return
    plan=protected(OUT/'plan.json')
    require(sources()==plan['sources'] and image==plan['image'] and settings_invariant(live)==plan['invariant'],'Source or configuration drift')
    only_target(b.bindings(b.current_resources()),plan['bindings'])
    if stage=='apply':
        b.runtime.safe_window();b.runtime.idle(JOB)
        require(not (OUT/'apply-intent.json').exists() and live['properties']['template']['containers'][0]['image']==before,'Reconcile previous mutation')
        save(OUT,'apply-intent',{'at':datetime.now(timezone.utc).isoformat(),'plan_receipt':plan['protected_receipt']})
        latest=b.request('GET',PATH+b.API)
        require(settings_invariant(latest)==plan['invariant'] and latest['properties']['template']['containers'][0]['image']==before,'Immediate target drift')
        ack=b.request('PATCH',PATH+b.API,data=plan['apply_body']);save(OUT,'apply-ack',{'acknowledgement_received':True,'state':ack.get('properties',{}).get('provisioningState')});print('Onboarding image-only update submitted');return
    if live['properties'].get('provisioningState')!='Succeeded' or live['properties']['template']['containers'][0]['image']!=image:print('Not ready; not verified');return
    if not (OUT/'verified.json').exists():save(OUT,'verified',{'passed':True,'image':image,'job':JOB,'saved_settings_preserved':True,'unrelated_workloads_preserved':True,'normal_onboarding_workflow_executed':False,'reports_and_approvals_changed':False,'database_writes':0,'real_viewer_sign_in':'UNVERIFIED','fourteen_day_observation':'UNVERIFIED','full_upgrade_complete':False})
    print('Onboarding saved image and unchanged settings verified; no onboarding workflow launched')

if __name__=='__main__':main(*sys.argv[1:])
