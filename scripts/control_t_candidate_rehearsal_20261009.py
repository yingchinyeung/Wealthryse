"""Run the exact scanned T image once, without updating any saved Azure job."""
import base64,copy,hashlib,json,sys,zlib
from pathlib import Path
import run_alpha_ms_recovery_operation as controller
from scan_ms_presentation_worker_candidate import verify
from ibkr_etl.alpha_world_t_presentation import build_model

ROOT=Path(__file__).resolve().parents[1]
FOLDER=ROOT/'.work/alpha-t-candidate-rehearsal-20261009-v3'
CANDIDATE=ROOT/'.work/alpha-t-runtime-release-20261009-v7/worker'


def start():
    FOLDER.mkdir(exist_ok=True);controller.FOLDER=FOLDER
    if (FOLDER/'runtime-intent.json').exists():raise ValueError('Existing dispatch; reconcile, never repeat')
    source,native,output=verify(CANDIDATE)
    scan=json.loads((CANDIDATE/'scan-completed.json').read_bytes())
    image=output['registry']+'/'+output['repository']+'@'+output['digest']
    # Match the repository gate, not merely existence of scanner output.
    if (scan['image']!=image or scan['assessment']['status']!='pass'
            or scan['assessment']['blocking_findings']!=0):
        raise ValueError('Exact candidate must pass the vulnerability gate')
    release=json.loads((CANDIDATE/'context/ALPHA_T_RUNTIME_RELEASE.json').read_bytes())
    months=[]
    for path in sorted((CANDIDATE/'context/tests/t-release-fixtures/inputs').glob('*.json')):
        snapshot=json.loads(path.read_bytes());model=build_model(snapshot)
        months.append({'period_end':snapshot['period_end'],'metrics':model['risk']})
    if len(months)!=21:raise ValueError('Expected 21 frozen inputs')
    bundle={'sources':{},'hashes':{},'installed_candidate':True,
        'installed_files_sha256':release['installed_files_sha256'],
        'months':months,'nav':snapshot['performance']['series'],
        'expected_subject_state':[{'subject_id':'alpha-world-t','lifecycle_status':'onboarding',
            'base_currency':'HKD','reporting_currency':'HKD'}]}
    encoded=base64.b64encode(zlib.compress(json.dumps(bundle).encode())).decode()
    parts=[encoded[i:i+24000] for i in range(0,len(encoded),24000)]
    env={f'T_QUALIFICATION_{i}':v for i,v in enumerate(parts)}
    env.update(IBKR_RUN_MIGRATIONS='false',T_QUALIFICATION_PARTS=str(len(parts)))
    path=controller.BASE+'Microsoft.App/jobs/ibkrv2stg-onboard-render'
    job=controller.request('GET',path+'?api-version=2024-03-01')
    runs=controller.request('GET',path+'/executions?api-version=2024-03-01')['value']
    if any(r['properties']['status'] in ('Running','Processing') for r in runs):
        raise ValueError('Existing reporting job is busy')
    if job['properties']['template']['containers'][0]['image']!=source['base_image']:
        raise ValueError('Saved job baseline drift')
    template={k:v for k,v in copy.deepcopy(job['properties']['template']).items() if v is not None}
    container=template['containers'][0];container['image']=image
    script=(ROOT/'scripts/qualify_t_postgres_20261009.py').read_bytes()
    container['command']=['python'];container['args']=['-c',
        "import base64,zlib;exec(compile(zlib.decompress(base64.b64decode('"+
        base64.b64encode(zlib.compress(script)).decode()+"')),'t-candidate-rehearsal','exec'))"]
    for key,value in env.items():
        container['env']=[e for e in container.get('env',[]) if e['name']!=key]+[{'name':key,'value':value}]
    controller.save('runtime-intent',{'job':'ibkrv2stg-onboard-render','image':image,
        'job_invariant':controller.invariant(job),'source_sha256':hashlib.sha256(script).hexdigest(),
        'bundle_sha256':hashlib.sha256(json.dumps(bundle,sort_keys=True).encode()).hexdigest(),
        'persistent_database_writes':0,'persistent_job_update':False})
    ack=controller.request('POST',path+'/start?api-version=2024-03-01',data=template)
    controller.save('runtime-dispatch',{'job':'ibkrv2stg-onboard-render','execution':ack['name']})
    print(json.dumps({'execution':ack['name'],'saved_job_unchanged':True}))


if __name__=='__main__':
    if sys.argv[1:]==['start']:start()
    elif sys.argv[1:]==['read']:
        controller.FOLDER=FOLDER;controller.read('runtime')
    else:raise ValueError('Expected start or read')
