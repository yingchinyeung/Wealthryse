"""One-shot private read-only cloud rendering. Does not update any service."""
import json
from pathlib import Path
import sys

from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
from pypdf import PdfReader

import release_alpha_ms_headers_v14r15 as candidate
import build_ms_presentation_worker_candidate as build
import deploy_ms_presentation_worker_release as deploy
from deploy_metadata_monitor import request
from verify_installed_ms_worker_release import corrected_template,no_matching_execution
from run_specialist_gateway_read_verification import retain
from ibkr_etl.alpha_world_ms_subtitle import validate as subtitle_validate
from ibkr_etl.alpha_world_ms_pdf_validation import validate_date_headers

API='?api-version=2024-03-01'


def save(folder,name,record):
    build.save(folder,name,{**record,'protected_receipt':retain(record,'ms-headers-v14r15-'+folder.name+'-'+name)})


def read(folder,name):return json.loads((folder/(name+'.json')).read_bytes())


def binding(kind,mode):
    folder=candidate.FOLDER/kind
    intent=read(folder,'source-intent')
    release=read(folder/'context','MS_LAYOUT_RELEASE')
    if mode=='rollback':
        image=candidate.IMAGES[kind];files=release['baseline_files_sha256']
        layout=json.loads((candidate.BASE/'ALPHA_MS_HKD_RELEASE.json').read_bytes())['layout_version']
    else:
        scan=read(folder,'scan-completed')
        if scan['assessment']['status']!='pass':raise ValueError('Exact candidate scan must pass')
        output=read(folder,'build-completed')['output_images'][0]
        image=output['registry']+'/'+output['repository']+'@'+output['digest']
        if image!=scan['image']:raise ValueError('Scan/native image differs')
        files=release['installed_files_sha256'];layout=candidate.LAYOUT
    return {'mode':mode,'image':image,'installed_files_sha256':files,'layout_version':layout,
        'prefix':'review/alpha-world-ms/headers-v14r15-20261008-r2/'+kind+'/'+mode+'/',
        'periods':sorted(json.loads((candidate.BASE/'ALPHA_MS_HKD_RELEASE.json').read_bytes())['snapshot_sha256'])}


def main():
    stage,kind,mode=sys.argv[1:4]
    if kind not in candidate.IMAGES or mode not in ('candidate','rollback','installed'):raise ValueError('Unknown scope')
    folder=candidate.FOLDER/kind/(mode+'-runtime-v1')
    job_name=build.JOBS[0] if kind=='web' else build.JOBS[1]
    b=binding(kind,mode)
    job=request('GET',deploy.path(job_name)+API)
    expected=candidate.IMAGES['worker'] if mode!='installed' else binding('worker','candidate')['image']
    if job['properties']['template']['containers'][0]['image']!=expected:raise ValueError('Persisted job image drift')
    if stage=='prepare':
        if folder.exists():raise ValueError('Frozen probe exists; inspect it')
        deploy.window();deploy.idle(job_name)
        probe=(build.ROOT/'scripts/probe_ms_headers_runtime_v14r15.py').read_text(encoding='utf-8')
        source='PROBE_BINDING='+repr(b)+'\n'+probe
        template=corrected_template(job,source);template['containers'][0]['image']=b['image']
        no_matching_execution(job_name,[template['containers'][0]['args']])
        folder.mkdir()
        with (folder/'frozen-probe.py').open('x',encoding='utf-8') as stream:stream.write(source)
        save(folder,'intent',{'binding':b,'job':job_name,'template':template,
            'before_job':job,'settings_invariant':deploy.settings_invariant(job),
            'probe_sha256':build.sha(folder/'frozen-probe.py'),'database_writes':0,
            'normal_entrypoint_invoked':False,'persistent_configuration_changed':False})
        print(json.dumps({'prepared':True,'kind':kind,'mode':mode,'months':21}));return
    intent=read(folder,'intent')
    if intent['binding']!=b or deploy.settings_invariant(job)!=intent['settings_invariant'] or build.sha(folder/'frozen-probe.py')!=intent['probe_sha256']:raise ValueError('Probe binding/settings drift')
    if stage=='dispatch':
        deploy.window();deploy.idle(job_name)
        no_matching_execution(job_name,[intent['template']['containers'][0]['args']])
        save(folder,'dispatch-intent',{'image':b['image'],'probe_sha256':intent['probe_sha256']})
        ack=request('POST',deploy.path(job_name)+'/start'+API,data=intent['template'])
        save(folder,'dispatch',{'execution':ack['name'],'image':b['image']})
        print(json.dumps({'execution':ack['name'],'kind':kind,'mode':mode}));return
    if stage!='inspect':raise ValueError('Unknown stage')
    ack=read(folder,'dispatch');run=request('GET',deploy.path(job_name)+'/executions/'+ack['execution']+API)
    status=run['properties']['status'];print(json.dumps({'status':status,'kind':kind,'mode':mode}),flush=True)
    if status!='Succeeded':return
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Executed source/image differs')
    env={e['name']:e for e in wanted['env']}
    service=BlobServiceClient(env['AZURE_STORAGE_ACCOUNT_URL']['value'],credential=AzureCliCredential())
    data=service.get_blob_client('internal-reports',b['prefix']+'verification.json').download_blob().readall()
    proof=json.loads(data)
    if proof['status']!='passed' or proof['count']!=21 or proof['image']!=b['image'] or proof['database_writes']!=0 or not proof['read_only_transaction_enforced']:raise ValueError('Private renderer proof differs')
    if [r['period_end'] for r in proof['artifacts']]!=b['periods']:raise ValueError('Month coverage differs')
    output=folder/'outputs';output.mkdir(exist_ok=True)
    for row in proof['artifacts']:
        content=service.get_blob_client('internal-reports',row['factsheet_blob']).download_blob().readall()
        import hashlib
        if hashlib.sha256(content).hexdigest()!=row['factsheet_sha256']:raise ValueError('Output hash differs')
        target=output/Path(row['factsheet_blob']).name
        if target.exists():
            if target.read_bytes()!=content:raise ValueError('Local output conflict')
        else:
            with target.open('xb') as stream:stream.write(content)
        if mode!='rollback':
            reader=PdfReader(target);subtitle_validate(reader.pages[0]);validate_date_headers(reader,row['period_end'])
    with (folder/'verification.json').open('xb') as stream:stream.write(data)
    save(folder,'qualified',{'status':'passed','image':b['image'],'execution':ack['execution'],
        'proof_sha256':build.sha(folder/'verification.json'),'count':21,'database_writes':0,
        'persistent_settings_preserved':True,'report_approvals_changed':False})
    print(json.dumps({'qualified':True,'months':21,'kind':kind,'mode':mode}))


if __name__=='__main__':main()
