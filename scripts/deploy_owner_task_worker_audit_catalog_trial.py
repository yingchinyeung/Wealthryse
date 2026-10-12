"""New prospective audit trial; fixed private scope, consumed failures retained."""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import types

import owner_task_worker_audit_catalog_candidate as candidate
import owner_task_catalog_execution_metadata as metadata

ROOT=candidate.ROOT
NAME_PREFIX='serviceaud-step3-audit-sql-v3-'
READBACK_NAME='serviceaud-step3-catalog-live-fresh-readback.json'
READBACK_SHA='e4d244187275b9e577d07b2ccc7011211f5d5c76195c747fc7a0382ba04b741f'
REVIEW_SHA='0801abb52e067e8c69451d18a55bc1f7c7601d5b63c7d2b34cafbb97711f1bbf'
OBSERVATION_SHA='f66d0b7fdcfbcfda0b4dd39a4da60a477ac82740abfdcc0b891751573afd5285'


def require(value,code='fence_audit_catalog_control_unverified'):
    if not value:raise ValueError(code)


def extension(before,after):
    require(type(before) is dict and len(before)==838 and type(after) is dict
        and set(after)==set(before)|candidate.ADDITIONS and not set(before)&candidate.ADDITIONS
        and all(after[k]==v for k,v in before.items())
        and all(type(v) is str and re.fullmatch('[0-9a-f]{64}',v) for v in after.values()))


def chain(before,after):
    predecessor={k:v for k,v in after.items() if k not in candidate.ADDITIONS}
    metadata.chain(before,predecessor);extension(predecessor,after)


def predecessor_context():
    base=metadata.prior.base
    source=Path(base.__file__).read_text()
    body=ast.get_source_segment(source,next(n for n in ast.parse(source).body
        if isinstance(n,ast.FunctionDef) and n.name=='old_context'))
    old='failed_sql_control[\'worker\'].JOB, worker.JOB, catalog_job):'
    require(body.count(old)==1)
    body=body.replace(old,'failed_sql_control[\'worker\'].JOB, worker.JOB, catalog_job, future_job):')
    old='detached=dict(n,catalog_source_chain=chain,catalog_job=worker.JOB)'
    require(body.count(old)==1)
    body=body.replace(old,'detached=dict(n,catalog_source_chain=chain,catalog_job=worker.JOB,future_job=future_job)')
    namespace=dict(vars(base),source_extension=chain,future_job=candidate.JOB)
    exec(compile(body,__file__,'exec'),namespace)
    return namespace['old_context']()


def baseline(p):
    rr,readback=p.old.load(READBACK_NAME);mr,review=p.old.load(metadata.REVIEW_NAME)
    oraw,ob=p.old.load(metadata.prior.base.NAME_PREFIX+'live-observation.json')
    require(rr['protected_receipt']['sha256']==READBACK_SHA and mr['protected_receipt']['sha256']==REVIEW_SHA
        and oraw['protected_receipt']['sha256']==OBSERVATION_SHA
        and readback['metadata_review_receipt']==mr['protected_receipt']
        and readback['observation_receipt']==oraw['protected_receipt']
        and readback['sources']==review['sources'] and readback['stable_observations_verified'] is True
        and readback['catalog_snapshot_sha256']==candidate.BASELINE_SHA
        and readback['historical_drift_cause_proven'] is False and readback['live_audit_sql_accepted'] is False
        and readback['step_3_complete'] is False and readback['step_4_started'] is False)
    snapshot=candidate.catalog.CatalogSnapshot.from_rows(ob['catalog_diagnostic']['catalog'])
    require(snapshot.sha256==candidate.BASELINE_SHA)
    extension(readback['sources'],p.old.prior.host.sources())
    return rr,readback


def native_predecessors(p):
    rr,rb=baseline(p);old,guard=predecessor_context()
    identity,failed=metadata.prior.base.predecessor(old,guard)
    m=metadata.prior.control();_,pf=m['load']('live-preflight')
    _,inspection=p.old.load(metadata.INSPECTION_NAME)
    native=metadata.native(m['Transport'](),m,pf,identity,inspection)
    require(native==rb['native'])
    return identity,dict(failed_audit_v2=failed,catalog_diagnostic=native)


def control():
    # Every call constructs a fresh owned namespace. No frozen globals change.
    n=candidate.previous.controller_namespace();worker=candidate.worker_module();p=n['p']
    n.update(candidate=candidate,worker=worker,ROOT=ROOT,JOB=worker.JOB_ID,
        PREFIX='probes/owner-codex-audit-sql-v3-20261005/')
    modules=[(candidate.PATH if name=='run_owner_task_execution_fence_trial' else path,name)
        for path,name in n['MODULES']]
    modules.insert(len(modules)-1,('src/ibkr_etl/owner_codex_catalog_snapshot.py','ibkr_etl.owner_codex_catalog_snapshot'))
    n['MODULES']=tuple(modules);n['PATHS']=tuple(path for path,_ in modules)+(worker.DDL_PATH,worker.CONSTRAINT_PATH)
    def save(value,name):return p.old.save(value,NAME_PREFIX+name+'.json')
    def load(name):return p.old.load(NAME_PREFIX+name+'.json')
    def guards(pf):
        rr,rb=baseline(p)
        require(pf['operation_id']==worker.OPERATION and pf['sources']==p.old.prior.host.sources()
            and pf['predecessor_sources']==rb['sources'] and pf['baseline_receipt']==rr['protected_receipt'])
        identity,native=native_predecessors(p)
        require(identity==pf['sql_identity'] and native==pf['native_predecessors'])
        return identity
    n.update(save=save,load=load,guards=guards)
    # The generic marker and reservation logic is copied, not bypassed.
    source=Path(metadata.prior.base.generic.__file__).read_text()
    node=next(x for x in ast.parse(source).body if isinstance(x,ast.FunctionDef) and x.name=='dispatch')
    body=ast.get_source_segment(source,node)
    require(body.count("'serviceaud-step3-fence-'")==1)
    body=body.replace("'serviceaud-step3-fence-'",repr(NAME_PREFIX))
    exec(compile(body,__file__,'exec'),n)
    # Normalize only the observed execution metadata, for this new fixed job.
    text=Path(metadata.__file__).read_text()
    node=next(x for x in ast.parse(text).body if isinstance(x,ast.FunctionDef) and x.name=='execution_template')
    body=ast.get_source_segment(text,node).replace('prior.base.worker.','worker.')
    exec(compile(body,__file__,'exec'),n)
    original_template=n['checked_template'];collector_globals=dict(n)
    collector_globals['checked_template']=lambda v,e:n['execution_template'](v,e,original_template)
    original_collect=n['collect']
    def retain(value,name):
        if name=='live-observation':
            table=value['full_bounded_observed_family']['tables'][0]
            wire=[json.loads(row[3][len(worker.WIRE_PREFIX):]) for row in table['rows'] if row[3].startswith(worker.WIRE_PREFIX)]
            record,binding=worker.reconstruct(wire)
            summary=value['worker_result']
            require(record['operation_id']==worker.OPERATION and record['job_resource_id']==worker.JOB_ID
                and summary['catalog_record_sha256']==binding['record_sha256']
                and summary['catalog_frame_count']==binding['frame_count']
                and record['historical_drift_cause_proven'] is False)
            value['catalog_observations']=record;value['historical_drift_cause_proven']=False
            if value['status']=='step_3_synthetic_postgres_audit_sql_accepted':
                require(len(record['catalog_snapshots'])==5)
                for observation in record['catalog_snapshots']:
                    full=candidate.catalog.CatalogSnapshot.from_rows(observation['full_catalog'])
                    scoped=worker.project_predecessors(full)
                    require(full.sha256==observation['full_sha256'] and scoped.sha256==candidate.BASELINE_SHA
                        and observation['predecessor_catalog']==scoped.rows()
                        and observation['predecessor_sha256']==scoped.sha256)
            value['protected_worker_receipts_verified']=False
        return save(value,name)
    collector_globals['save']=retain
    n['collect']=types.FunctionType(original_collect.__code__,collector_globals,original_collect.__name__,
        original_collect.__defaults__,original_collect.__closure__)
    return n


def prepare():
    n=control();p=n['p'];client=n['Transport']()
    require(not (p.old.FOLDER/(NAME_PREFIX+'live-preflight.json')).exists())
    br,rb=baseline(p);sources=p.old.prior.host.sources();identity,before=native_predecessors(p)
    require(client.call('GET',n['JOB'],missing=True) is None)
    _,oldpf=candidate.previous.controller_namespace()['load']('live-preflight')
    source={path:(ROOT/path).read_bytes() for path in n['PATHS']}
    source[n['worker'].PATH]=candidate.worker_source((ROOT/candidate.BASE_WORKER).read_bytes()).encode()
    plan=n['build'](source,{path:hashlib.sha256(data).hexdigest() for path,data in source.items()},oldpf['plan']['expected_runtime'])
    checks=p.old.prior.parent.reviewed.run_local_checks()
    identity2,after=native_predecessors(p)
    require(identity==identity2 and before==after and p.old.prior.host.sources()==sources
        and client.call('GET',n['JOB'],missing=True) is None)
    return n['save'](dict(status='step_3_prospective_audit_catalog_trial_prepared',checked_at_utc=p.old.now(),
        operation_id=n['worker'].OPERATION,sources=sources,predecessor_sources=rb['sources'],
        baseline_receipt=br['protected_receipt'],native_predecessors=before,sql_identity=identity,
        plan=plan,local_checks=checks,all_838_predecessor_sources_preserved=True,
        historical_drift_cause_proven=False,production_changed=False,source_rows_read=0,source_rows_written=0,
        step_3_complete=False,step_4_started=False),'live-preflight')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=('prepare','job','accept-job','start','observe','collect'))
    command=parser.parse_args().command
    try:
        if command=='prepare':r=prepare()
        else:
            n=control();client=n['Transport']()
            r=n['dispatch'](client,command) if command in ('job','start') else n['accept_job'](client) if command=='accept-job' else n['observe'](client) if command=='observe' else n['collect'](client)
        print(json.dumps({k:r[k] for k in ('status','protected_receipt','execution','native_status') if k in r}),flush=True)
    except Exception as error:
        print(json.dumps(dict(status='step_3_prospective_audit_catalog_unaccepted',code=type(error).__name__,automatic_retry=False)),flush=True)
        raise SystemExit(1) from None
