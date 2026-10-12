"""Source-only catalog diagnostic checkpoint; no SQL/job/start/model dispatch.

Preserves every one of the 828 predecessor hashes. Both failed trials remain
unaccepted. This source checkpoint cannot resolve the historical catalog drift.
"""
from __future__ import annotations
import ast
import json
import re
import owner_task_worker_audit_sql_revocation_candidate as candidate

ADDITIONS=frozenset(('src/ibkr_etl/owner_codex_catalog_snapshot.py',
    'tests/test_owner_codex_catalog_snapshot.py','scripts/qualify_owner_task_catalog_diagnostic_source.py'))
FAILURE_SHA='1502f7730c4114ee462cc03621b849ba2c3b442145f9646e5d76a24fddc2660b'
READBACK_SHA='11ae1869eef34d2735e399b7e21ada61ff1f506bbf26cb0628519dfcd8aa1e34'
NAME='serviceaud-step3-catalog-diagnostic-source.json'


def require(value):
    if not value:raise ValueError('catalog_diagnostic_source_unverified')


def source_extension(before,after):
    require(type(before) is dict and len(before)==828 and type(after) is dict
        and set(after)==set(before)|ADDITIONS and not set(before)&ADDITIONS
        and all(after[k]==v for k,v in before.items())
        and all(type(v) is str and re.fullmatch('[0-9a-f]{64}',v) for v in after.values()))


def detached_guard(namespace):
    source=candidate.controller_source((candidate.ROOT/candidate.BASE_CONTROL).read_bytes())
    nodes=[n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='guards']
    require(len(nodes)==1)
    original=ast.get_source_segment(source,nodes[0])
    old="    require(p.old.prior.host.sources() == pf['sources'], 'fence_source_drift')\n    sources_valid(pf['predecessor_sources'], pf['sources'])"
    require(original.count(old)==1)
    changed=original.replace(old,"    diagnostic_source_extension(pf['sources'],p.old.prior.host.sources())")
    isolated=dict(namespace,diagnostic_source_extension=source_extension)
    exec(compile(changed,__file__,'exec'),isolated)
    return isolated['guards']


def baseline(n,pf,ob):
    identity=detached_guard(n)(pf);client=n['Transport']()
    job=client.call('GET',n['JOB']);n['checked_job'](job,pf['plan'],identity)
    execution=client.call('GET',ob['native_execution']['id'])
    inventory=client.call('GET',n['JOB']+'/executions')
    require(execution==ob['native_execution'] and execution['properties']['status']=='Failed'
        and inventory.get('nextLink') in (None,'') and len(inventory['value'])==1
        and inventory['value'][0]['id']==execution['id'])
    return dict(job=job,execution=execution,inventory=inventory)


def qualify():
    n=candidate.controller_namespace();p=n['p']
    require(not (p.old.FOLDER/NAME).exists())
    pr,pf=n['load']('live-preflight');ar,ob=n['load']('live-observation');rr,rb=n['load']('failure-readback')
    require(ar['protected_receipt']['sha256']==FAILURE_SHA and rr['protected_receipt']['sha256']==READBACK_SHA
        and rb['failure_receipt']==ar['protected_receipt'] and rb['sources']==pf['sources']
        and rb['live_sql_accepted'] is False and rb['predecessor_sql_catalog_unchanged_verified'] is False
        and ob['worker_result']['code']=='fence_audit_predecessor_drift' and ob['worker_result']['checks']==[])
    sources=p.old.prior.host.sources();source_extension(pf['sources'],sources)
    before=baseline(n,pf,ob)
    checks=p.old.prior.parent.reviewed.run_local_checks()
    after=baseline(n,pf,ob)
    require(before==after and p.old.prior.host.sources()==sources)
    return p.old.save(dict(status='step_3_catalog_diagnostic_source_qa_verified',
        checked_at_utc=p.old.now(),sources=sources,local_checks=checks,
        failed_sql_preflight_receipt=pr['protected_receipt'],failed_sql_observation_receipt=ar['protected_receipt'],
        failed_sql_fresh_readback_receipt=rr['protected_receipt'],native_before=before,native_after=after,
        all_828_predecessor_sources_preserved=True,source_only=True,
        live_catalog_diagnostics_run=False,predecessor_sql_catalog_unchanged_verified=False,
        live_audit_sql_accepted=False,protected_worker_receipts_verified=False,
        runtime_process_cessation_verified=False,website_owner_authenticated=False,
        production_changed=False,source_rows_read=0,source_rows_written=0,model_calls=0,
        owner_runtime_active=False,six_specialist_runtimes_active=False,
        step_3_complete=False,step_4_started=False),NAME)


if __name__=='__main__':
    try:
        r=qualify();print(json.dumps({k:r[k] for k in ('status','protected_receipt','local_checks')}),flush=True)
    except Exception as error:
        print(json.dumps(dict(status='step_3_catalog_diagnostic_source_unaccepted',code=type(error).__name__,automatic_retry=False)),flush=True)
        raise SystemExit(1) from None
