"""Read-only software inventory on the approved private administration VM."""
import json,sys,shlex,hashlib
from pathlib import Path
from deploy_viewer_private_admin_20261008 import request,rid,VM,COMPUTE_API
from qualify_upgrade_private_tls_20261009 import vm_boundary
from run_specialist_gateway_read_verification import retain

ROOT=Path(__file__).resolve().parents[1]/'.work/platform-upgrade-20261009-stage9/private-build-discovery'
RUN=rid('Microsoft.Compute/virtualMachines',VM)+'/runCommands/upgrade-build-discovery-20261009'
PREFIX='PRIVATE_BUILD_DISCOVERY '
CODE="""import json,shutil,subprocess
result={}
for command,args in [('python3',['--version']),('docker',['--version']),('podman',['--version']),('git',['--version']),('az',['version']),('curl',['--version'])]:
    path=shutil.which(command)
    if not path:result[command]={'installed':False};continue
    r=subprocess.run([path,*args],capture_output=True,text=True,timeout=15)
    result[command]={'installed':True,'exit_code':r.returncode,'version':r.stdout[:200].splitlines()[0] if r.stdout else r.stderr[:100]}
print('PRIVATE_BUILD_DISCOVERY '+json.dumps({'software':result,'credentials_read':False,'packages_changed':False}))
"""

def save(name,value):
    with (ROOT/(name+'.json')).open('x',encoding='utf-8') as f:json.dump({**value,'protected_receipt':retain(value,'upgrade-private-build-'+name)},f,indent=2)

def main(stage):
    boundary=vm_boundary()
    if stage=='start':
        if ROOT.exists() or request('GET',RUN+'?api-version='+COMPUTE_API,missing=True) is not None:raise ValueError('Preserve existing probe')
        body={'location':'eastasia','properties':{'source':{'script':'python3 -c '+shlex.quote(CODE)},'asyncExecution':True,'timeoutInSeconds':150,'treatFailureAsDeploymentFailure':True}}
        ROOT.mkdir(parents=True)
        save('intent',{'vm_boundary':boundary,'resource':RUN,'body':body,'source_sha256':hashlib.sha256(CODE.encode()).hexdigest(),'read_only_on_vm':True})
        ack=request('PUT',RUN+'?api-version='+COMPUTE_API,data=body);save('ack',{'id':ack.get('id')})
        print('Submitted bounded read-only private build-tool inventory');return
    if stage!='read':raise ValueError('Unknown stage')
    intent=json.loads((ROOT/'intent.json').read_bytes())
    if boundary!=intent['vm_boundary']:raise ValueError('Private VM boundary changed')
    run=request('GET',RUN+'?api-version='+COMPUTE_API+'&$expand=instanceView');v=run['properties'].get('instanceView',{})
    if v.get('executionState')!='Succeeded':print(json.dumps({'state':v.get('executionState')}));return
    if v.get('exitCode')!=0 or run['properties']['source']!=intent['body']['properties']['source']:raise ValueError('Execution differs')
    lines=[line[len(PREFIX):] for line in v.get('output','').splitlines() if line.startswith(PREFIX)]
    if len(lines)!=1 or len(lines[0])>3000:raise ValueError('Bounded result missing')
    result=json.loads(lines[0]);save('result',result);print(json.dumps(result))

if __name__=='__main__':main(sys.argv[1])
