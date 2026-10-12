"""One-shot registration phases with protected baseline and rollback rehearsal."""
import hashlib,json,sys,uuid
from pathlib import Path
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
import deploy_ms_crisis as deploy
import build_ms_presentation_worker_candidate as build
import deploy_ms_presentation_worker_release as jobs
from deploy_metadata_monitor import request
from deploy_alpha_ms_allocation_geometry import node_source
from verify_installed_ms_worker_release import corrected_template,no_matching_execution
from run_specialist_gateway_read_verification import retain
API='?api-version=2024-03-01';FOLDER=deploy.FOLDER/'registration-v1';JOB=build.JOBS[0]
PREFIX='review/alpha-world-ms/crisis-release-20261008/candidate-v6/registration/'
def read(folder,name):return json.loads((folder/(name+'.json')).read_bytes())
def save(folder,name,value):build.save(folder,name,{**value,'protected_receipt':retain(value,'ms-crisis-v6-registration-'+folder.name+'-'+name)})
def main():
    stage,mode=sys.argv[1:3]
    if mode not in ('baseline','rehearse','register','verify'):raise ValueError('Unknown phase')
    deploy.configure();deploy.gates();base=deploy.base
    completion=read(base.FOLDER,'completion');base.verify_protected(completion)
    if completion['status']!='scoped_crisis_deployed':raise ValueError('Verified installed candidate required')
    image=base.image('worker')
    for name in build.JOBS:
        job=request('GET',jobs.path(name)+API)
        if job['properties']['template']['containers'][0]['image']!=image:raise ValueError('Installed worker differs')
    job=request('GET',jobs.path(JOB)+API)
    if not FOLDER.exists():
        if stage!='prepare' or mode!='baseline':raise ValueError('Baseline first')
        FOLDER.mkdir();proof=deploy.FOLDER/'worker/candidate-runtime-v1/verification.json'
        rows=json.loads(proof.read_bytes())['artifacts'];manifest=read(deploy.FOLDER/'worker/context','MS_CRISIS_RELEASE')
        save(FOLDER,'plan',{'release_id':str(uuid.uuid4()),'image':image,'layout_version':deploy.LAYOUT,
            'installed_files_sha256':manifest['installed_files_sha256'],'periods':[r['period_end'] for r in rows],
            'proof_blob':'review/alpha-world-ms/crisis-release-20261008/candidate-v6/worker/candidate-runtime-v1/verification.json',
            'proof_sha256':build.sha(proof),'scope':'21 new immutable private snapshots, 21 prior private status supersessions, 42 awaiting-approval report runs, one audit; no source or approval writes'})
    plan=read(FOLDER,'plan');base.verify_protected(plan)
    if mode=='register':
        rehearsed=read(FOLDER/'rehearse','qualified');base.verify_protected(rehearsed)
        if rehearsed['status']!='passed' or not rehearsed['transaction_rolled_back']:raise ValueError('Exact registration rollback rehearsal required')
    if mode=='verify':
        committed=read(FOLDER/'register','qualified');base.verify_protected(committed)
        if committed['status']!='passed':raise ValueError('Reconcile commit before verification')
    binding={k:v for k,v in plan.items() if k not in ('protected_receipt','scope')}
    binding.update(mode=mode,result_blob=PREFIX+mode+'.json')
    if mode!='baseline':
        baseline=read(FOLDER/'baseline','qualified');base.verify_protected(baseline)
        binding.update(baseline_blob=PREFIX+'baseline.json',baseline_sha256=baseline['result_sha256'])
    folder=FOLDER/mode
    if stage=='prepare':
        jobs.window();jobs.idle(JOB)
        if folder.exists():raise ValueError('Frozen phase exists; inspect it, never repeat')
        runtime=(build.ROOT/'scripts/register_ms_crisis_runtime.py').read_text(encoding='utf-8').split("\nif __name__=='__main__':")[0]
        old=(build.ROOT/'scripts/register_ms_private_batch_runtime_v2.py').read_text(encoding='utf-8')
        helpers='\n'.join(node_source(old,n)[1] for n in ('digest','rows','normalize_uuids','state'))
        source='REGISTRATION_BINDING='+repr(binding)+'\n'+runtime+'\n'+helpers+'\ndef digest_state(value):return digest(value)\nmain()\n'
        compile(source,'ms-crisis-private-registration','exec')
        template=corrected_template(job,source);no_matching_execution(JOB,[template['containers'][0]['args']]);folder.mkdir()
        with (folder/'frozen-source.py').open('x',encoding='utf-8') as f:f.write(source)
        save(folder,'intent',{'binding':binding,'template':template,'image':image,'settings_invariant':jobs.settings_invariant(job),
            'source_sha256':build.sha(folder/'frozen-source.py'),'database_writes':85 if mode=='register' else 0,
            'rollback_rehearsal':mode=='rehearse','approval_writes':0,'source_data_writes':0})
        print(json.dumps({'prepared':mode,'approval_writes':0}));return
    intent=read(folder,'intent');base.verify_protected(intent)
    if intent['binding']!=binding or jobs.settings_invariant(job)!=intent['settings_invariant'] or build.sha(folder/'frozen-source.py')!=intent['source_sha256']:raise ValueError('Frozen phase/settings changed')
    if stage=='dispatch':
        jobs.window()
        for name in build.JOBS:jobs.idle(name)
        no_matching_execution(JOB,[intent['template']['containers'][0]['args']])
        save(folder,'dispatch-intent',{'image':image,'source_sha256':intent['source_sha256']})
        ack=request('POST',jobs.path(JOB)+'/start'+API,data=intent['template']);save(folder,'dispatch',{'execution':ack['name'],'image':image})
        print(json.dumps({'phase':mode,'execution':ack['name']}));return
    if stage!='inspect':raise ValueError('Unknown stage')
    ack=read(folder,'dispatch');run=request('GET',jobs.path(JOB)+'/executions/'+ack['execution']+API)
    status=run['properties']['status'];print(json.dumps({'phase':mode,'status':status}),flush=True)
    if status!='Succeeded':return
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Execution differs')
    env={r['name']:r for r in wanted['env']};service=BlobServiceClient(env['AZURE_STORAGE_ACCOUNT_URL']['value'],credential=AzureCliCredential())
    data=service.get_blob_client('internal-reports',binding['result_blob']).download_blob().readall();result=json.loads(data)
    expected={'baseline':'baseline_passed','rehearse':'rehearsal_passed','register':'private_crisis_reports_registered','verify':'private_crisis_reports_verified'}[mode]
    if result['status']!=expected or result['count']!=21:raise ValueError('Registration proof differs')
    if mode!='baseline' and any(result[k] for k in ('source_data_changed','approvals_changed','reports_released','onboarding_changed')):raise ValueError('Registration exceeded authority')
    if mode=='rehearse' and (not result['transaction_rolled_back'] or result['database_writes']):raise ValueError('Rehearsal rollback failed')
    with (folder/'result.json').open('xb') as f:f.write(data)
    save(folder,'qualified',{'status':'passed','phase':mode,'count':21,'result_sha256':hashlib.sha256(data).hexdigest(),
        'execution':ack['execution'],'approval_writes':0,'transaction_rolled_back':result.get('transaction_rolled_back',False)})
    print(json.dumps({'qualified':mode,'months':21,'approval_writes':0}))
if __name__=='__main__':main()
