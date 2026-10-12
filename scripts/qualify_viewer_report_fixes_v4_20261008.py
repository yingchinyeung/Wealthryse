"""One-shot private read-only diagnostic using the currently installed web image."""
import argparse
import hashlib
import json
import re
from pathlib import Path
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
import run_ms_website_input_probe as p
from read_ms_website_input_probe import verify_env

FOLDER=p.ROOT/'.work/viewer-report-investigation-20261008/qualification-v4'
def main():
    global FOLDER
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=('prepare','dispatch','inspect'));parser.add_argument('--revision',choices=('v4','v5'),default='v4');args=parser.parse_args();stage=args.stage
    FOLDER=p.ROOT/('.work/viewer-report-investigation-20261008/qualification-'+args.revision)
    if stage=='prepare':
        if FOLDER.exists(): raise ValueError('Preserve prior evidence')
        job=p.request('GET',p.release.path(p.JOB)+p.release.API);p.release.scope(job,p.JOB)
        web=p.request('GET',p.release.BASE+'Microsoft.App/containerApps/ibkrv2stg-web'+p.release.API)
        image=web['properties']['template']['containers'][0]['image']
        probe=(p.ROOT/'scripts/probe_viewer_report_20261008.py').read_text(encoding='utf-8').replace("'get_performance'","'get_performance_series'").replace('probe-v1.json','qualification-'+args.revision+'.json')
        candidate='candidate-v2' if args.revision=='v5' else 'candidate'
        modules=p.ROOT/('.work/viewer-report-investigation-20261008/'+candidate+'/context/src/ibkr_etl')
        prelude="import sys,types,importlib\nsys.path[:0]=['/app/src','/app/scripts']\n"
        for name in ('subject_exports','viewer_labels','metrics_guide','web_theme','agent_tools','ai_analyst','web'):
            filename=modules/(name+'.py');code=filename.read_text(encoding='utf-8')
            prelude+="m=types.ModuleType('ibkr_etl."+name+"');m.__package__='ibkr_etl';m.__file__='/app/src/ibkr_etl/"+name+".py';sys.modules[m.__name__]=m;exec(compile("+repr(code)+",m.__file__,'exec'),m.__dict__);setattr(importlib.import_module('ibkr_etl'),"+repr(name)+",m)\n"
        source=prelude+probe
        # Freeze dependencies locally into the transient source, without writing in the image.
        for name in ('run_onboarded_viewer_access','collect_operational_evidence'):
            content=(p.ROOT/'scripts'/f'{name}.py').read_text(encoding='utf-8')
            source="import types,sys\nm=types.ModuleType("+repr(name)+");sys.modules["+repr(name)+"]=m;exec(compile("+repr(content)+",'<frozen-read-only-helper>','exec'),m.__dict__)\n"+source
        template=p.corrected_template(job,source);template['containers'][0]['image']=image
        # Linux limits individual argv strings to 128 KiB. Keep the frozen
        # gzip payload byte-identical, but split its base64 into bounded argv.
        encoded=re.findall(r"'([A-Za-z0-9+/=]{100,})'",template['containers'][0]['args'][1])
        if len(encoded)!=1:raise ValueError('Unexpected compressed source encoding')
        import base64,gzip
        if gzip.decompress(base64.b64decode(encoded[0])).decode()!=source:raise ValueError('Frozen source differs')
        template['containers'][0]['args']=['-c',"import sys,base64,gzip;exec(compile(gzip.decompress(base64.b64decode(''.join(sys.argv[1:]))),'private-viewer-qualification','exec'))"]+[encoded[0][i:i+32000] for i in range(0,len(encoded[0]),32000)]
        env=template['containers'][0]['env'];env[:]=[e for e in env if e['name']!='AUTHORIZATION_MODE'];env.append({'name':'AUTHORIZATION_MODE','value':'database'})
        p.release.idle(p.JOB);FOLDER.mkdir(parents=True)
        record={'template':template,'website_image':image,'job_settings_sha256':p.release.settings_invariant(job),'authorization':'Owner requested detailed viewer report investigation and resource group diagnostic; read-only application and metadata probe','database_writes':0}
        p.release.build.save(FOLDER,'intent',{**record,'protected_receipt':p.retain(record,'viewer-report-read-only-probe-intent')});print('Prepared read-only probe');return
    intent=p.release.read(FOLDER,'intent');p.verify_protected(intent)
    job=p.request('GET',p.release.path(p.JOB)+p.release.API)
    if p.release.settings_invariant(job)!=intent['job_settings_sha256']:raise ValueError('Persisted job changed')
    template=intent['template']
    if stage=='dispatch':
        if (FOLDER/'dispatch-intent.json').exists():raise ValueError('Inspect instead of repeating')
        p.release.idle(p.JOB);p.no_matching_execution(p.JOB,[template['containers'][0]['args']])
        p.release.build.save(FOLDER,'dispatch-intent',{'intent_sha256':p.sha(FOLDER/'intent.json')})
        ack=p.request('POST',p.release.path(p.JOB)+'/start'+p.release.API,data=template)
        if not ack.get('name'):raise ValueError('Unknown dispatch; inspect')
        p.release.build.save(FOLDER,'dispatch',{'execution':ack['name']});print(json.dumps({'execution':ack['name']}));return
    name=p.release.read(FOLDER,'dispatch')['execution'];run=p.request('GET',p.release.path(p.JOB)+'/executions/'+name+p.release.API)
    print(json.dumps({'execution':name,'status':run['properties']['status']}))
    if run['properties']['status']!='Succeeded':return
    actual=run['properties']['template']['containers'][0];expected=template['containers'][0]
    if any(actual.get(k)!=expected.get(k) for k in ('image','command','args','name')):raise ValueError('Executed source differs')
    verify_env(expected['env'],actual['env'],job)
    blob=BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=AzureCliCredential()).get_blob_client('internal-reports','review/viewer-report-investigation-20261008/qualification-'+args.revision+'.json')
    data=blob.download_blob().readall();digest=hashlib.sha256(data).hexdigest()
    if blob.get_blob_properties().metadata.get('sha256')!=digest:raise ValueError('Evidence digest differs')
    if not (FOLDER/'proof.json').exists():
        with (FOLDER/'proof.json').open('xb') as stream:stream.write(data)
    elif (FOLDER/'proof.json').read_bytes()!=data:raise ValueError('Preserve prior proof')
    value=json.loads(data);print(json.dumps({'checks':len(value['checks']),'non_200':[r for r in value['checks'] if r.get('status',200)!=200 or r.get('error_type')],'empty_exports':[r for r in value['checks'] if r.get('csv_rows')==0],'subjects':value['subjects'],'source_trade_counts':value['source_trade_counts']},indent=2))

if __name__=='__main__':main()
