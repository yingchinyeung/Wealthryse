"""Fresh read-only preflight after scheduled work; private create-only evidence."""
import argparse
import hashlib
import json
from pathlib import Path

from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
import qualify_ms_website_native_runtime as qualified
from read_ms_website_input_probe import verify_env

p=qualified.probe
FOLDER=p.ROOT/'.work/alpha-ms-website-release-20261008-v1'
STAGE=FOLDER/'fresh-inputs-v1'
PREFIX='review/alpha-world-ms/website-release-20261008-v1/fresh-inputs/'


def main():
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=('prepare','dispatch','inspect'));args=parser.parse_args()
    base=p.release.read(FOLDER,'baseline');p.verify_protected(base)
    job=p.request('GET',base['manual_job']['id']+p.release.API)
    if job['properties']['template']['containers'][0]['image']!=p.IMAGE:
        raise ValueError('Installed manual worker changed')
    if args.stage=='prepare':
        if STAGE.exists():raise ValueError('Preserve preflight')
        source=(p.ROOT/'scripts/probe_ms_website_comparison_inputs.py').read_text(encoding='utf-8')
        original="PREFIX = 'review/alpha-world-ms/website-comparisons-hkd-20261008-v1/'"
        if source.count(original)!=1:raise ValueError('Input probe prefix differs')
        source=source.replace(original,'PREFIX = '+repr(PREFIX))
        template=p.corrected_template(job,source)
        p.release.window();p.release.idle(p.JOB)
        p.no_matching_execution(p.JOB,[template['containers'][0]['args']])
        STAGE.mkdir()
        with (STAGE/'frozen-source.py').open('x',encoding='utf-8',newline='\n') as f:f.write(source)
        intent={'template':template,'source_sha256':p.sha(STAGE/'frozen-source.py'),
            'controller_sha256':p.sha(Path(__file__)),'job_settings_sha256':p.release.settings_invariant(job),
            'persistent_database_writes':0,'factsheets_changed':False}
        p.release.build.save(STAGE,'intent',{**intent,'protected_receipt':p.retain(intent,'ms-website-release-fresh-input-v1-intent')})
        print(json.dumps({'status':'prepared','read_only':True}),flush=True);return
    intent=p.release.read(STAGE,'intent');p.verify_protected(intent)
    template=p.corrected_template(job,(STAGE/'frozen-source.py').read_text(encoding='utf-8'))
    if (template!=intent['template'] or p.sha(Path(__file__))!=intent['controller_sha256']
            or p.sha(STAGE/'frozen-source.py')!=intent['source_sha256']
            or p.release.settings_invariant(job)!=intent['job_settings_sha256']):
        raise ValueError('Frozen preflight changed')
    if args.stage=='dispatch':
        if (STAGE/'dispatch-intent.json').exists():raise ValueError('Inspect rather than redispatch')
        p.release.window();p.release.idle(p.JOB)
        p.no_matching_execution(p.JOB,[template['containers'][0]['args']])
        p.release.build.save(STAGE,'dispatch-intent',{'source_sha256':intent['source_sha256']})
        ack=p.request('POST',base['manual_job']['id']+'/start'+p.release.API,data=template)
        if not ack.get('name'):raise ValueError('Unknown dispatch; do not repeat')
        p.release.build.save(STAGE,'dispatch',{'execution':ack['name']})
        print(json.dumps({'execution':ack['name'],'read_only':True}),flush=True);return
    name=p.release.read(STAGE,'dispatch')['execution']
    run=p.request('GET',base['manual_job']['id']+'/executions/'+name+p.release.API)
    print(json.dumps({'execution':name,'status':run['properties']['status']}),flush=True)
    if run['properties']['status']!='Succeeded':return
    actual=run['properties']['template']['containers']
    if len(actual)!=1 or any(actual[0].get(k)!=template['containers'][0].get(k) for k in ('name','command','args','image')):
        raise ValueError('Executed input preflight differs')
    verify_env(template['containers'][0]['env'],actual[0]['env'],job)
    blob=BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=AzureCliCredential()).get_blob_client('internal-reports',PREFIX+'inputs.json')
    content=blob.download_blob().readall();value=json.loads(content);digest=hashlib.sha256(content).hexdigest()
    if (blob.get_blob_properties().metadata.get('sha256')!=digest or value['status']!='read_only_inputs_captured'
            or value['database_writes']!=0 or any(value[k] is not False for k in ('factsheets_changed','ibkr_source_changed','reports_released','onboarding_activated'))):
        raise ValueError('Preflight integrity/scope failed')
    if not (STAGE/'inputs.json').exists():
        with (STAGE/'inputs.json').open('xb') as f:f.write(content)
    elif (STAGE/'inputs.json').read_bytes()!=content:raise ValueError('Prior input bytes differ')
    proof={'status':'fresh_read_only_inputs_verified','execution':name,'inputs_sha256':digest,
        'input_blob':PREFIX+'inputs.json','persistent_database_writes':0,'factsheets_changed':False}
    if not (STAGE/'qualified.json').exists():
        p.release.build.save(STAGE,'qualified',{**proof,'protected_receipt':p.retain(proof,'ms-website-release-fresh-input-v1-passed')})
    print(json.dumps({'status':proof['status'],'nav_rows':len(value['nav']),
        'nav_latest':max(r['snapshot_date'] for r in value['nav']),'price_rows':len(value['prices'])}),flush=True)


if __name__=='__main__':main()
