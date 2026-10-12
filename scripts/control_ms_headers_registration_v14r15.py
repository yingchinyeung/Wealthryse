"""One-shot, protected 21-factsheet registration; old approvals remain intact."""
import hashlib
import json
from pathlib import Path
import sys
import uuid

from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
import release_alpha_ms_headers_v14r15 as c
import build_ms_presentation_worker_candidate as build
import deploy_ms_headers_v14r15 as deployed
import deploy_ms_presentation_worker_release as jobs
from deploy_alpha_ms_allocation_geometry import node_source
from deploy_metadata_monitor import request
from verify_installed_ms_worker_release import corrected_template,no_matching_execution
from run_specialist_gateway_read_verification import retain

API='?api-version=2024-03-01'
FOLDER=c.FOLDER/'registration-v1'
PREFIX='review/alpha-world-ms/headers-v14r15-20261008-r2/registration/'
JOB=build.JOBS[0]


def read(folder,name):return json.loads((folder/(name+'.json')).read_bytes())


def save(folder,name,record):
    build.save(folder,name,{**record,'protected_receipt':retain(record,'ms-headers-v14r15-registration-'+folder.name+'-'+name)})


def main():
    stage,mode=sys.argv[1:3]
    if mode not in ('baseline','register','verify'):raise ValueError('Unknown phase')
    deployed.gates()
    completion=read(deployed.FOLDER,'completion');deployed.verify_protected(completion)
    if completion['status']!='scoped_headers_deployed':raise ValueError('Verified installed layout required')
    image=deployed.image('worker')
    for name in build.JOBS:
        current=request('GET',jobs.path(name)+API)
        if current['properties']['template']['containers'][0]['image']!=image:raise ValueError('Installed worker image drift')
    job=request('GET',jobs.path(JOB)+API)
    if not FOLDER.exists():
        if stage!='prepare' or mode!='baseline':raise ValueError('Baseline must be prepared first')
        FOLDER.mkdir()
        proof=c.FOLDER/'worker/candidate-runtime-v1/verification.json'
        rows=json.loads(proof.read_bytes())['artifacts']
        manifest=read(c.FOLDER/'worker/context','MS_LAYOUT_RELEASE')
        record={'release_id':str(uuid.uuid4()),'image':image,'layout_version':c.LAYOUT,
            'installed_files_sha256':manifest['installed_files_sha256'],
            'periods':[r['period_end'] for r in rows],
            'proof_blob':'review/alpha-world-ms/headers-v14r15-20261008-r2/worker/candidate/verification.json',
            'proof_sha256':build.sha(proof),'scope':'21 factsheet generation-run inserts for owner review only; all old records preserved'}
        save(FOLDER,'plan',record)
    plan=read(FOLDER,'plan');deployed.verify_protected(plan)
    folder=FOLDER/mode
    binding={k:v for k,v in plan.items() if k not in ('protected_receipt','scope')}
    binding.update(mode=mode,result_blob=PREFIX+mode+'.json')
    if mode!='baseline':
        baseline=read(FOLDER/'baseline','qualified');deployed.verify_protected(baseline)
        binding.update(baseline_blob=PREFIX+'baseline.json',baseline_sha256=baseline['result_sha256'])
    if stage=='prepare':
        jobs.window();jobs.idle(JOB)
        if folder.exists():raise ValueError('Frozen phase exists; inspect, never repeat')
        runtime=(build.ROOT/'scripts/register_ms_headers_runtime_v14r15.py').read_text(encoding='utf-8').split("\nif __name__=='__main__':")[0]
        old=(build.ROOT/'scripts/register_ms_private_batch_runtime_v2.py').read_text(encoding='utf-8')
        helpers='\n'.join(node_source(old,name)[1] for name in ('digest','rows','normalize_uuids','state'))
        source='REGISTRATION_BINDING='+repr(binding)+'\n'+runtime+'\n'+helpers+'\nmain()\n'
        template=corrected_template(job,source)
        no_matching_execution(JOB,[template['containers'][0]['args']])
        folder.mkdir()
        with (folder/'frozen-source.py').open('x',encoding='utf-8') as stream:stream.write(source)
        save(folder,'intent',{'binding':binding,'template':template,'image':image,
            'settings_invariant':jobs.settings_invariant(job),'source_sha256':build.sha(folder/'frozen-source.py'),
            'database_writes':22 if mode=='register' else 0,
            'factsheet_runs_created':21 if mode=='register' else 0,
            'audit_events_created':1 if mode=='register' else 0,
            'approval_writes':0,'source_data_writes':0})
        print(json.dumps({'prepared':mode,'approval_writes':0}));return
    intent=read(folder,'intent');deployed.verify_protected(intent)
    if intent['binding']!=binding or jobs.settings_invariant(job)!=intent['settings_invariant'] or build.sha(folder/'frozen-source.py')!=intent['source_sha256']:raise ValueError('Frozen phase changed')
    if stage=='dispatch':
        jobs.window()
        for name in build.JOBS:jobs.idle(name)
        no_matching_execution(JOB,[intent['template']['containers'][0]['args']])
        save(folder,'dispatch-intent',{'image':image,'source_sha256':intent['source_sha256']})
        ack=request('POST',jobs.path(JOB)+'/start'+API,data=intent['template'])
        save(folder,'dispatch',{'execution':ack['name'],'image':image})
        print(json.dumps({'phase':mode,'execution':ack['name']}));return
    if stage!='inspect':raise ValueError('Unknown stage')
    ack=read(folder,'dispatch');run=request('GET',jobs.path(JOB)+'/executions/'+ack['execution']+API)
    status=run['properties']['status'];print(json.dumps({'phase':mode,'status':status}),flush=True)
    if status!='Succeeded':return
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Execution projection differs')
    env={e['name']:e for e in wanted['env']}
    service=BlobServiceClient(env['AZURE_STORAGE_ACCOUNT_URL']['value'],credential=AzureCliCredential())
    data=service.get_blob_client('internal-reports',binding['result_blob']).download_blob().readall()
    result=json.loads(data);expected={'baseline':'baseline_passed','register':'factsheets_registered_for_owner_review','verify':'registered_factsheets_verified'}[mode]
    if result['status']!=expected or result.get('count',21)!=21:raise ValueError('Registration proof differs')
    if mode!='baseline' and (result['approvals_changed'] or result['source_data_changed'] or result['reports_released'] or result['onboarding_changed']):raise ValueError('Registration exceeded scope')
    with (folder/'result.json').open('xb') as stream:stream.write(data)
    save(folder,'qualified',{'status':'passed','phase':mode,'result_sha256':hashlib.sha256(data).hexdigest(),
        'count':21,'execution':ack['execution'],'approval_writes':0})
    print(json.dumps({'qualified':mode,'factsheets':21,'approval_writes':0}))


if __name__=='__main__':main()
