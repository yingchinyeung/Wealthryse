"""One scheduled-job image-only promotion; full-upgrade gates remain separate."""
import copy,hashlib,json,sys
from pathlib import Path
from datetime import datetime,timezone
from deploy_metadata_monitor import request,BASE
from deploy_t_runtime_release_20261009 import idle,safe_window
from build_ms_presentation_worker_candidate import invariant
from run_specialist_gateway_read_verification import retain
from rehearse_ms_worker_image_rollback import restore_protected
from scan_ms_presentation_worker_candidate import verify
import release_platform_worker_stage3_v3_20261009 as release

ROOT=release.BUNDLE/'deployment-v2'
JOB=release.JOB
PATH=BASE+'Microsoft.App/jobs/'+JOB
API='?api-version=2024-03-01'

def save(name,value):
    ROOT.mkdir(parents=True,exist_ok=True)
    release.release.save(ROOT/(name+'.json'),{**value,'protected_receipt':retain(value,'platform-monthly-worker-'+name)})

def read(name):
    value=json.loads((ROOT/(name+'.json')).read_bytes())
    protected,_=restore_protected(value['protected_receipt'])
    if protected!={k:v for k,v in value.items() if k!='protected_receipt'}:raise ValueError('Protected deployment record differs')
    return value

def runtime(folder,image):
    value=json.loads((release.BUNDLE/folder/'result.json').read_bytes())
    if value['status']!='Succeeded' or value['image']!=image or not value['job_settings_preserved'] or len(value['records'])!=1:
        raise ValueError('Exact-image normal workflow acceptance missing')
    result=json.loads(value['records'][0]['Result'])
    if (result.get('passed') is not True or result.get('database_writes')!=0 or result.get('blob_writes')!=0
        or result.get('model_calls')!=0 or result.get('report_approvals_changed') is not False
        or {r['subject'] for r in result['normal_subject_paths']}!={'alpha-world-ms','alpha-world-t','hong-kong-china','U16088571','U16088606'}
        or any(r['status'] not in ('already_released','already_generated') for r in result['normal_subject_paths'])):
        raise ValueError('Normal worker acceptance failed')
    return result

def scanned_image(folder):
    source,native,output=verify(folder)
    image=output['registry']+'/'+output['repository']+'@'+output['digest']
    scan=json.loads((folder/'scan-completed.json').read_bytes())
    protected,_=restore_protected(json.loads((folder/'scan-protected-receipt.json').read_bytes()))
    if protected!=scan or scan['image']!=image or scan['assessment']['status']!='pass' or scan['assessment']['blocking_findings']:
        raise ValueError('Exact protected security gate failed')
    return image,source

def gates():
    image,source=scanned_image(release.release.WEB)
    accepted=runtime('entrypoint-probe-v3',image)
    if accepted['worker_sha256']!=source['files_sha256']['worker/generate_month_end_package.py']:
        raise ValueError('Installed worker source differs from reviewed candidate')
    actual=json.loads((release.BUNDLE/'actual-process-probe-v1/result.json').read_bytes())
    if actual['image']!=image or actual['status']!='Succeeded' or actual['job_settings_preserved'] is not True or len(actual['records'])!=1:
        raise ValueError('Actual process acceptance missing')
    processes=json.loads(actual['records'][0]['Result'])
    if (processes.get('passed') is not True or processes.get('actual_registry_and_worker_subprocesses') is not True
        or processes.get('worker_sha256')!=accepted['worker_sha256'] or processes.get('packages_reused')!=5
        or processes.get('report_and_approval_state_unchanged') is not True
        or processes.get('new_reports_generated')!=0 or processes.get('database_mode')!='read_only'):
        raise ValueError('Actual process preservation gate failed')
    accepted={**accepted,'actual_process_qualification':processes}
    recovery_image,recovery_source=scanned_image(release.BUNDLE/'worker-recovery-v2')
    if recovery_source['unmodified_previous_image'] is not False or recovery_source['base_image']!=release.BASE_IMAGE:
        raise ValueError('Compatible recovery scope differs')
    rollback=runtime('recovery-entrypoint-v2',recovery_image)
    if rollback['worker_sha256']!=recovery_source['files_sha256']['worker/generate_month_end_package.py']:
        raise ValueError('Rollback source differs')
    rollback={**rollback,'image':recovery_image,'unmodified_previous_image':False,
        'scope':'Compatible recovery baseline containing only duplicate template identity fix',
        'live_image_switch_rehearsed':False}
    return image,accepted,rollback

def main(stage):
    image,accepted,rollback=gates()
    live=request('GET',PATH+API)
    template=live['properties']['template']
    if len(template['containers'])!=1 or template.get('initContainers') or template.get('volumes'):
        raise ValueError('Unexpected job shape')
    container=template['containers'][0]
    if container.get('command')!=['python'] or container.get('args')!=['scripts/run_registry_month_end_packages.py']:
        raise ValueError('Unexpected persistent entrypoint')
    if any(s.get('value') for s in live['properties']['configuration'].get('secrets',[])):
        raise ValueError('Do not retain inline secret values')
    if stage=='plan':
        if ROOT.exists() or container['image']!=release.BASE_IMAGE:raise ValueError('Existing plan or baseline drift')
        before=json.loads((release.release.WEB/'build-intent.json').read_bytes())['before_jobs'][JOB]
        if invariant(live)!=before['settings_invariant_sha256']:raise ValueError('Worker settings changed since build')
        new_template=copy.deepcopy(template);new_template['containers'][0]['image']=image
        recovery_template=copy.deepcopy(template);recovery_template['containers'][0]['image']=rollback['image']
        save('plan',{'created_at':datetime.now(timezone.utc).isoformat(),'before':live,
            'before_image':release.BASE_IMAGE,'candidate_image':image,'invariant':invariant(live),
            'candidate_patch':{'properties':{'template':new_template}},
            'rollback_patch':{'properties':{'template':recovery_template}},'candidate_acceptance':accepted,
            'rollback_acceptance':rollback,'scope':'Normal monthly scheduled worker only; image-only.',
            'full_upgrade_complete':False,'controller_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
        print(json.dumps({'planned':JOB,'image':image,'live_changed':False}))
    elif stage=='apply':
        plan=read('plan');safe_window();idle(JOB)
        if plan['controller_sha256']!=hashlib.sha256(Path(__file__).read_bytes()).hexdigest():raise ValueError('Controller changed')
        if container['image']!=plan['before_image'] or invariant(live)!=plan['invariant']:raise ValueError('Action-time job drift')
        save('apply-intent',{'image':image,'plan_receipt':plan['protected_receipt'],'started_at':datetime.now(timezone.utc).isoformat()})
        response=request('PATCH',PATH+API,data=plan['candidate_patch'])
        save('apply-ack',{'name':response.get('name'),'state':response.get('properties',{}).get('provisioningState')})
        print(json.dumps({'image_change_submitted':True,'normal_job_not_started':True}))
    elif stage=='verify':
        plan=read('plan')
        if container['image']!=image or invariant(live)!=plan['invariant'] or live['properties'].get('provisioningState')!='Succeeded':
            raise ValueError('Live image/configuration verification failed')
        save('verified',{'verified_at':datetime.now(timezone.utc).isoformat(),'job':JOB,'image':image,
            'settings_preserved':True,'normal_job_not_started':True,'full_upgrade_complete':False})
        print(json.dumps({'job':JOB,'installed_image_verified':True,'settings_preserved':True}))
    else:raise ValueError('Unknown stage')

if __name__=='__main__':main(sys.argv[1])
