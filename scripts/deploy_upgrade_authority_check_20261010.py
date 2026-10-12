"""Deploy only qualified authority preflight check and secure runtime image."""
import copy,hashlib,json,sys
from datetime import datetime,timezone
from pathlib import Path
import control_upgrade_authority_check_20261010 as c
from control_upgrade_legacy_recovery_probe_20261009 import protected
from deploy_ms_presentation_worker_release import settings_invariant
from deploy_upgrade_inception_20261009 import patch


def body(job,image,args):
    result=patch(job,image);result['properties']['template']['containers'][0]['args']=args
    trial=copy.deepcopy(job);trial['properties']['template']=copy.deepcopy(result['properties']['template'])
    trial['properties']['template']['containers'][0]['args']=job['properties']['template']['containers'][0]['args']
    if settings_invariant(trial)!=settings_invariant(job):raise ValueError('Unexpected non-image/non-args mutation')
    return result


def main(stage):
    image=c.readers.candidate();b,args,digest=c.build_args();source=protected(c.OUT/'source.json')
    if source['args']!=args or source['payload_sha256']!=digest:raise ValueError('Source drift')
    trials={}
    for mode,expected in (('candidate',image),('fallback',c.readers.SOURCE)):
        trial=protected(c.trial_folder(mode)/'result.json');c.shape(trial['proof'])
        if not trial['passed'] or trial['image']!=expected or trial['payload_sha256']!=digest or not trial['saved_settings_preserved']:raise ValueError('Exact compatible trial required')
        trials[mode]=trial['protected_receipt']
    live=c.request('GET',c.PATH+c.API);current=live['properties']['template']['containers'][0]
    normalized=copy.deepcopy(live);normalized['properties']['template']['containers'][0]['args']=b['job']['properties']['template']['containers'][0]['args']
    if settings_invariant(normalized)!=b['invariant'] or live['properties']['configuration'].get('identitySettings',[])!=b['job']['properties']['configuration'].get('identitySettings',[]):raise ValueError('Unrelated configuration drift')
    controller=hashlib.sha256(Path(__file__).read_bytes()).hexdigest();folder=c.OUT/'deployment-v1'
    if stage=='plan':
        if folder.exists() or current['image']!=c.readers.SOURCE or current['args']!=b['job']['properties']['template']['containers'][0]['args']:raise ValueError('Existing plan or original boundary drift')
        c.readers.save(folder,'plan',{'image':image,'payload_sha256':digest,'controller_sha256':controller,
            'apply_body':body(live,image,args),'compatible_rollback_body':body(live,c.readers.SOURCE,args),
            'original_rollback_body':patch(live,c.readers.SOURCE),'original_baseline':b['protected_receipt'],
            'source_receipt':source['protected_receipt'],'trial_receipts':trials,
            'scope':'Runtime image and obsolete empty-store preflight only. All authority modules/policies, enrolled agents, permission grants, secrets and schedules preserved.',
            'original_preflight_passes':False,'compatible_fallback_security_qualified':False,'full_upgrade_complete':False})
        print('Authority scoped deployment/compatible rollback plans protected');return
    plan=protected(folder/'plan.json')
    if plan['controller_sha256']!=controller or plan['image']!=image:raise ValueError('Plan drift')
    if stage in ('apply','restore'):
        c.readers.runtime.safe_window();c.readers.runtime.idle(c.JOB)
        expected_image=c.readers.SOURCE if stage=='apply' else image
        expected_args=b['job']['properties']['template']['containers'][0]['args'] if stage=='apply' else args
        if current['image']!=expected_image or current['args']!=expected_args or (folder/(stage+'-intent.json')).exists():raise ValueError('Reconcile existing/unknown mutation')
        c.readers.save(folder,stage+'-intent',{'at':datetime.now(timezone.utc).isoformat(),'plan':plan['protected_receipt']})
        ack=c.request('PATCH',c.PATH+c.API,data=plan['apply_body'] if stage=='apply' else plan['compatible_rollback_body'])
        c.readers.save(folder,stage+'-ack',{'state':ack.get('properties',{}).get('provisioningState')})
        print(json.dumps({'submitted':stage}));return
    if stage!='verify':raise ValueError('Unknown stage')
    if current['image']!=image or current['args']!=args or live['properties']['provisioningState']!='Succeeded':print('Authority deployment not ready');return
    if not (folder/'verified.json').exists():c.readers.save(folder,'verified',{'passed':True,'image':image,'payload_sha256':digest,'all_unrelated_settings_preserved':True,'authority_policy_changed':False,'grants_changed':False,'full_upgrade_complete':False})
    print('Authority scoped deployment verified')


if __name__=='__main__':main(sys.argv[1])
