"""Reconcile partial v1, then use registered private template containers."""
import hashlib,json,sys
from pathlib import Path
import control_upgrade_corrected_reports_20261011 as old
from control_upgrade_readers_20261010 import save
from control_upgrade_legacy_recovery_probe_20261009 import protected
from read_upgrade_g04_worker_20261010 import exact_environment
b=old.b;a=old.a;ROOT=old.ROOT;OUT=ROOT/'.work/platform-upgrade-20261011-stage33/corrected-reports-v2'
def hashes():return {**old.hashes(),'scripts/'+Path(__file__).name:hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
def main(stage):
    if stage not in ('start','read'):raise ValueError('Unknown stage')
    baseline=protected(ROOT/'.work/platform-upgrade-20261011-stage31/resume-v1/result.json');job=b.request('GET',a.PATH+b.API)
    if b.bindings([job])[a.JOB]!=baseline['bindings'][a.JOB]:raise ValueError('Reporting binding drift')
    if stage=='start':
        if OUT.exists():raise ValueError('Preserve prior attempt')
        failure=protected(old.OUT/'failure.json');intent=protected(old.OUT/'intent.json')
        if intent['sources']!=old.hashes() or failure['proof']!={'type':'ValueError','stage':'input:alpha-world-ms','message':'Unexpected template container','reconcile_before_retry':True}:raise ValueError('Prior failure/source differs')
        name=failure['execution'];prefix='UPGRADE_CORRECTED_SUBJECT '
        rows=b.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+'))')
        subjects=[json.loads(r['Result']) for r in rows]
        if subjects:raise ValueError('Expected failure before first output; reconcile any receipt')
        from azure.identity import AzureCliCredential
        from azure.storage.blob import BlobServiceClient
        with BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=AzureCliCredential()) as service:
            bucket=service.get_container_client('upgrade-g04-reports-20261010');expected={}
            for r in subjects:
                if r['validation_status']!='passed' or len(r['artifacts'])!=2:raise ValueError('Partial candidate validation failed')
                for item in r['artifacts']:
                    container,key=item['output_reference'].split('/',1)
                    if container!='upgrade-g04-reports-20261010' or not key.startswith('candidate-stage33-v1/'+r['subject']+'/'):raise ValueError('Unexpected partial output')
                    if hashlib.sha256(bucket.download_blob(key).readall()).hexdigest()!=item['sha256']:raise ValueError('Partial output hash differs')
                    expected[key]=item['sha256']
            if set(v.name for v in bucket.list_blobs(name_starts_with='candidate-stage33-v1/'))!=set(expected):raise ValueError('Unexpected partial candidate inventory')
        b.runtime.safe_window();b.runtime.idle(a.JOB)
        if b.bindings(b.current_resources())!=baseline['bindings']:raise ValueError('Workload drift')
        source=(ROOT/old.FILES[1]).read_text(encoding='utf-8')
        anchor="if asset['container_name']!='internal-reports':raise ValueError('Unexpected template container')"
        if source.count(anchor)!=1:raise ValueError('Exact template guard anchor required')
        source=source.replace("PREFIX='candidate-stage33-v1/'","PREFIX='candidate-stage33-v2/'").replace(anchor,
            "if service.get_container_client(asset['container_name']).get_container_properties().get('public_access') is not None:raise ValueError('Approved template container must be private')")
        snapshots=protected(ROOT/'.work/platform-upgrade-20261011-stage31/fresh-snapshots-v1/result.json')
        code='import types,sys,os\nos.environ["DATABASE_ADMIN_URL"]=os.environ["DATABASE_URL"]\nq=types.ModuleType("qa_read_only");sys.modules[q.__name__]=q\nexec('+repr((ROOT/old.FILES[2]).read_text(encoding='utf-8'))+',q.__dict__)\n'
        code+='EXPECTED_HASHES='+repr({s['subject']:s['sha256'] for s in snapshots['proof']['subjects']})+'\nPYTHON_CANDIDATES='+repr({k:(ROOT/v).read_text(encoding='utf-8') for k,v in old.PYTHON.items()})+'\nJS_CANDIDATES='+repr({n:(ROOT/'scripts'/n).read_text(encoding='utf-8') for n in old.JS})+'\n'+source
        template=b.corrected_template(job,code);names={e['name'] for e in intent['template']['containers'][0]['env']}
        template['containers'][0]['env']=[e for e in template['containers'][0]['env'] if e['name'] in names]
        save(OUT,'intent',{'template':template,'sources':hashes(),'prior_failure':failure['protected_receipt'],'partial_outputs_reconciled':subjects,
            'scope':'Private registered template container reads, exact template hashes, read-only QA DB, ten new QA v2 outputs only. Failed v1 had zero QA outputs, confirmed by inventory. No registration/publication/deployment.'})
        ack=b.request('POST',a.PATH+'/start'+b.API,data=template);save(OUT,'dispatch',{'execution':ack['name']});print(json.dumps({'execution':ack['name'],'partial_outputs_reconciled':0}));return
    intent=protected(OUT/'intent.json')
    if intent['sources']!=hashes():raise ValueError('Candidate source drift')
    name=protected(OUT/'dispatch.json')['execution'];run=b.request('GET',a.PATH+'/executions/'+name+b.API)
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Execution drift')
    exact_environment(a.JOB,actual.get('env') or [],wanted['env']);state=run['properties']['status']
    if state not in ('Succeeded','Failed'):print(state);return
    prefix='UPGRADE_CORRECTED_FINAL ' if state=='Succeeded' else 'UPGRADE_CORRECTED_ERROR '
    rows=b.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+'))')
    if len(rows)!=1:print('Awaiting proof ingestion');return
    proof=json.loads(rows[0]['Result'])
    if state=='Failed':
        if not (OUT/'failure.json').exists():save(OUT,'failure',{'execution':name,'proof':proof})
        print(json.dumps({'failed':True,'proof':proof}));return
    if proof.get('passed') is not True or proof.get('artifacts')!=10 or proof.get('database_writes')!=0 or proof.get('registered_reports')!=0 or not proof.get('qa_state_unchanged') or proof.get('production_connection_used') is not False:raise ValueError('Incomplete isolation evidence')
    if not (OUT/'result.json').exists():save(OUT,'result',{'passed':True,'execution':name,'proof':proof,'full_upgrade_complete':False})
    print(json.dumps({'passed':True,'artifacts':10,'deployed':False,'visual_acceptance':'UNVERIFIED'}))
if __name__=='__main__':main(*sys.argv[1:])
