"""Synthetic behavioural PostgreSQL 059 acceptance in one disposable database.

Nothing runs on import. A reviewed root wrapper supplies a fresh trusted
connection factory, MIGRATION_SQL and MIGRATION_SHA256. It authenticates only
to the fixed test database and sets the fixed NOLOGIN role before yielding.
This harness has no explicit URL/password or Azure credential argument and
never grants or resets a role. Its raw connection remains administrator-
authenticated underneath SET ROLE; libpq may retain credentials. This is a
trusted administrator-controlled test, not a containment boundary, and its
factory/connections must never be made available to an agent/model. The
effective test role owns its schema, so this is NOT a fresh-login or restricted
non-owner DML privilege-boundary acceptance test.

All created rows are synthetic. Protected receipt verification is simulated,
not a claim of live archive protection. The root retains protected evidence and
reviews it before separately removing the precisely registered test database.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import threading
import uuid

import psycopg
from psycopg import sql

from ibkr_etl.db import PostgresConnection
from ibkr_etl.owner_codex_tasks import (
    AUDIT_NAMESPACE, OwnerAuthority, OwnerCodexTasks, OwnerRef, OwnerTaskError,
    ServerValidatedTaskRequest, WorkerAuthority, _hash,
)


HOST = "ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com"
DATABASE = "owner_codex_acceptance_20261003"
ROLE = "ibkr_owner_codex_trial_20261003"
SCHEMA = "owner_codex_059_trial"
EXPECTED_MIGRATION_SHA256 = "b4a78f64969d328f6b3ce1ce71b73cd3c033606fc8580ed1a933dd47857e9861"
TASK_TABLES = ("owner_codex_sessions", "owner_codex_tasks", "owner_codex_task_events")
FIXTURE_TABLES = ("tenants", "owner_task_authority_fixture", "owner_task_worker_fixture")
TABLES = tuple(sorted(TASK_TABLES + FIXTURE_TABLES))
FUNCTIONS = ("app_tenant_id", "guard_owner_codex_append_only", "guard_owner_codex_task",
             "guard_owner_codex_event", "guard_owner_codex_event_presence")
TENANT_A = "00000000-0000-0000-0000-000000000001"
TENANT_B = "00000000-0000-0000-0000-000000000101"
OWNER_A = OwnerRef(TENANT_A, "00000000-0000-0000-0000-000000000201")
OWNER_B = OwnerRef(TENANT_A, "00000000-0000-0000-0000-000000000202")
OTHER_TENANT_OWNER = OwnerRef(TENANT_B, OWNER_A.object_id)
WORKER = "00000000-0000-0000-0000-000000000301"
SERVICE_PRINCIPAL = "00000000-0000-0000-0000-000000000302"
INPUT_SHA = "a" * 64
REQUEST = ServerValidatedTaskRequest(("SYNTH_A",), INPUT_SHA)

# Exact production-001 helper semantics, including its default-tenant fallback.
# Explicit-tenant policies and the service guard must therefore reject missing
# settings themselves; an artificially stricter test helper would hide the gap.
TENANT_HELPER_SQL = """CREATE OR REPLACE FUNCTION app_tenant_id() RETURNS UUID
LANGUAGE SQL STABLE AS $$
    SELECT COALESCE(
        NULLIF(current_setting('app.tenant_id', true), '')::UUID,
        '00000000-0000-0000-0000-000000000001'::UUID
    )
$$;"""


class TrialFailure(ValueError):
    def __init__(self, code):
        if type(code) is not str or not re.fullmatch("[a-z][a-z0-9_]{0,79}", code):
            code = "trial_contract_failed"
        self.code = code
        super().__init__(code)


def require(value, code):
    if not value:
        raise TrialFailure(code)


def _migration():
    text = globals().get("MIGRATION_SQL")
    digest = globals().get("MIGRATION_SHA256")
    require(type(text) is str and digest == EXPECTED_MIGRATION_SHA256
            and hashlib.sha256(text.encode()).hexdigest() == digest, "migration_source_mismatch")
    return text


def _preflight(raw):
    require(raw.autocommit is True and raw.info.transaction_status == 0,
            "fresh_idle_autocommit_connection_required")
    require(raw.info.host == HOST and raw.info.dbname == DATABASE, "unregistered_test_database")
    identity = raw.execute("""SELECT current_database() AS database,current_user::text AS role,
        session_user::text AS session_identity,current_setting('search_path') AS search_path,
        current_setting('server_version') AS server_version""").fetchone()
    require(identity["database"] == DATABASE and identity["role"] == ROLE
            and identity["session_identity"] != ROLE
            and identity["search_path"] == SCHEMA + ", pg_catalog", "unexpected_test_identity_or_path")
    attributes = raw.execute("""SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,
        rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=current_user""").fetchone()
    require(attributes is not None and all(v is False for v in attributes.values()), "privileged_test_role")
    owner = raw.execute("""SELECT pg_get_userbyid(nspowner)=current_user AS owns_schema
        FROM pg_namespace WHERE nspname=%s""", (SCHEMA,)).fetchone()
    require(owner is not None and owner["owns_schema"] is True, "test_schema_owner_mismatch")
    memberships = raw.execute("SELECT count(*) AS n FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname=current_user)").fetchone()
    require(memberships["n"] == 0, "test_role_has_other_memberships")
    require(raw.execute("SELECT pg_my_temp_schema() AS oid").fetchone()["oid"] == 0,
            "connection_is_not_fresh")
    return identity["server_version"]


@contextmanager
def _connection(factory, tenant=TENANT_A):
    raw = factory()
    try:
        _preflight(raw)
        raw.autocommit = False
        if tenant is not None:
            raw.execute("SELECT set_config('app.tenant_id',%s,false)", (tenant,))
            raw.commit()
        yield raw, PostgresConnection(raw)
    finally:
        try:
            raw.rollback()
        finally:
            raw.close()


def _scope(raw, tenant=TENANT_A, owner=OWNER_A.object_id):
    if tenant is not None:
        raw.execute("SELECT set_config('app.tenant_id',%s,true)", (tenant,))
    if owner is not None:
        raw.execute("SELECT set_config('app.owner_object_id',%s,true)", (owner,))


def _initialize(raw):
    names = raw.execute("""SELECT relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=%s AND c.relkind IN ('r','p','v','m','f')""", (SCHEMA,)).fetchall()
    require(not names, "preexisting_trial_objects")
    functions = raw.execute("SELECT count(*) AS n FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=%s", (SCHEMA,)).fetchone()
    require(functions["n"] == 0, "preexisting_trial_functions")
    raw.execute("CREATE TABLE tenants (tenant_id UUID PRIMARY KEY)")
    raw.execute(TENANT_HELPER_SQL)
    raw.execute("""CREATE TABLE owner_task_authority_fixture (
        tenant_id UUID NOT NULL,owner_object_id UUID NOT NULL,active BOOLEAN NOT NULL,
        role_name TEXT NOT NULL,registered_subject_ids JSONB NOT NULL,
        authorized_subject_ids JSONB NOT NULL,PRIMARY KEY(tenant_id,owner_object_id))""")
    raw.execute("""CREATE TABLE owner_task_worker_fixture (
        tenant_id UUID NOT NULL,worker_id UUID NOT NULL,service_principal_id UUID NOT NULL,
        enabled BOOLEAN NOT NULL,PRIMARY KEY(tenant_id,worker_id))""")
    for tenant in (TENANT_A, TENANT_B):
        raw.execute("INSERT INTO tenants VALUES (%s)", (tenant,))
        raw.execute("INSERT INTO owner_task_worker_fixture VALUES (%s,%s,%s,TRUE)", (tenant, WORKER, SERVICE_PRINCIPAL))
    for owner in (OWNER_A, OWNER_B, OTHER_TENANT_OWNER):
        raw.execute("""INSERT INTO owner_task_authority_fixture VALUES (%s,%s,TRUE,'owner',%s,%s)""",
                    (owner.tenant_id, owner.object_id, '["SYNTH_A","SYNTH_B"]', '["SYNTH_A","SYNTH_B"]'))
    raw.execute(_migration())
    raw.commit()


def _catalog(raw):
    rows = raw.execute("""SELECT relname,relrowsecurity,relforcerowsecurity,
        pg_get_userbyid(relowner) AS owner FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=%s AND c.relkind IN ('r','p','v','m','f') ORDER BY relname""", (SCHEMA,)).fetchall()
    require(tuple(r["relname"] for r in rows) == TABLES, "trial_table_inventory_mismatch")
    require(all(r["owner"] == ROLE and r["relrowsecurity"] and r["relforcerowsecurity"]
                for r in rows if r["relname"] in TASK_TABLES), "forced_rls_not_confirmed")
    policies = raw.execute("""SELECT tablename,policyname,cmd,qual,with_check FROM pg_policies
        WHERE schemaname=%s ORDER BY tablename""", (SCHEMA,)).fetchall()
    require(tuple(r["tablename"] for r in policies) == tuple(sorted(TASK_TABLES)), "owner_policy_inventory_mismatch")
    require(all(r["policyname"] == "owner_isolation" and r["cmd"] == "ALL"
                and "owner_object_id" in r["qual"] and "app.owner_object_id" in r["qual"]
                and "app.tenant_id" in r["qual"] and r["qual"] == r["with_check"] for r in policies),
            "owner_policy_definition_mismatch")
    helper_dependencies = raw.execute("""SELECT DISTINCT c.relname,nf.nspname AS function_schema,fp.proname
        FROM pg_policy pol JOIN pg_class c ON c.oid=pol.polrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_depend d ON d.classid='pg_policy'::regclass AND d.objid=pol.oid
            AND d.refclassid='pg_proc'::regclass
        JOIN pg_proc fp ON fp.oid=d.refobjid JOIN pg_namespace nf ON nf.oid=fp.pronamespace
        WHERE n.nspname=%s AND fp.proname='app_tenant_id' ORDER BY c.relname""", (SCHEMA,)).fetchall()
    require(tuple(r["relname"] for r in helper_dependencies) == tuple(sorted(TASK_TABLES))
            and all(r["function_schema"] == SCHEMA for r in helper_dependencies), "policy_helper_oid_binding_mismatch")
    functions = raw.execute("""SELECT proname,prosecdef FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname=%s ORDER BY proname""", (SCHEMA,)).fetchall()
    require(tuple(r["proname"] for r in functions) == tuple(sorted(FUNCTIONS))
            and all(r["prosecdef"] is False for r in functions), "trial_function_inventory_mismatch")
    foreign_acl = raw.execute("""SELECT count(*) AS n FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace,
        LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a
        WHERE n.nspname=%s AND c.relname=ANY(%s) AND a.grantee<>c.relowner""", (SCHEMA, list(TASK_TABLES))).fetchone()
    require(foreign_acl["n"] == 0, "unexpected_task_relation_grants")
    trigger = raw.execute("""SELECT tgdeferrable,tginitdeferred FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND tgname='owner_codex_revision_event'""", (SCHEMA,)).fetchone()
    require(trigger == {"tgdeferrable": True, "tginitdeferred": True}, "deferred_event_trigger_missing")
    raw.commit()


class SyntheticArchive:
    def __init__(self):
        self.records = {}
        self.lock = threading.Lock()

    def add(self, binding):
        reference = _hash({"synthetic_fixture": True, "binding": binding})
        with self.lock:
            self.records[reference] = dict(binding)
        return reference

    def __call__(self, reference, binding):
        with self.lock:
            require(self.records.get(reference) == binding, "synthetic_receipt_binding_failed")
        return {"record_sha256": reference, "binding_sha256": _hash(binding), "namespace": AUDIT_NAMESPACE,
                "protected_archive_verified": True, "database_committed": True}


def _service(connection, archive, clock, *, before_authority=None):
    def authorize(owner):
        row = connection.execute("""SELECT active,role_name,registered_subject_ids,authorized_subject_ids
            FROM owner_task_authority_fixture WHERE tenant_id=? AND owner_object_id=?""",
            (owner.tenant_id, owner.object_id)).fetchone()
        if row is None or row["active"] is not True or row["role_name"] != "owner":
            return None
        if before_authority:
            before_authority()
        return OwnerAuthority(owner, tuple(row["registered_subject_ids"]), tuple(row["authorized_subject_ids"]), INPUT_SHA)

    def worker(tenant, worker_id):
        row = connection.execute("""SELECT service_principal_id,enabled FROM owner_task_worker_fixture
            WHERE tenant_id=? AND worker_id=?""", (tenant, worker_id)).fetchone()
        if row is None or row["enabled"] is not True:
            return None
        return WorkerAuthority(tenant, worker_id, str(row["service_principal_id"]))

    return OwnerCodexTasks(connection, authorize=authorize, authorize_worker=worker,
                           verify_receipt=archive, clock=clock)


def _request(service, owner=OWNER_A, *, session=None, key=None, request=REQUEST):
    if session is None:
        session = service.create_session(owner, idempotency_key=str(uuid.uuid4()))["session_id"]
    return service.submit(owner, session_id=session, idempotency_key=key or str(uuid.uuid4()), request=request)


def _row(connection, task, owner=OWNER_A):
    with connection._connection.transaction():
        _scope(connection._connection, owner.tenant_id, owner.object_id)
        row = connection.execute("SELECT * FROM owner_codex_tasks WHERE task_id=?", (task["task_id"],)).fetchone()
    return dict(row)


def _claim(service, connection, archive, owner=OWNER_A):
    task = _request(service, owner)
    intent = archive.add(service.receipt_binding(_row(connection, task, owner), "intent"))
    task = service.attach_intent(owner, task_id=task["task_id"], expected_revision=1,
                                 receipt_sha256=intent, worker_id=WORKER)
    lease = service.claim(owner, task_id=task["task_id"], expected_revision=2, worker_id=WORKER)
    return lease["task"], intent, lease


def _denied(operation, expected):
    try:
        operation()
    except OwnerTaskError as error:
        require(error.code == expected, "unexpected_service_denial")
    else:
        raise TrialFailure("service_denial_not_enforced")


def _sql_denied(raw, operation, expected_states=("P0001", "42501", "23514", "23503")):
    try:
        with raw.transaction():
            _scope(raw)
            operation()
    except psycopg.Error as error:
        require(error.sqlstate in expected_states, "unexpected_sql_denial")
    else:
        raise TrialFailure("sql_denial_not_enforced")


def _isolation(factory, fixtures):
    for owner, task in fixtures:
        with _connection(factory, owner.tenant_id) as (raw, _):
            with raw.transaction():
                _scope(raw, owner.tenant_id, owner.object_id)
                for table in TASK_TABLES:
                    count = raw.execute(sql.SQL("SELECT count(*) AS n FROM {}").format(sql.Identifier(table))).fetchone()["n"]
                    require(count == 1, "owner_or_tenant_row_isolation_failed")
                other = OWNER_B if owner.object_id == OWNER_A.object_id else OWNER_A
                require(raw.execute("SELECT count(*) AS n FROM owner_codex_tasks WHERE owner_object_id=%s", (other.object_id,)).fetchone()["n"] == 0,
                        "cross_owner_read_allowed")
    for tenant, owner in ((None, OWNER_A.object_id), ("", OWNER_A.object_id), (TENANT_A, None), (TENANT_A, "")):
        with _connection(factory, None) as (raw, _):
            with raw.transaction():
                _scope(raw, tenant, owner)
                for table in TASK_TABLES:
                    require(raw.execute(sql.SQL("SELECT count(*) AS n FROM {}").format(sql.Identifier(table))).fetchone()["n"] == 0,
                            "missing_scope_did_not_fail_closed")
    with _connection(factory) as (raw, _):
        _sql_denied(raw, lambda: raw.execute("INSERT INTO owner_codex_sessions VALUES (%s,%s,%s,%s,clock_timestamp())",
            (TENANT_A, OWNER_B.object_id, str(uuid.uuid4()), str(uuid.uuid4()))), ("42501",))


def _guard_checks(factory, task, archive, clock):
    with _connection(factory) as (raw, _):
        _sql_denied(raw, lambda: raw.execute("UPDATE owner_codex_tasks SET input_sha256=%s,revision=revision+1,updated_at=clock_timestamp() WHERE task_id=%s",
                    ("b" * 64, task["task_id"])), ("P0001",))
        for query in ("UPDATE owner_codex_sessions SET created_at=created_at",
                      "DELETE FROM owner_codex_task_events", "UPDATE owner_codex_task_events SET code=code",
                      "DELETE FROM owner_codex_tasks", "TRUNCATE owner_codex_task_events"):
            _sql_denied(raw, lambda q=query: raw.execute(q), ("P0001",))
        # Valid same-state intent update with deliberately omitted status event:
        # the error must be raised by the deferred trigger at transaction exit.
        _sql_denied(raw, lambda: raw.execute("""UPDATE owner_codex_tasks SET revision=revision+1,
            updated_at=clock_timestamp(),intent_receipt_sha256=%s WHERE task_id=%s""", (INPUT_SHA, task["task_id"])), ("P0001",))
        require(raw.info.transaction_status == 0, "denied_sql_left_open_transaction")
    # Use a permitted running->failed transition so the row CHECK, rather than
    # the queued-state trigger, is proven to reject SQL NULL three-valued logic.
    with _connection(factory) as (raw, c):
        running, _, _ = _claim(_service(c, archive, clock), c, archive)
        _sql_denied(raw, lambda: raw.execute("""UPDATE owner_codex_tasks SET state='failed',revision=revision+1,
            updated_at=clock_timestamp(),error_code=NULL,delivery_available=TRUE,
            worker_id=NULL,lease_token=NULL,lease_expires_at=NULL,
            outcome_receipt_sha256=%s WHERE task_id=%s""", (INPUT_SHA, running["task_id"])), ("23514",))
        require(raw.info.transaction_status == 0, "failed_check_left_open_transaction")


def _decoy_checks(factory, task):
    with _connection(factory) as (raw, _):
        with raw.transaction():
            _scope(raw)
            raw.execute("CREATE TEMP TABLE owner_codex_tasks (task_id UUID,revision BIGINT,state TEXT) ON COMMIT DROP")
            raw.execute("CREATE TEMP TABLE owner_codex_task_events (event_no BIGINT) ON COMMIT DROP")
            new_id, key = str(uuid.uuid4()), str(uuid.uuid4())
            raw.execute("INSERT INTO pg_temp.owner_codex_tasks VALUES (%s,999,'failed')", (new_id,))
            raw.execute(sql.SQL("SET LOCAL search_path TO pg_temp,{},pg_catalog").format(sql.Identifier(SCHEMA)))
            raw.execute(sql.SQL("""INSERT INTO {}.owner_codex_tasks
                (tenant_id,owner_object_id,task_id,session_id,idempotency_key,task_kind,subject_ids,input_sha256,
                 request_sha256,authority_sha256,state,revision,delivery_available,created_at,updated_at)
                SELECT tenant_id,owner_object_id,%s,session_id,%s,task_kind,subject_ids,input_sha256,
                    request_sha256,authority_sha256,'queued',1,FALSE,clock_timestamp(),clock_timestamp()
                FROM {}.owner_codex_tasks WHERE task_id=%s""").format(sql.Identifier(SCHEMA), sql.Identifier(SCHEMA)),
                (new_id, key, task["task_id"]))
            raw.execute(sql.SQL("""INSERT INTO {}.owner_codex_task_events
                VALUES (%s,%s,%s,1,'queued','queued',clock_timestamp())""").format(sql.Identifier(SCHEMA)),
                (TENANT_A, OWNER_A.object_id, new_id))


def _concurrent_submissions(factory, archive, clock):
    with _connection(factory) as (_, c):
        session = _service(c, archive, clock).create_session(OWNER_A, idempotency_key=str(uuid.uuid4()))["session_id"]
    barrier, key = threading.Barrier(2), str(uuid.uuid4())

    def submit(request):
        with _connection(factory) as (_, c):
            service = _service(c, archive, clock, before_authority=lambda: barrier.wait(timeout=15))
            try:
                return service.submit(OWNER_A, session_id=session, idempotency_key=key, request=request)
            except OwnerTaskError as error:
                return {"denial": error.code}

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(submit, (REQUEST, REQUEST)))
    require(first == second and "denial" not in first, "concurrent_idempotency_failed")
    with _connection(factory) as (_, c):
        service = _service(c, archive, clock)
        require(len(service.events(OWNER_A, task_id=first["task_id"])) == 1,
                "duplicate_submission_created_duplicate_event")
    barrier, key = threading.Barrier(2), str(uuid.uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, (REQUEST, ServerValidatedTaskRequest(("SYNTH_B",), INPUT_SHA))))
    require(sum(r.get("denial") == "idempotency_conflict" for r in results) == 1
            and sum("task_id" in r for r in results) == 1, "concurrent_payload_conflict_failed")


def _cancel_race(factory, archive, clock):
    with _connection(factory) as (_, c):
        task, intent, lease = _claim(_service(c, archive, clock), c, archive)
        success = archive.add(OwnerCodexTasks.receipt_binding(_row(c, task), "outcome", outcome="succeeded", result_sha256=INPUT_SHA))
    cancel_updated, release_cancel, finish_authorized = threading.Event(), threading.Event(), threading.Event()

    class CommitBarrier(PostgresConnection):
        def commit(self):
            cancel_updated.set()
            require(release_cancel.wait(15), "cancellation_commit_barrier_timeout")
            super().commit()

    def cancel():
        with _connection(factory) as (raw, _):
            service = _service(CommitBarrier(raw), archive, clock)
            return service.cancel(OWNER_A, task_id=task["task_id"], expected_revision=3)

    def finish():
        with _connection(factory) as (_, c):
            service = _service(c, archive, clock, before_authority=finish_authorized.set)
            try:
                return service.finish(OWNER_A, task_id=task["task_id"], expected_revision=3,
                    lease_token=lease["lease_token"], worker_id=WORKER, outcome="succeeded",
                    outcome_receipt_sha256=success, result_sha256=INPUT_SHA)
            except OwnerTaskError as error:
                return {"denial": error.code}

    with ThreadPoolExecutor(max_workers=2) as pool:
        cancel_future = pool.submit(cancel)
        try:
            require(cancel_updated.wait(15), "cancellation_did_not_lock_first")
            finish_future = pool.submit(finish)
            require(finish_authorized.wait(15), "concurrent_finish_did_not_authorize")
        finally:
            release_cancel.set()
        cancelled, result = cancel_future.result(timeout=20), finish_future.result(timeout=20)
    require(cancelled["state"] == "cancel_requested" and result == {"denial": "revision_conflict"},
            "cancel_finish_revision_race_failed")
    with _connection(factory) as (_, c):
        service = _service(c, archive, clock)
        _denied(lambda: service.finish(OWNER_A, task_id=task["task_id"], expected_revision=4,
            lease_token=lease["lease_token"], worker_id=WORKER, outcome="succeeded",
            outcome_receipt_sha256=success, result_sha256=INPUT_SHA), "transition_denied")
        later = lambda: clock() + timedelta(seconds=121)
        service.clock = later
        task = service.recover_expired(OWNER_A, task_id=task["task_id"], expected_revision=4, worker_id=WORKER)
        require(task["state"] == "needs_reconciliation" and task["cancellation_requested"],
                "sticky_cancellation_lost_on_expiry")
        _denied(lambda: service.reconcile(OWNER_A, task_id=task["task_id"], expected_revision=5, outcome="succeeded",
            intent_receipt_sha256=intent, outcome_receipt_sha256=success, result_sha256=INPUT_SHA, worker_id=WORKER), "transition_denied")
        stopped = archive.add(service.receipt_binding(_row(c, task), "outcome", outcome="cancelled"))
        done = service.reconcile(OWNER_A, task_id=task["task_id"], expected_revision=5, outcome="cancelled",
            intent_receipt_sha256=intent, outcome_receipt_sha256=stopped, worker_id=WORKER)
        require(done["state"] == "cancelled" and done["result_sha256"] is None, "cancelled_result_delivered")


def _late_cancel_and_revocation(factory, archive, clock):
    with _connection(factory) as (raw, c):
        service = _service(c, archive, clock)
        task, intent, _ = _claim(service, c, archive)
        service.clock = lambda: clock() + timedelta(seconds=121)
        task = service.recover_expired(OWNER_A, task_id=task["task_id"], expected_revision=3, worker_id=WORKER)
        _denied(lambda: service.claim(OWNER_A, task_id=task["task_id"], expected_revision=4, worker_id=WORKER), "transition_denied")
        task = service.cancel(OWNER_A, task_id=task["task_id"], expected_revision=4)
        require(task["state"] == "needs_reconciliation" and task["cancellation_requested"], "late_cancel_was_discarded")
        success = archive.add(service.receipt_binding(_row(c, task), "outcome", outcome="succeeded", result_sha256=INPUT_SHA))
        _denied(lambda: service.reconcile(OWNER_A, task_id=task["task_id"], expected_revision=5, outcome="succeeded",
            intent_receipt_sha256=intent, outcome_receipt_sha256=success, result_sha256=INPUT_SHA, worker_id=WORKER), "transition_denied")
        raw.execute("UPDATE owner_task_authority_fixture SET active=FALSE WHERE tenant_id=%s AND owner_object_id=%s",
                    (TENANT_A, OWNER_A.object_id))
        raw.commit()
        _denied(lambda: service.get(OWNER_A, task_id=task["task_id"]), "access_denied")
        _denied(lambda: service.cancel(OWNER_A, task_id=task["task_id"], expected_revision=5), "access_denied")
        raw.execute("UPDATE owner_task_authority_fixture SET active=TRUE WHERE tenant_id=%s AND owner_object_id=%s",
                    (TENANT_A, OWNER_A.object_id))
        raw.commit()
        task = service.get(OWNER_A, task_id=task["task_id"])
        require(task["state"] == "needs_reconciliation" and not task["delivery_available"], "revocation_published_or_restarted_task")
    for tenant in (None, "", TENANT_B):
        with _connection(factory, tenant) as (_, c):
            _denied(lambda: _service(c, archive, clock).create_session(OWNER_A, idempotency_key=str(uuid.uuid4())), "access_denied")
            require(c._connection.info.transaction_status == 0, "denied_request_left_open_transaction")
    # The public metadata adapter must refuse autocommit before any transaction
    # or authority lookup, rather than partially insert a task without an event.
    raw = factory()
    try:
        _preflight(raw)
        service = _service(PostgresConnection(raw), archive, clock)
        _denied(lambda: service.create_session(OWNER_A, idempotency_key=str(uuid.uuid4())), "transition_denied")
        require(raw.info.transaction_status == 0, "autocommit_rejection_started_transaction")
    finally:
        raw.close()


def verify_trial(connection_factory):
    """Run ONLY after the root has approved/provisioned the disposable scope.

    Factory returns fresh idle autocommit psycopg connections with dict_row,
    effective ROLE already set and search_path ``SCHEMA, pg_catalog``. Its
    raw connection is still administrator-authenticated underneath SET ROLE.
    No explicit credential argument, cleanup, retry, role reset, model inference
    or production installation is attempted. This is trusted test code.
    """
    result = {"status": "failed", "database": DATABASE, "schema": SCHEMA, "effective_role": ROLE,
              "migration_sha256": EXPECTED_MIGRATION_SHA256, "checks": [],
              "scope": "synthetic_admin_context_effective_role_postgres_behavior",
              "fresh_login_verified": False, "non_owner_privilege_boundary_verified": False,
              "protected_archive_live_verified": False, "receipt_verifier_is_synthetic": True,
              "source_rows_read": 0, "source_rows_written": 0, "service_grants_created": 0,
              "model_calls": 0, "agents_activated": 0, "production_migration_installed": False,
              "cleanup_performed": False}
    try:
        require(callable(connection_factory), "trusted_connection_factory_required")
        _migration()
        with _connection(connection_factory) as (raw, _):
            result["server_version"] = _preflight_version(raw)
            _initialize(raw)
            _catalog(raw)
        result["checks"].append("exact_scope_role_catalog_and_migration")
        archive, now = SyntheticArchive(), datetime.now(timezone.utc)
        clock = lambda: now
        fixtures = []
        for owner in (OWNER_A, OWNER_B, OTHER_TENANT_OWNER):
            with _connection(connection_factory, owner.tenant_id) as (_, c):
                fixtures.append((owner, _request(_service(c, archive, clock), owner)))
        _isolation(connection_factory, fixtures)
        result["checks"].append("forced_tenant_owner_and_missing_scope_rls")
        _guard_checks(connection_factory, fixtures[0][1], archive, clock)
        result["checks"].append("immutability_null_checks_and_deferred_event_atomicity")
        _decoy_checks(connection_factory, fixtures[0][1])
        result["checks"].append("trigger_schema_binding_with_temp_decoys")
        _concurrent_submissions(connection_factory, archive, clock)
        result["checks"].append("concurrent_idempotency_payload_conflict_and_single_event")
        _cancel_race(connection_factory, archive, clock)
        result["checks"].append("cancel_first_cas_race_sticky_expiry_and_verified_stop_gate")
        _late_cancel_and_revocation(connection_factory, archive, clock)
        result["checks"].append("late_cancel_no_redispatch_and_fresh_authority_revocation")
        with _connection(connection_factory) as (raw, _):
            _catalog(raw)
        result["checks"].append("committed_final_catalog_readback")
        result["status"] = "passed"
        result["check_manifest_sha256"] = _hash(result["checks"])
    except Exception as error:
        result["reason"] = error.code if isinstance(error, TrialFailure) else "postgresql_probe_failed"
        state = getattr(error, "sqlstate", None)
        if type(state) is str and re.fullmatch("[0-9A-Z]{5}", state):
            result["sqlstate"] = state
    return result


def _preflight_version(raw):
    # Connection was already validated by _connection before changing its mode.
    value = raw.execute("SELECT current_setting('server_version') AS version").fetchone()["version"]
    raw.commit()
    return value
