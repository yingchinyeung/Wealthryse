"""One-shot private T report rendering with exact image/source binding."""
import base64,copy,hashlib,json,sys,zlib
from pathlib import Path
import run_alpha_ms_recovery_operation as controller
from scan_ms_presentation_worker_candidate import verify

ROOT=Path(__file__).resolve().parents[1]
CANDIDATE=ROOT/'.work/alpha-t-runtime-release-20261009-v7/worker'
FOLDER=ROOT/'.work/alpha-t-candidate-reports-20261009-v4'
JOB='ibkrv2stg-month-end-factsheet'


def start():
    FOLDER.mkdir(exist_ok=True);controller.FOLDER=FOLDER
    if (FOLDER/'reports-intent.json').exists():raise ValueError('Existing dispatch; reconcile, never repeat')
    source,native,output=verify(CANDIDATE)
    image=output['registry']+'/'+output['repository']+'@'+output['digest']
    scan=json.loads((CANDIDATE/'scan-completed.json').read_bytes())
    if scan['image']!=image or scan['assessment']['status']!='pass' or scan['assessment']['blocking_findings']:
        raise ValueError('Exact scanned candidate required')
    release=json.loads((CANDIDATE/'context/ALPHA_T_RUNTIME_RELEASE.json').read_bytes())
    binding={'image':image,'installed_files_sha256':release['installed_files_sha256'],
        'periods':release['periods'],'prefix':'review/alpha-world-t/candidate-reports-20261009-v4/',
        'input_sha256':{p.stem:hashlib.sha256(p.read_bytes()).hexdigest() for p in
            (CANDIDATE/'context/tests/t-release-fixtures/inputs').glob('*.json')}}
    path=controller.BASE+'Microsoft.App/jobs/'+JOB
    job=controller.request('GET',path+'?api-version=2024-03-01')
    runs=controller.request('GET',path+'/executions?api-version=2024-03-01')['value']
    if any(r['properties']['status'] in ('Running','Processing') for r in runs):raise ValueError('Saved job busy')
    if job['properties']['template']['containers'][0]['image']!=source['base_image']:raise ValueError('Baseline drift')
    template={k:v for k,v in copy.deepcopy(job['properties']['template']).items() if v is not None}
    container=template['containers'][0];container['image']=image
    script=(ROOT/'scripts/probe_t_candidate_reports_20261009.py').read_bytes()
    container['command']=['python'];container['args']=['-c',
        "import base64,zlib;exec(compile(zlib.decompress(base64.b64decode('"+
        base64.b64encode(zlib.compress(script)).decode()+"')),'t-report-probe','exec'))"]
    for key,value in {'IBKR_RUN_MIGRATIONS':'false','T_REPORT_PROBE_BINDING':json.dumps(binding)}.items():
        container['env']=[e for e in container.get('env',[]) if e['name']!=key]+[{'name':key,'value':value}]
    controller.save('reports-intent',{'job':JOB,'image':image,'binding':binding,
        'job_invariant':controller.invariant(job),'source_sha256':hashlib.sha256(script).hexdigest(),
        'database_writes':0,'persistent_job_update':False,'reports_released':False})
    ack=controller.request('POST',path+'/start?api-version=2024-03-01',data=template)
    controller.save('reports-dispatch',{'job':JOB,'execution':ack['name']})
    print(json.dumps({'execution':ack['name'],'saved_job_unchanged':True}))


if __name__=='__main__':
    if sys.argv[1:]==['start']:start()
    elif sys.argv[1:]==['read']:
        controller.FOLDER=FOLDER;controller.read('reports')
    else:raise ValueError('Expected start or read')
