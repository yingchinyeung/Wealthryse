"""One reserved read-only observation; preserves all 831 predecessor sources."""
from __future__ import annotations
import argparse
import ast
import base64
import hashlib
import json
import lzma
from pathlib import Path
import re
import qualify_owner_task_catalog_diagnostic_source as prior
import deploy_owner_task_execution_fence_trial as generic
import owner_task_isolated_sql_root_launch as launcher
import run_owner_task_catalog_diagnostic as worker

ROOT=prior.candidate.ROOT
ADDITIONS=frozenset((worker.PATH,'scripts/deploy_owner_task_catalog_diagnostic.py','tests/test_owner_task_catalog_diagnostic.py'))
SOURCE_SHA='6346a7ed2de59629b84f475a6cccdaaa2d75bbf95755cd777a677966f5946918'
READBACK_SHA='416d57d4a2ff56b267a5f0cdc8172843966ae6363d498d77c6b63f0606e4773d'
MODULES=tuple(pair for pair in launcher.SOURCE_MODULES if pair[1] in (
    'ibkr_etl.owner_codex_isolated_target','ibkr_etl.owner_codex_catalog_transport',
    'inspect_owner_task_isolated_provider','owner_task_provider_runtime','run_owner_task_isolated_provider_observation'))+(
    ('src/ibkr_etl/owner_codex_catalog_snapshot.py','ibkr_etl.owner_codex_catalog_snapshot'),
    (worker.PATH,'run_owner_task_catalog_diagnostic'))
PATHS=tuple(k for k,_ in MODULES)
NAME_PREFIX='serviceaud-step3-catalog-live-'


def require(value,code='fence_catalog_control_unverified'):
    worker.require(value,code)


def source_extension(before,after):
    require(type(before) is dict and len(before)==831 and type(after) is dict
        and set(after)==set(before)|ADDITIONS and not set(before)&ADDITIONS
        and all(after[k]==v for k,v in before.items())
        and all(type(v) is str and re.fullmatch('[0-9a-f]{64}',v) for v in after.values()))


def old_context():
    n=prior.candidate.controller_namespace()
    source=prior.candidate.controller_source((ROOT/prior.candidate.BASE_CONTROL).read_bytes())
    node=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='guards')
    body=ast.get_source_segment(source,node)
    old="    require(p.old.prior.host.sources() == pf['sources'], 'fence_source_drift')\n    sources_valid(pf['predecessor_sources'], pf['sources'])"
    require(body.count(old)==1)
    body=body.replace(old,"    catalog_source_chain(pf['sources'],p.old.prior.host.sources())")
    exclusion="failed_sql_control['worker'].JOB, worker.JOB):"
    require(body.count(exclusion)==1)
    body=body.replace(exclusion,"failed_sql_control['worker'].JOB, worker.JOB, catalog_job):")
    def chain(before,after):
        raw,checked=n['p'].old.load(prior.NAME)
        reread,proof=n['p'].old.load('serviceaud-step3-catalog-diagnostic-source-readback.json')
        require(raw['protected_receipt']['sha256']==SOURCE_SHA and reread['protected_receipt']['sha256']==READBACK_SHA
            and proof['source_checkpoint_receipt']==raw['protected_receipt'] and checked['sources']==proof['sources']
            and checked['source_only'] is True and proof['step_3_complete'] is False and proof['step_4_started'] is False)
        prior.source_extension(before,checked['sources']);source_extension(checked['sources'],after)
    detached=dict(n,catalog_source_chain=chain,catalog_job=worker.JOB)
    exec(compile(body,__file__,'exec'),detached)
    return n,detached['guards']


def predecessor(n,guard):
    pr,pf=n['load']('live-preflight');ar,ob=n['load']('live-observation');rr,rb=n['load']('failure-readback')
    require(ar['protected_receipt']['sha256']==prior.FAILURE_SHA and rr['protected_receipt']['sha256']==prior.READBACK_SHA
        and rb['failure_receipt']==ar['protected_receipt'] and rb['live_sql_accepted'] is False)
    identity=guard(pf);client=n['Transport']()
    job=client.call('GET',n['JOB']);n['checked_job'](job,pf['plan'],identity)
    execution=client.call('GET',ob['native_execution']['id']);inventory=client.call('GET',n['JOB']+'/executions')
    require(execution==ob['native_execution'] and execution['properties']['status']=='Failed'
        and inventory.get('nextLink') in (None,'') and len(inventory['value'])==1
        and inventory['value'][0]['id']==execution['id'])
    return identity,dict(job=job,execution=execution,inventory=inventory)


def build(sources,hashes,runtime,mi):
    require(set(sources)==set(hashes)==set(PATHS) and sum(len(v) for v in sources.values())<=256*1024
        and all(type(v) is bytes and hashlib.sha256(v).hexdigest()==hashes[k] for k,v in sources.items()))
    generic.image.runtime.validate_descriptor(runtime)
    decoded={k:v.decode() for k,v in sources.items()}
    for k,v in decoded.items():compile(v,k,'exec')
    old='sys.modules["run_owner_task_isolated_sql_root"].main(_secret,_sources[_migration],connect=psycopg.connect)'
    require(launcher.LAUNCHER.count(old)==1)
    launch=launcher.LAUNCHER.replace(old,'sys.modules["run_owner_task_catalog_diagnostic"].main(_secret,connect=psycopg.connect)')
    launch=launch.replace('OWNER_TASK_ISOLATED_SQL_ROOT_LAUNCH_FAILURE_V1 ','OWNER_TASK_CATALOG_DIAGNOSTIC_LAUNCH_FAILURE_V1 ')
    script='_sources='+repr(decoded)+'\n_hashes='+repr(hashes)+'\n_modules='+repr(MODULES)+'\n_runtime='+repr(runtime)+'\n'+launch
    packed=base64.b85encode(lzma.compress(script.encode(),preset=9)).decode().replace(chr(36),':')
    args=['-I','-B','-c',"import base64,lzma;exec(compile(lzma.decompress(base64.b85decode('"+packed+"'.replace(':',chr(36)))),'owner-catalog-diagnostic','exec'))"]
    require(len(worker.canonical(args))<=65536 and chr(36) not in worker.canonical(args).decode())
    definition=dict(location=worker.host.LOCATION,identity=dict(type='UserAssigned',userAssignedIdentities={mi:{}}),
        tags=dict(purpose='owner-readonly-catalog-diagnostic',operation=worker.OPERATION,sourceSha256=generic.digest(hashes),fullSourceSha256=hashlib.sha256(script.encode()).hexdigest()),
        properties=dict(environmentId=worker.host.ENVIRONMENT,configuration=dict(triggerType='Manual',replicaTimeout=600,replicaRetryLimit=0,
            identitySettings=[dict(identity=mi,lifecycle='None')],manualTriggerConfig=dict(parallelism=1,replicaCompletionCount=1),
            secrets=[dict(name=worker.host.SECRET_NAME,keyVaultUrl=generic.image.SECRET_VERSION_URL,identity=mi)],
            registries=[dict(server=generic.image.REGISTRY_SERVER,identity=mi)]),
            template=dict(containers=[dict(name='execution-fence-trial',image=generic.image.IMAGE,command=['python'],args=args,
                env=[dict(name='OWNERTRIAL_ADMIN_URL',secretRef=worker.host.SECRET_NAME)],resources=dict(cpu=.5,memory='1Gi'))])))
    return dict(definition=definition,definition_sha256=generic.digest(definition),source_hashes=hashes,expected_runtime=runtime,full_script_sha256=hashlib.sha256(script.encode()).hexdigest())


def control():
    old,old_guard=old_context();p=old['p']
    n=dict(vars(generic),__name__='catalog_diagnostic_control',worker=worker,p=p,ROOT=ROOT,JOB=worker.JOB_ID,
        MI=old['MI'],PREFIX='probes/owner-catalog-live-20261005/')
    source=Path(generic.__file__).read_text()
    functions=[node for node in ast.parse(source).body if isinstance(node,(ast.FunctionDef,ast.ClassDef))
        and node.name in ('Transport','checked_job','checked_template','reserve','dispatch','accept_job','observe','collect')]
    for node in functions:
        body=ast.get_source_segment(source,node)
        if node.name=='collect':
            start=body.index("    result = dict(status='step_3_synthetic_postgres_fence_accepted'")
            body=body[:start]+'''    frames=[json.loads(row[3][len(worker.WIRE_PREFIX):]) for row in table['rows'] if row[3].startswith(worker.WIRE_PREFIX)]
    if record['status']=='unaccepted':
        return save(dict(status='step_3_readonly_catalog_failure_retained',checked_at_utc=p.old.now(),
            preflight_receipt=rr['protected_receipt'],start_ack_receipt=ar['protected_receipt'],
            native_execution=value,full_bounded_observed_family=family,query=query,worker_result=record,
            live_audit_sql_accepted=False,step_3_complete=False,step_4_started=False),'live-observation')
    diagnostic,binding=worker.reconstruct(frames)
    require(record['record_sha256']==binding['record_sha256'] and record['frame_count']==binding['frame_count']
        and diagnostic['operation_id']==worker.OPERATION and diagnostic['job_resource_id']==JOB
        and diagnostic['sql_mutations']==0 and diagnostic['source_rows_read']==diagnostic['source_rows_written']==0
        and diagnostic['historical_drift_cause_proven'] is False, 'fence_catalog_record_invalid')
    snapshots=[worker.snapshot.CatalogSnapshot.from_rows(diagnostic['catalog'])]
    require(diagnostic['fresh_connections']==3 and len(diagnostic['snapshot_sha256'])==3
        and diagnostic['snapshot_sha256'][0]==snapshots[0].sha256
        and record['catalog_stable'] is True and len(set(diagnostic['snapshot_sha256']))==1
        and diagnostic['observed_differences']==[{},{}]
        and record['status']=='catalog_diagnostic_observed' and value['properties']['status']=='Succeeded', 'fence_catalog_observation_unaccepted')
    return save(dict(status='step_3_readonly_catalog_observed_not_historical_drift_acceptance',
        checked_at_utc=p.old.now(),preflight_receipt=rr['protected_receipt'],start_ack_receipt=ar['protected_receipt'],
        native_execution=value,full_bounded_observed_family=family,query=query,worker_result=record,
        catalog_diagnostic=diagnostic,catalog_snapshot_sha256=snapshots[0].sha256,
        historical_drift_cause_proven=False,predecessor_sql_catalog_unchanged_verified=False,
        live_audit_sql_accepted=False,step_3_complete=False,step_4_started=False,
        owner_runtime_active=False,six_specialist_runtimes_active=False),'live-observation')
'''
        exec(compile(body,__file__,'exec'),n)
    def save(value,name):return p.old.save(value,NAME_PREFIX+name+'.json')
    def load(name):return p.old.load(NAME_PREFIX+name+'.json')
    def guards(pf):
        require(p.old.prior.host.sources()==pf['sources'] and pf['operation_id']==worker.OPERATION)
        _,checked=p.old.load(prior.NAME);source_extension(checked['sources'],pf['sources'])
        identity,native=predecessor(old,old_guard)
        require(native==pf['native_predecessor'] and identity==pf['sql_identity'])
        return identity
    n.update(save=save,load=load,guards=guards)
    def prepare(client):
        require(not (p.old.FOLDER/(NAME_PREFIX+'live-preflight.json')).exists())
        _,checked=p.old.load(prior.NAME);sources=p.old.prior.host.sources();source_extension(checked['sources'],sources)
        identity,before=predecessor(old,old_guard)
        require(client.call('GET',worker.JOB_ID,missing=True) is None)
        _,oldpf=old['load']('live-preflight')
        packed={k:(ROOT/k).read_bytes() for k in PATHS}
        plan=build(packed,{k:hashlib.sha256(v).hexdigest() for k,v in packed.items()},oldpf['plan']['expected_runtime'],old['MI'])
        checks=p.old.prior.parent.reviewed.run_local_checks()
        identity2,after=predecessor(old,old_guard)
        require(before==after and identity==identity2 and p.old.prior.host.sources()==sources
            and client.call('GET',worker.JOB_ID,missing=True) is None)
        return save(dict(status='step_3_readonly_catalog_live_prepared',checked_at_utc=p.old.now(),operation_id=worker.OPERATION,
            sources=sources,predecessor_sources=checked['sources'],native_predecessor=before,sql_identity=identity,
            plan=plan,local_checks=checks,production_changed=False,source_rows_read=0,source_rows_written=0,
            sql_mutations=0,step_3_complete=False,step_4_started=False),'live-preflight')
    n['prepare']=prepare
    return n


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=('prepare','job','accept-job','start','observe','collect'))
    command=parser.parse_args().command
    try:
        n=control();client=n['Transport']()
        r=n['prepare'](client) if command=='prepare' else n['dispatch'](client,command) if command in ('job','start') else n['accept_job'](client) if command=='accept-job' else n['observe'](client) if command=='observe' else n['collect'](client)
        print(json.dumps({k:r[k] for k in ('status','protected_receipt','execution','native_status') if k in r}),flush=True)
    except Exception as error:
        print(json.dumps(dict(status='step_3_catalog_diagnostic_unaccepted_inspect_only',code=type(error).__name__,automatic_retry=False)),flush=True)
        raise SystemExit(1) from None
