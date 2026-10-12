"""Inert, deterministic new artifact derived from the frozen failed trial.

Never edits its predecessor module, globals, source, database or consumed job.
The new physical synthetic namespace is explicit. A LOGIN owns its backend
statistics; SET ROLE to a NOLOGIN capability can hide that row. Read live TLS
under the actual restricted session LOGIN and restore the capability before
any fence operation. This is neither an administrator statistics substitute
nor signed-owner/audit/runtime acceptance.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import types

ROOT = Path(__file__).resolve().parents[1]
BASE_WORKER = 'scripts/run_owner_task_execution_fence_trial.py'
BASE_WORKER_SHA = 'f418834388d7ecf1c02eae80277ee9498035f10e068fb383956caa005812aa5f'
BASE_CONTROL = 'scripts/deploy_owner_task_execution_fence_trial.py'
BASE_CONTROL_SHA = '4dd78954e6845f1b74abe518d6628314f6421371b216c8ccfd9c3d30b4259a17'
PATH = 'scripts/owner_task_execution_fence_tls_candidate.py'
CONTROL_PATH = 'scripts/deploy_owner_task_execution_fence_tls_trial.py'
TEST_PATH = 'tests/test_owner_task_execution_fence_tls_trial.py'
OPERATION = '3f4efdd4-a3d7-4167-97c9-c8da9328fd9b'
JOB = 'ibkrv2stg-ownfenc2-20261005'
DATABASE = 'owner_codex_fence_20261005_v2'
SCHEMA = 'owner_codex_fence_trial_v2'
OWNER = 'ibkr_owner_fence_schema_v2_20261005'
CAPABILITY = 'ibkr_owner_fence_dml_v2_20261005'
LOGIN = 'ibkr_owner_fence_login_v2_20261005'
MARKER = 'owner-codex-fence-isolated-20261005-v2'
FAILURE_SHA = '1dd0e488fcf478cc457d3adbda411f15723b4631d66209e70a9058461ba13883'


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('fence_candidate_recipe_drift')
    return text.replace(old, new, 1)


def checked_source(raw, sha):
    if type(raw) is not bytes or hashlib.sha256(raw).hexdigest() != sha:
        raise ValueError('fence_candidate_predecessor_drift')
    return raw.decode('utf-8').replace('\r\n', '\n')


TLS_HELPER = '''def authenticated_tls(raw):
    require(raw.info.user == LOGIN and raw.info.dbname == DATABASE
        and raw.info.host == host.HOST and raw.autocommit is False,
        'fence_tls_context_invalid')
    before = raw.execute('SELECT session_user::text AS login,current_user::text AS role').fetchone()
    require(before['login'] == LOGIN and before['role'] == CAPABILITY, 'fence_tls_context_invalid')
    capability_row = raw.execute('SELECT ssl,version FROM pg_stat_ssl WHERE pid=pg_backend_pid()').fetchone()
    # A nested transaction/savepoint restores the original capability on error.
    with raw.transaction():
        raw.execute(sql.SQL('SET LOCAL ROLE {}').format(sql.Identifier(LOGIN)))
        row = raw.execute('SELECT ssl,version FROM pg_stat_ssl WHERE pid=pg_backend_pid()').fetchone()
        require(row is not None and row['ssl'] is True
            and row['version'] in ('TLSv1.2','TLSv1.3'), 'fence_tls_invalid')
        raw.execute(sql.SQL('SET LOCAL ROLE {}').format(sql.Identifier(CAPABILITY)))
        after = raw.execute('SELECT session_user::text AS login,current_user::text AS role').fetchone()
        require(after['login'] == LOGIN and after['role'] == CAPABILITY, 'fence_tls_context_invalid')
    return dict(ssl=row['ssl'], version=row['version'], capability_statistics_row_visible=capability_row is not None)


'''


def worker_source(raw):
    text = checked_source(raw, BASE_WORKER_SHA)
    old_values = dict(OPERATION='bb07cb08-106a-43f5-ac36-1d8a2f1f2ad1', JOB='ibkrv2stg-ownfence-20261005',
        DATABASE='owner_codex_fence_20261005', SCHEMA='owner_codex_fence_trial',
        OWNER='ibkr_owner_fence_schema_20261005', CAPABILITY='ibkr_owner_fence_dml_20261005',
        LOGIN='ibkr_owner_fence_login_20261005', MARKER='owner-codex-fence-isolated-20261005-v1',
        PATH=BASE_WORKER, PREFIX='OWNER_TASK_EXECUTION_FENCE_TRIAL_V1 ')
    new_values = dict(OPERATION=OPERATION, JOB=JOB, DATABASE=DATABASE, SCHEMA=SCHEMA,
        OWNER=OWNER, CAPABILITY=CAPABILITY, LOGIN=LOGIN, MARKER=MARKER,
        PATH=PATH, PREFIX='OWNER_TASK_EXECUTION_FENCE_TRIAL_V2 ')
    for key, value in old_values.items():
        text = replace_once(text, key + ' = ' + repr(value), key + ' = ' + repr(new_values[key]))
    text = replace_once(text, 'def verify(connect, parameters, admin_parameters, *, passed=None):',
        TLS_HELPER + 'def verify(connect, parameters, admin_parameters, *, passed=None):')
    text = replace_once(text,
        "ssl = raw.execute('SELECT ssl,version FROM pg_stat_ssl WHERE pid=pg_backend_pid()').fetchone()",
        'ssl = authenticated_tls(raw)')
    text = replace_once(text,
        "positive_tls=dict(sslmode='verify-full', protocol=ssl['version'])",
        "positive_tls=dict(sslmode='verify-full', protocol=ssl['version'], authenticated_login=LOGIN, "
        "capability_statistics_row_visible=ssl['capability_statistics_row_visible'])")
    text = replace_once(text, "result['error_class'] = type(error).__name__",
        "result['error_class'] = type(error).__name__\n        trace, frame = [], error.__traceback__\n"
        "        while frame is not None and len(trace) < 8:\n"
        "            trace.append(dict(function=frame.tb_frame.f_code.co_name, line=frame.tb_lineno))\n"
        "            frame = frame.tb_next\n        result['failure_location'] = trace")
    compile(text, PATH, 'exec')
    return text


def worker_module():
    module = types.ModuleType('owner_task_execution_fence_tls_candidate_worker')
    module.__file__, module.__package__ = str(ROOT / PATH), ''
    exec(compile(worker_source((ROOT / BASE_WORKER).read_bytes()), PATH, 'exec'), module.__dict__)
    return module


def controller_source(raw):
    text = checked_source(raw, BASE_CONTROL_SHA)
    text = replace_once(text, 'import run_owner_task_execution_fence_trial as worker',
        'import owner_task_execution_fence_tls_candidate as candidate\n'
        'import deploy_owner_task_execution_fence_trial as failed_control\nworker = candidate.worker_module()')
    text = replace_once(text, "PREFIX = 'probes/owner-codex-execution-fence-20261005/'",
        "PREFIX = 'probes/owner-codex-execution-fence-v2-20261005/'")
    text = replace_once(text,
        "ADDITIONS = frozenset((worker.PATH, worker.CONSTRAINT_PATH,\n"
        "    'scripts/deploy_owner_task_execution_fence_trial.py', 'tests/test_owner_task_execution_fence_trial.py'))",
        'ADDITIONS = frozenset((candidate.PATH, candidate.CONTROL_PATH, candidate.TEST_PATH))')
    text = replace_once(text, 'len(before) == 807', 'len(before) == 811')
    text = replace_once(text,
        "for name in (p.w.JOB, 'ibkrv2stg-ownmid-20261005', worker.JOB):",
        "for name in (p.w.JOB, 'ibkrv2stg-ownmid-20261005', failed_control.worker.JOB, worker.JOB):")
    text = replace_once(text, '    client = p.Transport()\n', '''    old_raw, failure = failed_control.load('failure-forensics')
    require(old_raw['protected_receipt']['sha256'] == candidate.FAILURE_SHA
        and failure['status'] == 'step_3_failed_fence_execution_retained'
        and failure['worker_result']['failure_stage'] == 'restricted_login'
        and failure['worker_result']['error_class'] == 'TypeError'
        and failure['worker_result']['checks'] == [] and failure['live_sql_accepted'] is False,
        'fence_predecessor_failure_unverified')
    _, old_pf = failed_control.load('live-preflight')
    old_client = failed_control.Transport()
    failed_control.checked_job(old_client.call('GET', failed_control.JOB), old_pf['plan'], identity_for_old(p))
    require(old_client.call('GET', failure['native_execution']['id']) == failure['native_execution'],
        'fence_predecessor_execution_drift')
    client = p.Transport()
''')
    # The exact original SQL identity is obtained by the same restricted native
    # reader; this helper adds no route, identity selector or grant.
    text = replace_once(text, 'def guards(pf):', '''def identity_for_old(p):
    client = p.old.prior.parent.Transport()
    return p.old.prior.parent.live_identity(client.call('GET', MI, '2023-01-31'))


def guards(pf):''')
    text = replace_once(text,
        "        require(template.pop(field, None) is None, 'fence_native_job_invalid')",
        "        item = template.pop(field, None)\n"
        "        require(item is None or (field == 'initContainers' and item == []), 'fence_native_job_invalid')")
    text = replace_once(text,
        "if env.get('secretRef') == 'cappjob-' + worker.host.SECRET_NAME:",
        "if env.get('secretRef') in ('cappjob-' + worker.host.SECRET_NAME, 'cappjob-' + worker.JOB):")
    text = replace_once(text,
        "p.old.save(value, 'serviceaud-step3-fence-' + name + '.json')",
        "p.old.save(value, 'serviceaud-step3-fence-v2-' + name + '.json')")
    text = replace_once(text,
        "p.old.load('serviceaud-step3-fence-' + name + '.json')",
        "p.old.load('serviceaud-step3-fence-v2-' + name + '.json')")
    text = replace_once(text,
        "p.old.FOLDER / 'serviceaud-step3-fence-live-preflight.json'",
        "p.old.FOLDER / 'serviceaud-step3-fence-v2-live-preflight.json'")
    text = replace_once(text,
        "_, earlier = p.old.load('serviceaud-step3-fence-readback.json')",
        "_, earlier = failed_control.load('live-preflight')")
    text = replace_once(text,
        "source = {path: (ROOT / path).read_bytes() for path in PATHS}",
        "source = {path: (ROOT / path).read_bytes() for path in PATHS}\n"
        "    source[worker.PATH] = candidate.worker_source((ROOT / candidate.BASE_WORKER).read_bytes()).encode('utf-8')")
    text = replace_once(text,
        "'serviceaud-step3-fence-' + stage + '-' + suffix + '.json'",
        "'serviceaud-step3-fence-v2-' + stage + '-' + suffix + '.json'")
    compile(text, CONTROL_PATH, 'exec')
    return text


def controller_namespace():
    namespace = dict(__name__='owner_task_execution_fence_tls_candidate_control',
        __file__=str(ROOT / CONTROL_PATH), __package__='')
    exec(compile(controller_source((ROOT / BASE_CONTROL).read_bytes()), CONTROL_PATH, 'exec'), namespace)
    return namespace
