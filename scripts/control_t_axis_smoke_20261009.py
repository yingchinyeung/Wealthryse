"""Private candidate smoke without changing persistent Azure job settings."""
import base64,copy,json,sys,zlib
from pathlib import Path
import run_alpha_ms_recovery_operation as controller
from scan_ms_presentation_worker_candidate import verify
from release_t_axis_contract_20261009 import FOLDER,IMAGES

ROOT=Path(__file__).resolve().parents[1]
controller.FOLDER=FOLDER/'worker/smoke-v1';controller.FOLDER.mkdir(exist_ok=True)
if sys.argv[1]=='read':controller.read('runtime')
elif sys.argv[1]=='start':
    if (controller.FOLDER/'runtime-intent.json').exists():raise ValueError('Dispatch exists; reconcile first')
    _,_,output=verify(FOLDER/'worker')
    image=output['registry']+'/'+output['repository']+'@'+output['digest']
    path=controller.BASE+'Microsoft.App/jobs/ibkrv2stg-onboard-render'
    job=controller.request('GET',path+'?api-version=2024-03-01')
    if job['properties']['template']['containers'][0]['image']!=IMAGES['worker']:raise ValueError('Worker baseline drift')
    runs=controller.request('GET',path+'/executions?api-version=2024-03-01')['value']
    if any(r['properties']['status'] in ('Running','Processing') for r in runs):raise ValueError('Worker busy')
    template={k:v for k,v in copy.deepcopy(job['properties']['template']).items() if v is not None}
    container=template['containers'][0];container['image']=image
    source=(ROOT/'scripts/probe_t_axis_worker_20261009.py').read_bytes()
    container['command']=['python'];container['args']=['-c',"import base64,zlib;exec(compile(zlib.decompress(base64.b64decode('"+base64.b64encode(zlib.compress(source)).decode()+"')),'t-axis-probe','exec'))"]
    container['env']=[e for e in container.get('env',[]) if e['name']!='IBKR_RUN_MIGRATIONS']+[{'name':'IBKR_RUN_MIGRATIONS','value':'false'}]
    controller.save('runtime-intent',{'image':image,'job_invariant':controller.invariant(job),'database_writes':0,'persistent_job_change':False})
    ack=controller.request('POST',path+'/start?api-version=2024-03-01',data=template)
    controller.save('runtime-dispatch',{'job':'ibkrv2stg-onboard-render','execution':ack['name']})
    print(json.dumps({'execution':ack['name'],'image':image,'normal_job_not_invoked':True}))
else:raise ValueError('Expected start/read')
