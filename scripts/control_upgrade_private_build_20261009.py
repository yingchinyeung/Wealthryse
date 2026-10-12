"""Bounded private build/push/cold-pull qualification, isolated VM namespace."""
import json,sys,shlex,hashlib
from pathlib import Path
from deploy_viewer_private_admin_20261008 import request,rid,VM,COMPUTE_API
from qualify_upgrade_private_tls_20261009 import vm_boundary
from run_specialist_gateway_read_verification import retain
from rehearse_ms_worker_image_rollback import restore_protected
from authorize_upgrade_private_build_20261009 import load as role_evidence,boundary
from authorize_upgrade_private_build_20261009 import configure_legacy

ROOT=Path(__file__).resolve().parents[1]/'.work/platform-upgrade-20261009-stage9/private-build-proof-v1'
SOURCE=Path(__file__).with_name('probe_upgrade_private_build_remote_20261009.py')
RUN=rid('Microsoft.Compute/virtualMachines',VM)+'/runCommands/upgrade-private-build-proof-20261009-v1'
PREFIX='PRIVATE_BUILD_QUALIFICATION '
LEGACY=False

def save(name,value):
    with (ROOT/(name+'.json')).open('x',encoding='utf-8') as f:json.dump({**value,'protected_receipt':retain(value,'upgrade-private-build-proof-'+name)},f,indent=2)

def main(stage):
    current=vm_boundary()
    if stage=='start':
        approved=role_evidence('plan');verified=role_evidence('verified')
        if not verified['passed'] or boundary()!=approved['boundary']:raise ValueError('Private build role not qualified')
        if ROOT.exists() or request('GET',RUN+'?api-version='+COMPUTE_API,missing=True) is not None:raise ValueError('Reconcile existing qualification')
        code=SOURCE.read_text(encoding='utf-8')
        if LEGACY:
            from allow_upgrade_legacy_admin_paths_20261009 import load as network_evidence
            if not network_evidence('verified')['passed']:raise ValueError('Exact legacy private paths not qualified')
            marker="if __name__=='__main__':main()"
            if code.count(marker)!=1:raise ValueError('Remote entrypoint differs')
            overrides="""REG='ibkrv115rcc62xxqblay.azurecr.io'
DATA='ibkrv115rcc62xxqblay.eastasia.data.azurecr.io'
TARGET=REG+'/private-path-qualification:upgrade-20261009-v1'
HOSTS={'ibkrv2stgauhecwc4itaoo.azurecr.io':'10.50.10.5','ibkrv2stgauhecwc4itaoo.eastasia.data.azurecr.io':'10.50.10.4',REG:'10.50.10.11',DATA:'10.50.10.10','ibkrv115rcc62xxqblay.blob.core.windows.net':'10.50.10.9'}
AUTH_REGISTRIES=('ibkrv2stgauhecwc4itaoo.azurecr.io',REG)
"""
            code=code.replace(marker,overrides+marker)
        command='unshare --mount --propagation private python3 -c '+shlex.quote(code)
        body={'location':'eastasia','properties':{'source':{'script':command},'asyncExecution':True,'timeoutInSeconds':3000,'treatFailureAsDeploymentFailure':True}}
        ROOT.mkdir(parents=True)
        save('intent',{'vm_boundary':current,'body':body,'source_sha256':hashlib.sha256(code.encode()).hexdigest(),'scope':'Disposable mount namespace and isolated Docker daemons; exact scanned base image; one qualification-only tag; no live application or database access','host_dns_changed':False,'production_network_changed':False})
        ack=request('PUT',RUN+'?api-version='+COMPUTE_API,data=body);save('ack',{'id':ack.get('id')})
        print('Submitted private build/push/empty-cache pull qualification; no production network cutover');return
    if stage!='read':raise ValueError('Unknown stage')
    intent=json.loads((ROOT/'intent.json').read_bytes());remote,_=restore_protected(intent['protected_receipt'])
    if remote!={k:v for k,v in intent.items() if k!='protected_receipt'} or current!=intent['vm_boundary']:raise ValueError('Frozen qualification boundary differs')
    run=request('GET',RUN+'?api-version='+COMPUTE_API+'&$expand=instanceView');v=run['properties'].get('instanceView',{})
    if run['properties']['source']!=intent['body']['properties']['source']:raise ValueError('Execution source differs')
    print(json.dumps({'state':v.get('executionState'),'exit_code':v.get('exitCode')}))
    if v.get('executionState') not in ('Succeeded','Failed','TimedOut'):return
    if v.get('executionState')!='Succeeded' or v.get('exitCode')!=0:
        save('failure',{'state':v.get('executionState'),'exit_code':v.get('exitCode'),'error':v.get('error','')[-2500:],'output':v.get('output','')[-2500:]})
        print(v.get('error','')[-1500:]);return
    lines=[line[len(PREFIX):] for line in v.get('output','').splitlines() if line.startswith(PREFIX)]
    if len(lines)!=1:raise ValueError('Qualification summary absent')
    result=json.loads(lines[0])
    if not result.get('passed') or not result.get('empty_cache_pull_passed') or result.get('live_database_writes')!=0 or result.get('application_started') is not False:raise ValueError('Acceptance incomplete')
    save('result',result);print(json.dumps(result))

if __name__=='__main__':
    if sys.argv[2:]==['legacy']:
        LEGACY=True;configure_legacy()
        ROOT=Path(__file__).resolve().parents[1]/'.work/platform-upgrade-20261009-stage10/legacy-private-build-proof-v1'
        RUN=rid('Microsoft.Compute/virtualMachines',VM)+'/runCommands/upgrade-legacy-private-build-proof-20261009-v1'
    elif sys.argv[2:]:raise ValueError('Unknown build scope')
    main(sys.argv[1])
