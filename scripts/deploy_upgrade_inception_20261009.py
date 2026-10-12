"""Qualified image-only risk-worker update with a protected exact-image rollback."""
import copy,json,sys
from pathlib import Path
from datetime import datetime,timezone
import release_upgrade_inception_20261009 as release
from control_upgrade_inception_rehearsal_20261009 import candidate
from deploy_viewer_private_admin_20261008 import request
from deploy_ms_presentation_worker_release import settings_invariant
from deploy_t_runtime_release_20261009 import idle,safe_window
from rehearse_ms_worker_image_rollback import restore_protected
from run_specialist_gateway_read_verification import retain

FOLDER=release.FOLDER/'deployment-v1'
REHEARSAL=release.FOLDER.parent/'clone-risk-candidate-history-all'

def save(name,value):
    with (FOLDER/(name+'.json')).open('x',encoding='utf-8') as f:json.dump({**value,'protected_receipt':retain(value,'upgrade-inception-deployment-'+name)},f,indent=2)

def protected(path):
    value=release.r.read(path);remote,_=restore_protected(value['protected_receipt'])
    if remote!={k:v for k,v in value.items() if k!='protected_receipt'}:raise ValueError('Protected evidence differs')
    return value

def patch(live,image):
    template=copy.deepcopy(live['properties']['template'])
    if len(template['containers'])!=1:raise ValueError('One risk container required')
    for e in template['containers'][0].get('env',[]):
        if e.get('secretRef') and e.get('value')=='':e.pop('value')
    template['containers'][0]['image']=image
    return {'properties':{'template':template}}

def accept(proof,image):
    if not proof['passed'] or proof['image']!=image or not proof['job_settings_preserved'] or not proof['actual_inception_boundaries_checked']:raise ValueError('Exact candidate rehearsal required')
    history=proof.get('history_coverage') or {}
    if not history.get('passed') or history.get('live_database_writes')!=0 or not 1<=history.get('expected',0)<=3000 or history.get('unique_checked')!=history['expected']:raise ValueError('Full isolated historical qualification required')

def main(stage):
    image=candidate();proof=protected(REHEARSAL/'result.json')
    accept(proof,image)
    live=request('GET',release.PATH);actual=live['properties']['template']['containers'][0]['image']
    if any(s.get('value') for s in live['properties']['configuration'].get('secrets',[])):raise ValueError('Inline secret capture prohibited')
    if stage=='plan':
        if FOLDER.exists() or actual!=release.IMAGE:raise ValueError('Baseline drift or existing plan')
        body=patch(live,image);trial=copy.deepcopy(live);trial['properties']['template']=body['properties']['template']
        if settings_invariant(trial)!=settings_invariant(live):raise ValueError('Only image may change')
        FOLDER.mkdir()
        save('plan',{'before_image':actual,'image':image,'before':live,'invariant':settings_invariant(live),'apply_body':body,'rollback_body':patch(live,actual),
            'controller_sha256':release.r.sha(Path(__file__)),'scope':'Expected unavailable MS inception history only; no numerical formulas, source data, reports, approvals, credentials, schedules or other job settings change',
            'authorization':'Owner requested completion of the full platform upgrade','rehearsal_receipt':proof['protected_receipt'],
            'rollback_limitation':'Previous exact image remains compatible with the unchanged schema/cache format, but retains the observed inception classification defect; no passing full-cycle rollback claim',
            'normal_job_started':False,'full_upgrade_complete':False})
        print('Protected image-only deployment and exact rollback plan');return
    plan=protected(FOLDER/'plan.json')
    if settings_invariant(live)!=plan['invariant'] or release.r.sha(Path(__file__))!=plan['controller_sha256']:raise ValueError('Deployment boundary drift')
    if stage in ('apply','restore'):
        safe_window();idle(release.JOB)
        expected=plan['before_image'] if stage=='apply' else plan['image']
        if actual!=expected or (FOLDER/(stage+'-intent.json')).exists():raise ValueError('Reconcile existing deployment; never retry')
        save(stage+'-intent',{'plan_receipt':plan['protected_receipt'],'started_at':datetime.now(timezone.utc).isoformat()})
        ack=request('PATCH',release.PATH,data=plan['apply_body'] if stage=='apply' else plan['rollback_body'])
        save(stage+'-ack',{'state':ack.get('properties',{}).get('provisioningState')})
        print('Image change submitted; no normal processing started');return
    if stage!='verify':raise ValueError('Unknown stage')
    if actual!=image or live['properties']['provisioningState']!='Succeeded':print('Image not ready; acceptance not recorded');return
    save('verified',{'passed':True,'image':image,'settings_preserved':True,'normal_job_started':False,'full_upgrade_complete':False})
    print('Risk-worker image verified; remaining upgrade gates remain open')

if __name__=='__main__':main(sys.argv[1])
