"""Exact read-only specialist payload trials; never enroll agents or write data."""
import ast,base64,copy,gzip,hashlib,json,sys
from pathlib import Path
from deploy_viewer_private_admin_20261008 import request,rid
from deploy_ms_presentation_worker_release import settings_invariant
from verify_installed_ms_worker_release import corrected_template
from control_upgrade_legacy_recovery_probe_20261009 import protected
from rehearse_ms_worker_image_rollback import restore_protected
from run_specialist_gateway_read_verification import retain
from scan_ms_presentation_worker_candidate import verify
from audit_azure_runtime import az
import deploy_t_runtime_release_20261009 as runtime
runtime.request=request
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.work/platform-upgrade-20261010-stage16/readers-v1'
IMAGE_FOLDER=ROOT/'.work/platform-upgrade-20261010-stage13/metadata-security-v2'
SOURCE='ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:276a4783bf4ddf437bd5d449e08df39729fb43f449975082af80fd4c13e1142c'
API='?api-version=2026-07-01'
JOBS={'ibkrv2stg-agent-audit':('audit','SPECIALIST_AUDIT_WORKER '),
      'ibkrv2stg-authority':('authority','SPECIALIST_AUTHORITY_WORKER '),
      'ibkrv2stg-observe-reader':('observation','SPECIALIST_OBSERVATION_WORKER ')}


def candidate():
    _,_,output=verify(IMAGE_FOLDER)
    native=json.loads((IMAGE_FOLDER/'native-build-qualified.json').read_bytes())
    image=output['registry']+'/'+output['repository']+'@'+output['digest']
    record=json.loads((IMAGE_FOLDER/'scan-completed.json').read_bytes())
    remote,_=restore_protected(json.loads((IMAGE_FOLDER/'scan-protected-receipt.json').read_bytes()))
    if record!=remote or record['image']!=image or record['assessment']['status']!='pass' or record['assessment']['blocking_findings']:raise ValueError('Protected passing scan required')
    a,b=native['proofs']
    if a['source_files']!=530 or a['source_manifest_sha256']!=b['source_manifest_sha256'] or a['tests']!=44 or b['tests']!=44:raise ValueError('Source preservation differs')
    return image


def save(folder,name,value):
    folder.mkdir(parents=True,exist_ok=True)
    path=folder/(name+'.json')
    if path.exists():raise ValueError('Preserve existing local evidence')
    receipt=retain(value,'upgrade-readers-'+folder.name+'-'+name)
    with path.open('x',encoding='utf-8') as f:json.dump({**value,'protected_receipt':receipt},f,indent=2)


def decode(args):
    if len(args)!=2 or args[0]!='-c':raise ValueError('Exact Python wrapper required')
    tree=ast.parse(args[1])
    literals=[n.value for n in ast.walk(tree) if isinstance(n,ast.Call) and ast.unparse(n.func)=='base64.b64decode' for n in n.args if isinstance(n,ast.Constant) and isinstance(n.value,str)]
    if len(literals)!=1:raise ValueError('One exact encoded payload required')
    raw=base64.b64decode(literals[0],validate=True)
    return (gzip.decompress(raw) if raw.startswith(b'\x1f\x8b') else raw).decode()


def reviewed(job,name):
    kind=JOBS[name][0];c=job['properties']['template']['containers'][0]
    module=__import__('deploy_specialist_'+kind+'_worker')
    expected,_=module.bundle(False)
    actual=decode(c['args']);local=decode(expected)
    runner=(ROOT/('scripts/run_specialist_'+kind+'_worker.py')).read_text(encoding='utf-8')
    if kind=='authority':
        original=(ROOT/'.work/platform-upgrade-20261010-stage16/original_authority_worker.py').read_text(encoding='utf-8')
        if not local.endswith(runner):raise ValueError('Local runner boundary differs')
        local=local[:-len(runner)]+original;runner=original
    if c.get('command')!=['python'] or not actual.endswith(runner):raise ValueError('Read-only runner differs')
    class Modules(ast.NodeTransformer):
        def visit_Constant(self,node):
            if isinstance(node.value,str) and len(node.value)>500 and '\n' in node.value:
                ast.parse(node.value)
                return ast.copy_location(ast.Constant('<embedded-module>'),node)
            return node
    if ast.dump(Modules().visit(ast.parse(actual)))!=ast.dump(Modules().visit(ast.parse(local))):raise ValueError('Runner/module wiring differs')
    if kind!='authority' and actual!=local:raise ValueError('Reviewed source differs')
    return hashlib.sha256(actual.encode()).hexdigest()


def accept(job,p):
    if p.get('status')!='passed' or p.get('messages_sent')!=0:raise ValueError('Read-only worker failed')
    if JOBS[job][0]=='audit':
        if p.get('database_access')!='verified' or not p.get('reachable_roles_verified') or p.get('source_mutation_authority') is not False or not p.get('ledger_append_only') or p.get('events_written')!=0 or p.get('source_table_access') is not False or p.get('mode')!='verify':raise ValueError('Audit boundary differs')
    elif JOBS[job][0]=='authority':
        if p.get('database_access')!='verified' or not p.get('authority_source_checked') or not p.get('unregistered_principal_denied') or p.get('tenant_authority_rows')!={'controls':0,'grants':0} or p.get('authority_rows_written')!=0 or p.get('agents_enabled')!=0 or p.get('source_table_access') is not False:raise ValueError('Authority boundary differs')
    else:
        if p.get('database_access')!='verified' or p.get('allowed_relation_checks')!=9 or any(p.get(k)!=0 for k in ('financial_rows_read','source_rows_written','authority_rows_written','agents_enabled')):raise ValueError('Observation boundary differs')


def execution_env(job,actual,wanted):
    # ACA execution readback replaces this single Key Vault reference with its
    # generated job-secret alias. Never accept values or arbitrary new aliases.
    if len(actual)!=1 or len(wanted)!=1:raise ValueError('Exactly one restricted database reference required')
    a,b=actual[0],wanted[0]
    if a.get('name')!=b.get('name') or not b.get('secretRef') or a.get('value') not in (None,'') or b.get('value') not in (None,'') or a.get('secretRef') not in (b['secretRef'],'cappjob-'+job):raise ValueError('Execution credential mapping differs')
    if set(a)-{'name','secretRef','value'} or set(b)-{'name','secretRef','value'}:raise ValueError('Unknown environment field')
    return {'name':a['name'],'saved_reference':b['secretRef'],'execution_reference':a['secretRef'],'resolved_value_returned':False}


def main(stage,name,mode='candidate'):
    if name not in JOBS or mode not in ('candidate','fallback','installed'):raise ValueError('Outside reader scope')
    image=candidate();path=rid('Microsoft.App/jobs',name);job=request('GET',path+API)
    digest=reviewed(job,name);c=job['properties']['template']['containers'][0]
    folder=OUT/(name+'-'+mode)
    if stage=='freeze':
        if c['image']!=SOURCE or job['properties']['configuration']['triggerType']!='Manual':raise ValueError('Original manual job required')
        if any(s.get('value') for s in job['properties']['configuration'].get('secrets',[])):raise ValueError('Inline secret capture forbidden')
        save(OUT/name,'baseline',{'job':job,'invariant':settings_invariant(job),'payload_sha256':digest,'image':SOURCE})
        print(json.dumps({'job':name,'baseline':'protected'}));return
    baseline=protected(OUT/name/'baseline.json')
    if settings_invariant(job)!=baseline['invariant'] or digest!=baseline['payload_sha256'] or c['image']!=(image if mode=='installed' else SOURCE):raise ValueError('Frozen boundary drift')
    target=SOURCE if mode=='fallback' else image
    if stage=='start':
        if folder.exists():raise ValueError('Reconcile existing execution')
        runtime.safe_window();runtime.idle(name)
        template=copy.deepcopy(job['properties']['template'])
        for key in list(template):
            if template[key] is None:del template[key]
        template['containers'][0]['image']=target
        for e in template['containers'][0].get('env',[]):
            if e.get('secretRef') and e.get('value')=='':del e['value']
        save(folder,'intent',{'image':target,'template':template,'invariant':baseline['invariant'],'payload_sha256':digest,
            'scope':'Exact existing restricted read-only preflight; no financial rows, event insert, enrollment, permission changes or messages'})
        ack=request('POST',path+'/start'+API,data=template);save(folder,'dispatch',{'execution':ack['name']})
        print(json.dumps({'job':name,'mode':mode,'execution':ack['name']}));return
    if stage!='read':raise ValueError('Unknown stage')
    intent=protected(folder/'intent.json');execution=protected(folder/'dispatch.json')['execution']
    run=request('GET',path+'/executions/'+execution+API);actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Actual execution differs')
    mapping=execution_env(name,actual.get('env',[]),wanted.get('env',[]))
    state=run['properties']['status'];print(json.dumps({'job':name,'mode':mode,'status':state}))
    if state not in ('Succeeded','Failed'):return
    prefix=JOBS[name][1]
    query="ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+execution+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+'))'
    rows=az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',query)
    if len(rows)!=1:print('Waiting for bounded proof ingestion');return
    proof=json.loads(rows[0]['Result'])
    if state!='Succeeded':
        if not (folder/'failure.json').exists():save(folder,'failure',{'passed':False,'execution':execution,'proof':proof})
        raise ValueError('Read-only preflight failed; do not deploy')
    accept(name,proof)
    if not (folder/'result.json').exists():save(folder,'result',{'passed':True,'job':name,'image':target,'execution':execution,'proof':proof,'settings_preserved':True,'payload_sha256':digest,'execution_credential_mapping':mapping})
    print(json.dumps({'job':name,'mode':mode,'passed':True}))


if __name__=='__main__':main(*sys.argv[1:])
