"""Create-only Step 3 source/QA checkpoint; dispatches no SQL/model/runtime.

This explicitly reviews an additive extension of the completed Step 2 manifest.
Native guards use a detached source-context copy only AFTER all 803 predecessor
hashes and the exact four additions pass. No predecessor function/global/receipt,
job definition or consumed stage is modified or rearmed.
"""
from __future__ import annotations

import copy
import json
import re

import deploy_owner_task_model_service_audience_trial as prior

ADDITIONS = frozenset((
    'src/ibkr_etl/owner_codex_execution_fence.py',
    'scripts/owner_task_execution_fence_schema.sql',
    'scripts/qualify_owner_task_execution_fence.py',
    'tests/test_owner_codex_execution_fence.py',
))
ACCEPTANCE_SHA = '65ed4e07115fac9bbd530797b046e6a2e0b8763f9398fd16bb9b0175d891a607'
READBACK_SHA = '8a094fc90ce359dcd58d200f47a4d79afc3854abcd3ba756e98d36bc518fd2aa'
START_SHA = 'b756bc93add03d53ef97cfa473dc7b31f0ca98067bc28ed9369efd41949f3276'


def sources_valid(before, after):
    prior.w.require(type(before) is dict and len(before) == 803 and type(after) is dict
        and set(after) == set(before) | ADDITIONS and not set(before) & ADDITIONS
        and all(after[k] == value for k, value in before.items())
        and all(type(v) is str and re.fullmatch('[0-9a-f]{64}', v) for v in after.values()),
        'execution_fence_source_drift')


def guards(sources, pf, client):
    sources_valid(pf['sources'], sources)
    prior.w.require(prior.old.prior.host.sources() == sources, 'execution_fence_source_drift')
    detached = copy.deepcopy(pf)
    detached['sources'] = dict(sources)
    prior.live(detached, client)
    prior.native(client.call('GET', prior.JOB), pf['plan'], pf['registered_identity'])
    run = client.call('GET', prior.JOB + '/executions/ibkrv2stg-ownmod2-20261005-2u4sv88')
    prior.w.require(run['properties']['status'] == 'Succeeded', 'execution_fence_predecessor_not_accepted')
    return run


def qualify():
    prior.w.require(not (prior.old.FOLDER / 'serviceaud-step3-fence-source.json').exists(),
        'execution_fence_checkpoint_consumed')
    _, pf = prior.load()
    ar, acceptance = prior.old.load('serviceaud-step2-acceptance.json')
    rr, readback = prior.old.load('serviceaud-step2-fresh-readback.json')
    sr, started = prior.old.load('serviceaud-step3-start.json')
    prior.w.require(ar['protected_receipt']['sha256'] == ACCEPTANCE_SHA
        and acceptance['status'] == 'step_2_isolated_integration_accepted'
        and acceptance['step_2_complete'] is True and acceptance['step_3_started'] is False
        and rr['protected_receipt']['sha256'] == READBACK_SHA
        and readback['status'] == 'step_2_fresh_protected_and_native_readback_verified'
        and sr['protected_receipt']['sha256'] == START_SHA
        and started['status'] == 'step_3_authorized_started_source_implementation_pending'
        and started['step_3_started'] is True and started['step_3_complete'] is False,
        'execution_fence_predecessor_receipt_invalid')
    sources = prior.old.prior.host.sources()
    client = prior.Transport()
    native_before = guards(sources, pf, client)
    checks = prior.old.prior.parent.reviewed.run_local_checks()
    native_after = guards(sources, pf, client)
    prior.w.require(native_before == native_after, 'execution_fence_predecessor_execution_drift')
    return prior.old.save(dict(status='step_3_durable_fence_source_qa_verified',
        checked_at_utc=prior.old.now(), sources=sources, local_checks=checks,
        step_2_acceptance_receipt=ar['protected_receipt'], step_2_readback_receipt=rr['protected_receipt'],
        step_3_start_receipt=sr['protected_receipt'], native_predecessor_execution=native_after,
        all_803_predecessor_source_hashes_preserved=True,
        qualification_scope='synthetic_sqlite_transactions_process_races_and_coordinator_faults',
        restricted_postgres_fence_rls_accepted=False, protected_worker_audit_adapter_accepted=False,
        signed_website_owner_integration_accepted=False, production_migration_applied=False,
        production_changed=False, owner_runtime_active=False, six_specialist_runtimes_active=False,
        ibkr_received_rows_read=0, ibkr_received_rows_modified=0, model_calls=0,
        step_2_complete=True, step_3_started=True, step_3_complete=False),
        'serviceaud-step3-fence-source.json')


if __name__ == '__main__':
    try:
        result = qualify()
        print(json.dumps({k: result[k] for k in ('status', 'protected_receipt', 'local_checks')}), flush=True)
    except Exception as error:
        print(json.dumps(dict(status='step_3_checkpoint_unaccepted_inspect_only',
            code=getattr(error, 'code', type(error).__name__), automatic_retry=False)), flush=True)
        raise SystemExit(1) from None
