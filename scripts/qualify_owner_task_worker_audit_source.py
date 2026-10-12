"""New source-only audit checkpoint; no SQL/model/job dispatch or enrollment.

Exact 817-file predecessor is preserved. Only a reviewed detached copy of its
native read guard admits the four additions. Frozen functions/globals and
consumed effects are never modified. Full QA and protected readback required.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re

import owner_task_execution_fence_authority_candidate as candidate

ADDITIONS = frozenset((
    'src/ibkr_etl/owner_codex_worker_audit.py',
    'scripts/owner_task_worker_audit_schema.sql',
    'scripts/qualify_owner_task_worker_audit_source.py',
    'tests/test_owner_codex_worker_audit.py',
))
FRESH_SHA = 'de5a2ca52211e823c62d756b3d2c476c1b75d99d8f597f0c5b1fd9b11743d6b1'
OBSERVATION_SHA = 'b5a4260f267360df67996a33537e0286983bd4eadbcb158d3227ef2703cd80f0'
NAME = 'serviceaud-step3-worker-audit-source.json'


def require(value):
    if not value:
        raise ValueError('worker_audit_source_checkpoint_unverified')


def source_extension(before, after):
    require(type(before) is dict and len(before) == 817 and type(after) is dict
        and set(after) == set(before) | ADDITIONS and not set(before) & ADDITIONS
        and all(after[k] == v for k,v in before.items())
        and all(type(v) is str and re.fullmatch('[0-9a-f]{64}',v) for v in after.values()))


def detached_guard(namespace):
    source = candidate.controller_source((candidate.ROOT / candidate.BASE_CONTROL).read_bytes())
    tree = ast.parse(source)
    guard = [node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='guards']
    require(len(guard) == 1)
    original = ast.get_source_segment(source,guard[0])
    before = ("    require(p.old.prior.host.sources() == pf['sources'], 'fence_source_drift')\n"
        "    sources_valid(pf['predecessor_sources'], pf['sources'])")
    require(original.count(before) == 1)
    changed = original.replace(before,
        "    audit_source_extension(pf['sources'], p.old.prior.host.sources())")
    detached = dict(namespace, audit_source_extension=source_extension)
    exec(compile(changed,__file__,'exec'),detached)
    return detached['guards']


def baseline(namespace, preflight, observation):
    identity = detached_guard(namespace)(preflight)
    client = namespace['Transport']()
    job = client.call('GET', namespace['JOB'])
    namespace['checked_job'](job,preflight['plan'],identity)
    execution = client.call('GET', observation['native_execution']['id'])
    require(execution == observation['native_execution'] and execution['properties']['status']=='Succeeded')
    inventory = client.call('GET', namespace['JOB']+'/executions')
    require(inventory.get('nextLink') in (None,'') and len(inventory.get('value',[]))==1
        and inventory['value'][0]['id']==execution['id'])
    return dict(job=job,execution=execution,inventory=inventory)


def qualify():
    namespace = candidate.controller_namespace()
    p = namespace['p']
    require(not (p.old.FOLDER / NAME).exists())
    rr,fresh = namespace['load']('fresh-readback')
    pr,preflight = namespace['load']('live-preflight')
    ar,observation = namespace['load']('live-observation')
    require(rr['protected_receipt']['sha256']==FRESH_SHA
        and ar['protected_receipt']['sha256']==OBSERVATION_SHA
        and fresh['isolated_sql_substage_accepted'] is True
        and fresh['step_3_complete'] is False and fresh['step_4_started'] is False)
    sources = p.old.prior.host.sources()
    source_extension(preflight['sources'],sources)
    before = baseline(namespace,preflight,observation)
    checks = p.old.prior.parent.reviewed.run_local_checks()
    after = baseline(namespace,preflight,observation)
    require(before == after and p.old.prior.host.sources()==sources)
    return p.old.save(dict(status='step_3_worker_audit_source_qa_verified',
        checked_at_utc=p.old.now(),sources=sources,local_checks=checks,
        isolated_sql_fresh_readback_receipt=rr['protected_receipt'],
        isolated_sql_preflight_receipt=pr['protected_receipt'],
        isolated_sql_observation_receipt=ar['protected_receipt'],
        native_before=before,native_after=after,
        all_817_predecessor_sources_preserved=True,
        qualification_scope='synthetic_sql_and_archive_faults_only',
        protected_worker_receipts_verified=False,
        restricted_audit_writer_reader_live_qualified=False,
        production_evidence_registry_enrolled=False,
        runtime_process_cessation_verified=False,website_owner_authenticated=False,
        source_rows_read=0,source_rows_written=0,model_calls=0,
        production_changed=False,owner_runtime_active=False,six_specialist_runtimes_active=False,
        step_3_complete=False,step_4_started=False), NAME)


if __name__=='__main__':
    try:
        result=qualify()
        print(json.dumps({k:result[k] for k in ('status','protected_receipt','local_checks')}),flush=True)
    except Exception as error:
        print(json.dumps(dict(status='step_3_worker_audit_source_unaccepted',
            code=type(error).__name__,automatic_retry=False)),flush=True)
        raise SystemExit(1) from None
