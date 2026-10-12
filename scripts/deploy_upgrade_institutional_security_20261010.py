"""Three independent image-only promotions with per-job protected rollback."""
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from release_upgrade_institutional_security_20261010 import OUT, SOURCE, JOBS, API
from control_upgrade_institutional_postgres_20261010 import candidate, accept
from control_upgrade_legacy_recovery_probe_20261009 import protected
from deploy_viewer_private_admin_20261008 import request, rid
from deploy_ms_presentation_worker_release import settings_invariant
from deploy_upgrade_inception_20261009 import patch
from run_specialist_gateway_read_verification import retain
import deploy_t_runtime_release_20261009 as runtime
runtime.request = request


def window(job, now=None):
    now = now or datetime.now(timezone.utc)
    runtime.safe_window()
    # Every-five-minute discovery: leave a bounded two-minute deployment slot.
    if job == 'ibkrv2stg-flex-discovery' and not 60 <= (now.minute % 5)*60 + now.second <= 180:
        raise ValueError('Wait for a discovery inter-run deployment window')


def validate_rehearsal(record, image):
    if record.get('image') != image or not record.get('passed') or not record.get('saved_job_preserved'):
        raise ValueError('Exact passing candidate/fallback rehearsal required')
    accept(record.get('proof',{}))


def save(folder, name, value):
    with (folder/(name+'.json')).open('x',encoding='utf-8') as f:
        json.dump({**value,'protected_receipt':retain(value,'upgrade-institutional-'+folder.name+'-'+name)},f,indent=2)


def main(stage, job):
    if job not in JOBS: raise ValueError('Job outside exact release scope')
    image = candidate()
    trial = protected(OUT/'postgres-candidate-v1/result.json'); validate_rehearsal(trial,image)
    fallback = protected(OUT/'postgres-fallback-v1/result.json'); validate_rehearsal(fallback,SOURCE)
    native = json.loads((OUT/'native-build-qualified.json').read_bytes())
    a,b = native['proofs']
    if a['source_manifest_sha256'] != b['source_manifest_sha256'] or a['source_files'] != b['source_files'] or a['source_files'] != 451 or a['tests'] != b['tests'] or a['tests'] != 23 or a['skipped'] or b['skipped']:
        raise ValueError('Native preservation/test boundary drift')
    folder = OUT/'deployment-v1'/job
    path = rid('Microsoft.App/jobs',job)
    live = request('GET',path+API)
    actual = live['properties']['template']['containers'][0]['image']
    digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    original = json.loads((OUT/'source-intent.json').read_bytes())['before_jobs'][job]
    if settings_invariant(live) != original['settings_invariant_sha256'] or live['properties']['configuration'].get('identitySettings',[]) != original['identity_settings']:
        raise ValueError('Frozen settings or runtime identity boundary drift')
    if any(s.get('value') for s in live['properties']['configuration'].get('secrets',[])):
        raise ValueError('Never archive inline secret values')
    if stage == 'plan':
        if folder.exists() or actual != SOURCE: raise ValueError('Existing plan or changed baseline')
        body = patch(live,image); hypothetical=copy.deepcopy(live); hypothetical['properties']['template']=body['properties']['template']
        if settings_invariant(hypothetical) != settings_invariant(live): raise ValueError('Non-image mutation rejected')
        folder.mkdir(parents=True)
        save(folder,'plan',{'job':job,'before_image':SOURCE,'image':image,'before':live,'apply_body':body,
             'rollback_body':patch(live,SOURCE),'invariant':settings_invariant(live),'controller_sha256':digest,
             'candidate_rehearsal':trial['protected_receipt'],'fallback_rehearsal':fallback['protected_receipt'],
             'scope':'Only OS/Python runtime image; 451 application files, source data, formulas, permissions, credentials, schedule and command remain unchanged.',
             'rollback_limitation':'Exact previous image passed the same fixtures but has 51 high and 3 unknown findings; functional emergency rollback, not security-qualified.',
             'full_upgrade_complete':False,'normal_scheduled_cycle_verified':False})
        print(json.dumps({'job':job,'plan':'protected'})); return
    plan = protected(folder/'plan.json')
    if digest != plan['controller_sha256'] or image != plan['image'] or settings_invariant(live) != plan['invariant']:
        raise ValueError('Protected plan drift')
    if stage in ('apply','restore'):
        window(job); runtime.idle(job)
        if actual != (SOURCE if stage=='apply' else image) or (folder/(stage+'-intent.json')).exists():
            raise ValueError('Existing or unknown mutation: reconcile, never repeat')
        save(folder,stage+'-intent',{'started_at':datetime.now(timezone.utc).isoformat(),'plan':plan['protected_receipt']})
        ack=request('PATCH',path+API,data=plan['apply_body'] if stage=='apply' else plan['rollback_body'])
        save(folder,stage+'-ack',{'state':ack.get('properties',{}).get('provisioningState')})
        print(json.dumps({'job':job,'mutation':'submitted','step':stage})); return
    if stage!='verify': raise ValueError('Unknown stage')
    if actual!=image or live['properties']['provisioningState']!='Succeeded':
        print(json.dumps({'job':job,'verified':False})); return
    if not (folder/'verified.json').exists():
        save(folder,'verified',{'passed':True,'job':job,'image':image,'all_other_settings_preserved':True,
             'normal_scheduled_cycle_verified':False,'full_upgrade_complete':False})
    print(json.dumps({'job':job,'verified':True,'normal_scheduled_cycle_verified':False}))


if __name__=='__main__': main(*sys.argv[1:])
