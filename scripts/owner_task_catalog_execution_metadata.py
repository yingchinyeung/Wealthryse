"""Read-only correction for this exact job's observed execution metadata.

All 836 predecessor sources and the executed worker stay unchanged. Only the
execution-template validator normalizes the empty initializer list and the
exact job-scoped secret alias. The job definition, pinned Key Vault version,
single secret/environment, nonempty initializers and volumes remain unchanged.
This cannot create/start/retry jobs or accept the historical catalog failure.
"""
from __future__ import annotations

import argparse
import ast
import copy
import json
from pathlib import Path
import re
import types

import owner_task_catalog_dispatch_scope as prior

PATH='scripts/owner_task_catalog_execution_metadata.py'
TEST_PATH='tests/test_owner_task_catalog_execution_metadata.py'
ADDITIONS=frozenset((PATH,TEST_PATH))
REVIEW_NAME='serviceaud-step3-catalog-live-execution-template-scope-review.json'
INSPECTION_NAME='serviceaud-step3-catalog-live-native-template-inspection.json'
INSPECTION_SHA='f7d7a3b621f94198b0c7a172175fd06f3b7cfd57da307a00ea03e346ead073ff'
SCOPE_SHA='046971852d8f8e2c7e435fc4a2229a4008e7be9e643b6e48fceb1e49d2dbf161'
require=prior.require


def extension(before,after):
    require(type(before) is dict and len(before)==836 and type(after) is dict
        and set(after)==set(before)|ADDITIONS and not set(before)&ADDITIONS
        and all(after[k]==v for k,v in before.items())
        and all(type(v) is str and re.fullmatch('[0-9a-f]{64}',v) for v in after.values()))


def chain(before,after):
    require(len(after)==838)
    predecessor={k:v for k,v in after.items() if k not in ADDITIONS}
    prior.source_chain(before,predecessor);extension(predecessor,after)


def predecessor_context():
    # A new owned namespace, never a mutation of a frozen module's globals.
    source=Path(prior.__file__).read_text()
    body=ast.get_source_segment(source,next(n for n in ast.parse(source).body
        if isinstance(n,ast.FunctionDef) and n.name=='predecessor_context'))
    namespace=dict(vars(prior),source_chain=chain)
    exec(compile(body,__file__,'exec'),namespace)
    return namespace['predecessor_context']()


def execution_template(value,expected,original):
    normalized=copy.deepcopy(value)
    if 'initContainers' in normalized:
        require(normalized['initContainers'] in (None,[]),'fence_execution_initializers_unexpected')
        if normalized['initContainers']==[]:
            del normalized['initContainers']
    containers=normalized.get('containers')
    if type(containers) is list and len(containers)==1:
        env=containers[0].get('env')
        alias='cappjob-'+prior.base.worker.JOB
        if type(env) is list and len(env)==1 and env[0].get('secretRef')==alias:
            expected_env=expected.get('containers',[{}])[0].get('env')
            registered=dict(name='OWNERTRIAL_ADMIN_URL',secretRef=prior.base.worker.host.SECRET_NAME)
            require(expected_env==[registered] and env==[dict(name=registered['name'],secretRef=alias)],
                'fence_execution_secret_alias_unexpected')
            env[0]['secretRef']=registered['secretRef']
    return original(normalized,expected)


def inspected(n,pf):
    raw,inspection=n['p'].old.load(INSPECTION_NAME)
    sr,scope=n['p'].old.load(prior.REVIEW_NAME)
    require(raw['protected_receipt']['sha256']==INSPECTION_SHA
        and sr['protected_receipt']['sha256']==SCOPE_SHA
        and inspection['sources']==scope['sources']
        and inspection['step_3_complete'] is False and inspection['step_4_started'] is False
        and inspection['native_execution']['properties']['status']=='Succeeded'
        and inspection['native_execution']['properties']['template'].get('initContainers')==[]
        and inspection['native_execution']['properties']['template'].get('volumes') is None)
    pr,registered=n['load']('live-preflight');ar,ack=n['load']('start-ack')
    require(pr['protected_receipt']['sha256']==prior.PREFLIGHT_SHA and registered==pf
        and inspection['preflight_receipt']==pr['protected_receipt']
        and inspection['start_ack_receipt']==ar['protected_receipt']
        and inspection['native_execution']['id']==ack['raw_ack']['id'])
    extension(inspection['sources'],n['p'].old.prior.host.sources())
    return raw,inspection


def guarded(n,pf):
    inspected(n,pf)
    old,guard=predecessor_context()
    identity,native=prior.base.predecessor(old,guard)
    require(identity==pf['sql_identity'] and native==pf['native_predecessor'])
    return identity


def native(client,n,pf,identity,inspection):
    job=client.call('GET',n['JOB'])
    n['checked_job'](job,pf['plan'],identity)
    execution=client.call('GET',inspection['native_execution']['id'])
    inventory=client.call('GET',n['JOB']+'/executions')
    require(execution==inspection['native_execution']
        and inventory.get('nextLink') in (None,'') and len(inventory['value'])==1
        and inventory['value'][0]['id']==execution['id'])
    execution_template(execution['properties']['template'],
        pf['plan']['definition']['properties']['template'],n['checked_template'])
    return dict(job=job,execution=execution,inventory=inventory)


def control():
    n=prior.control()
    original_template=n['checked_template']
    n['checked_execution_template']=lambda v,e:execution_template(v,e,original_template)
    # The frozen diagnostic factory supplies the fully bounded collector.
    # Copy its exact code into an owned namespace, without retargeting globals
    # belonging to the predecessor factory or its job validator.
    diagnostic=prior.base.control()['collect']
    # Its bytecode refers to checked_template. Bind that name only in the private
    # collector namespace; the other functions keep their original globals.
    collector_namespace=dict(n,checked_template=n['checked_execution_template'])
    collector=types.FunctionType(diagnostic.__code__,collector_namespace,diagnostic.__name__,diagnostic.__defaults__,diagnostic.__closure__)
    def guards(pf):
        rr,review=n['p'].old.load(REVIEW_NAME)
        require(review['status']=='step_3_catalog_execution_metadata_reviewed'
            and review['sources']==n['p'].old.prior.host.sources()
            and review['only_observed_execution_defaults_normalized'] is True
            and review['worker_plan_unchanged'] is True
            and review['step_3_complete'] is False and review['step_4_started'] is False)
        return guarded(n,pf)
    n['guards']=guards;collector_namespace['guards']=guards
    def collect(client):
        _,pf=n['load']('live-preflight');_,inspection=inspected(n,pf)
        native(client,n,pf,pf['sql_identity'],inspection)
        return collector(client)
    n['collect']=collect
    return n


def review():
    n=prior.control();p=n['p']
    require(not (p.old.FOLDER/REVIEW_NAME).exists())
    _,pf=n['load']('live-preflight');ir,inspection=inspected(n,pf)
    client=n['Transport']()
    sources=p.old.prior.host.sources();identity=guarded(n,pf)
    before=native(client,n,pf,identity,inspection)
    checks=p.old.prior.parent.reviewed.run_local_checks()
    require(p.old.prior.host.sources()==sources and guarded(n,pf)==identity
        and native(client,n,pf,identity,inspection)==before)
    return p.old.save(dict(status='step_3_catalog_execution_metadata_reviewed',
        checked_at_utc=p.old.now(),inspection_receipt=ir['protected_receipt'],sources=sources,
        local_checks=checks,native_before=before,native_after=before,
        all_836_predecessor_sources_preserved=True,
        only_observed_execution_defaults_normalized=True,
        observed_initializer_count=0,observed_secret_alias='cappjob-'+prior.base.worker.JOB,
        exact_job_definition_and_secret_version_verified=True,worker_plan_unchanged=True,
        diagnostic_restarted=False,sql_mutations=0,source_rows_read=0,source_rows_written=0,
        historical_drift_cause_proven=False,live_audit_sql_accepted=False,
        step_3_complete=False,step_4_started=False),REVIEW_NAME)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=('review','collect'))
    try:
        if parser.parse_args().command=='review':r=review()
        else:
            n=control();r=n['collect'](n['Transport']())
        print(json.dumps({k:r[k] for k in ('status','protected_receipt') if k in r}),flush=True)
    except Exception as error:
        print(json.dumps(dict(status='step_3_execution_metadata_unaccepted',code=type(error).__name__,automatic_retry=False)),flush=True)
        raise SystemExit(1) from None
