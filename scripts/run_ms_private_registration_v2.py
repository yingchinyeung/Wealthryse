"""One-shot baseline, private metadata registration and read-only verification.

Never approves reports or activates onboarding. Every phase has a protected
frozen intent and no automatic redispatch after an uncertain result.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import uuid

from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
import deploy_ms_presentation_worker_release as release
from deploy_metadata_monitor import request
from qualify_ms_presentation_worker_runtime import verify_execution_projection
from rehearse_ms_worker_image_rollback import restore_protected
from run_specialist_gateway_read_verification import retain
from run_ms_installed_private_batch import protected, PREFIX, installed_prefix
from verify_installed_ms_worker_release import corrected_template, no_matching_execution

JOB = release.build.JOBS[1]


def require(ok, message):
    if not ok: raise ValueError(message)


def main():
    global PREFIX
    p=argparse.ArgumentParser();p.add_argument('stage',choices=('prepare','dispatch','inspect'))
    p.add_argument('--mode',choices=('baseline','register','verify'),required=True)
    p.add_argument('--folder',type=Path,required=True); args=p.parse_args()
    folder=args.folder.resolve();root=folder/'registered-batch-v1';mode=args.mode;phase=root/('registration-'+mode+'-v2')
    PREFIX=installed_prefix(folder)
    deployment=release.read(folder/'deployment-v1','plan');complete=release.read(folder/'deployment-v1','completion')
    require(complete['status']=='scoped_reporting_workers_deployed_and_identity_verified'
        and deployment['image']==complete['image']
        and deployment['controller_sha256']==release.build.sha(Path(release.__file__)), 'Qualified deployed workers required')
    jobs=release.all_jobs(folder/'deployment-v1',deployment)
    qualified=release.read(root,'render-qualified');protected(qualified)
    require(qualified['status']=='installed_private_render_readback_passed' and qualified['image']==deployment['image'], 'Installed private render gate required')
    semantic_path=root/'semantic-package-v2.json'
    if folder.name.startswith('alpha-ms-format-worker-candidate-'):
        semantic=json.loads(semantic_path.read_bytes())
        require(semantic['status']=='passed' and semantic['months']==21
            and semantic['render_qualified_sha256']==release.build.sha(root/'render-qualified.json')
            and semantic['accepted_qualified_sha256']==release.build.sha(folder/'runtime-v1/candidate-qualified.json')
            and semantic['checker_sha256']==release.build.sha(release.build.ROOT/'scripts/compare_ms_package_semantics.py')
            and not any(i['semantic_differences'] for i in semantic['items']), 'Installed package semantics must match accepted outputs')
    runner=release.build.ROOT/'scripts/register_ms_private_batch_runtime_v2.py'
    shared=root/'registration-plan-v2.json'
    if args.stage=='prepare' and mode=='baseline':
        require(not shared.exists(),'Preserve existing registration plan')
        protected(complete)
        release.window()
        for n in release.build.JOBS:release.idle(n)
        record={'batch_id':str(uuid.uuid4()),'image':deployment['image'],
            'installed_files_sha256':deployment['binding']['installed_files_sha256'],
            'periods':[r['period_end'] for r in qualified['proof']['artifacts']],
            'proof_blob':PREFIX+'render/verification.json','proof_sha256':qualified['proof_sha256'],
            'render_execution':qualified['execution'], 'actor_sha256':hashlib.sha256(b'anthony.ying@wealthryse.ai').hexdigest(),
            'runner_sha256':release.build.sha(runner),'controller_sha256':release.build.sha(Path(__file__)),
            'render_qualified_sha256':release.build.sha(root/'render-qualified.json'),
            'semantic_package_sha256':release.build.sha(semantic_path) if folder.name.startswith('alpha-ms-format-worker-candidate-') else None,
            'completion_sha256':release.build.sha(folder/'deployment-v1/completion.json'),
            'reports_released':False,'onboarding_activated':False}
        release.build.save(root,'registration-plan-v2',{**record,'protected_receipt':retain(record,'ms-installed-private-batch-v2-registration-plan-v2')})
    plan=release.read(root,'registration-plan-v2');protected(plan)
    require(plan['image']==deployment['image'] and plan['runner_sha256']==release.build.sha(runner)
        and plan['controller_sha256']==release.build.sha(Path(__file__))
        and plan['render_qualified_sha256']==release.build.sha(root/'render-qualified.json')
        and plan.get('semantic_package_sha256')==(release.build.sha(semantic_path) if folder.name.startswith('alpha-ms-format-worker-candidate-') else None)
        and plan['completion_sha256']==release.build.sha(folder/'deployment-v1/completion.json'), 'Frozen registration plan differs')
    binding={k:v for k,v in plan.items() if k!='protected_receipt'}
    binding.update(mode=mode,result_blob=PREFIX+'registration-v2/'+mode+'.json')
    if mode!='baseline':
        baseline=release.read(root/'registration-baseline-v2','qualified');protected(baseline)
        require(baseline['proof']['status']=='baseline_passed','Protected database baseline required')
        visual_path=root/'visual-v1/receipt.json';visual=json.loads(visual_path.read_bytes())
        require(visual['status']=='accepted_pixel_identity_passed' and visual['slides_rendered']==420
            and visual['pdf_pages_rendered']==42 and visual['differences']==0 and not visual['errors']
            and visual['render_qualified_sha256']==plan['render_qualified_sha256'], 'Complete visual acceptance required')
        audit_path=root/'independent-audit-v1/receipt.json';audit=json.loads(audit_path.read_bytes())
        require(audit['status']=='passed' and audit['months']==21 and audit['financial_charts']==126
            and audit['risk_metrics']==147 and audit['editable_workbooks']==126
            and audit['render_qualified_sha256']==plan['render_qualified_sha256'], 'Independent financial/workbook audit required')
        binding.update(baseline_blob=baseline['result_blob'],baseline_sha256=baseline['proof_sha256'],
            visual_sha256=release.build.sha(visual_path), independent_audit_sha256=release.build.sha(audit_path))
        if mode=='verify':
            registration=release.read(root/'registration-register-v2','qualified');protected(registration)
            require(registration['proof']['status']=='private_batch_registered'
                and registration['proof']['batch_id']==plan['batch_id'],'Exact acknowledged registration required')
    source='BATCH_BINDING='+repr(binding)+'\n'+runner.read_text()
    template=corrected_template(jobs[JOB],source)
    if args.stage=='prepare':
        require(not phase.exists(),'Preserve existing registration phase')
        release.window()
        for n in release.build.JOBS:release.idle(n)
        no_matching_execution(JOB,[template['containers'][0]['args']])
        record={'mode':mode,'image':plan['image'],'batch_id':plan['batch_id'],'template':template,
            'source_sha256':hashlib.sha256(source.encode()).hexdigest(),'binding':binding,
            'plan_sha256':release.build.sha(shared),'database_writes_authorized':mode=='register',
            'authorization':'Owner requested new private historical batch registration; old reports, source data and approvals preserved'}
        phase.mkdir();release.build.save(phase,'intent',{**record,'protected_receipt':retain(record,'ms-installed-private-batch-v2-'+mode+'-intent')})
        print(json.dumps({'status':'prepared','mode':mode,'batch_id':plan['batch_id']}),flush=True);return
    intent=release.read(phase,'intent');protected(intent)
    require(intent['template']==template and intent['source_sha256']==hashlib.sha256(source.encode()).hexdigest()
        and intent['plan_sha256']==release.build.sha(shared),'Frozen phase intent differs')
    if args.stage=='dispatch':
        require(not (phase/'dispatch-intent.json').exists(),'Dispatch attempted; inspect without retry')
        release.window()
        for n in release.build.JOBS:release.idle(n)
        no_matching_execution(JOB,[template['containers'][0]['args']])
        release.build.save(phase,'dispatch-intent',{'mode':mode,'source_sha256':intent['source_sha256']})
        ack=request('POST',release.path(JOB)+'/start'+release.API,data=template)
        require(re.fullmatch(re.escape(JOB)+'-[a-z0-9]{7}',ack.get('name','')),'Unknown dispatch; do not retry')
        release.build.save(phase,'dispatch',{'execution':ack['name'],'mode':mode})
        print(json.dumps({'execution':ack['name'],'mode':mode}),flush=True);return
    ack=release.read(phase,'dispatch');run=request('GET',release.path(JOB)+'/executions/'+ack['execution']+release.API)
    state=run['properties']['status'];print(json.dumps({'mode':mode,'execution':ack['execution'],'status':state}),flush=True)
    if state!='Succeeded':return
    old=release.read(folder/'deployment-v1',JOB+'-check-qualified')
    baseline_run=request('GET',release.path(JOB)+'/executions/'+old['execution']+release.API)
    actual=run['properties']['template']['containers'];require(len(actual)==1,'Unexpected execution inventory')
    projection=verify_execution_projection(template['containers'][0],actual[0],
        baseline_run['properties']['template']['containers'][0],jobs[JOB])
    env={e['name']:e for e in template['containers'][0]['env']}
    service=BlobServiceClient(env['AZURE_STORAGE_ACCOUNT_URL']['value'],credential=AzureCliCredential())
    content=service.get_blob_client('internal-reports',binding['result_blob']).download_blob().readall()
    require(len(content)<=16*1024*1024,'Oversized private registration proof')
    proof=json.loads(content);expected={'baseline':'baseline_passed','register':'private_batch_registered','verify':'private_batch_verified'}[mode]
    require(proof['status']==expected and proof['batch_id']==plan['batch_id'],'Registration proof identity differs')
    if mode=='baseline':require(proof['database_writes']==0,'Baseline must remain read-only')
    else:
        require(proof['report_count']==42 and proof['prior_report_records_preserved'] and not proof['approvals_changed']
            and not proof['reports_released'] and not proof['onboarding_activated'],'Registration scope differs')
    record={'status':'passed','mode':mode,'batch_id':plan['batch_id'],'execution':ack['execution'],
        'proof':proof,'proof_sha256':hashlib.sha256(content).hexdigest(),'result_blob':binding['result_blob'],
        'execution_projection':projection,'phase_intent_sha256':release.build.sha(phase/'intent.json')}
    release.build.save(phase,'qualified',{**record,'protected_receipt':retain(record,'ms-installed-private-batch-v2-'+mode+'-qualified')})
    print(json.dumps({'status':'passed','mode':mode,'batch_id':plan['batch_id'],'report_count':proof.get('report_count')}),flush=True)


if __name__=='__main__': main()
