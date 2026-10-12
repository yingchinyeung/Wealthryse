"""Exact-image derived benchmark correction; no other job settings change."""
import copy,hashlib,json,sys
from pathlib import Path
from datetime import datetime,timezone
import release_upgrade_derived_tail_20261009 as release
from control_upgrade_derived_tail_probe_20261009 import candidate
from control_upgrade_legacy_recovery_probe_20261009 import protected
from deploy_viewer_private_admin_20261008 import request
from deploy_ms_presentation_worker_release import settings_invariant
from deploy_upgrade_inception_20261009 import patch
import deploy_t_runtime_release_20261009 as runtime
from run_specialist_gateway_read_verification import retain
runtime.request=request
FOLDER=release.OUT/'deployment-v1'
ROOT=Path(__file__).resolve().parents[1]

def save(name,value):
    with (FOLDER/(name+'.json')).open('x',encoding='utf-8') as f:json.dump({**value,'protected_receipt':retain(value,'upgrade-derived-tail-deploy-'+name)},f,indent=2)

def accept(proof,image):
    p=proof.get('proof',{})
    if not proof.get('passed') or proof.get('image')!=image or not proof.get('job_settings_preserved') or not p.get('passed') or p.get('live_database_writes')!=0 or p.get('clone_public_table_differences')!=[] or p.get('public_tables_checked')!=21 or p.get('session_temporary_rows_removed')!=4 or not p.get('session_temporary_tables_dropped'):raise ValueError('Isolated full-preservation acceptance required')
    records=p.get('records',[])
    if len(records)!=2 or {r['subject'] for r in records}!={'alpha-world-ms','alpha-world-t'} or not all(r['approved_pair_refreshed'] and not r['onboarding_activated'] for r in records):raise ValueError('Both governed branches must pass')

def main(stage):
    image=candidate();proof=protected(release.OUT/'postgres-v1/result.json');accept(proof,image)
    live=request('GET',release.PATH);actual=live['properties']['template']['containers'][0]['image']
    digest=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if any(s.get('value') for s in live['properties']['configuration'].get('secrets',[])):raise ValueError('No inline secret retention')
    if stage=='plan':
        if FOLDER.exists() or actual!=release.IMAGE:raise ValueError('Existing plan or baseline drift')
        health=protected(ROOT/'.work/platform-upgrade-20261009-stage9/live-cycle-postflight-v6/result.json')
        record=json.loads(health['records'][0]['Result'])
        if health['status']!='Succeeded' or not record['passed'] or record['protected_table_differences'] or len(record['consumer_records'])!=10:raise ValueError('Healthy live baseline required')
        body=patch(live,image);trial=copy.deepcopy(live);trial['properties']['template']=body['properties']['template']
        if settings_invariant(trial)!=settings_invariant(live):raise ValueError('Non-image scope change')
        FOLDER.mkdir();save('plan',{'before_image':actual,'image':image,'before':live,'invariant':settings_invariant(live),'apply_body':body,'rollback_body':patch(live,actual),'controller_sha256':digest,'qualification':proof['protected_receipt'],'baseline_health':health['protected_receipt'],'scope':'Only daily MS/T benchmark refresh after validated NAV; source data, formulas, report templates, approvals, identity, schedules and other settings unchanged','rollback_limitation':'Exact prior image and unchanged schema retained. Baseline normal cycle and consumer reads passed after ordered benchmark/risk catch-up; the prior image still contains the benchmark-tail defect. No live rollback interruption claimed.','full_upgrade_complete':False});print('Protected image-only deployment and fallback plan saved');return
    plan=protected(FOLDER/'plan.json')
    if digest!=plan['controller_sha256'] or settings_invariant(live)!=plan['invariant'] or image!=plan['image']:raise ValueError('Frozen release boundary drift')
    if stage in ('apply','restore'):
        runtime.safe_window()
        for name in ('ibkrv2stg-derived-daily','ibkrv2stg-benchmark-daily','ibkrv2stg-risk-snapshots'):runtime.idle(name)
        expected=plan['before_image'] if stage=='apply' else image
        if actual!=expected or (FOLDER/(stage+'-intent.json')).exists():raise ValueError('Reconcile operation; never redispatch')
        save(stage+'-intent',{'started_at':datetime.now(timezone.utc).isoformat(),'plan':plan['protected_receipt']})
        ack=request('PATCH',release.PATH,data=plan['apply_body'] if stage=='apply' else plan['rollback_body'])
        save(stage+'-ack',{'state':ack.get('properties',{}).get('provisioningState')});print('Submitted image-only daily-worker update');return
    if stage!='verify':raise ValueError('Unknown stage')
    if actual!=image or live['properties']['provisioningState']!='Succeeded':print('Update not yet verified');return
    if not (FOLDER/'verified.json').exists():save('verified',{'passed':True,'image':image,'settings_preserved':True,'normal_cycle_verified':False,'full_upgrade_complete':False})
    print('Installed image verified; normal-cycle acceptance still required')

if __name__=='__main__':main(sys.argv[1])
