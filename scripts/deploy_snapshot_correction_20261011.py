"""Image-only snapshot deployment, gated by native tests and exact full-data parity."""
import copy
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from datetime import datetime,timezone
import control_snapshot_correction_20261011 as qa
import release_snapshot_correction_20261011 as release
from deploy_ms_presentation_worker_release import settings_invariant
from deploy_upgrade_inception_20261009 import patch
from control_upgrade_legacy_recovery_probe_20261009 import protected
from control_upgrade_readers_20261010 import save

b=qa.b;JOB=release.JOB;PATH=b.rid('Microsoft.App/jobs',JOB);OUT=qa.STAGE/'snapshot-deployment-v1'

def require(value,message):
    if not value:raise ValueError(message)

def scope(job):
    p=job['properties'];t=p['template'];cfg=p['configuration']
    require(job['name']==JOB and job['id'].casefold()==PATH.casefold(),'Exact snapshot target required')
    require(len(t['containers'])==1 and not t.get('initContainers') and not t.get('volumes'),'Single-container topology required')
    c=t['containers'][0]
    require(c.get('command')==['python'] and c.get('args')==['scripts/freeze_month_end_snapshot.py'],'Snapshot entrypoint required')
    require(cfg.get('triggerType')=='Manual' and not cfg.get('scheduleTriggerConfig') and not cfg.get('eventTriggerConfig'),'Manual trigger required')
    require(not any(s.get('value') for s in cfg.get('secrets',[])),'Inline secrets must not be archived')

def only_target(actual,expected):
    require(set(actual)==set(expected) and {k:v for k,v in actual.items() if k!=JOB}=={k:v for k,v in expected.items() if k!=JOB},'Unrelated workload drift')

def parity(reference,candidate,image,source):
    require(reference.get('passed') is True and candidate.get('passed') is True and candidate.get('image')==image,'Two successful exact-image trials required')
    qa.accept(candidate['proof'],image,source)
    qa.accept(reference['proof'],reference['image'],reference['proof']['source_hashes'])
    require(candidate['proof']['subjects']==reference['proof']['subjects'],'Full five-subject snapshot payload parity required')
    for row in candidate['proof']['subjects']:
        require(bool(re.fullmatch('[0-9a-f]{64}',row['payload_sha256'])) and len(row['sections_sha256'])==6 and all(re.fullmatch('[0-9a-f]{64}',v) for v in row['sections_sha256'].values()),'Complete payload and financial-section hashes required')

def sources():return {n:hashlib.sha256((qa.ROOT/'scripts'/n).read_bytes()).hexdigest() for n in
    (Path(__file__).name,'control_snapshot_correction_20261011.py','probe_snapshot_correction_20261011.py','test_snapshot_deployment_20261011.py','control_snapshot_role_20261011.py','probe_snapshot_role_20261011.py')}

def entrypoint_scope():
    name='scripts/freeze_month_end_snapshot.py'
    original=(release.BASE_CAPTURE/name).read_text()
    expected=original.replace('from ibkr_etl.reporting_snapshot import freeze_month_end_snapshot',
        'from ibkr_etl.reporting_snapshot import active_month_end_snapshot_id, freeze_month_end_snapshot')
    guard='''    try:
        existing = active_month_end_snapshot_id(
            connection, tenant_id=tenant_id, subject_id=account_id, period_end=period_end
        )
        if existing is not None:
            print(json.dumps({"status": "already_frozen", "snapshot_id": existing,
                              "subject_id": account_id, "period_end": period_end,
                              "inserted": False, "snapshot_blob_uploaded": False}, sort_keys=True))
            return 0
        result = freeze_month_end_snapshot('''
    anchor='    try:\n        result = freeze_month_end_snapshot('
    require(expected.count(anchor)==1,'Entrypoint preflight anchor differs')
    expected=expected.replace(anchor,guard)
    anchor='            reporting_currency=reporting_currency,\n        )'
    require(expected.count(anchor)==1,'Entrypoint quality anchor differs')
    expected=expected.replace(anchor,'            reporting_currency=reporting_currency,\n            reject_quality_fail=True,\n        )')
    actual=(release.OUT/'context/payload'/name).read_text()
    require(ast.dump(ast.parse(expected),include_attributes=False)==ast.dump(ast.parse(actual),include_attributes=False),'Unreviewed entrypoint change')

def main(stage):
    require(stage in ('plan','apply','verify','rollback'),'Unknown deployment action')
    live=b.request('GET',PATH+b.API);scope(live)
    if stage=='rollback':
        plan=protected(OUT/'plan.json');require(settings_invariant(live)==plan['invariant'] and live['properties']['template']['containers'][0]['image']==plan['image'],'Rollback target drift')
        only_target(b.bindings(b.current_resources()),plan['bindings']);b.runtime.safe_window();b.runtime.idle(JOB)
        require(not (OUT/'rollback-intent.json').exists(),'Reconcile earlier rollback')
        save(OUT,'rollback-intent',{'at':datetime.now(timezone.utc).isoformat(),'image':plan['before_image']})
        ack=b.request('PATCH',PATH+b.API,data=plan['rollback_body']);save(OUT,'rollback-ack',{'received':True,'state':ack.get('properties',{}).get('provisioningState')});return
    image,source=qa.candidate('candidate')
    entrypoint_scope()
    native=release.h.read(release.OUT/'native-build-qualified.json')
    require(native['qualified'] is True and native['linux_tests']==88,'88 native tests required')
    reference=protected(qa.STAGE/'snapshot-reference-runtime-v1/result.json');candidate=protected(qa.STAGE/'snapshot-candidate-runtime-v1/result.json')
    parity(reference,candidate,image,source)
    role=protected(qa.STAGE/'snapshot-normal-role-v1/result.json');proof=role['proof']
    require(role.get('passed') is True and role['image']==image and proof.get('passed') is True and proof['source_hashes']==source
        and len(proof['subjects'])==5 and {r['subject'] for r in proof['subjects']}==qa.SUBJECTS and all(r['passed'] for r in proof['subjects'])
        and proof['database_writes']==0 and proof['privileged_role'] is False and proof['database_readonly'] and proof['verified_tls'],'Restricted normal-role full collection required')
    before=release.h.read(release.OUT/'source-intent.json')['base_image'];expected=protected(qa.STAGE/'resume-v1/result.json')['boundary']['workloads']
    if stage=='plan':
        require(not OUT.exists() and b.bindings(b.current_resources())==expected and live['properties']['template']['containers'][0]['image']==before,'Existing plan or baseline drift')
        apply=patch(live,image);trial=copy.deepcopy(live);trial['properties']['template']=apply['properties']['template']
        require(settings_invariant(trial)==settings_invariant(live),'Image-only invariant failed')
        restored=copy.deepcopy(trial);restored['properties']['template']=patch(trial,before)['properties']['template']
        require(settings_invariant(restored)==settings_invariant(live) and restored['properties']['template']['containers'][0]['image']==before,'Rollback inverse failed')
        run=subprocess.run([sys.executable,'-m','unittest','scripts.test_snapshot_deployment_20261011','-q'],cwd=qa.ROOT,env={**os.environ,'PYTHONPATH':'src'+os.pathsep+'scripts'},capture_output=True,text=True,timeout=60)
        require(not run.returncode and re.findall(r'Ran (\d+) tests',run.stderr)==['12'],'Deployment safeguard tests failed')
        save(OUT,'plan',{'image':image,'before_image':before,'before':live,'invariant':settings_invariant(live),'bindings':expected,'sources':sources(),'apply_body':apply,'rollback_body':patch(live,before),'guard_tests':12,
            'candidate_receipt':candidate['protected_receipt'],'reference_receipt':reference['protected_receipt'],
            'normal_role_receipt':role['protected_receipt'],
            'scope':'Snapshot saved image only. Original command, manual trigger, identities, secrets, production records, reports and approvals unchanged. No normal snapshot workflow is invoked.',
            'rollback_payload_roundtrip_verified':True,'live_rollback_execution':'NOT_PERFORMED','rollback_limitation':'Restoring the old image also restores its known obsolete reporting calculations.'})
        print('Protected snapshot image-only plan prepared');return
    plan=protected(OUT/'plan.json')
    require(sources()==plan['sources'] and image==plan['image'] and settings_invariant(live)==plan['invariant'],'Source or settings drift')
    only_target(b.bindings(b.current_resources()),plan['bindings'])
    if stage=='apply':
        b.runtime.safe_window();b.runtime.idle(JOB)
        require(not (OUT/'apply-intent.json').exists() and live['properties']['template']['containers'][0]['image']==before,'Reconcile previous update')
        save(OUT,'apply-intent',{'at':datetime.now(timezone.utc).isoformat(),'plan_receipt':plan['protected_receipt']})
        latest=b.request('GET',PATH+b.API)
        require(settings_invariant(latest)==plan['invariant'] and latest['properties']['template']['containers'][0]['image']==before,'Immediate target drift')
        ack=b.request('PATCH',PATH+b.API,data=plan['apply_body']);save(OUT,'apply-ack',{'received':True,'state':ack.get('properties',{}).get('provisioningState')});print('Snapshot image-only update submitted');return
    if live['properties'].get('provisioningState')!='Succeeded' or live['properties']['template']['containers'][0]['image']!=image:print('Not ready; not verified');return
    if not (OUT/'verified.json').exists():save(OUT,'verified',{'passed':True,'image':image,'job':JOB,'saved_settings_preserved':True,'unrelated_workloads_preserved':True,'normal_snapshot_workflow_executed':False,'production_database_writes':0,'reports_or_approvals_changed':False,'fourteen_day_observation':'UNVERIFIED','real_viewer_sign_in':'UNVERIFIED','full_upgrade_complete':False})
    print('Snapshot image and settings verified')

if __name__=='__main__':main(sys.argv[1])
