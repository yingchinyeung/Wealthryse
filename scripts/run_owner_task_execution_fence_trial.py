"""Fixed synthetic PostgreSQL fence qualification on the existing private host.

Trusted installer only: no Codex/model runs in this process. Passwords exist
only here, never in argv/results. A new database/three roles are created once;
existing databases and roles are unchanged. No retry, cleanup, received-data
access, production migration, real audit-receipt or website acceptance occurs.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import json
import re
import secrets
import threading

from psycopg import sql
from psycopg.rows import dict_row
from ibkr_etl import owner_codex_isolated_target as host
from ibkr_etl.db import PostgresConnection, _hybrid_row_factory
from ibkr_etl.owner_codex_tasks import OwnerRef, ServerValidatedTaskRequest, WorkerAuthority
from ibkr_etl.owner_codex_worker import AUDIT_NAMESPACE, ProtectedWorkerReceipt, ReviewWork, WorkerError, WorkerEvidence, _digest
from ibkr_etl.owner_codex_execution_fence import ExecutionFenceStore
import run_owner_task_isolated_provider_observation as credential_parser
import prepare_owner_task_isolated_trial as predecessor

OPERATION = 'bb07cb08-106a-43f5-ac36-1d8a2f1f2ad1'
JOB = 'ibkrv2stg-ownfence-20261005'
JOB_ID = host.BASE + 'Microsoft.App/jobs/' + JOB
DATABASE = 'owner_codex_fence_20261005'
SCHEMA = 'owner_codex_fence_trial'
OWNER = 'ibkr_owner_fence_schema_20261005'
CAPABILITY = 'ibkr_owner_fence_dml_20261005'
LOGIN = 'ibkr_owner_fence_login_20261005'
MARKER = 'owner-codex-fence-isolated-20261005-v1'
ROLES = (OWNER, CAPABILITY, LOGIN)
TABLES = ('owner_codex_execution_fences', 'owner_codex_execution_runtimes', 'owner_codex_execution_fence_events')
PATH = 'scripts/run_owner_task_execution_fence_trial.py'
DDL_PATH = 'scripts/owner_task_execution_fence_schema.sql'
DDL_SHA = '4fbbb64498a3fb46fb8cdeb652d33672130fb5db7ed4f6708d9a711d7b15596e'
CONSTRAINT_PATH = 'scripts/owner_task_execution_fence_constraints.sql'
PREFIX = 'OWNER_TASK_EXECUTION_FENCE_TRIAL_V1 '
TENANT = '00000000-0000-0000-0000-000000000001'
OTHER_TENANT = '00000000-0000-0000-0000-000000000101'
OWNER_REF = OwnerRef(TENANT, '00000000-0000-0000-0000-000000000201')
WORKER = '00000000-0000-0000-0000-000000000301'
OTHER_WORKER = '00000000-0000-0000-0000-000000000303'
PRINCIPAL = '00000000-0000-0000-0000-000000000302'
CHECKS = ('fresh_non_owner_login_tls_and_acl', 'durable_reservation_reconstruction',
    'three_connection_reservation_race', 'tenant_worker_rls_and_scope_reset',
    'immutable_bindings_and_append_only_denials', 'launch_and_stopped_state_guards',
    'persistent_quarantine_and_rearm_denials', 'fresh_worker_authority_revocation',
    'predecessor_database_role_metadata_unchanged')


def require(value, code):
    if not value:
        raise ValueError(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def uid(number):
    return f'00000000-0000-0000-0000-{number:012d}'


def work(number, *, worker=WORKER):
    return ReviewWork(OWNER_REF, uid(1000 + number), uid(2000 + number), worker,
        uid(3000 + number), 1, 'b' * 64, ServerValidatedTaskRequest(('SYNTH_FENCE',), 'a' * 64))


def intent(item):
    return WorkerEvidence(item, 'intent', None, None, 'not_started', False, 'c' * 64, 'd' * 64, PRINCIPAL)


def synthetic_receipt(evidence):
    # Explicit fixture, NOT actual independent audit/archival acceptance.
    return ProtectedWorkerReceipt(_digest(['synthetic_fence_trial', evidence.payload()]),
        _digest(evidence.payload()), AUDIT_NAMESPACE, True, True, True, True)


def outcome(item, *, launched=False, receipt=None):
    return WorkerEvidence(item, 'outcome', 'cancelled', 'cancel_requested',
        'stopped' if launched else 'not_started', launched, intent_receipt_sha256=receipt)


def connect_exact(connect, parameters, database, *, restricted=False):
    require(database in ('postgres', DATABASE, host.DATABASE), 'fence_database_unregistered')
    options = '-c search_path=pg_catalog -c statement_timeout=45000 -c lock_timeout=3000 -c idle_in_transaction_session_timeout=45000'
    if restricted:
        require(database == DATABASE and parameters['user'] == LOGIN, 'fence_login_unregistered')
        options += ' -c role=' + CAPABILITY + ' -c search_path=' + SCHEMA + ',pg_catalog'
    raw = connect(**dict(parameters, dbname=database), connect_timeout=20, autocommit=True,
        row_factory=dict_row, options=options, application_name='owner-fence-isolated-trial')
    require(raw.info.host == host.HOST and raw.info.dbname == database and raw.pgconn.ssl_in_use is True
        and raw.info.user == (LOGIN if restricted else host.ADMIN)
        and raw.info.get_parameters().get('sslmode') == 'verify-full'
        and raw.info.get_parameters().get('sslrootcert') == credential_parser.CA_PATH,
        'fence_transport_invalid')
    return raw


def metadata(raw):
    return dict(database=raw.execute("SELECT datname,pg_get_userbyid(datdba) AS owner,shobj_description(oid,'pg_database') AS marker FROM pg_database WHERE datname=%s", (DATABASE,)).fetchone(),
        roles=raw.execute('SELECT rolname FROM pg_roles WHERE rolname=ANY(%s) ORDER BY rolname', (list(ROLES),)).fetchall())


def install(admin, connect, parameters, ddl, constraints, password):
    require(metadata(admin) == dict(database=None, roles=[]), 'fence_names_already_exist')
    before = predecessor.scope_metadata(admin)
    predecessor.validate_registered_metadata(before, login_enabled=True)
    with admin.transaction():
        for name in ROLES:
            admin.execute(sql.SQL('CREATE ROLE {} NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT {}')
                .format(sql.Identifier(name), sql.Literal(4 if name == LOGIN else -1)))
            admin.execute(sql.SQL('COMMENT ON ROLE {} IS {}').format(sql.Identifier(name), sql.Literal(MARKER)))
        admin.execute(sql.SQL('GRANT {} TO CURRENT_USER WITH ADMIN FALSE, INHERIT FALSE, SET TRUE').format(sql.Identifier(OWNER)))
        admin.execute(sql.SQL('GRANT {} TO {} WITH ADMIN FALSE, INHERIT FALSE, SET TRUE').format(sql.Identifier(CAPABILITY), sql.Identifier(LOGIN)))
    admin.execute(sql.SQL('CREATE DATABASE {} OWNER {} TEMPLATE template0').format(sql.Identifier(DATABASE), sql.Identifier(OWNER)))
    with admin.transaction():
        admin.execute(sql.SQL('SET LOCAL ROLE {}').format(sql.Identifier(OWNER)))
        admin.execute(sql.SQL('COMMENT ON DATABASE {} IS {}').format(sql.Identifier(DATABASE), sql.Literal(MARKER)))
        admin.execute(sql.SQL('REVOKE ALL ON DATABASE {} FROM PUBLIC').format(sql.Identifier(DATABASE)))
        for name in (host.ADMIN, LOGIN):
            admin.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(sql.Identifier(DATABASE), sql.Identifier(name)))
    raw = connect_exact(connect, parameters, DATABASE)
    try:
        with raw.transaction():
            identity = raw.execute("SELECT session_user::text AS session_user,current_user::text AS current_user,pg_get_userbyid(nspowner) AS public_schema_owner FROM pg_namespace WHERE nspname='public'").fetchone()
            require(identity == dict(session_user=host.ADMIN, current_user=host.ADMIN,
                public_schema_owner='azure_pg_admin') and raw.info.host == host.HOST
                and raw.info.dbname == DATABASE, 'fence_public_schema_installer_invalid')
            raw.execute('REVOKE ALL ON SCHEMA public FROM PUBLIC')
            raw.execute(sql.SQL('SET LOCAL ROLE {}').format(sql.Identifier(OWNER)))
            raw.execute(sql.SQL('CREATE SCHEMA {} AUTHORIZATION {}').format(sql.Identifier(SCHEMA), sql.Identifier(OWNER)))
            raw.execute(sql.SQL('COMMENT ON SCHEMA {} IS {}').format(sql.Identifier(SCHEMA), sql.Literal(MARKER)))
            raw.execute(sql.SQL('REVOKE ALL ON SCHEMA {} FROM PUBLIC').format(sql.Identifier(SCHEMA)))
            raw.execute(sql.SQL('SET LOCAL search_path TO {},pg_catalog').format(sql.Identifier(SCHEMA)))
            raw.execute("CREATE FUNCTION app_tenant_id() RETURNS UUID LANGUAGE SQL STABLE AS $$ SELECT NULLIF(current_setting('app.tenant_id',true),'')::UUID $$")
            raw.execute('REVOKE ALL ON FUNCTION app_tenant_id() FROM PUBLIC')
            raw.execute('CREATE TABLE worker_authority_fixture (tenant_id UUID NOT NULL,worker_id UUID NOT NULL,service_principal_id UUID NOT NULL,enabled BOOLEAN NOT NULL,PRIMARY KEY(tenant_id,worker_id))')
            for worker in (WORKER, OTHER_WORKER):
                raw.execute('INSERT INTO worker_authority_fixture VALUES (%s,%s,%s,TRUE)', (TENANT, worker, PRINCIPAL))
            raw.execute(ddl)
            raw.execute(constraints)
            raw.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(SCHEMA), sql.Identifier(CAPABILITY)))
            raw.execute(sql.SQL('GRANT EXECUTE ON FUNCTION app_tenant_id() TO {}').format(sql.Identifier(CAPABILITY)))
            raw.execute(sql.SQL('GRANT SELECT ON worker_authority_fixture TO {}').format(sql.Identifier(CAPABILITY)))
            for name in TABLES:
                raw.execute(sql.SQL('GRANT SELECT,INSERT ON {} TO {}').format(sql.Identifier(name), sql.Identifier(CAPABILITY)))
            raw.execute(sql.SQL('GRANT UPDATE(state,intent_receipt_sha256,outcome_receipt_sha256) ON owner_codex_execution_fences TO {}').format(sql.Identifier(CAPABILITY)))
            raw.execute(sql.SQL('GRANT UPDATE(active_attempt_id,quarantined) ON owner_codex_execution_runtimes TO {}').format(sql.Identifier(CAPABILITY)))
            raw.execute(sql.SQL('REVOKE ALL ON worker_authority_fixture FROM PUBLIC'))
        encrypted = admin.pgconn.encrypt_password(password.encode(), LOGIN.encode(), b'scram-sha-256')
        predecessor.validate_scram_verifier(encrypted)
        with admin.transaction():
            admin.execute(sql.SQL('ALTER ROLE {} LOGIN PASSWORD {}').format(sql.Identifier(LOGIN), sql.Literal(encrypted.decode('ascii'))))
    finally:
        raw.close()
    require(predecessor.scope_metadata(admin) == before, 'fence_predecessor_metadata_changed')
    return hashlib.sha256(canonical(before)).hexdigest()


@contextmanager
def restricted(connect, parameters):
    raw = connect_exact(connect, parameters, DATABASE, restricted=True)
    try:
        raw.row_factory = _hybrid_row_factory
        raw.autocommit = False
        raw.execute("SELECT set_config('app.tenant_id',%s,false)", (TENANT,))
        raw.commit()
        db = PostgresConnection(raw)
        def authorize(item):
            row = db.execute('SELECT tenant_id,worker_id,service_principal_id,enabled FROM worker_authority_fixture WHERE tenant_id=? AND worker_id=?',
                (item.owner.tenant_id, item.worker_id)).fetchone()
            return None if row is None else WorkerAuthority(str(row['tenant_id']), str(row['worker_id']), str(row['service_principal_id']), row['enabled'])
        yield raw, ExecutionFenceStore(db, authorize_worker=authorize)
    finally:
        raw.rollback()
        raw.close()


def denied(raw, statement, parameters=(), *, expected='42501'):
    try:
        with raw.transaction():
            raw.execute("SELECT set_config('app.execution_worker_id',%s,true)", (WORKER,))
            raw.execute(statement, parameters)
            raise ValueError('fence_unexpected_allowed')
    except ValueError:
        raise
    except Exception as error:
        require(getattr(error, 'sqlstate', None) == expected, 'fence_denial_wrong_cause')
    require(raw.info.transaction_status == 0, 'fence_denial_transaction_not_reset')


def verify(connect, parameters, admin_parameters, *, passed=None):
    passed = [] if passed is None else passed
    denial_count = 0
    with restricted(connect, parameters) as (raw, store):
        role = raw.execute('SELECT session_user::text,current_user::text,current_database()::text').fetchone()
        require(tuple(role.values()) == (LOGIN, CAPABILITY, DATABASE), 'fence_role_invalid')
        attrs = raw.execute('SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=%s', (LOGIN,)).fetchone()
        require(attrs['rolcanlogin'] is True and all(attrs[k] is False for k in attrs if k != 'rolcanlogin'), 'fence_login_privileged')
        ssl = raw.execute('SELECT ssl,version FROM pg_stat_ssl WHERE pid=pg_backend_pid()').fetchone()
        require(ssl['ssl'] is True and ssl['version'] in ('TLSv1.2', 'TLSv1.3'), 'fence_tls_invalid')
        grants = raw.execute("SELECT relname,relrowsecurity,relforcerowsecurity,pg_get_userbyid(relowner) AS owner FROM pg_class JOIN pg_namespace n ON n.oid=relnamespace WHERE n.nspname=%s AND relkind='r' ORDER BY relname", (SCHEMA,)).fetchall()
        require(len(grants) == 4 and all(r['owner'] == OWNER for r in grants)
            and all(r['relrowsecurity'] and r['relforcerowsecurity'] for r in grants if r['relname'] in TABLES), 'fence_rls_catalog_invalid')
        require(raw.execute('SELECT has_schema_privilege(current_user,%s,%s)', (SCHEMA, 'CREATE')).fetchone()[0] is False, 'fence_schema_write_allowed')
        raw.commit()
        passed.append(CHECKS[0])
        first = work(1)
        permit = store.reserve(intent(first))
    with restricted(connect, parameters) as (raw, store):
        try:
            store.reserve(intent(replace(first, attempt_id=uid(9990), lease_token=uid(9991))))
            raise ValueError('fence_reconstruction_rearmed')
        except WorkerError as error:
            require(error.code == 'attempt_already_consumed', 'fence_reconstruction_denial_invalid')
        evidence = outcome(first)
        store.settle(permit, synthetic_receipt(evidence), evidence)
    passed.append(CHECKS[1])

    barrier = threading.Barrier(3)
    def race(number):
        item = replace(work(2), attempt_id=uid(8000 + number), lease_token=uid(8100 + number))
        with restricted(connect, parameters) as (_, store):
            barrier.wait(timeout=20)
            try:
                return item, store.reserve(intent(item)), 'won'
            except WorkerError as error:
                return item, None, error.code
    with ThreadPoolExecutor(max_workers=3) as pool:
        raced = list(pool.map(race, range(3)))
    require(sum(result[2] == 'won' for result in raced) == 1
        and sum(result[2] == 'attempt_already_consumed' for result in raced) == 2, 'fence_race_invalid')
    winner, permit, _ = next(result for result in raced if result[2] == 'won')
    with restricted(connect, parameters) as (_, store):
        evidence = outcome(winner)
        store.settle(permit, synthetic_receipt(evidence), evidence)
    passed.append(CHECKS[2])

    with restricted(connect, parameters) as (raw, store):
        for tenant, worker, expected in ((TENANT, WORKER, 2), (TENANT, OTHER_WORKER, 0), (OTHER_TENANT, WORKER, 0), ('', '', 0)):
            with raw.transaction():
                raw.execute("SELECT set_config('app.tenant_id',%s,true),set_config('app.execution_worker_id',%s,true)", (tenant, worker))
                require(raw.execute('SELECT count(*) FROM owner_codex_execution_fences').fetchone()[0] == expected, 'fence_rls_rows_exposed')
        require(raw.execute("SELECT current_setting('app.execution_worker_id',true)").fetchone()[0] in (None, ''), 'fence_worker_scope_leaked')
        raw.commit()
        passed.append(CHECKS[3])
        for statement in ('SET ROLE ' + OWNER,
            'CREATE TABLE unexpected_fence_probe (n INT)',
            'UPDATE worker_authority_fixture SET enabled=FALSE',
            "UPDATE owner_codex_execution_fences SET request_sha256='" + '0' * 64 + "'",
            'DELETE FROM owner_codex_execution_fences',
            'TRUNCATE owner_codex_execution_fences',
            "UPDATE owner_codex_execution_fence_events SET binding_sha256='" + '0' * 64 + "'",
            'DELETE FROM owner_codex_execution_fence_events'):
            denied(raw, statement)
            denial_count += 1
        denied(raw, "UPDATE owner_codex_execution_fences SET state='reserved'", expected='P0001')
        denial_count += 1
        # The new constraints reject a forged worker association, even if the
        # caller can select its own session-local worker scope.
        with raw.transaction():
            raw.execute("SELECT set_config('app.execution_worker_id',%s,true)", (OTHER_WORKER,))
            try:
                with raw.transaction():
                    raw.execute('INSERT INTO owner_codex_execution_fence_events VALUES (%s,%s,%s,%s,%s,%s)',
                        (TENANT, OWNER_REF.object_id, first.task_id, OTHER_WORKER, 'reserved', 'a' * 64))
                    raise ValueError('fence_unexpected_allowed')
            except ValueError:
                raise
            except Exception as error:
                require(getattr(error, 'sqlstate', None) == 'P0001', 'fence_denial_wrong_cause')
        denial_count += 1
        passed.append(CHECKS[4])
        item = work(3)
        permit = store.reserve(intent(item))
        receipt = synthetic_receipt(intent(item))
        store.intent_verified(permit, receipt, intent(item))
        store.launch_reserved(permit)
        try:
            bad = outcome(item, receipt=receipt.record_sha256)
            store.settle(permit, synthetic_receipt(bad), bad)
            raise ValueError('fence_not_started_closed_launch')
        except WorkerError as error:
            require(error.code == 'stop_unverified', 'fence_wrong_stop_denial')
        good = outcome(item, launched=True, receipt=receipt.record_sha256)
        store.settle(permit, synthetic_receipt(good), good)
        passed.append(CHECKS[5])
        quarantined = work(4)
        permit = store.reserve(intent(quarantined))
        store.quarantine(permit)
        denied(raw, 'UPDATE owner_codex_execution_runtimes SET quarantined=FALSE', expected='P0001')
        denial_count += 1
    with restricted(connect, parameters) as (_, store):
        try:
            store.reserve(intent(work(5)))
            raise ValueError('fence_quarantine_lost')
        except WorkerError as error:
            require(error.code == 'worker_quarantined', 'fence_quarantine_denial_invalid')
        revoked = work(6, worker=OTHER_WORKER)
        permit = store.reserve(intent(revoked))
    passed.append(CHECKS[6])
    admin = connect_exact(connect, admin_parameters, DATABASE)
    try:
        with admin.transaction():
            admin.execute(sql.SQL('SET LOCAL ROLE {}').format(sql.Identifier(OWNER)))
            admin.execute(sql.SQL('UPDATE {}.worker_authority_fixture SET enabled=FALSE WHERE tenant_id=%s AND worker_id=%s')
                .format(sql.Identifier(SCHEMA)), (TENANT, OTHER_WORKER))
    finally:
        admin.close()
    with restricted(connect, parameters) as (_, store):
        try:
            store.verify_active(permit)
            raise ValueError('fence_revocation_not_fresh')
        except WorkerError as error:
            require(error.code == 'access_denied', 'fence_revocation_denial_invalid')
    passed.append(CHECKS[7])
    return dict(checks=passed, denied_privilege_probes=denial_count, positive_tls=dict(sslmode='verify-full', protocol=ssl['version']),
        synthetic_receipts_only=True, restricted_sql_fence_verified=True,
        protected_worker_receipts_verified=False, runtime_process_cessation_verified=False, website_owner_authenticated=False)


def main(admin_secret, ddl, constraints, *, connect, emit=print):
    result = dict(status='unaccepted', operation_id=OPERATION, job_resource_id=JOB_ID,
        database=DATABASE, schema=SCHEMA, installation_attempted=False, checks=[],
        source_rows_read=0, source_rows_written=0, production_changed=False,
        model_calls=0, agents_activated=0, automatic_retry=False, step_3_complete=False, step_4_started=False)
    admin = None
    stage = 'input'
    try:
        require(hashlib.sha256(ddl.encode()).hexdigest() == DDL_SHA and callable(connect), 'fence_source_invalid')
        parameters = credential_parser.validate_admin_secret(admin_secret)
        credential_parser.require_ca_bundle()
        stage = 'preparation'
        admin = connect_exact(connect, parameters, 'postgres')
        result['installation_attempted'] = True
        password = secrets.token_urlsafe(48)
        before = install(admin, connect, parameters, ddl, constraints, password)
        stage = 'restricted_login'
        restricted_parameters = dict(parameters, user=LOGIN, password=password, dbname=DATABASE)
        result.update(verify(connect, restricted_parameters, parameters, passed=result['checks']))
        after = hashlib.sha256(canonical(predecessor.scope_metadata(admin))).hexdigest()
        require(after == before, 'fence_predecessor_metadata_changed')
        result['checks'].append(CHECKS[8])
        result.update(status='synthetic_postgres_fence_verified', predecessor_metadata_sha256=before,
            predecessor_metadata_unchanged=True)
        require(password.encode() not in canonical(result) and admin_secret.encode() not in canonical(result), 'fence_sensitive_output')
    except Exception as error:
        result['failure_stage'] = stage
        code = str(error) if isinstance(error, ValueError) else ''
        result['code'] = code if re.fullmatch('fence_[a-z_]+', code) else 'fence_trial_unaccepted'
        result['error_class'] = type(error).__name__
        state = getattr(error, 'sqlstate', None)
        result['sqlstate'] = state if type(state) is str and re.fullmatch('[A-Z0-9]{5}', state) else None
    finally:
        if admin is not None:
            admin.close()
    require(len(canonical(result)) < 16384, 'fence_result_oversized')
    emit(PREFIX + canonical(result).decode('ascii'), flush=True)
    return result
