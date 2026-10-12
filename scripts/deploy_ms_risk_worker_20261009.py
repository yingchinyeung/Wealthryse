"""Exact-image MS worker promotion; schedules, credentials and all settings retained."""
import copy,hashlib,json,sys
from pathlib import Path
from datetime import datetime,timezone
from deploy_metadata_monitor import request,BASE
from deploy_t_runtime_release_20261009 import idle,safe_window
from build_ms_presentation_worker_candidate import invariant
from run_specialist_gateway_read_verification import retain
from rehearse_ms_worker_image_rollback import restore_protected
from scan_ms_presentation_worker_candidate import verify
import release_ms_risk_worker_20261009 as release
FOLDER=release.BUNDLE/'worker-deployment-v1'
PATH=BASE+'Microsoft.App/jobs/'+release.JOB
API='?api-version=2024-03-01'

def save(name,value):
    FOLDER.mkdir(parents=True,exist_ok=True)
    release.release.save(FOLDER/(name+'.json'),{**value,'protected_receipt':retain(value,'platform-ms-risk-worker-'+name)})
def read(name):
    value=release.release.read(FOLDER/(name+'.json'));remote,_=restore_protected(value['protected_receipt'])
    if remote!={k:v for k,v in value.items() if k!='protected_receipt'}:raise ValueError('Protected evidence differs')
    return value
def runtime(folder,image,fallback):
    value=release.release.read(release.BUNDLE/folder/'result.json')
    if value['status']!='Succeeded' or value['image']!=image or not value['job_settings_preserved'] or len(value['records'])!=1:raise ValueError('Exact runtime proof missing')
    result=json.loads(value['records'][0]['Result'])
    if not result['passed'] or result['fallback']!=fallback or result['database_writes']!=0 or not result['core_metrics_identical']:raise ValueError('Worker runtime failed')
    return result
def gates():
    folder=release.release.WEB
    source,_,output=verify(folder);image=output['registry']+'/'+output['repository']+'@'+output['digest']
    scan=release.release.read(folder/'scan-completed.json');remote,_=restore_protected(release.release.read(folder/'scan-protected-receipt.json'))
    if scan!=remote or scan['image']!=image or scan['assessment']['status']!='pass' or scan['assessment']['blocking_findings']:raise ValueError('Exact scan gate failed')
    accepted=runtime('worker-candidate-probe-v1',image,False)
    if set(accepted['file_sha256'])!={'risk_snapshots.py','alpha_world_ms_runtime.py'}:raise ValueError('Wrong worker source scope')
    if any(source['files_sha256']['src/ibkr_etl/'+n]!=h for n,h in accepted['file_sha256'].items()):raise ValueError('Runtime source differs')
    rollback=runtime('worker-rollback-probe-v1',release.IMAGE,True)
    recovery=release.release.read(release.BUNDLE/'cache-rehearsal-v1/protected.json');package,_=restore_protected(recovery['protected_receipt'])
    if (not recovery['passed'] or not package['temporary_table_update_and_rollback_passed']
        or accepted['payload_sha256']!=package['candidate_payload_sha256'] or rollback['payload_sha256']!=package['old_row']['payload_sha256']):raise ValueError('Worker/cache recovery differs')
    return image,accepted,rollback

def main(stage):
    image,accepted,rollback=gates();live=request('GET',PATH+API);container=live['properties']['template']['containers'][0]
    if len(live['properties']['template']['containers'])!=1 or container.get('command')!=['python'] or container.get('args')!=['scripts/build_risk_snapshots.py']:raise ValueError('Unexpected worker shape')
    if any(s.get('value') for s in live['properties']['configuration'].get('secrets',[])):raise ValueError('Inline secrets prohibited')
    digest=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if stage=='plan':
        if FOLDER.exists() or container['image']!=release.IMAGE:raise ValueError('Plan exists or base drift')
        expected=release.release.read(release.release.WEB/'build-intent.json')['before_jobs'][release.JOB]
        if invariant(live)!=expected['settings_invariant_sha256']:raise ValueError('Worker settings drift')
        template=copy.deepcopy(live['properties']['template']);template['containers'][0]['image']=image
        save('plan',{'before':live,'before_image':release.IMAGE,'candidate_image':image,'invariant':invariant(live),
            'candidate_patch':{'properties':{'template':template}},'rollback_patch':{'properties':{'template':live['properties']['template']}},
            'accepted':accepted,'rollback':rollback,'controller_sha256':digest,'normal_job_not_started':True,'complete_upgrade':False})
        print('Scoped worker plan protected');return
    plan=read('plan')
    if plan['controller_sha256']!=digest or invariant(live)!=plan['invariant']:raise ValueError('Controller/configuration drift')
    if stage=='apply':
        safe_window();idle(release.JOB)
        if container['image']!=plan['before_image']:raise ValueError('Image drift; reconcile existing action')
        save('apply-intent',{'image':image,'plan_receipt':plan['protected_receipt'],'started_at':datetime.now(timezone.utc).isoformat()})
        result=request('PATCH',PATH+API,data=plan['candidate_patch'])
        save('apply-ack',{'state':result.get('properties',{}).get('provisioningState')})
        print('Worker image update submitted; normal job not started');return
    if stage!='verify' or container['image']!=image or live['properties']['provisioningState']!='Succeeded':raise ValueError('Worker image not verified')
    save('verified',{'status':'passed','job':release.JOB,'image':image,'settings_preserved':True,'normal_job_not_started':True,'complete_upgrade':False})
    print('Risk worker image verified; schedule and settings preserved')

if __name__=='__main__':main(sys.argv[1])
