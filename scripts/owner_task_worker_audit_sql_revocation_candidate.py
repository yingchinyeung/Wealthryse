"""Separate audit SQL trial with exact connection-denial qualification.

Both the consumed trial and all 825 source hashes remain unchanged. No generic
connection error or missing SQLSTATE is accepted as proof of revocation.
"""
from __future__ import annotations

import hashlib
import types

import owner_task_worker_audit_sql_candidate as previous

ROOT=previous.ROOT
BASE_WORKER=previous.BASE_WORKER
BASE_CONTROL=previous.BASE_CONTROL
BASE_WORKER_SHA='94411bab1c8ce0cf2ee8968b6d6cab5e5a574b1c6abd2430e5ef566d0068ff1f'
PREVIOUS_RECIPE_SHA='17e29a4b28e83da548eafd9743b18fc08efe1de0a32c9e44691502dbc74c4c68'
PATH='scripts/owner_task_worker_audit_sql_revocation_candidate.py'
CONTROL_PATH='scripts/deploy_owner_task_worker_audit_sql_revocation_trial.py'
TEST_PATH='tests/test_owner_task_worker_audit_sql_revocation_trial.py'
ADDITIONS=frozenset((PATH,CONTROL_PATH,TEST_PATH))
OPERATION='bf89d02a-19b9-45ed-bdd8-15e56860ccdd'
JOB='ibkrv2stg-ownaud2-20261005'
DATABASE='owner_codex_audit_sql_20261005_v2'
SCHEMA='owner_codex_audit_sql_trial_v2'
OWNER='ibkr_owner_audit_schema_v2_20261005'
CAPABILITY='ibkr_owner_audit_writer_v2_20261005'
LOGIN='ibkr_owner_audit_wlogin_v2_20261005'
READER_CAP='ibkr_owner_audit_reader_v2_20261005'
READER_LOGIN='ibkr_owner_audit_rlogin_v2_20261005'
MARKER='owner-codex-audit-sql-isolated-20261005-v2'
FAILURE_SHA,FAILURE_READBACK_SHA=previous.FAILURE_SHA,previous.FAILURE_READBACK_SHA
SOURCE_SHA,SOURCE_READBACK_SHA=previous.SOURCE_SHA,previous.SOURCE_READBACK_SHA
SQL_FAILURE_SHA='3386bd630e471562f1d4a937508132a619274467781a75eb5284b2dab3c28f7c'
SQL_FAILURE_READBACK_SHA='933377c87b546f468e98428f3fb17f9d01a0e968b6bf14b7713624711f04bb47'
REVOKED_HELPER='''def revocation_proof(error, admin):
    # Query-error SQLSTATE is not promised for a failed connection. Require
    # the actual finished libpq connection and exact server FATAL instead.
    require(isinstance(error, OperationalError) and error.pgresult is None
        and error.sqlstate in (None,'28000'), 'fence_audit_revocation_error_invalid')
    failed=error.pgconn
    require(failed is not None and failed.status==pq.ConnStatus.BAD
        and failed.host==host.HOST.encode() and failed.db==DATABASE.encode()
        and failed.user==READER_LOGIN.encode(), 'fence_audit_revocation_connection_invalid')
    options={o.keyword:o.val for o in failed.info if o.keyword in (b'sslmode',b'sslrootcert')}
    require(options=={b'sslmode':b'verify-full',b'sslrootcert':credential_parser.CA_PATH.encode()},
        'fence_audit_revocation_tls_configuration_invalid')
    message=failed.error_message
    require(type(message) is bytes and 0<len(message)<=4096, 'fence_audit_revocation_message_invalid')
    text=message.decode('utf-8','strict')
    fatals=re.findall(r'(?m)FATAL:[ \\t]+(?:28000:[ \\t]+)?([^\\r\\n]+)$',text)
    require(fatals==['role "'+READER_LOGIN+'" is not permitted to log in'],
        'fence_audit_revocation_wrong_cause')
    require(admin.execute('SELECT rolcanlogin FROM pg_roles WHERE rolname=%s',
        (READER_LOGIN,)).fetchone()==dict(rolcanlogin=False), 'fence_audit_revocation_catalog_invalid')
    # Emit selected metadata only, never connection details/password/message.
    return dict(method='catalog_and_exact_finished_connection_failure',
        authenticated_login=READER_LOGIN,role_login_disabled=True,
        fresh_connection_rejected=True,finished_connection_bound=True,
        exact_role_nologin_fatal=True,sslmode='verify-full',
        driver_error_class=type(error).__name__,driver_sqlstate=error.sqlstate)


'''


def frozen_recipe():
    if (hashlib.sha256((ROOT/BASE_WORKER).read_bytes()).hexdigest()!=BASE_WORKER_SHA
        or hashlib.sha256((ROOT/previous.PATH).read_bytes()).hexdigest()!=PREVIOUS_RECIPE_SHA):
        raise ValueError('fence_audit_revocation_predecessor_drift')


def worker_source(raw):
    frozen_recipe()
    if hashlib.sha256(raw).hexdigest()!=BASE_WORKER_SHA:
        raise ValueError('fence_audit_revocation_worker_drift')
    text=previous.worker_source(raw);replace=previous.previous.tls.replace_once
    old=previous.worker
    names=dict(OPERATION=OPERATION,JOB=JOB,DATABASE=DATABASE,SCHEMA=SCHEMA,
        OWNER=OWNER,CAPABILITY=CAPABILITY,LOGIN=LOGIN,READER_CAP=READER_CAP,
        READER_LOGIN=READER_LOGIN,MARKER=MARKER,PATH=PATH,
        PREFIX='OWNER_TASK_WORKER_AUDIT_SQL_TRIAL_V2 ')
    for key,value in names.items():
        text=replace(text,key+' = '+repr(getattr(old,key)),key+' = '+repr(value))
    text=replace(text,'from psycopg import sql','from psycopg import sql, OperationalError, pq')
    text=replace(text,'def qualify(connect, parameters, passwords, admin, *, passed):',
        REVOKED_HELPER+'def qualify(connect, parameters, passwords, admin, *, passed):')
    text=replace(text,"require(getattr(error,'sqlstate',None)=='28000','fence_audit_revocation_wrong_cause')",
        'proof=revocation_proof(error,admin)')
    text=replace(text,'return dict(denied_privilege_probes=denial_count,positive_tls=tls_records,',
        'return dict(denied_privilege_probes=denial_count,positive_tls=tls_records,reader_revocation=proof,')
    compile(text,PATH,'exec')
    return text


def worker_module():
    module=types.ModuleType('owner_audit_sql_revocation_candidate_worker')
    module.__file__,module.__package__=str(ROOT/PATH),''
    exec(compile(worker_source((ROOT/BASE_WORKER).read_bytes()),PATH,'exec'),module.__dict__)
    return module


def controller_source(raw):
    frozen_recipe();replace=previous.previous.tls.replace_once
    text=previous.controller_source(raw)
    text=replace(text,'import owner_task_worker_audit_sql_candidate as candidate',
        'import owner_task_worker_audit_sql_revocation_candidate as candidate\n'
        'import owner_task_worker_audit_sql_candidate as failed_sql\n'
        'failed_sql_control=failed_sql.controller_namespace()')
    text=replace(text,'len(before) == 821','len(before) == 825')
    text=replace(text,"accepted_fence_control['worker'].JOB, worker.JOB):",
        "accepted_fence_control['worker'].JOB, failed_sql_control['worker'].JOB, worker.JOB):")
    text=replace(text,"_, earlier = p.old.load('serviceaud-step3-worker-audit-source.json')",
        "_, earlier = failed_sql_control['load']('live-preflight')")
    text=replace(text,"    _, predecessor = p.load()\n",'''    _, predecessor = p.load()
    _, failed_sql_pf = failed_sql_control['load']('live-preflight')
    failed_raw, failed_ob = failed_sql_control['load']('live-observation')
    read_raw, failure_read = failed_sql_control['load']('failure-readback')
    require(failed_raw['protected_receipt']['sha256']==candidate.SQL_FAILURE_SHA
        and read_raw['protected_receipt']['sha256']==candidate.SQL_FAILURE_READBACK_SHA
        and failure_read['failure_receipt']==failed_raw['protected_receipt']
        and failure_read['sources']==failed_sql_pf['sources']==pf['predecessor_sources']
        and failure_read['live_sql_accepted'] is False
        and failure_read['step_3_complete'] is False and failure_read['step_4_started'] is False
        and failed_ob['status']=='step_3_fence_trial_failed_retained'
        and failed_ob['worker_result']['checks']==list(failed_sql_control['worker'].CHECKS[:6])
        and failed_ob['worker_result']['code']=='fence_audit_revocation_wrong_cause',
        'fence_audit_revocation_failure_unverified')
''')
    text=replace(text,"audit_source['sources'] == pf['predecessor_sources'] == audit_read['sources']",
        "audit_source['sources'] == failed_sql_pf['predecessor_sources'] == audit_read['sources']")
    text=replace(text,'    return identity\n', '''    failed_sql_client=failed_sql_control['Transport']()
    failed_sql_control['checked_job'](failed_sql_client.call('GET',failed_sql_control['JOB']),
        failed_sql_pf['plan'],identity)
    require(failed_sql_client.call('GET',failed_ob['native_execution']['id'])==failed_ob['native_execution'],
        'fence_audit_revocation_failure_native_drift')
    return identity
''')
    for before,after,count in (
        ('probes/owner-codex-audit-sql-20261005/','probes/owner-codex-audit-sql-v2-20261005/',1),
        ('serviceaud-step3-audit-sql-','serviceaud-step3-audit-sql-v2-',4)):
        if text.count(before)!=count:
            raise ValueError('fence_audit_revocation_controller_recipe_drift')
        text=text.replace(before,after)
    text=replace(text,"'fence_audit_sql_acceptance_invalid')",''' 'fence_audit_sql_acceptance_invalid')
        proof=record['reader_revocation']
        require(proof['method']=='catalog_and_exact_finished_connection_failure'
            and proof['authenticated_login']==worker.READER_LOGIN
            and proof['role_login_disabled'] is True and proof['fresh_connection_rejected'] is True
            and proof['finished_connection_bound'] is True and proof['exact_role_nologin_fatal'] is True
            and proof['sslmode']=='verify-full' and proof['driver_error_class']=='OperationalError'
            and proof['driver_sqlstate'] in (None,'28000'), 'fence_audit_revocation_proof_invalid')''')
    compile(text,CONTROL_PATH,'exec')
    return text


def controller_namespace():
    namespace=dict(__name__='owner_audit_sql_revocation_control',__file__=str(ROOT/CONTROL_PATH),__package__='')
    exec(compile(controller_source((ROOT/BASE_CONTROL).read_bytes()),CONTROL_PATH,'exec'),namespace)
    return namespace
