"""Manual-only image-switch rehearsal. NEVER mutates a production job.

Creates one bounded scratch job with an inert import/check command, the existing
identity and secret references. No RBAC, secrets, database or report mutations.
Restores an exact-version protected definition and removes only its own scratch
job after recording the result. Every mutation has a create-only intent; unknown
outcomes must be inspected, never redispatched.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
from time import perf_counter

from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient

import build_ms_presentation_worker_candidate as build
from deploy_metadata_monitor import BASE, request
from qualify_ms_presentation_worker_runtime import API, candidate_gate
from run_specialist_gateway_read_verification import retain
from ibkr_etl.specialist_archive_reader import ACCOUNT, CONTAINER

NAME = 'ibkrv2stg-ms-rollback-20261008'
TARGET = BASE + 'Microsoft.App/jobs/' + NAME
PURPOSE = 'alpha-ms-isolated-image-rollback'
ACTIVE = {'Running', 'Processing', 'Pending'}
CHECK = """import hashlib,json,sys
from pathlib import Path
sys.path[:0]=['/app/src','/app/scripts']
def require(ok):
 if not ok: raise ValueError('Bounded runtime import check failed')
from ibkr_etl.monthly_artifacts import factsheet_layout_version
require(factsheet_layout_version('alpha-world-ms')=='subject-completed-factsheet-model-v14r13-alpha-world-ms-distribution-quarter-ticks')
p=Path('/app/MS_PRESENTATION_WORKER_RELEASE.json')
if p.exists():
 r=json.loads(p.read_bytes())
 require(r['content_contract_version']=='alpha-world-ms-v24-hkd-hibor-evidence-v2' and r['chart_workbooks_required'] is True)
 for n,h in r['installed_files_sha256'].items():
  require(hashlib.sha256(Path('/app',n).read_bytes()).hexdigest()==h)
 import generate_month_end_package as m,render_onboarding_historical_reports as o
 from validate_subject_presentation import validate_subject_presentation as v
 require(m.validate_subject_presentation is v and o.validate_subject_presentation is v)
print('MS_ROLLBACK_IMPORT_CHECK_PASSED',flush=True)
"""


def require(ok, message):
    if not ok:
        raise ValueError(message)


def clean(value):
    if isinstance(value, dict):
        return {k: clean(v) for k,v in value.items() if v is not None}
    if isinstance(value, list):
        return [clean(v) for v in value]
    return value


def definition(job):
    config = clean(copy.deepcopy(job['properties']['configuration']))
    require(not any(s.get('value') for s in config.get('secrets', [])), 'Inline secret bytes prohibited')
    for secret in config.get('secrets', []):
        require(secret.get('keyVaultUrl') and secret.get('identity'), 'Existing Key Vault reference required')
    for key in ('scheduleTriggerConfig', 'eventTriggerConfig'):
        config.pop(key, None)
    config.update(triggerType='Manual', replicaRetryLimit=0,
                  manualTriggerConfig={'parallelism':1, 'replicaCompletionCount':1})
    template = clean(copy.deepcopy(job['properties']['template']))
    require(len(template['containers']) == 1 and not template.get('initContainers')
            and not template.get('volumes'), 'One volume-free worker required')
    container = template['containers'][0]
    container.update(command=['python'], args=['-c', CHECK])
    identity = job.get('identity', {})
    require(identity.get('type') == 'UserAssigned' and identity.get('userAssignedIdentities'), 'Existing user identity required')
    return {'location':job['location'],
            'identity':{'type':'UserAssigned', 'userAssignedIdentities':{k:{} for k in identity['userAssignedIdentities']}},
            'tags':{'purpose':PURPOSE, 'sourceJob':build.JOBS[1], 'expiresOn':'2026-10-08'},
            'properties':{'environmentId':job['properties']['environmentId'], 'configuration':config, 'template':template}}


def reference_settings(value):
    """Normalize only empty defaults observed on this scratch resource."""
    result=copy.deepcopy(value)
    for registry in result['properties']['configuration'].get('registries', []):
        for key in ('username','passwordSecretRef'):
            if registry.get(key) == '':
                registry.pop(key)
    for container in result['properties']['template']['containers']:
        if container.get('resources',{}).get('ephemeralStorage') == '':
            container['resources'].pop('ephemeralStorage')
        for env in container.get('env',[]):
            if env.get('secretRef') and env.get('value') == '':
                env.pop('value')
    return result


def settings_invariant(job):
    """Image PATCH omits an empty storage default; every other setting is strict."""
    result=copy.deepcopy(job)
    for container in result['properties']['template']['containers']:
        if container.get('resources',{}).get('ephemeralStorage') == '':
            container['resources'].pop('ephemeralStorage')
    return build.invariant(result)


def settings_match(job, baseline, expected_raw=None):
    require(build.invariant(baseline['job'])==baseline['invariant_sha256'], 'Raw archived baseline changed')
    if expected_raw is not None:
        require(expected_raw==baseline['invariant_sha256'], 'Intent baseline differs')
    return settings_invariant(job)==settings_invariant(baseline['job'])


def scope(job):
    require(job.get('name') == NAME and job.get('id','').casefold() == TARGET.casefold()
            and job.get('tags',{}).get('purpose') == PURPOSE, 'Scratch target identity differs')
    config = job['properties']['configuration']
    containers = job['properties']['template']['containers']
    require(config['triggerType'] == 'Manual' and not config.get('scheduleTriggerConfig')
            and not config.get('eventTriggerConfig') and config['replicaRetryLimit'] == 0
            and config['manualTriggerConfig'] == {'parallelism':1, 'replicaCompletionCount':1}
            and len(containers) == 1 and containers[0]['command'] == ['python']
            and containers[0]['args'] == ['-c', CHECK], 'Scratch command or trigger differs')
    require(not any(s.get('value') for s in config.get('secrets',[])), 'Secret values must not be read or archived')


def read(folder, name):
    return json.loads((folder / (name+'.json')).read_bytes())


def live():
    return request('GET', TARGET + API)


def idle():
    rows = request('GET', TARGET + '/executions' + API).get('value', [])
    require(not any(r['properties']['status'] in ACTIVE for r in rows), 'Scratch execution still active')


def restore_protected(receipt):
    require(receipt.get('protected_readback_verified') is True and receipt.get('version_id'), 'Exact protected version required')
    start = perf_counter()
    service = BlobServiceClient('https://'+ACCOUNT+'.blob.core.windows.net', credential=AzureCliCredential())
    blob = service.get_blob_client(CONTAINER, receipt['blob'], version_id=receipt['version_id'])
    props = blob.get_blob_properties()
    require(props.version_id == receipt['version_id'] and props.size <= 2*1024**2, 'Protected recovery object differs')
    content = blob.download_blob(offset=0, length=props.size+1).readall()
    require(hashlib.sha256(content).hexdigest() == receipt['sha256'], 'Protected recovery bytes differ')
    return json.loads(content), round(perf_counter()-start, 3)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=('prepare','provision','qualify-projection','qualify-switch-projection','capture','switch','verify-switch',
        'smoke-candidate','inspect-candidate','restore','verify-restore','smoke-base','inspect-base','cleanup','inspect-cleanup','seal'))
    parser.add_argument('--folder', type=Path, required=True)
    args = parser.parse_args()
    folder = args.folder.resolve()
    source, image = candidate_gate(folder)
    acceptance_path = folder / 'runtime-v1/candidate-render-acceptance.json'
    acceptance = json.loads(acceptance_path.read_bytes())
    require(acceptance['status'] == 'isolated_cloud_render_and_output_acceptance_passed'
            and acceptance['image'] == image and acceptance['protected_receipt']['protected_readback_verified'], 'Protected all-output acceptance required')
    stage = folder / 'rollback-rehearsal-v1'
    if args.stage == 'prepare':
        require(not stage.exists(), 'Preserve existing rehearsal')
        require(request('GET', TARGET + API, missing=True) is None, 'Scratch job already exists')
        before = {name:request('GET', BASE+'Microsoft.App/jobs/'+name+API) for name in build.JOBS}
        wanted = definition(before[build.JOBS[1]])
        stage.mkdir()
        record = {'definition':wanted, 'before_live_jobs':before, 'candidate_image':image,
                  'base_image':source['base_image'], 'source_intent_sha256':build.sha(folder/'source-intent.json'),
                  'candidate_acceptance_sha256':build.sha(acceptance_path),
                  'controller_sha256':build.sha(Path(__file__)), 'live_job_mutations':0, 'database_writes':0}
        receipt = retain(record, folder.name+'-rollback-rehearsal-plan')
        build.save(stage,'plan',{**record,'protected_receipt':receipt})
        print(json.dumps({'status':'prepared','scratch_job':NAME,'live_job_mutations':0}))
        return
    plan = read(stage, 'plan')
    require(plan['source_intent_sha256'] == build.sha(folder/'source-intent.json')
            and plan['candidate_acceptance_sha256'] == build.sha(acceptance_path), 'Rehearsal intent changed')
    if args.stage == 'qualify-projection':
        require(not (stage/'projection-controller-continuation.json').exists(), 'Preserve existing projection continuation')
        old=stage/'frozen-controller-v1.py'
        require(build.sha(old) == plan['controller_sha256'], 'Original controller evidence changed')
        recovered,_=restore_protected(plan['protected_receipt'])
        require(recovered == {k:v for k,v in plan.items() if k!='protected_receipt'}, 'Original protected plan differs')
        job=live();scope(job)
        require(job['properties']['provisioningState']=='Succeeded'
                and job['properties']['template']['containers'][0]['image']==plan['base_image']
                and reference_settings(definition(job))==reference_settings(plan['definition']),
                'Projection difference exceeds the four observed empty defaults')
        continuation={'status':'four_empty_projection_defaults_qualified','original_plan_sha256':build.sha(stage/'plan.json'),
            'original_controller_sha256':plan['controller_sha256'],'controller_sha256':build.sha(Path(__file__)),
            'original_controller_source':old.read_text(encoding='utf-8'),
            'controller_source':Path(__file__).read_text(encoding='utf-8'),
            'observed_job':job,'no_image_switch_performed':True,'live_job_mutations':0,
            'prior_capture_result':'Rejected before image switch; no baseline was accepted or overwritten'}
        build.save(stage,'projection-controller-continuation',{**continuation,
            'protected_receipt':retain(continuation,folder.name+'-rollback-projection-continuation')})
        print(json.dumps({'status':continuation['status'],'live_job_mutations':0}))
        return
    if args.stage == 'qualify-switch-projection':
        require(not (stage/'switch-projection-continuation.json').exists(), 'Preserve existing switch projection evidence')
        prior=read(stage,'projection-controller-continuation')
        old=stage/'projection-controller-v1.py'
        require(build.sha(old)==prior['controller_sha256']
                and prior['original_plan_sha256']==build.sha(stage/'plan.json')
                and prior['original_controller_sha256']==plan['controller_sha256'], 'Prior controller identity differs')
        recovered,_=restore_protected(prior['protected_receipt'])
        require(recovered=={k:v for k,v in prior.items() if k!='protected_receipt'}, 'Prior protected continuation differs')
        baseline=read(stage,'baseline')
        recovered,_=restore_protected(baseline['protected_receipt'])
        require(recovered=={k:v for k,v in baseline.items() if k!='protected_receipt'}, 'Protected baseline differs')
        intent=read(stage,'switch-intent');job=live();scope(job);idle()
        require(intent['target']==TARGET and intent['from_image']==plan['base_image']
                and intent['to_image']==image and baseline['image']==plan['base_image']
                and job['properties']['provisioningState']=='Succeeded'
                and job['properties']['template']['containers'][0]['image']==image
                and settings_match(job,baseline,intent['non_image_invariant']), 'Switch difference exceeds empty storage default')
        require(not (stage/'switch-qualified.json').exists()
                and not (stage/'candidate-smoke-intent.json').exists(), 'Rejected switch check must remain unqualified')
        continuation={'status':'empty_storage_patch_projection_qualified',
            'original_plan_sha256':build.sha(stage/'plan.json'),
            'prior_continuation_sha256':build.sha(stage/'projection-controller-continuation.json'),
            'baseline_sha256':build.sha(stage/'baseline.json'),
            'switch_intent_sha256':build.sha(stage/'switch-intent.json'),
            'prior_controller_sha256':prior['controller_sha256'], 'controller_sha256':build.sha(Path(__file__)),
            'prior_controller_source':old.read_text(encoding='utf-8'),
            'controller_source':Path(__file__).read_text(encoding='utf-8'),
            'observed_job':job, 'live_job_mutations':0,
            'prior_switch_verification':'Rejected solely because empty ephemeralStorage was omitted; candidate was not started'}
        build.save(stage,'switch-projection-continuation',{**continuation,
            'protected_receipt':retain(continuation,folder.name+'-rollback-switch-projection-continuation')})
        print(json.dumps({'status':continuation['status'],'live_job_mutations':0}))
        return
    if (stage/'switch-projection-continuation.json').exists():
        continuation=read(stage,'switch-projection-continuation')
        require(continuation['status']=='empty_storage_patch_projection_qualified'
                and continuation['original_plan_sha256']==build.sha(stage/'plan.json')
                and continuation['prior_continuation_sha256']==build.sha(stage/'projection-controller-continuation.json')
                and continuation['baseline_sha256']==build.sha(stage/'baseline.json')
                and continuation['switch_intent_sha256']==build.sha(stage/'switch-intent.json')
                and continuation['controller_sha256']==build.sha(Path(__file__)), 'Switch projection continuation differs')
        recovered,_=restore_protected(continuation['protected_receipt'])
        require(recovered=={k:v for k,v in continuation.items() if k!='protected_receipt'}, 'Protected switch continuation differs')
    elif plan['controller_sha256'] != build.sha(Path(__file__)):
        continuation=read(stage,'projection-controller-continuation')
        require(continuation['status']=='four_empty_projection_defaults_qualified'
                and continuation['original_plan_sha256']==build.sha(stage/'plan.json')
                and continuation['original_controller_sha256']==plan['controller_sha256']
                and continuation['controller_sha256']==build.sha(Path(__file__)), 'Controller continuation differs')
        recovered,_=restore_protected(continuation['protected_receipt'])
        require(recovered=={k:v for k,v in continuation.items() if k!='protected_receipt'}, 'Protected controller continuation differs')
    if args.stage == 'provision':
        require(not (stage/'provision-intent.json').exists(), 'Provision attempted; capture rather than repeat')
        require(request('GET', TARGET+API, missing=True) is None, 'Refuse to overwrite scratch resource')
        build.save(stage,'provision-intent', {'target':TARGET,'definition_sha256':hashlib.sha256(json.dumps(plan['definition'],sort_keys=True).encode()).hexdigest()})
        request('PUT', TARGET+API, data=plan['definition'])
    elif args.stage == 'capture':
        job = live(); scope(job)
        require(job['properties']['provisioningState'] == 'Succeeded'
                and job['properties']['template']['containers'][0]['image'] == plan['base_image'], 'Initial scratch job not ready')
        wanted = plan['definition']
        actual = definition(job)
        require(reference_settings(actual) == reference_settings(wanted), 'Scratch reference settings differ from live metadata')
        snapshot = {'job':job,'image':plan['base_image'],'invariant_sha256':build.invariant(job)}
        build.save(stage,'baseline',{**snapshot,'protected_receipt':retain(snapshot,folder.name+'-scratch-rollback-baseline')})
    elif args.stage in ('switch','restore'):
        label = 'switch' if args.stage == 'switch' else 'restore'
        require(not (stage/(label+'-intent.json')).exists(), 'Switch attempted; inspect rather than repeat')
        baseline = read(stage,'baseline')
        job = live(); scope(job); idle()
        expected_image = plan['base_image'] if label == 'switch' else image
        require(settings_match(job,baseline)
                and job['properties']['template']['containers'][0]['image'] == expected_image, 'Scratch settings drifted')
        if label == 'restore':
            require(read(stage,'candidate-smoke-qualified')['status'] == 'passed', 'Candidate import check must pass first')
            recovered, elapsed = restore_protected(baseline['protected_receipt'])
            require(recovered['job'] == baseline['job'], 'Recovered definition differs')
            build.save(stage,'recovered-baseline', {'record':recovered,'exact_version_sha256':baseline['protected_receipt']['sha256'], 'recovery_readback_seconds':elapsed})
        else:
            recovered = baseline
        template = copy.deepcopy(recovered['job']['properties']['template'])
        template['containers'][0]['image'] = image if label == 'switch' else plan['base_image']
        body = {'properties':{'template':template}}
        build.save(stage,label+'-intent', {'target':TARGET,'from_image':expected_image,'to_image':template['containers'][0]['image'],'body':body,'non_image_invariant':baseline['invariant_sha256']})
        request('PATCH',TARGET+API,data=body)
    elif args.stage in ('verify-switch','verify-restore'):
        label = args.stage.removeprefix('verify-')
        intent = read(stage,label+'-intent'); job=live(); scope(job)
        require(job['properties']['provisioningState'] == 'Succeeded'
                and settings_match(job,read(stage,'baseline'),intent['non_image_invariant'])
                and job['properties']['template']['containers'][0]['image'] == intent['to_image'], 'Image update or settings verification failed')
        build.save(stage,label+'-qualified', {'status':'passed','image':intent['to_image'],'settings_unchanged':True,'job':job})
    elif args.stage.startswith('smoke-'):
        mode=args.stage.removeprefix('smoke-'); label=mode+'-smoke'
        require(not (stage/(label+'-intent.json')).exists(), 'Smoke attempted; inspect rather than repeat')
        proof=read(stage,'switch-qualified' if mode=='candidate' else 'restore-qualified')
        job=live(); scope(job); idle()
        require(settings_match(job,read(stage,'baseline'))
                and job['properties']['template']['containers'][0]['image'] == proof['image'], 'Smoke target differs')
        build.save(stage,label+'-intent', {'image':proof['image'],'template':job['properties']['template'],'database_connections':0})
        run=request('POST',TARGET+'/start'+API)
        require(run.get('name'), 'Unknown smoke dispatch; do not retry')
        build.save(stage,label+'-dispatch', {'execution':run['name'],'image':proof['image']})
    elif args.stage.startswith('inspect-') and args.stage != 'inspect-cleanup':
        mode=args.stage.removeprefix('inspect-'); label=mode+'-smoke'
        ack=read(stage,label+'-dispatch')
        run=request('GET',TARGET+'/executions/'+ack['execution']+API)
        print(json.dumps({'execution':ack['execution'],'status':run['properties']['status']}),flush=True)
        if run['properties']['status'] != 'Succeeded':
            return
        actual=run['properties']['template']['containers']
        require(len(actual)==1 and actual[0]['image']==ack['image']
                and actual[0]['command']==['python'] and actual[0]['args']==['-c',CHECK], 'Executed image/check differs')
        build.save(stage,label+'-qualified', {'status':'passed','execution':ack['execution'],'image':ack['image'],
            'database_connections':0,'production_entrypoint_invocations':0,'settings_switch_on_scratch_job_only':True})
    elif args.stage == 'cleanup':
        require(read(stage,'base-smoke-qualified')['status']=='passed', 'Restored base must run successfully')
        require(not (stage/'cleanup-intent.json').exists(), 'Cleanup attempted; inspect rather than repeat')
        job=live(); scope(job); idle()
        require(settings_match(job,read(stage,'baseline'))
                and job['properties']['template']['containers'][0]['image']==plan['base_image'], 'Scratch baseline not restored')
        build.save(stage,'cleanup-intent', {'exact_target':TARGET,'restored_base_verified':True})
        request('DELETE',TARGET+API)
    elif args.stage == 'inspect-cleanup':
        require(read(stage,'cleanup-intent')['exact_target']==TARGET, 'Cleanup scope differs')
        absent=request('GET',TARGET+API,missing=True) is None
        require(absent, 'Scratch removal still in progress')
        build.save(stage,'cleanup-qualified', {'status':'passed','scratch_resource_absent':True})
    elif args.stage == 'seal':
        require(read(stage,'cleanup-qualified')['scratch_resource_absent'], 'Temporary resource must be removed')
        for name in ('switch-qualified','candidate-smoke-qualified','restore-qualified','base-smoke-qualified'):
            require(read(stage,name)['status']=='passed', 'Incomplete isolated switch/recovery evidence')
        recovered_plan, plan_elapsed = restore_protected(plan['protected_receipt'])
        require(recovered_plan['before_live_jobs'] == plan['before_live_jobs'], 'Live rollback metadata recovery differs')
        evidence={p.name:{'sha256':build.sha(p),'record':json.loads(p.read_bytes())} for p in stage.glob('*.json')}
        archive=retain(evidence,folder.name+'-isolated-switch-recovery-evidence')
        proof={'status':'isolated_image_switch_and_exact_archive_restore_passed','scratch_job':NAME,
            'candidate_image':image,'base_image':plan['base_image'],'live_reporting_jobs_unchanged':True,
            'candidate_import_execution':read(stage,'candidate-smoke-dispatch')['execution'],
            'restored_base_import_execution':read(stage,'base-smoke-dispatch')['execution'],
            'protected_definition_restored':True,'recovery_readback_seconds':read(stage,'recovered-baseline')['recovery_readback_seconds'],
            'both_live_worker_rollback_definitions_recovered':True,'live_definition_readback_seconds':plan_elapsed,
            'protected_operation_evidence':archive,
            'scratch_resource_removed':True,'database_connections':0,'database_writes':0,'ibkr_source_changed':False,
            'reports_released':False,'production_promotion':False,'full_live_image_switch_rollback_exercised':False,
            'scope':'Real ARM configuration switch and restoration on an isolated manual-only job, not the production jobs'}
        build.save(stage,'acceptance', {**proof,'protected_receipt':retain(proof,folder.name+'-isolated-switch-recovery')})
        print(json.dumps({'status':proof['status'],'live_job_mutations':0,'scratch_resource_removed':True}))
    print(json.dumps({'stage':args.stage,'scratch_job':NAME,'live_job_mutations':0}),flush=True)


if __name__ == '__main__':
    main()
