"""Exact-image read-only qualification and baseline recovery rehearsal on a manual job."""
import argparse,base64,gzip,hashlib,json,re
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
import run_ms_website_input_probe as p
from read_ms_website_input_probe import verify_env
from scan_ms_presentation_worker_candidate import verify,jobs_unchanged

ROOT=p.ROOT/'.work/viewer-report-investigation-20261008'
CANDIDATE=ROOT/'candidate-v2'

def gate():
    source,native,output=verify(CANDIDATE)
    p.verify_protected(source)
    scan=p.release.read(CANDIDATE,'scan-completed')
    protected=p.release.read(CANDIDATE,'scan-protected-receipt')
    restored,_=p.restore_protected(protected)
    image=output['registry']+'/'+output['repository']+'@'+output['digest']
    if restored!=scan or scan['image']!=image or scan['assessment']['status']!='pass':raise ValueError('Exact protected passing scan required')
    jobs_unchanged(CANDIDATE)
    return image

def main():
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=('prepare','dispatch','inspect'));parser.add_argument('--kind',choices=('candidate','rollback'),required=True);args=parser.parse_args()
    image=gate();folder=ROOT/('native-'+args.kind+'-v2')
    baseline=p.release.read(CANDIDATE,'baseline');p.verify_protected(baseline)
    if args.kind=='rollback':image=baseline['image']
    job=p.request('GET',p.release.path(p.JOB)+p.release.API)
    p.release.scope(job,p.JOB)
    if args.stage=='prepare':
        if folder.exists():raise ValueError('Preserve prior runtime intent')
        manifest=json.loads((CANDIDATE/'context/MS_WEBSITE_BENCHMARK_RELEASE.json').read_bytes())
        expected=manifest['approved_report_files_sha256'].copy()
        if args.kind=='candidate':expected.update(manifest['website_files_sha256'])
        guard="import sys,json,hashlib\nfrom pathlib import Path\nsys.path[:0]=['/app/src','/app/scripts']\nexpected="+repr(expected)+"\nfor name,digest in expected.items():\n if hashlib.sha256(Path('/app',name).read_bytes()).hexdigest()!=digest:raise ValueError('Runtime installed file differs: '+name)\nfrom ibkr_etl.monthly_artifacts import factsheet_layout_version\nif factsheet_layout_version('alpha-world-ms')!='subject-completed-factsheet-model-v14r13-alpha-world-ms-distribution-quarter-ticks':raise ValueError('Approved report layout differs')\n"
        if args.kind=='candidate':
            guard+="import importlib.util,os\nif importlib.util.find_spec('pip') is not None or os.geteuid()!=10001:raise ValueError('Qualified installer-free non-root runtime required')\n"
        probe=(p.ROOT/'scripts/probe_viewer_report_20261008.py').read_text(encoding='utf-8').replace("'get_performance'","'get_performance_series'").replace('probe-v1.json','native-'+args.kind+'-v2.json')
        source=guard+probe
        for name in ('run_onboarded_viewer_access','collect_operational_evidence'):
            text=(p.ROOT/'scripts'/f'{name}.py').read_text(encoding='utf-8')
            source="import sys,types\nm=types.ModuleType("+repr(name)+");sys.modules[m.__name__]=m;exec(compile("+repr(text)+",'<frozen-read-only-helper>','exec'),m.__dict__)\n"+source
        template=p.corrected_template(job,source);c=template['containers'][0];c['image']=image
        encoded=re.findall(r"'([A-Za-z0-9+/=]{100,})'",c['args'][1])
        if len(encoded)!=1 or gzip.decompress(base64.b64decode(encoded[0])).decode()!=source:raise ValueError('Frozen runtime source differs')
        c['args']=['-c',"import sys,base64,gzip;exec(compile(gzip.decompress(base64.b64decode(''.join(sys.argv[1:]))),'exact-viewer-runtime','exec'))"]+[encoded[0][i:i+32000] for i in range(0,len(encoded[0]),32000)]
        c['env']=[e for e in c['env'] if e['name']!='AUTHORIZATION_MODE']+[{'name':'AUTHORIZATION_MODE','value':'database'}]
        p.release.idle(p.JOB);folder.mkdir()
        record={'kind':args.kind,'image':image,'template':template,'job_settings_sha256':p.release.settings_invariant(job),'source_sha256':hashlib.sha256(source.encode()).hexdigest(),'database_writes':0,'gateway_tested':False}
        p.release.build.save(folder,'intent',{**record,'protected_receipt':p.retain(record,'viewer-native-'+args.kind+'-intent')});print('Prepared exact '+args.kind+' runtime');return
    intent=p.release.read(folder,'intent');p.verify_protected(intent)
    if intent['image']!=image or p.release.settings_invariant(job)!=intent['job_settings_sha256']:raise ValueError('Runtime scope drift')
    if args.stage=='dispatch':
        if (folder/'dispatch-intent.json').exists():raise ValueError('Inspect, never repeat dispatch')
        p.release.idle(p.JOB);p.no_matching_execution(p.JOB,[intent['template']['containers'][0]['args']])
        p.release.build.save(folder,'dispatch-intent',{'intent_sha256':p.sha(folder/'intent.json')})
        ack=p.request('POST',p.release.path(p.JOB)+'/start'+p.release.API,data=intent['template'])
        if not ack.get('name'):raise ValueError('Unknown dispatch; inspect')
        p.release.build.save(folder,'dispatch',{'execution':ack['name']});print(json.dumps({'execution':ack['name']}));return
    name=p.release.read(folder,'dispatch')['execution'];run=p.request('GET',p.release.path(p.JOB)+'/executions/'+name+p.release.API)
    print(json.dumps({'execution':name,'status':run['properties']['status']}),flush=True)
    if run['properties']['status']!='Succeeded':return
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','name','command','args')):raise ValueError('Executed runtime differs')
    verify_env(wanted['env'],actual['env'],job)
    blob=BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=AzureCliCredential()).get_blob_client('internal-reports','review/viewer-report-investigation-20261008/native-'+args.kind+'-v2.json')
    data=blob.download_blob().readall();digest=hashlib.sha256(data).hexdigest()
    if blob.get_blob_properties().metadata.get('sha256')!=digest:raise ValueError('Runtime proof hash differs')
    proof=json.loads(data)
    if proof['database_writes']!=0 or len(proof['checks'])!=208:raise ValueError('Complete four-viewer runtime proof required')
    if args.kind=='candidate':
        errors=[r for r in proof['checks'] if r.get('error_type') or r.get('passed') is False or r.get('status',200)!=200 and not (r.get('status')==409 and r['endpoint']=='export/trades.csv' and r['subject_id'] in ('alpha-world-ms','hong-kong-china'))]
        if errors or any(r.get('csv_rows')==0 for r in proof['checks'] if r['endpoint'] in ('export/performance.csv','export/holdings.csv')):raise ValueError('Native candidate viewer checks failed')
    else:
        summaries=[r for r in proof['checks'] if r['endpoint']=='summary']
        if len(summaries)!=16 or any(r.get('status')!=200 or r.get('error_type') for r in summaries):raise ValueError('Rollback image cannot read current portfolio data')
    target=folder/'proof.json'
    if not target.exists():target.write_bytes(data)
    elif target.read_bytes()!=data:raise ValueError('Preserve prior proof')
    record={'status':'passed','kind':args.kind,'image':image,'execution':name,'checks':208,'proof_sha256':digest,'source_sha256':intent['source_sha256'],'approved_report_assets_unchanged':True,'database_writes':0,'gateway_tested':False}
    if not (folder/'qualified.json').exists():p.release.build.save(folder,'qualified',{**record,'protected_receipt':p.retain(record,'viewer-native-'+args.kind+'-qualified')})
    print(json.dumps(record))

if __name__=='__main__':main()
