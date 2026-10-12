"""New fixed SQL-only artifact derived from an unchanged reviewed controller.

Preserves all 821 predecessor files and consumed trials. This qualifies separate
PostgreSQL audit logins only: no Azure archive writer/verifier or model grant.
"""
from __future__ import annotations

import hashlib
import types

import owner_task_execution_fence_authority_candidate as previous
import run_owner_task_worker_audit_sql_trial as worker

ROOT=previous.ROOT
BASE_WORKER=worker.PATH
BASE_CONTROL=previous.BASE_CONTROL
PATH='scripts/owner_task_worker_audit_sql_candidate.py'
CONTROL_PATH='scripts/deploy_owner_task_worker_audit_sql_trial.py'
TEST_PATH='tests/test_owner_task_worker_audit_sql_trial.py'
OPERATION,JOB=worker.OPERATION,worker.JOB
FAILURE_SHA,FAILURE_READBACK_SHA=previous.FAILURE_SHA,previous.FAILURE_READBACK_SHA
ADDITIONS=frozenset((worker.PATH,PATH,CONTROL_PATH,TEST_PATH))
SOURCE_SHA='5f6965b7425fdb16ab86bef4bf4ce87eb6c536fbb6de8d04b7f81e391a61e836'
SOURCE_READBACK_SHA='a59fd86d67591d18174201a7a6c58c4ad2e11297dc5d7f9cedf3d42a6d1fcf1c'


def worker_source(raw):
    if type(raw) is not bytes:
        raise ValueError('fence_audit_worker_source_invalid')
    text=raw.decode('utf-8').replace('\r\n','\n')
    compile(text,worker.PATH,'exec')
    return text


def worker_module():
    return worker


def controller_source(raw):
    text=previous.controller_source(raw)
    replace=previous.tls.replace_once
    text=replace(text,'import owner_task_execution_fence_authority_candidate as candidate',
        'import owner_task_worker_audit_sql_candidate as candidate\n'
        'import owner_task_execution_fence_authority_candidate as accepted_fence\n'
        'accepted_fence_control=accepted_fence.controller_namespace()')
    text=replace(text,'ADDITIONS = frozenset((candidate.PATH, candidate.CONTROL_PATH, candidate.TEST_PATH))',
        'ADDITIONS = candidate.ADDITIONS')
    text=replace(text,'len(before) == 814','len(before) == 821')
    text=replace(text,"tls_control['worker'].JOB, worker.JOB):",
        "tls_control['worker'].JOB, accepted_fence_control['worker'].JOB, worker.JOB):")
    text=replace(text,"_, earlier = tls_control['load']('live-preflight')",
        "_, earlier = p.old.load('serviceaud-step3-worker-audit-source.json')")
    text=replace(text,
        "    ('src/ibkr_etl/owner_codex_execution_fence.py', 'ibkr_etl.owner_codex_execution_fence'),",
        "    ('src/ibkr_etl/owner_codex_execution_fence.py', 'ibkr_etl.owner_codex_execution_fence'),\n"
        "    ('src/ibkr_etl/owner_codex_worker_audit.py', 'ibkr_etl.owner_codex_worker_audit'),")
    text=replace(text,"    client = p.Transport()\n",'''    audit_raw, audit_source = p.old.load('serviceaud-step3-worker-audit-source.json')
    audit_read_raw, audit_read = p.old.load('serviceaud-step3-worker-audit-source-readback.json')
    require(audit_raw['protected_receipt']['sha256'] == candidate.SOURCE_SHA
        and audit_read_raw['protected_receipt']['sha256'] == candidate.SOURCE_READBACK_SHA
        and audit_read['source_checkpoint_receipt'] == audit_raw['protected_receipt']
        and audit_source['sources'] == pf['predecessor_sources'] == audit_read['sources']
        and audit_source['protected_worker_receipts_verified'] is False
        and audit_read['step_3_complete'] is False and audit_read['step_4_started'] is False,
        'fence_audit_source_checkpoint_invalid')
    client = p.Transport()
''')
    text=replace(text,'    return identity\n', '''    _, accepted_pf = accepted_fence_control['load']('live-preflight')
    _, accepted_observation = accepted_fence_control['load']('live-observation')
    fence_client = accepted_fence_control['Transport']()
    accepted_fence_control['checked_job'](fence_client.call('GET', accepted_fence_control['JOB']),
        accepted_pf['plan'], identity)
    require(fence_client.call('GET', accepted_observation['native_execution']['id'])
        == accepted_observation['native_execution'], 'fence_audit_predecessor_execution_drift')
    return identity
''')
    for before,after,count in (
        ('probes/owner-codex-execution-fence-v3-20261005/','probes/owner-codex-audit-sql-20261005/',1),
        ('serviceaud-step3-fence-v3-','serviceaud-step3-audit-sql-',4)):
        if text.count(before)!=count:
            raise ValueError('fence_audit_controller_recipe_drift')
        text=text.replace(before,after)
    text=replace(text,"purpose='owner-execution-fence-trial'","purpose='owner-worker-audit-sql-trial'")
    text=replace(text,"'step_3_synthetic_postgres_fence_accepted' if record['status'] == 'synthetic_postgres_fence_verified'",
        "'step_3_synthetic_postgres_audit_sql_accepted' if record['status'] == 'synthetic_postgres_audit_sql_verified'")
    text=replace(text,"if result['status'] == 'step_3_synthetic_postgres_fence_accepted':",
        "if result['status'] == 'step_3_synthetic_postgres_audit_sql_accepted':")
    start=text.index("        require(value['properties']['status'] == 'Succeeded' and record['checks'] == list(worker.CHECKS)")
    end=text.index("    return save(result, 'live-observation')",start)
    text=text[:start]+'''        require(value['properties']['status'] == 'Succeeded' and record['checks'] == list(worker.CHECKS)
            and record['synthetic_registration_only'] is True and record['restricted_audit_sql_verified'] is True
            and record['protected_worker_receipts_verified'] is False
            and record['runtime_process_cessation_verified'] is False
            and record['website_owner_authenticated'] is False
            and record['denied_privilege_probes'] == 16
            and record['predecessor_metadata_unchanged'] is True
            and [r['login'] for r in record['positive_tls']] == [worker.LOGIN,worker.READER_LOGIN]
            and all(r['sslmode']=='verify-full' and r['protocol'] in ('TLSv1.2','TLSv1.3')
                for r in record['positive_tls']), 'fence_audit_sql_acceptance_invalid')
'''+text[end:]
    compile(text,CONTROL_PATH,'exec')
    return text


def controller_namespace():
    namespace=dict(__name__='owner_task_worker_audit_sql_control',__file__=str(ROOT/CONTROL_PATH),__package__='')
    exec(compile(controller_source((ROOT/BASE_CONTROL).read_bytes()),CONTROL_PATH,'exec'),namespace)
    return namespace
