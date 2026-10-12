"""Image-only updates of three qualified, restricted manual reader jobs."""
import copy,hashlib,json,sys
from datetime import datetime,timezone
from pathlib import Path
from control_upgrade_readers_20261010 import OUT,JOBS,SOURCE,API,candidate,accept,reviewed,save
from control_upgrade_legacy_recovery_probe_20261009 import protected
from deploy_viewer_private_admin_20261008 import request,rid
from deploy_ms_presentation_worker_release import settings_invariant
from deploy_upgrade_inception_20261009 import patch
import deploy_t_runtime_release_20261009 as runtime
runtime.request=request


def validate_trial(job,record,image,digest):
    if not record.get('passed') or record.get('image')!=image or record.get('payload_sha256')!=digest or not record.get('settings_preserved'):raise ValueError('Exact successful image/payload trial required')
    accept(job,record['proof'])


def main(stage,name):
    if name not in JOBS:raise ValueError('Outside three-reader scope')
    target=candidate();baseline=protected(OUT/name/'baseline.json')
    trials={}
    for mode,image in (('candidate',target),('fallback',SOURCE)):
        t=protected(OUT/(name+'-'+mode)/'result.json');validate_trial(name,t,image,baseline['payload_sha256']);trials[mode]=t['protected_receipt']
    path=rid('Microsoft.App/jobs',name);live=request('GET',path+API);actual=live['properties']['template']['containers'][0]['image']
    digest=hashlib.sha256(Path(__file__).read_bytes()).hexdigest();folder=OUT/name/'deployment-v1'
    if settings_invariant(live)!=baseline['invariant'] or reviewed(live,name)!=baseline['payload_sha256'] or live['properties']['configuration'].get('identitySettings',[])!=baseline['job']['properties']['configuration'].get('identitySettings',[]):raise ValueError('Settings, source or identity boundary drift')
    if stage=='plan':
        if folder.exists() or actual!=SOURCE:raise ValueError('Existing plan or baseline drift')
        body=patch(live,target);check=copy.deepcopy(live);check['properties']['template']=body['properties']['template']
        if settings_invariant(check)!=baseline['invariant']:raise ValueError('Non-image change forbidden')
        save(folder,'plan',{'job':name,'image':target,'before_image':SOURCE,'apply_body':body,'rollback_body':patch(live,SOURCE),
            'invariant':baseline['invariant'],'payload_sha256':baseline['payload_sha256'],'controller_sha256':digest,
            'trial_receipts':trials,'baseline_receipt':baseline['protected_receipt'],
            'scope':'OS/Python image only; installed inline application, identities, permissions, secrets, schedules, commands and agent enablement unchanged',
            'fallback_security_qualified':False,'full_upgrade_complete':False})
        print(json.dumps({'job':name,'plan':'protected'}));return
    plan=protected(folder/'plan.json')
    if plan['controller_sha256']!=digest or plan['image']!=target:raise ValueError('Protected controller/image drift')
    if stage in ('apply','restore'):
        runtime.safe_window();runtime.idle(name)
        if actual!=(SOURCE if stage=='apply' else target) or (folder/(stage+'-intent.json')).exists():raise ValueError('Reconcile unknown/existing mutation')
        save(folder,stage+'-intent',{'started_at':datetime.now(timezone.utc).isoformat(),'plan':plan['protected_receipt']})
        ack=request('PATCH',path+API,data=plan['apply_body'] if stage=='apply' else plan['rollback_body'])
        save(folder,stage+'-ack',{'state':ack.get('properties',{}).get('provisioningState')})
        print(json.dumps({'job':name,'submitted':stage}));return
    if stage!='verify':raise ValueError('Unknown stage')
    if actual!=target or live['properties']['provisioningState']!='Succeeded':print('Deployment not ready');return
    if not (folder/'verified.json').exists():save(folder,'verified',{'passed':True,'job':name,'image':target,'payload_sha256':baseline['payload_sha256'],'all_nonimage_settings_preserved':True,'full_upgrade_complete':False})
    print(json.dumps({'job':name,'verified':True}))


if __name__=='__main__':main(*sys.argv[1:])
