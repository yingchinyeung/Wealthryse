"""New isolated artifact: disabled workers resolve to no current authority.

Preserves both consumed predecessors and all 814 frozen source files. No
permissions, fence checks or error expectations are weakened. This module is
inert until the separately bound one-shot controller is explicitly invoked.
"""
from __future__ import annotations

import hashlib
import types

import owner_task_execution_fence_tls_candidate as tls

ROOT = tls.ROOT
BASE_WORKER, BASE_WORKER_SHA = tls.BASE_WORKER, tls.BASE_WORKER_SHA
BASE_CONTROL, BASE_CONTROL_SHA = tls.BASE_CONTROL, tls.BASE_CONTROL_SHA
TLS_SHA = '14c9707fbbeef4155ea0a170376deb74618c4815d1438045481392c78319ddbf'
PATH = 'scripts/owner_task_execution_fence_authority_candidate.py'
CONTROL_PATH = 'scripts/deploy_owner_task_execution_fence_authority_trial.py'
TEST_PATH = 'tests/test_owner_task_execution_fence_authority_trial.py'
OPERATION = '471be18f-27ab-4eaa-967b-d499a6bf39b0'
JOB = 'ibkrv2stg-ownfenc3-20261005'
DATABASE = 'owner_codex_fence_20261005_v3'
SCHEMA = 'owner_codex_fence_trial_v3'
OWNER = 'ibkr_owner_fence_schema_v3_20261005'
CAPABILITY = 'ibkr_owner_fence_dml_v3_20261005'
LOGIN = 'ibkr_owner_fence_login_v3_20261005'
MARKER = 'owner-codex-fence-isolated-20261005-v3'
FAILURE_SHA = '4c35037b993a36716b604a9e36657687ac2ab77c9cdcfc6e673504fb60bc83ec'
FAILURE_READBACK_SHA = '4989d526c5fc2aa8d019d08012c706e10cb6bc12cc9b8714a481837361a6f8e2'

AUTHORITY_HELPER = '''def resolve_authority(row):
    # Missing/disabled is a valid revocation, never an enabled capability.
    if row is None or row['enabled'] is not True:
        return None
    return WorkerAuthority(str(row['tenant_id']), str(row['worker_id']),
        str(row['service_principal_id']), True)


'''


def frozen_recipe():
    if hashlib.sha256((ROOT / tls.PATH).read_bytes()).hexdigest() != TLS_SHA:
        raise ValueError('fence_authority_predecessor_drift')


def worker_source(raw):
    frozen_recipe()
    text = tls.worker_source(raw)
    old = dict(OPERATION=tls.OPERATION, JOB=tls.JOB, DATABASE=tls.DATABASE,
        SCHEMA=tls.SCHEMA, OWNER=tls.OWNER, CAPABILITY=tls.CAPABILITY,
        LOGIN=tls.LOGIN, MARKER=tls.MARKER, PATH=tls.PATH,
        PREFIX='OWNER_TASK_EXECUTION_FENCE_TRIAL_V2 ')
    new = dict(OPERATION=OPERATION, JOB=JOB, DATABASE=DATABASE, SCHEMA=SCHEMA,
        OWNER=OWNER, CAPABILITY=CAPABILITY, LOGIN=LOGIN, MARKER=MARKER,
        PATH=PATH, PREFIX='OWNER_TASK_EXECUTION_FENCE_TRIAL_V3 ')
    for key, value in old.items():
        text = tls.replace_once(text, key + ' = ' + repr(value), key + ' = ' + repr(new[key]))
    text = tls.replace_once(text, '@contextmanager\ndef restricted(connect, parameters):',
        AUTHORITY_HELPER + '@contextmanager\ndef restricted(connect, parameters):')
    text = tls.replace_once(text,
        "return None if row is None else WorkerAuthority(str(row['tenant_id']), str(row['worker_id']), str(row['service_principal_id']), row['enabled'])",
        'return resolve_authority(row)')
    compile(text, PATH, 'exec')
    return text


def worker_module():
    module = types.ModuleType('owner_task_execution_fence_authority_candidate_worker')
    module.__file__, module.__package__ = str(ROOT / PATH), ''
    exec(compile(worker_source((ROOT / BASE_WORKER).read_bytes()), PATH, 'exec'), module.__dict__)
    return module


def controller_source(raw):
    frozen_recipe()
    text = tls.controller_source(raw)
    text = tls.replace_once(text, 'import owner_task_execution_fence_tls_candidate as candidate',
        'import owner_task_execution_fence_authority_candidate as candidate\n'
        'import owner_task_execution_fence_tls_candidate as tls_predecessor\n'
        'tls_control = tls_predecessor.controller_namespace()')
    text = tls.replace_once(text, 'len(before) == 811', 'len(before) == 814')
    text = tls.replace_once(text,
        "for name in (p.w.JOB, 'ibkrv2stg-ownmid-20261005', failed_control.worker.JOB, worker.JOB):",
        "for name in (p.w.JOB, 'ibkrv2stg-ownmid-20261005', failed_control.worker.JOB, tls_control['worker'].JOB, worker.JOB):")
    # The original TypeError failure remains pinned to its own constant; the new
    # candidate also binds the later seven-check/revocation failure separately.
    text = tls.replace_once(text, "old_raw['protected_receipt']['sha256'] == candidate.FAILURE_SHA",
        "old_raw['protected_receipt']['sha256'] == tls_predecessor.FAILURE_SHA")
    text = tls.replace_once(text, '    client = p.Transport()\n', '''    failed_raw, failed = tls_control['load']('live-observation')
    read_raw, readback = tls_control['load']('failure-readback')
    require(failed_raw['protected_receipt']['sha256'] == candidate.FAILURE_SHA
        and read_raw['protected_receipt']['sha256'] == candidate.FAILURE_READBACK_SHA
        and readback['failure_receipt'] == failed_raw['protected_receipt']
        and readback['live_sql_accepted'] is False
        and failed['status'] == 'step_3_fence_trial_failed_retained'
        and failed['worker_result']['checks'] == list(tls_control['worker'].CHECKS[:7])
        and failed['worker_result']['code'] == 'fence_revocation_denial_invalid',
        'fence_predecessor_failure_unverified')
    _, tls_pf = tls_control['load']('live-preflight')
    tls_client = tls_control['Transport']()
    tls_control['checked_job'](tls_client.call('GET', tls_control['JOB']), tls_pf['plan'], tls_pf['sql_identity'])
    require(tls_client.call('GET', failed['native_execution']['id']) == failed['native_execution'],
        'fence_predecessor_execution_drift')
    client = p.Transport()
''')
    text = tls.replace_once(text, "_, earlier = failed_control.load('live-preflight')",
        "_, earlier = tls_control['load']('live-preflight')")
    # Every evidence/effect namespace is separately registered; no old stage is
    # resumed. Require the reviewed multiplicity so recipe drift fails closed.
    for before, after, count in (
        ('probes/owner-codex-execution-fence-v2-20261005/', 'probes/owner-codex-execution-fence-v3-20261005/', 1),
        ('serviceaud-step3-fence-v2-', 'serviceaud-step3-fence-v3-', 4)):
        if text.count(before) != count:
            raise ValueError('fence_authority_recipe_drift')
        text = text.replace(before, after)
    text = tls.replace_once(text,
        "and record['protected_worker_receipts_verified'] is False, 'fence_worker_acceptance_invalid')",
        "and record['protected_worker_receipts_verified'] is False\n"
        "            and record['runtime_process_cessation_verified'] is False\n"
        "            and record['website_owner_authenticated'] is False\n"
        "            and record['denied_privilege_probes'] == 11\n"
        "            and record['predecessor_metadata_unchanged'] is True\n"
        "            and record['positive_tls']['sslmode'] == 'verify-full'\n"
        "            and record['positive_tls']['protocol'] in ('TLSv1.2', 'TLSv1.3')\n"
        "            and record['positive_tls']['authenticated_login'] == worker.LOGIN, 'fence_worker_acceptance_invalid')")
    compile(text, CONTROL_PATH, 'exec')
    return text


def controller_namespace():
    namespace = dict(__name__='owner_task_execution_fence_authority_candidate_control',
        __file__=str(ROOT / CONTROL_PATH), __package__='')
    exec(compile(controller_source((ROOT / BASE_CONTROL).read_bytes()), CONTROL_PATH, 'exec'), namespace)
    return namespace
