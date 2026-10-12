"""One-shot native read-only qualification, not a deployment controller."""
import hashlib
import json
import sys
from pathlib import Path
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
import release_ms_crisis as candidate
import build_ms_presentation_worker_candidate as build
import deploy_ms_presentation_worker_release as jobs
from deploy_metadata_monitor import request
from verify_installed_ms_worker_release import corrected_template,no_matching_execution
from run_specialist_gateway_read_verification import retain
API='?api-version=2024-03-01'
def save(folder,name,record):build.save(folder,name,{**record,'protected_receipt':retain(record,'ms-crisis-runtime-'+folder.parent.name+'-'+folder.name+'-'+name)})
def main():
    stage,kind=sys.argv[1:3];folder=candidate.CANDIDATES[kind]/'candidate-runtime-v1'
    if kind not in candidate.IMAGES:raise ValueError('Unknown image scope')
    parent=folder.parent;scan=json.loads((parent/'scan-completed.json').read_bytes())
    if scan['assessment']['status']!='pass' or scan['assessment']['blocking_findings']!=0:raise ValueError('Exact image scan required')
    manifest=json.loads((parent/'context/MS_CRISIS_RELEASE.json').read_bytes())
    months=json.loads((candidate.FOLDER.parent/'qualified-inputs-v1/verification.json').read_bytes())['rows']
    binding={'image':scan['image'],'installed_files_sha256':manifest['installed_files_sha256'],'months':months,
             'prefix':'review/alpha-world-ms/crisis-release-20261008/'+parent.parent.name+'/'+kind+'/candidate-runtime-v1/'}
    jobname=build.JOBS[0] if kind=='worker' else build.JOBS[1]
    job=request('GET',jobs.path(jobname)+API)
    if job['properties']['template']['containers'][0]['image']!=candidate.IMAGES['worker']:raise ValueError('Persisted worker image drifted')
    if stage=='prepare':
        if folder.exists():raise ValueError('Frozen runtime already exists; inspect it')
        jobs.window();jobs.idle(jobname)
        source='PROBE_BINDING='+repr(binding)+'\n'+(build.ROOT/'scripts/probe_ms_crisis_runtime.py').read_text(encoding='utf-8')
        template=corrected_template(job,source);template['containers'][0]['image']=binding['image']
        no_matching_execution(jobname,[template['containers'][0]['args']]);folder.mkdir()
        with (folder/'frozen-probe.py').open('x',encoding='utf-8') as stream:stream.write(source)
        save(folder,'intent',{'binding':binding,'job':jobname,'template':template,'probe_sha256':build.sha(folder/'frozen-probe.py'),
            'settings_invariant':jobs.settings_invariant(job),'database_writes':0})
        print(json.dumps({'prepared':True,'kind':kind}));return
    intent=json.loads((folder/'intent.json').read_bytes())
    if intent['binding']!=binding or intent['settings_invariant']!=jobs.settings_invariant(job) or intent['probe_sha256']!=build.sha(folder/'frozen-probe.py'):raise ValueError('Runtime source/settings drift')
    if stage=='dispatch':
        jobs.window();jobs.idle(jobname);no_matching_execution(jobname,[intent['template']['containers'][0]['args']])
        save(folder,'dispatch-intent',{'image':binding['image'],'probe_sha256':intent['probe_sha256']})
        ack=request('POST',jobs.path(jobname)+'/start'+API,data=intent['template']);save(folder,'dispatch',{'execution':ack['name']})
        print(json.dumps({'execution':ack['name'],'kind':kind}),flush=True);return
    if stage!='inspect':raise ValueError('Unknown runtime stage')
    ack=json.loads((folder/'dispatch.json').read_bytes());run=request('GET',jobs.path(jobname)+'/executions/'+ack['execution']+API)
    status=run['properties']['status'];print(json.dumps({'status':status,'kind':kind}),flush=True)
    if status!='Succeeded':return
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Executed source differs')
    env={r['name']:r for r in wanted['env']};service=BlobServiceClient(env['AZURE_STORAGE_ACCOUNT_URL']['value'],credential=AzureCliCredential())
    data=service.get_blob_client('internal-reports',binding['prefix']+'verification.json').download_blob().readall();proof=json.loads(data)
    if proof['status']!='passed' or proof['count']!=21 or proof['image']!=binding['image'] or proof['database_writes']!=0:raise ValueError('Runtime evidence failed')
    output=folder/'outputs';output.mkdir()
    for row in proof['artifacts']:
        for kind2 in ('factsheet','presentation','payload'):
            content=service.get_blob_client('internal-reports',row[kind2+'_blob']).download_blob().readall()
            digest=row['data_sha256'] if kind2=='payload' else row[kind2+'_sha256']
            if hashlib.sha256(content).hexdigest()!=digest:raise ValueError('Private output differs')
            name=Path(row[kind2+'_blob']).name
            with (output/name).open('xb') as stream:stream.write(content)
    with (folder/'verification.json').open('xb') as stream:stream.write(data)
    save(folder,'qualified',{'status':'passed','count':21,'image':binding['image'],'execution':ack['execution'],
        'verification_sha256':build.sha(folder/'verification.json'),'database_writes':0,'reports_released':False})
    print(json.dumps({'qualified':True,'kind':kind,'months':21}))
if __name__=='__main__':main()
