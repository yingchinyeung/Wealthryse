"""Additive Step 3 archive qualification source gate; no native write API.

Preserve all 841 accepted predecessor sources and consumed operations. A fresh
owned read-context admits only these three new files. Source QA is not Azure
archive/identity acceptance, SQL commit, runtime activation or website wiring.
"""
from __future__ import annotations

import ast
import json
import re
import types
from pathlib import Path

import deploy_owner_task_worker_audit_catalog_trial as prior
import owner_task_worker_archive_trial as worker

ADDITIONS=frozenset((worker.PATH,'scripts/qualify_owner_task_worker_archive_source.py',
    'tests/test_owner_task_worker_archive_trial.py'))
NAME='serviceaud-step3-worker-archive-source.json'
FRESH_SHA='28f63375c5159b166d654916e99bab7d625febe7b77274939e0a68bc2e5bc31e'
OBSERVATION_SHA='bc0b6076ceee04ffcf1958477fb0a485e571957d2315ba1e7e335ce3693f5891'


def require(value):
    if not value:raise ValueError('worker_archive_source_unverified')


def project_sources(after):
    require(type(after) is dict and ADDITIONS.issubset(after)
        and all(type(v) is str and re.fullmatch('[0-9a-f]{64}',v) for v in after.values()))
    return {k:v for k,v in after.items() if k not in ADDITIONS}


def source_extension(before,after):
    require(type(before) is dict and len(before)==841 and not set(before)&ADDITIONS
        and len(after)==844 and project_sources(after)==before)
    return True


def predecessor_extension(before,after):
    prior.extension(before,project_sources(after))


def predecessor_chain(before,after):
    prior.chain(before,project_sources(after))


def clone(fn,**bindings):
    return types.FunctionType(fn.__code__,dict(fn.__globals__,**bindings),fn.__name__,
        fn.__defaults__,fn.__closure__)


def read_context():
    # Immutable old functions/globals remain unchanged. Only the owned source
    # comparison is extended; every old receipt/native/identity check remains.
    baseline=clone(prior.baseline,extension=predecessor_extension)
    context=clone(prior.predecessor_context,chain=predecessor_chain)
    native=clone(prior.native_predecessors,baseline=baseline,predecessor_context=context)
    text=Path(prior.__file__).read_text()
    node=next(x for x in ast.parse(text).body if isinstance(x,ast.FunctionDef) and x.name=='control')
    body=ast.get_source_segment(text,node)
    old="pf['sources']==p.old.prior.host.sources()"
    require(body.count(old)==1)
    body=body.replace(old,"archive_source_extension(pf['sources'],p.old.prior.host.sources())")
    namespace=dict(vars(prior),baseline=baseline,native_predecessors=native,
        archive_source_extension=source_extension)
    exec(compile(body,__file__,'exec'),namespace)
    return namespace['control']()


def native(n,pf,observation):
    identity=n['guards'](pf);client=n['Transport']()
    job=client.call('GET',n['JOB']);n['checked_job'](job,pf['plan'],identity)
    execution=client.call('GET',observation['native_execution']['id'])
    inventory=client.call('GET',n['JOB']+'/executions')
    require(execution==observation['native_execution'] and execution['properties']['status']=='Succeeded'
        and inventory.get('nextLink') in (None,'') and len(inventory.get('value',[]))==1
        and inventory['value'][0]['id']==execution['id'])
    return dict(job=job,execution=execution,inventory=inventory)


def qualify():
    n=read_context();p=n['p']
    require(not (p.old.FOLDER/NAME).exists())
    rr,fresh=n['load']('fresh-readback');pr,pf=n['load']('live-preflight');ar,ob=n['load']('live-observation')
    require(rr['protected_receipt']['sha256']==FRESH_SHA
        and ar['protected_receipt']['sha256']==OBSERVATION_SHA
        and fresh['observation_receipt']==ar['protected_receipt']
        and fresh['sources']==pf['sources'] and fresh['restricted_audit_sql_verified'] is True
        and fresh['exact_reader_connection_revocation_verified'] is True
        and fresh['predecessor_catalog_unchanged_verified'] is True
        and fresh['historical_drift_cause_proven'] is False
        and fresh['protected_worker_receipts_verified'] is False
        and fresh['step_3_complete'] is False and fresh['step_4_started'] is False)
    sources=p.old.prior.host.sources();source_extension(fresh['sources'],sources)
    before=native(n,pf,ob);require(before==fresh['native'])
    checks=p.old.prior.parent.reviewed.run_local_checks()
    after=native(n,pf,ob)
    require(before==after and p.old.prior.host.sources()==sources)
    return p.old.save(dict(status='step_3_worker_archive_source_qa_verified',checked_at_utc=p.old.now(),
        sources=sources,local_checks=checks,predecessor_sources=fresh['sources'],
        audit_sql_readback_receipt=rr['protected_receipt'],audit_sql_observation_receipt=ar['protected_receipt'],
        audit_sql_preflight_receipt=pr['protected_receipt'],native_before=before,native_after=after,
        all_841_predecessor_sources_preserved=True,
        qualification_scope='synthetic_archive_protection_identity_and_lost_ack_source_tests_only',
        archive_identities_live_qualified=False,protected_worker_receipts_verified=False,
        production_evidence_registry_enrolled=False,runtime_process_cessation_verified=False,
        website_owner_authenticated=False,owner_runtime_active=False,six_specialist_runtimes_active=False,
        native_writes=0,source_rows_read=0,source_rows_written=0,sql_connections=0,model_calls=0,
        production_changed=False,step_3_complete=False,step_4_started=False),NAME)


if __name__=='__main__':
    try:
        result=qualify()
        print(json.dumps({k:result[k] for k in ('status','protected_receipt','local_checks')}),flush=True)
    except Exception:
        print(json.dumps(dict(status='step_3_worker_archive_source_unaccepted',automatic_retry=False)),flush=True)
        raise SystemExit(1) from None
