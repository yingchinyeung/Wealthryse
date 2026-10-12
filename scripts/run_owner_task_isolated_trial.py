"""Source-only synthetic 059 trial under a fresh, restricted non-owner LOGIN.

A separately reviewed trusted wrapper prepares/cleans the disposable database
and supplies fresh restricted connections plus a zero-argument fixture-revocation
callback. This harness accepts no URL/password/admin connection, performs no
setup/grants/cleanup and never connects on import. Every denied SQL probe rolls
back even if unexpectedly permitted. Receipt verification is explicitly
synthetic; these tests do NOT prove signed-owner authentication, real protected
receipt readback, managed identity or model/CLI credential isolation.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
import re
import threading
import uuid

import psycopg
from psycopg import sql
from ibkr_etl import owner_codex_isolated_target as isolated

from ibkr_etl.db import PostgresConnection
from ibkr_etl.owner_codex_tasks import (AUDIT_NAMESPACE, OwnerAuthority, OwnerCodexTasks,
    OwnerRef, OwnerTaskError, ServerValidatedTaskRequest, WorkerAuthority, _hash)


HOST = isolated.HOST
DATABASE = isolated.DATABASE
SCHEMA = isolated.SCHEMA
CAPABILITY = isolated.CAPABILITY
LOGIN = isolated.LOGIN
SCHEMA_OWNER = isolated.SCHEMA_OWNER
MARKER = isolated.MARKER
TENANT_A = '00000000-0000-0000-0000-000000000001'
TENANT_B = '00000000-0000-0000-0000-000000000101'
OWNER_A = OwnerRef(TENANT_A, '00000000-0000-0000-0000-000000000201')
OWNER_B = OwnerRef(TENANT_A, '00000000-0000-0000-0000-000000000202')
OTHER_TENANT_OWNER = OwnerRef(TENANT_B, OWNER_A.object_id)
WORKER = '00000000-0000-0000-0000-000000000301'
SERVICE_PRINCIPAL = '00000000-0000-0000-0000-000000000302'
INPUT_SHA = 'a'*64
REQUEST = ServerValidatedTaskRequest(('SYNTH_A',), INPUT_SHA)
TASK_TABLES = ('owner_codex_sessions','owner_codex_tasks','owner_codex_task_events')
CHECKS = ('fresh_login_non_owner_catalog', 'readonly_fixture_authority_and_service_crud',
          'tenant_owner_missing_scope_and_pool_reset', 'non_owner_privilege_denials',
          'idempotency_conflict_and_revision_cas', 'cancelled_outcome_synthetic_receipts',
          'fresh_database_authority_revocation', 'final_non_owner_catalog_readback')


class TrialFailure(ValueError):
    def __init__(self, code):
        self.code = code if type(code) is str and re.fullmatch('[a-z][a-z0-9_]{0,79}',code) else 'restricted_trial_failed'
        super().__init__(self.code)


class UnexpectedAllowed(Exception):
    """Raised inside a transaction so unexpected privilege success rolls back."""


def require(value, code):
    if not value:
        raise TrialFailure(code)


def _require_installer(installer_identity):
    require(type(installer_identity) is str
        and re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,62}',installer_identity)
        and installer_identity == isolated.ADMIN,'trusted_installer_identity_required')


def _permission_manifest(raw,*,installer_identity):
    # Lazy import keeps this source harness inert until the reviewed permission
    # adapter is available. No caller may supply a different target or verifier.
    from owner_task_isolated_permissions import PermissionDenied, PermissionTarget, verify_permissions
    target = PermissionTarget()
    require((target.database,target.schema,target.schema_owner,target.capability,target.login) ==
            (DATABASE,SCHEMA,SCHEMA_OWNER,CAPABILITY,LOGIN), 'permission_target_differs')
    _require_installer(installer_identity)
    try:
        manifest = verify_permissions(raw,target,installer_identity=installer_identity)
    except PermissionDenied as error:
        raise TrialFailure(error.code) from None
    expected = dict(status='passed',fresh_login_verified=True,metadata_acl_verified=True,
        end_user_authentication_verified=False,protected_receipts_verified=False,
        source_rows_read=0,source_rows_written=0,grants_executed=0,runtime_activated=False)
    require(type(manifest) is dict and set(manifest) == set(expected)|
        {'permission_manifest_sha256','column_privileges_verified'}
        and all(type(manifest[key]) is type(value) and manifest[key] == value for key,value in expected.items())
        and type(manifest['permission_manifest_sha256']) is str
        and re.fullmatch('[0-9a-f]{64}',manifest['permission_manifest_sha256'])
        and type(manifest['column_privileges_verified']) is int
        and 0 < manifest['column_privileges_verified'] <= 10000,'permission_manifest_missing')
    return manifest


def _preflight(raw,*,installer_identity):
    _require_installer(installer_identity)
    require(raw.autocommit is True and raw.info.transaction_status == 0,
            'fresh_idle_restricted_connection_required')
    require(raw.info.host == HOST and raw.info.dbname == DATABASE and raw.info.user == LOGIN,
            'underlying_login_or_database_differs')
    require(raw.pgconn.ssl_in_use is True,'encrypted_restricted_connection_required')
    identity = raw.execute("""SELECT current_database() AS database,current_user::text AS role,
        session_user::text AS session_identity,current_setting('search_path') AS search_path,
        current_setting('server_version') AS server_version,
        current_setting('server_version_num')::integer AS server_version_num,
        current_setting('app.tenant_id',true) AS tenant_scope,
        current_setting('app.owner_object_id',true) AS owner_scope,
        pg_my_temp_schema() AS temp_schema""").fetchone()
    require(identity['database'] == DATABASE and identity['role'] == CAPABILITY
        and identity['session_identity'] == LOGIN and identity['search_path'] == SCHEMA+', pg_catalog'
        and identity['tenant_scope'] in (None,'') and identity['owner_scope'] in (None,'')
        and identity['temp_schema'] == 0, 'fresh_restricted_identity_or_scope_differs')
    require(type(identity['server_version_num']) is int and 160000 <= identity['server_version_num'] < 170000,
            'postgresql_16_required')
    markers = raw.execute("""SELECT shobj_description(d.oid,'pg_database') AS database_marker,
        obj_description(n.oid,'pg_namespace') AS schema_marker,
        shobj_description(l.oid,'pg_authid') AS login_marker,
        shobj_description(c.oid,'pg_authid') AS capability_marker,
        shobj_description(o.oid,'pg_authid') AS owner_marker
        FROM pg_database d CROSS JOIN pg_namespace n CROSS JOIN pg_roles l
        CROSS JOIN pg_roles c CROSS JOIN pg_roles o
        WHERE d.datname=%s AND n.nspname=%s AND l.rolname=%s AND c.rolname=%s AND o.rolname=%s""",
        (DATABASE,SCHEMA,LOGIN,CAPABILITY,SCHEMA_OWNER)).fetchone()
    require(markers is not None and set(markers) == {'database_marker','schema_marker','login_marker',
        'capability_marker','owner_marker'} and all(value == MARKER for value in markers.values()),
        'registered_disposable_markers_differ')
    manifest = _permission_manifest(raw,installer_identity=installer_identity)
    return dict(server_version=identity['server_version'],
                permission_manifest_sha256=manifest['permission_manifest_sha256'])


@contextmanager
def _connection(factory, tenant=TENANT_A,*,installer_identity):
    _require_installer(installer_identity)
    raw = factory()
    try:
        _preflight(raw,installer_identity=installer_identity)
        raw.autocommit = False
        if tenant is not None:
            _bind_tenant(raw,tenant)
        yield raw,PostgresConnection(raw)
    finally:
        try:
            raw.rollback()
        finally:
            raw.close()


def _bind_tenant(raw, tenant):
    require(raw.info.transaction_status == 0, 'idle_scope_reset_required')
    raw.execute("SELECT set_config('app.tenant_id',%s,false)",(tenant,))
    raw.commit()


def _scope(raw, tenant=TENANT_A, owner=OWNER_A.object_id):
    raw.execute("SELECT set_config('app.tenant_id',%s,true)",(tenant,))
    raw.execute("SELECT set_config('app.owner_object_id',%s,true)",(owner,))


class SyntheticArchive:
    """Only an in-memory binding oracle; never a real protected receipt."""
    def __init__(self):
        self.records = {}
        self.lock = threading.Lock()

    def add(self,binding):
        reference = _hash({'synthetic_fixture':True,'binding':binding})
        with self.lock:
            self.records[reference] = dict(binding)
        return reference

    def __call__(self,reference,binding):
        with self.lock:
            require(self.records.get(reference) == binding,'synthetic_receipt_binding_failed')
        return dict(record_sha256=reference,binding_sha256=_hash(binding),namespace=AUDIT_NAMESPACE,
                    protected_archive_verified=True,database_committed=True)


def _service(connection,archive,clock):
    def authorize(owner):
        require(owner in (OWNER_A,OWNER_B,OTHER_TENANT_OWNER),'unregistered_synthetic_owner')
        row = connection.execute("""SELECT active,role_name,registered_subject_ids,authorized_subject_ids
            FROM owner_task_authority_fixture WHERE tenant_id=? AND owner_object_id=?""",
            (owner.tenant_id,owner.object_id)).fetchone()
        if row is None or row['active'] is not True or row['role_name'] != 'owner':
            return None
        require(row['registered_subject_ids'] == ['SYNTH_A','SYNTH_B']
                and row['authorized_subject_ids'] == ['SYNTH_A','SYNTH_B'],'synthetic_authority_fixture_differs')
        return OwnerAuthority(owner,tuple(row['registered_subject_ids']),tuple(row['authorized_subject_ids']),
            _hash(dict(tenant_id=owner.tenant_id,owner_object_id=owner.object_id,
                       registered=row['registered_subject_ids'],authorized=row['authorized_subject_ids'])))

    def authorize_worker(tenant,worker):
        require(tenant in (TENANT_A,TENANT_B) and worker == WORKER,'unregistered_synthetic_worker')
        row = connection.execute("""SELECT service_principal_id,enabled FROM owner_task_worker_fixture
            WHERE tenant_id=? AND worker_id=?""",(tenant,worker)).fetchone()
        if row is None or row['enabled'] is not True:
            return None
        require(str(row['service_principal_id']) == SERVICE_PRINCIPAL,'synthetic_worker_fixture_differs')
        return WorkerAuthority(tenant,worker,str(row['service_principal_id']))

    return OwnerCodexTasks(connection,authorize=authorize,authorize_worker=authorize_worker,
                           verify_receipt=archive,clock=clock)


def _request(service,owner=OWNER_A,*,session=None,key=None,request=REQUEST):
    session = session or service.create_session(owner,idempotency_key=str(uuid.uuid4()))['session_id']
    return service.submit(owner,session_id=session,idempotency_key=key or str(uuid.uuid4()),request=request)


def _row(raw,connection,task,owner=OWNER_A):
    with raw.transaction():
        _scope(raw,owner.tenant_id,owner.object_id)
        row = connection.execute('SELECT * FROM owner_codex_tasks WHERE task_id=?',(task['task_id'],)).fetchone()
    require(row is not None,'synthetic_task_not_visible')
    return dict(row)


def _denied(operation,expected):
    try:
        operation()
    except OwnerTaskError as error:
        require(error.code == expected,'unexpected_service_denial')
    else:
        raise TrialFailure('service_denial_not_enforced')


def _sql_denied(raw,statement,parameters=(),*,states=('42501',),row_security_off=False):
    """Every unexpectedly allowed operation rolls back, including role/DDL."""
    try:
        with raw.transaction():
            _scope(raw)
            require(type(row_security_off) is bool,'explicit_row_security_probe_required')
            if row_security_off:
                raw.execute('SET LOCAL row_security=off')
            raw.execute(statement,parameters)
            # Deferred integrity checks must be tested before the forced
            # rollback; otherwise a deferred-event violation would be hidden.
            raw.execute('SET CONSTRAINTS ALL IMMEDIATE')
            raise UnexpectedAllowed()
    except psycopg.Error as error:
        require(error.sqlstate in states,'unexpected_sql_denial')
    except UnexpectedAllowed:
        raise TrialFailure('sql_denial_not_enforced') from None
    require(raw.info.transaction_status == 0,'denial_left_open_transaction')


def _crud_and_scopes(factory,archive,clock,*,installer_identity):
    fixtures = []
    for owner in (OWNER_A,OWNER_B,OTHER_TENANT_OWNER):
        with _connection(factory,owner.tenant_id,installer_identity=installer_identity) as (raw,connection):
            service = _service(connection,archive,clock)
            session_key = str(uuid.uuid4())
            session = service.create_session(owner,idempotency_key=session_key)
            require(service.create_session(owner,idempotency_key=session_key) == session,'session_idempotency_failed')
            task = _request(service,owner,session=session['session_id'])
            require(service.get(owner,task_id=task['task_id']) == task,'task_readback_failed')
            require(service.list_tasks(owner,session_id=session['session_id']) == [task],'task_list_scope_failed')
            require(len(service.events(owner,task_id=task['task_id'])) == 1,'queued_event_count_differs')
            with raw.transaction():
                scope = raw.execute("SELECT current_setting('app.owner_object_id',true) AS owner").fetchone()
                require(scope['owner'] in (None,''),'local_owner_scope_leaked')
            fixtures.append((owner,task))
    # Query without the service's redundant WHERE owner predicates: this proves
    # the database policies, not merely that the application filters its reads.
    for owner,task in fixtures:
        with _connection(factory,owner.tenant_id,installer_identity=installer_identity) as (raw,_):
            with raw.transaction():
                _scope(raw,owner.tenant_id,owner.object_id)
                for table in TASK_TABLES:
                    row = raw.execute(sql.SQL('SELECT count(*) AS n FROM {}').format(sql.Identifier(table))).fetchone()
                    require(row['n'] == 1,'cross_scope_rows_visible')
                for foreign_owner,foreign_task in fixtures:
                    row = raw.execute('SELECT count(*) AS n FROM owner_codex_tasks WHERE task_id=%s',
                        (foreign_task['task_id'],)).fetchone()
                    require(row['n'] == (1 if foreign_owner == owner else 0),'cross_scope_task_visible')
    with _connection(factory,None,installer_identity=installer_identity) as (raw,connection):
        _denied(lambda:_service(connection,archive,clock).create_session(OWNER_A,idempotency_key=str(uuid.uuid4())),
                'access_denied')
        for tenant,owner in (('',OWNER_A.object_id),(TENANT_A,''),('','')):
            with raw.transaction():
                _scope(raw,tenant,owner)
                for table in TASK_TABLES:
                    row = raw.execute(sql.SQL('SELECT count(*) AS n FROM {}').format(sql.Identifier(table))).fetchone()
                    require(row['n'] == 0,'missing_scope_rows_visible')
    with _connection(factory,installer_identity=installer_identity) as (raw,connection):
        own = fixtures[0][1]
        service = _service(connection,archive,clock)
        _denied(lambda:service.get(OWNER_B,task_id=own['task_id']),'not_found')
        _bind_tenant(raw,'')
        _denied(lambda:service.get(OWNER_A,task_id=own['task_id']),'access_denied')
        _bind_tenant(raw,TENANT_B)
        _denied(lambda:service.get(OWNER_A,task_id=own['task_id']),'access_denied')
        _denied(lambda:service.get(OTHER_TENANT_OWNER,task_id=own['task_id']),'not_found')
        _bind_tenant(raw,TENANT_A)
        require(service.get(OWNER_A,task_id=own['task_id']) == own,'pool_scope_rebind_failed')
    return fixtures[0][1]


def _privilege_denials(factory,task,*,installer_identity):
    table = sql.Identifier(SCHEMA,'owner_codex_tasks')
    statements = (
        sql.SQL('CREATE TABLE {} (value integer)').format(sql.Identifier(SCHEMA,'owner_task_forbidden_fixture')),
        sql.SQL('CREATE SCHEMA {}').format(sql.Identifier('owner_codex_forbidden_20261004')),
        sql.SQL('CREATE TEMP TABLE {} (value integer)').format(sql.Identifier('owner_task_forbidden_temp')),
        sql.SQL('ALTER TABLE {} DISABLE ROW LEVEL SECURITY').format(table),
        sql.SQL('DROP TABLE {}').format(table),
        sql.SQL('SET ROLE {}').format(sql.Identifier(SCHEMA_OWNER)),
        sql.SQL('SET SESSION AUTHORIZATION {}').format(sql.Identifier(SCHEMA_OWNER)),
        sql.SQL('ALTER ROLE {} SUPERUSER').format(sql.Identifier(LOGIN)),
        sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier('ibkr_owner_codex_forbidden_20261004')),
        sql.SQL('GRANT {} TO {}').format(sql.Identifier(SCHEMA_OWNER),sql.Identifier(LOGIN)),
        sql.SQL('SELECT * FROM {}').format(sql.Identifier(SCHEMA,'tenants')),
        sql.SQL('INSERT INTO {} VALUES (%s)').format(sql.Identifier(SCHEMA,'tenants')),
        sql.SQL('UPDATE {} SET active=FALSE').format(sql.Identifier(SCHEMA,'owner_task_authority_fixture')),
        sql.SQL('UPDATE {} SET enabled=FALSE').format(sql.Identifier(SCHEMA,'owner_task_worker_fixture')),
        sql.SQL('UPDATE {} SET input_sha256=%s WHERE task_id=%s').format(table),
        sql.SQL('DELETE FROM {} WHERE task_id=%s').format(table),
        sql.SQL('UPDATE {} SET created_at=created_at').format(sql.Identifier(SCHEMA,'owner_codex_sessions')),
        sql.SQL('DELETE FROM {}').format(sql.Identifier(SCHEMA,'owner_codex_task_events')),
        sql.SQL('SELECT {}()').format(sql.Identifier(SCHEMA,'guard_owner_codex_task')),
        sql.SQL('SET LOCAL session_replication_role=replica'),
    )
    params = {11:(str(uuid.uuid4()),),14:('b'*64,task['task_id']),15:(task['task_id'],)}
    with _connection(factory,installer_identity=installer_identity) as (raw,connection):
        for index,statement in enumerate(statements):
            _sql_denied(raw,statement,params.get(index,()))
        _sql_denied(raw,sql.SQL('SELECT * FROM {}').format(table),row_security_off=True)
        _sql_denied(raw,sql.SQL('UPDATE {} SET state=\'cancelled\',cancel_requested_at=clock_timestamp(), '
            'revision=revision+1,updated_at=clock_timestamp() WHERE task_id=%s').format(table),
            (task['task_id'],),states=('P0001',))
        row = _row(raw,connection,task)
        require(row['state'] == 'queued' and row['revision'] == 1,'denied_probe_changed_task')
    return len(statements)+1


def _idempotency_and_cas(factory,archive,clock,*,installer_identity):
    with _connection(factory,installer_identity=installer_identity) as (_,connection):
        session = _service(connection,archive,clock).create_session(OWNER_A,idempotency_key=str(uuid.uuid4()))['session_id']
    key = str(uuid.uuid4())
    barrier = threading.Barrier(2)
    def submit():
        with _connection(factory,installer_identity=installer_identity) as (_,connection):
            service = _service(connection,archive,clock)
            barrier.wait(timeout=15)
            return _request(service,session=session,key=key)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = [pool.submit(submit),pool.submit(submit)]
        first,second = [future.result(timeout=30) for future in pending]
    require(first == second,'concurrent_task_idempotency_failed')
    with _connection(factory,installer_identity=installer_identity) as (_,connection):
        service = _service(connection,archive,clock)
        require(len(service.events(OWNER_A,task_id=first['task_id'])) == 1,'duplicate_queued_events')
        _denied(lambda:_request(service,session=session,key=key,
            request=ServerValidatedTaskRequest(('SYNTH_B',),INPUT_SHA)),'idempotency_conflict')
        cancelled = service.cancel(OWNER_A,task_id=first['task_id'],expected_revision=1)
        require(cancelled['state'] == 'cancelled' and cancelled['delivery_available'] is False,'queued_cancel_failed')
        _denied(lambda:service.cancel(OWNER_A,task_id=first['task_id'],expected_revision=1),'revision_conflict')


def _cancelled_synthetic_outcome(factory,archive,clock,*,installer_identity):
    with _connection(factory,installer_identity=installer_identity) as (raw,connection):
        service = _service(connection,archive,clock)
        task = _request(service)
        intent = archive.add(service.receipt_binding(_row(raw,connection,task),'intent'))
        task = service.attach_intent(OWNER_A,task_id=task['task_id'],expected_revision=1,
            receipt_sha256=intent,worker_id=WORKER)
        lease = service.claim(OWNER_A,task_id=task['task_id'],expected_revision=task['revision'],worker_id=WORKER)
        task = service.heartbeat(OWNER_A,task_id=task['task_id'],expected_revision=lease['task']['revision'],
            lease_token=lease['lease_token'],worker_id=WORKER)
        task = service.cancel(OWNER_A,task_id=task['task_id'],expected_revision=task['revision'])
        require(task['state'] == 'cancel_requested' and task['delivery_available'] is False,'running_cancel_failed')
        _denied(lambda:service.finish(OWNER_A,task_id=task['task_id'],expected_revision=task['revision'],
            lease_token=lease['lease_token'],worker_id=WORKER,outcome='succeeded',
            outcome_receipt_sha256='b'*64,result_sha256=INPUT_SHA),'transition_denied')
        stopped = archive.add(service.receipt_binding(_row(raw,connection,task),'outcome',outcome='cancelled'))
        task = service.finish(OWNER_A,task_id=task['task_id'],expected_revision=task['revision'],
            lease_token=lease['lease_token'],worker_id=WORKER,outcome='cancelled',outcome_receipt_sha256=stopped)
        require(task['state'] == 'cancelled' and task['result_sha256'] is None,'cancelled_result_not_withheld')
        require(len(service.events(OWNER_A,task_id=task['task_id'])) == task['revision'],'event_revision_gap')


def expected_revocation():
    return dict(status='passed',database=DATABASE,schema=SCHEMA,marker=MARKER,
        fixture='owner_task_authority_fixture',tenant_id=TENANT_A,owner_object_id=OWNER_A.object_id,
        active=False,rows_changed=1,mutation_role=SCHEMA_OWNER,synthetic=True)


def _revocation(factory,revoke_authority,archive,clock,task,*,installer_identity):
    with _connection(factory,installer_identity=installer_identity) as (raw,connection):
        service = _service(connection,archive,clock)
        require(service.get(OWNER_A,task_id=task['task_id'])['state'] == 'queued','pre_revocation_task_differs')
        acknowledgment = revoke_authority()
        expected = expected_revocation()
        require(type(acknowledgment) is dict and set(acknowledgment) == set(expected)
            and all(type(acknowledgment[key]) is type(value) and acknowledgment[key] == value
                    for key,value in expected.items()),'trusted_revocation_acknowledgment_differs')
        with raw.transaction():
            row = raw.execute('SELECT active FROM owner_task_authority_fixture '
                'WHERE tenant_id=%s AND owner_object_id=%s',(TENANT_A,OWNER_A.object_id)).fetchone()
            require(row is not None and row['active'] is False,'database_authority_revocation_not_visible')
        _denied(lambda:service.get(OWNER_A,task_id=task['task_id']),'access_denied')
        _denied(lambda:service.cancel(OWNER_A,task_id=task['task_id'],expected_revision=1),'access_denied')
    with _connection(factory,installer_identity=installer_identity) as (_,connection):
        service = _service(connection,archive,clock)
        _denied(lambda:service.get(OWNER_A,task_id=task['task_id']),'access_denied')
        _denied(lambda:service.create_session(OWNER_A,idempotency_key=str(uuid.uuid4())),'access_denied')


def verify_trial(connection_factory,*,revoke_authority,installer_identity):
    """Bounded synthetic trial. Never retries uncertainty or cleans the database."""
    result = dict(status='failed',database=DATABASE,schema=SCHEMA,session_login=LOGIN,
        effective_role=CAPABILITY,checks=[],scope='synthetic_fresh_restricted_login_non_owner_postgres',
        fresh_login_verified=False,non_owner_privilege_boundary_verified=False,
        signed_owner_authentication_verified=False,protected_archive_live_verified=False,
        receipt_verifier_is_synthetic=True,managed_identity_verified=False,
        model_credential_containment_verified=False,source_rows_read=0,source_rows_written=0,
        production_source_access_acceptance=False,service_grants_created=0,model_calls=0,
        agents_activated=0,production_migration_installed=False,cleanup_performed=False,
        capability_authority_fixture_writes=0,fresh_database_authority_revocation_verified=False,
        automatic_retry=False)
    try:
        _require_installer(installer_identity)
        require(callable(connection_factory) and callable(revoke_authority),'trusted_callbacks_required')
        with _connection(connection_factory,installer_identity=installer_identity) as (raw,_):
            result.update(_permission_manifest_version(raw,installer_identity=installer_identity))
        result['checks'].append(CHECKS[0])
        archive,now = SyntheticArchive(),datetime.now(timezone.utc)
        clock = lambda:now
        task = _crud_and_scopes(connection_factory,archive,clock,installer_identity=installer_identity)
        result['checks'].extend(CHECKS[1:3])
        result['denied_privilege_probe_count'] = _privilege_denials(connection_factory,task,installer_identity=installer_identity)
        result['checks'].append(CHECKS[3])
        _idempotency_and_cas(connection_factory,archive,clock,installer_identity=installer_identity)
        result['checks'].append(CHECKS[4])
        _cancelled_synthetic_outcome(connection_factory,archive,clock,installer_identity=installer_identity)
        result['checks'].append(CHECKS[5])
        _revocation(connection_factory,revoke_authority,archive,clock,task,installer_identity=installer_identity)
        result['checks'].append(CHECKS[6])
        with _connection(connection_factory,installer_identity=installer_identity) as (raw,_):
            final = _permission_manifest_version(raw,installer_identity=installer_identity)
            require(final['permission_manifest_sha256'] == result['permission_manifest_sha256'],
                    'final_permission_manifest_differs')
        result['checks'].append(CHECKS[7])
        result.update(status='passed',fresh_login_verified=True,non_owner_privilege_boundary_verified=True,
                      fresh_database_authority_revocation_verified=True,check_manifest_sha256=_hash(result['checks']))
    except Exception as error:
        result['reason'] = error.code if isinstance(error,TrialFailure) else 'restricted_postgresql_trial_failed'
        state = getattr(error,'sqlstate',None)
        if type(state) is str and re.fullmatch('[0-9A-Z]{5}',state):
            result['sqlstate'] = state
    return result


def _permission_manifest_version(raw,*,installer_identity):
    manifest = _permission_manifest(raw,installer_identity=installer_identity)
    version = raw.execute("SELECT current_setting('server_version') AS version").fetchone()['version']
    raw.commit()
    return dict(server_version=version,permission_manifest_sha256=manifest['permission_manifest_sha256'])
