"""Source-only trusted preparation for a new restricted-login SQL rehearsal.

Importing this module does nothing. It has no CLI, environment reader, Azure
client, database connector or model adapter. Reviewed external orchestration
must retain protected intent, supply trusted connections, freeze the bundle and
separately review cleanup before invoking any helper. Raw connections/passwords
must never be supplied to models. A fresh SQL login is not process isolation.
"""
import base64
import hashlib
import re

import inspect_owner_task_isolated_provider as collector
import owner_task_azure_database_boundary as boundary

from psycopg import sql
from ibkr_etl import owner_codex_isolated_target as isolated


HOST = isolated.HOST
DATABASE = isolated.DATABASE
SCHEMA = isolated.SCHEMA
OWNER = isolated.SCHEMA_OWNER
CAPABILITY = isolated.CAPABILITY
LOGIN = isolated.LOGIN
MARKER = isolated.MARKER
MIGRATION_SHA256 = "b4a78f64969d328f6b3ce1ce71b73cd3c033606fc8580ed1a933dd47857e9861"
TENANT_A = "00000000-0000-0000-0000-000000000001"
TENANT_B = "00000000-0000-0000-0000-000000000101"
OWNER_A = "00000000-0000-0000-0000-000000000201"
OWNER_B = "00000000-0000-0000-0000-000000000202"
WORKER = "00000000-0000-0000-0000-000000000301"
PRINCIPAL = "00000000-0000-0000-0000-000000000302"
ROLES = (OWNER, CAPABILITY, LOGIN)
TENANT_HELPER_SQL = """CREATE OR REPLACE FUNCTION app_tenant_id() RETURNS UUID
LANGUAGE SQL STABLE AS $$
    SELECT COALESCE(
        NULLIF(current_setting('app.tenant_id', true), '')::UUID,
        '00000000-0000-0000-0000-000000000001'::UUID
    )
$$;"""


class PreparationError(ValueError):
    """Only fixed codes escape the trusted setup boundary."""
    def __init__(self, code):
        if type(code) is not str or not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", code):
            code = "preparation_contract_failed"
        self.code = code
        super().__init__(code)


def require(condition, code):
    if not condition:
        raise PreparationError(code)


def validate_inputs(migration_text, password):
    require(type(migration_text) is str and
            hashlib.sha256(migration_text.encode()).hexdigest() == MIGRATION_SHA256,
            "migration_source_mismatch")
    # A caller must generate this outside model-controlled code. Shape is not
    # an entropy guarantee; no password, hash or connection string is returned.
    require(type(password) is str and re.fullmatch(r"[A-Za-z0-9_-]{48,128}", password),
            "restricted_password_shape_invalid")


def bind_trusted_connection(raw, database):
    require(database in ("postgres", DATABASE), "unregistered_preparation_database")
    require(raw.autocommit is True and raw.info.transaction_status == 0,
            "fresh_idle_preparation_connection_required")
    require(raw.info.host == HOST and raw.info.dbname == database,
            "unregistered_preparation_connection")
    require(raw.pgconn.ssl_in_use is True, "encrypted_preparation_connection_required")
    identity = raw.execute("SELECT current_database()::text AS database,"
        "current_user::text AS role,session_user::text AS login,"
        "current_setting('server_version_num') AS version").fetchone()
    require(type(identity) is dict and set(identity) == {"database", "role", "login", "version"}
            and identity["database"] == database and type(identity["role"]) is str
            and identity["role"] == identity["login"] == isolated.ADMIN
            and raw.info.user == identity["login"]
            and type(identity["version"]) is str and re.fullmatch(r"16[0-9]{4}", identity["version"]),
            "trusted_preparation_identity_mismatch")
    return identity["role"]


def scope_metadata(admin):
    return {
        "database": admin.execute("SELECT datname,pg_get_userbyid(datdba) AS owner,"
            "shobj_description(oid,'pg_database') AS marker FROM pg_database WHERE datname=%s",
            (DATABASE,)).fetchone(),
        "roles": admin.execute("SELECT rolname,rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
            "rolcreaterole,rolreplication,rolbypassrls,rolconnlimit,"
            "shobj_description(oid,'pg_authid') AS marker FROM pg_roles WHERE rolname=ANY(%s)"
            " ORDER BY rolname", (list(ROLES),)).fetchall(),
    }


def validate_registered_metadata(metadata, *, login_enabled):
    require(type(login_enabled) is bool, "invalid_expected_login_state")
    require(type(metadata) is dict and set(metadata) == {"database", "roles"},
            "registered_metadata_shape_invalid")
    require(metadata["database"] == {"datname": DATABASE, "owner": OWNER, "marker": MARKER},
            "registered_database_mismatch")
    rows = metadata["roles"]
    keys = {"rolname", "rolcanlogin", "rolinherit", "rolsuper", "rolcreatedb", "rolcreaterole",
            "rolreplication", "rolbypassrls", "rolconnlimit", "marker"}
    require(type(rows) is list and len(rows) == 3 and all(type(r) is dict for r in rows)
            and {r.get("rolname") for r in rows} == set(ROLES),
            "registered_roles_mismatch")
    for row in rows:
        require(type(row) is dict and set(row) == keys and row["marker"] == MARKER
                and type(row["rolconnlimit"]) is int
                and row["rolconnlimit"] == (4 if row["rolname"] == LOGIN else -1)
                and row["rolcanlogin"] is (login_enabled and row["rolname"] == LOGIN)
                and all(row[k] is False for k in
                    ("rolinherit", "rolsuper", "rolcreatedb", "rolcreaterole", "rolreplication", "rolbypassrls")),
                "registered_role_attributes_mismatch")


def assert_public_database_boundary(admin):
    """Observe the approved complete catalog afresh BEFORE any trial effects."""
    require(admin.autocommit is True and admin.info.transaction_status == 0,
            "fresh_idle_preparation_connection_required")
    admin.execute("SET search_path=pg_catalog")
    admin.execute("SET default_transaction_read_only=on")
    try:
        observed = collector.inspect_provider(admin)
        boundary.review_catalog(observed)
    finally:
        admin.execute("SET default_transaction_read_only=off")
    return observed


def _create_roles(admin):
    with admin.transaction():
        for role in ROLES:
            admin.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB "
                "NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT {}")
                .format(sql.Identifier(role), sql.Literal(4 if role == LOGIN else -1)))
            admin.execute(sql.SQL("COMMENT ON ROLE {} IS {}").format(sql.Identifier(role), sql.Literal(MARKER)))
        # Preserve PostgreSQL 16's separate ADMIN-only creator lifecycle rows.
        admin.execute(sql.SQL("GRANT {} TO CURRENT_USER WITH ADMIN FALSE, INHERIT FALSE, SET TRUE")
            .format(sql.Identifier(OWNER)))
        admin.execute(sql.SQL("GRANT {} TO {} WITH ADMIN FALSE, INHERIT FALSE, SET TRUE")
            .format(sql.Identifier(CAPABILITY), sql.Identifier(LOGIN)))


def _initialize(raw, migration_text):
    identity = raw.execute("SELECT session_user::text AS session_user,"
        "current_user::text AS current_user,pg_get_userbyid(nspowner) AS public_schema_owner "
        "FROM pg_namespace WHERE nspname='public'").fetchone()
    require(type(identity) is dict, "public_schema_identity_missing")
    identity = dict(identity, host=raw.info.host, database=raw.info.dbname)
    raw.execute(boundary.public_schema_revoke(identity))
    # Azure owns public as azure_pg_admin, not the new database owner.
    raw.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(OWNER)))
    raw.execute(sql.SQL("CREATE SCHEMA {} AUTHORIZATION {}").format(sql.Identifier(SCHEMA), sql.Identifier(OWNER)))
    raw.execute(sql.SQL("COMMENT ON SCHEMA {} IS {}").format(sql.Identifier(SCHEMA), sql.Literal(MARKER)))
    raw.execute(sql.SQL("REVOKE ALL ON SCHEMA {} FROM PUBLIC").format(sql.Identifier(SCHEMA)))
    raw.execute(sql.SQL("SET LOCAL search_path TO {},pg_catalog").format(sql.Identifier(SCHEMA)))
    raw.execute("CREATE TABLE tenants (tenant_id UUID PRIMARY KEY)")
    raw.execute(TENANT_HELPER_SQL)
    raw.execute("REVOKE ALL ON FUNCTION app_tenant_id() FROM PUBLIC")
    raw.execute("""CREATE TABLE owner_task_authority_fixture (
        tenant_id UUID NOT NULL,owner_object_id UUID NOT NULL,active BOOLEAN NOT NULL,
        role_name TEXT NOT NULL,registered_subject_ids JSONB NOT NULL,
        authorized_subject_ids JSONB NOT NULL,PRIMARY KEY(tenant_id,owner_object_id))""")
    raw.execute("""CREATE TABLE owner_task_worker_fixture (
        tenant_id UUID NOT NULL,worker_id UUID NOT NULL,service_principal_id UUID NOT NULL,
        enabled BOOLEAN NOT NULL,PRIMARY KEY(tenant_id,worker_id))""")
    for tenant in (TENANT_A, TENANT_B):
        raw.execute("INSERT INTO tenants VALUES (%s)", (tenant,))
        raw.execute("INSERT INTO owner_task_worker_fixture VALUES (%s,%s,%s,TRUE)", (tenant, WORKER, PRINCIPAL))
    for tenant, owner in ((TENANT_A, OWNER_A), (TENANT_A, OWNER_B), (TENANT_B, OWNER_A)):
        raw.execute("INSERT INTO owner_task_authority_fixture VALUES (%s,%s,TRUE,'owner',%s,%s)",
                    (tenant, owner, '["SYNTH_A","SYNTH_B"]', '["SYNTH_A","SYNTH_B"]'))
    raw.execute(migration_text)


def validate_scram_verifier(encrypted):
    """Check libpq output without returning/logging password-derived material."""
    require(type(encrypted) is bytes and len(encrypted) < 512, "scram_verifier_generation_failed")
    match = re.fullmatch(rb"SCRAM-SHA-256\$([1-9][0-9]{0,6}):([A-Za-z0-9+/=]+)\$([A-Za-z0-9+/=]+):([A-Za-z0-9+/=]+)", encrypted)
    require(match is not None and 4096 <= int(match[1]) <= 1000000, "scram_verifier_generation_failed")
    for encoded, length in zip(match.groups()[1:], (16, 32, 32)):
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except ValueError:
            raise PreparationError("scram_verifier_generation_failed") from None
        require(len(decoded) == length and base64.b64encode(decoded) == encoded,
                "scram_verifier_generation_failed")


def prepare_trial(admin, connect_disposable, *, migration_text, password):
    """Mask provider messages; external orchestration still owns audit/recovery."""
    try:
        return _prepare_trial(admin, connect_disposable, migration_text=migration_text, password=password)
    except PreparationError:
        raise
    except Exception:
        raise PreparationError("trusted_preparation_failed") from None


def _prepare_trial(admin, connect_disposable, *, migration_text, password):
    """Trusted installer only; never exposes a credential or changes production.

    No implicit retries or cleanup. A failure after nontransactional CREATE
    DATABASE leaves the exact partial scope for protected diagnosis. Separate
    orchestration must verify this source and reserve dispatch before calling.
    """
    validate_inputs(migration_text, password)
    require(callable(connect_disposable), "missing_disposable_connector")
    installer = bind_trusted_connection(admin, "postgres")
    before = scope_metadata(admin)
    require(before == {"database": None, "roles": []}, "registered_names_already_exist")
    observed = assert_public_database_boundary(admin)
    # On this private blank host ONLY; provider-owned ACLs are untouched.
    with admin.transaction():
        for statement in boundary.customer_database_revokes(observed):
            admin.execute(statement)
    _create_roles(admin)
    admin.execute(sql.SQL("CREATE DATABASE {} OWNER {} TEMPLATE template0")
                  .format(sql.Identifier(DATABASE), sql.Identifier(OWNER)))
    with admin.transaction():
        admin.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(OWNER)))
        admin.execute(sql.SQL("COMMENT ON DATABASE {} IS {}").format(sql.Identifier(DATABASE), sql.Literal(MARKER)))
        admin.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(DATABASE)))
        for role in (installer, LOGIN):
            admin.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}")
                .format(sql.Identifier(DATABASE), sql.Identifier(role)))
    raw = connect_disposable()
    try:
        require(bind_trusted_connection(raw, DATABASE) == installer, "disposable_installer_mismatch")
        with raw.transaction():
            _initialize(raw, migration_text)
            from owner_task_isolated_permissions import PermissionTarget, grant_statements, verify_permissions
            for statement in grant_statements(PermissionTarget()):
                raw.execute(statement)
        validate_registered_metadata(scope_metadata(admin), login_enabled=False)
        # Verify named effective privileges while the login is still disabled.
        # Only the two approved Microsoft-owned databases retain provider
        # defaults. This metadata check never proves behavioral isolation.
        verify_permissions(raw, PermissionTarget(), installer_identity=installer,
                           require_fresh_login=False, login_enabled=False)
        # Pre-encrypt with SCRAM via libpq: never put the plaintext password in
        # SQL text. The verifier itself is still sensitive and never logged.
        encrypted = admin.pgconn.encrypt_password(password.encode(), LOGIN.encode(), b"scram-sha-256")
        validate_scram_verifier(encrypted)
        # The restricted harness must independently check fresh authentication.
        with admin.transaction():
            admin.execute(sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}")
                .format(sql.Identifier(LOGIN), sql.Literal(encrypted.decode("ascii"))))
            for option, value in (("statement_timeout", "45s"), ("lock_timeout", "3s"),
                                  ("idle_in_transaction_session_timeout", "45s")):
                admin.execute(sql.SQL("ALTER ROLE {} SET {} TO {}")
                    .format(sql.Identifier(LOGIN), sql.Identifier(option), sql.Literal(value)))
            # Failure rolls back LOGIN/password/options rather than reporting
            # failure after committing an enabled unverified login.
            validate_registered_metadata(scope_metadata(admin), login_enabled=True)
    finally:
        raw.close()
    return {"status": "prepared", "database": DATABASE, "marker": MARKER, "installer_role": installer,
            "migration_sha256": MIGRATION_SHA256, "fresh_login_verified": False,
            "non_owner_privilege_boundary_verified": False, "credential_isolation_verified": False,
            "production_migration_installed": False, "ibkr_source_rows_read": 0,
            "ibkr_source_rows_written": 0, "model_calls": 0, "agents_activated": 0}


def set_synthetic_owner_revoked(raw):
    try:
        return _set_synthetic_owner_revoked(raw)
    except PreparationError:
        raise
    except Exception:
        raise PreparationError("synthetic_revocation_failed") from None


def _set_synthetic_owner_revoked(raw):
    """Trusted test-control connection only. No model receives this callback."""
    installer = bind_trusted_connection(raw, DATABASE)
    require(installer not in ROLES, "untrusted_revocation_connector")
    row = raw.execute("SELECT pg_get_userbyid(datdba) AS owner,"
        "shobj_description(oid,'pg_database') AS marker FROM pg_database WHERE datname=current_database()").fetchone()
    require(row == {"owner": OWNER, "marker": MARKER}, "revocation_database_marker_mismatch")
    schema = raw.execute("SELECT pg_get_userbyid(n.nspowner) AS owner,"
        "obj_description(n.oid,'pg_namespace') AS marker,"
        "pg_get_userbyid(c.relowner) AS fixture_owner,c.relkind AS fixture_kind "
        "FROM pg_namespace n JOIN pg_class c ON c.relnamespace=n.oid "
        "WHERE n.nspname=%s AND c.relname='owner_task_authority_fixture'", (SCHEMA,)).fetchone()
    require(schema == {"owner": OWNER, "marker": MARKER, "fixture_owner": OWNER, "fixture_kind": "r"},
            "revocation_schema_fixture_mismatch")
    with raw.transaction():
        raw.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(OWNER)))
        raw.execute(sql.SQL("SET LOCAL search_path TO {},pg_catalog").format(sql.Identifier(SCHEMA)))
        cursor = raw.execute("UPDATE owner_task_authority_fixture SET active=FALSE "
            "WHERE tenant_id=%s AND owner_object_id=%s AND active IS TRUE", (TENANT_A, OWNER_A))
        require(type(cursor.rowcount) is int and cursor.rowcount == 1, "synthetic_authority_not_active")
    return {"status": "passed", "database": DATABASE, "schema": SCHEMA, "marker": MARKER,
            "fixture": "owner_task_authority_fixture", "tenant_id": TENANT_A,
            "owner_object_id": OWNER_A, "active": False, "rows_changed": 1,
            "mutation_role": OWNER, "synthetic": True}
