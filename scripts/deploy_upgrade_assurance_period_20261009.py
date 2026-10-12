"""Image-only assurance upgrade, exact evidence and non-image invariants."""
import copy,hashlib,json,sys
from pathlib import Path
from datetime import datetime,timezone
from release_upgrade_assurance_period_20261009 import OUT
from control_upgrade_assurance_period_probe_20261009 import candidate
from control_upgrade_assurance_candidate_20261009 import OLD,PATH,API,JOB,DAILY
from control_upgrade_legacy_recovery_probe_20261009 import protected
from deploy_viewer_private_admin_20261008 import request
from deploy_ms_presentation_worker_release import settings_invariant
from deploy_upgrade_inception_20261009 import patch
import deploy_t_runtime_release_20261009 as runtime
from run_specialist_gateway_read_verification import retain
runtime.request=request;FOLDER=OUT/'deployment-v1'
def save(name,value):
    with (FOLDER/(name+'.json')).open('x',encoding='utf-8') as f:json.dump({**value,'protected_receipt':retain(value,'upgrade-assurance-period-deploy-'+name)},f,indent=2)
def accept(proof,image,files):
    p=proof.get('proof',{});rows=p.get('records',[])
    if not proof.get('passed') or proof.get('image')!=image or not proof.get('saved_settings_preserved') or not p.get('passed') or p.get('database_writes')!=0 or p.get('model_calls')!=0 or p.get('verified_assurance_files')!=files:raise ValueError('Exact read-only candidate qualification required')
    if len(rows)!=5 or {r['subject'] for r in rows}!={'alpha-world-ms','alpha-world-t','hong-kong-china','U16088571','U16088606'} or any(r['checks_failed']!=0 or r['status']=='fail' for r in rows):raise ValueError('All five deterministic assurance checks must pass')
def main(stage):
    image,files=candidate();proof=protected(OUT/'read-only-v1/result.json');accept(proof,image,files)
    live=request('GET',PATH+API);actual=live['properties']['template']['containers'][0]['image'];digest=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if stage=='plan':
        if FOLDER.exists() or actual!=OLD:raise ValueError('Existing deployment or baseline drift')
        archive=protected(DAILY/'assurance-history-before-qualified-v2/result.json')
        if archive['status']!='Succeeded' or not json.loads(archive['records'][0]['Result'])['passed']:raise ValueError('Failed assurance history must be retained')
        if any(s.get('value') for s in live['properties']['configuration'].get('secrets',[])):raise ValueError('No inline secret retention')
        body=patch(live,image);trial=copy.deepcopy(live);trial['properties']['template']=body['properties']['template']
        if settings_invariant(trial)!=settings_invariant(live):raise ValueError('Only image may change')
        FOLDER.mkdir();save('plan',{'before_image':OLD,'image':image,'before':live,'invariant':settings_invariant(live),'apply_body':body,'rollback_body':patch(live,OLD),'controller_sha256':digest,'rehearsal':proof['protected_receipt'],'prior_history':archive['protected_receipt'],'scope':'Existing assurance worker only; corrected benchmark reader and independently aligned composite return validation. Financial formulas, sources, reports, approvals, model region/scope, credentials and schedules unchanged.','rollback_limitation':'Prior image remains schema-compatible but retains known assurance benchmark-reader failures; no passing fallback assurance claim','full_upgrade_complete':False});print('Protected assurance image-only deployment and rollback plan');return
    plan=protected(FOLDER/'plan.json')
    if digest!=plan['controller_sha256'] or settings_invariant(live)!=plan['invariant'] or image!=plan['image']:raise ValueError('Release boundary drift')
    if stage in ('apply','restore'):
        runtime.safe_window();runtime.idle(JOB)
        if actual!=(OLD if stage=='apply' else image) or (FOLDER/(stage+'-intent.json')).exists():raise ValueError('Reconcile mutation; never redispatch')
        save(stage+'-intent',{'started_at':datetime.now(timezone.utc).isoformat(),'plan':plan['protected_receipt']})
        ack=request('PATCH',PATH+API,data=plan['apply_body'] if stage=='apply' else plan['rollback_body']);save(stage+'-ack',{'state':ack.get('properties',{}).get('provisioningState')});print('Submitted image-only assurance update');return
    if stage!='verify':raise ValueError('Unknown step')
    if actual!=image or live['properties']['provisioningState']!='Succeeded':print('Awaiting image readiness');return
    if not (FOLDER/'verified.json').exists():save('verified',{'passed':True,'image':image,'saved_settings_preserved':True,'normal_cycle_verified':False,'full_upgrade_complete':False})
    print('Installed assurance image verified; normal five-account run still required')
if __name__=='__main__':main(sys.argv[1])
