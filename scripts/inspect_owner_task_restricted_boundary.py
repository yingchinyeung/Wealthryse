"""Inert, catalog-only inspection; does not provision or authorize a trial.

Only the trusted bootstrap wrapper supplies a connection. No credential,
connector, environment reader, model tool, setup or cleanup is present here.
An eligible catalog boundary is point-in-time evidence, not process isolation.
"""
import re

HOST = "ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com"
DATABASE = "owner_codex_restricted_20261004"
ROLES = ("ibkr_owner_codex_schema_20261004", "ibkr_owner_codex_dml_20261004",
         "ibkr_owner_codex_login_20261004")
PREFIX = "OWNER_TASK_RESTRICTED_BOUNDARY "


class BoundaryError(ValueError):
    def __init__(self, code):
        self.code = code if type(code) is str and re.fullmatch(r"[a-z][a-z0-9_]{0,79}", code) else "boundary_inspection_failed"
        super().__init__(self.code)


def require(condition, code):
    if not condition:
        raise BoundaryError(code)


def validate_record(record):
    fixed = dict(status="observed", management_database="postgres", readonly_verified=True,
        tls_verified=True, postgres_major=16, restricted_setup_authorized=False,
        public_schema_owner_compatibility_verified=False, fresh_restricted_login_verified=False,
        model_credential_isolation_verified=False, production_database_touched=False,
        source_rows_read=0, source_rows_written=0, ddl_executed=0, grants_changed=0,
        model_calls=0, agents_activated=0)
    dynamic = {"trial_database_absent", "trial_roles_absent", "non_target_public_connect_databases",
               "non_target_public_connect_count", "database_boundary_eligible", "reason"}
    require(type(record) is dict and set(record) == set(fixed) | dynamic, "boundary_record_shape_invalid")
    require(all(type(record[k]) is type(v) and record[k] == v for k, v in fixed.items()), "boundary_record_fixed_fields_differ")
    names = record["non_target_public_connect_databases"]
    require(type(names) is list and len(names) <= 64 and all(type(n) is str and
        re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,62}", n) and n != DATABASE for n in names)
        and names == sorted(set(names)), "boundary_database_inventory_invalid")
    require(type(record["non_target_public_connect_count"]) is int
        and record["non_target_public_connect_count"] == len(names), "boundary_database_count_invalid")
    require(all(type(record[k]) is bool for k in
        ("trial_database_absent", "trial_roles_absent", "database_boundary_eligible")), "boundary_flags_invalid")
    absent = record["trial_database_absent"] and record["trial_roles_absent"]
    require(record["database_boundary_eligible"] is (absent and not names), "boundary_eligibility_invalid")
    expected = "registered_scope_exists" if not absent else (
        "non_target_public_database_connect" if names else "catalog_boundary_eligible")
    require(record["reason"] == expected, "boundary_reason_invalid")
    return record


def inspect_boundary(raw):
    try:
        return _inspect_boundary(raw)
    except BoundaryError:
        raise
    except Exception:
        raise BoundaryError("boundary_inspection_failed") from None


def _inspect_boundary(raw):
    require(raw.autocommit is True and raw.info.transaction_status == 0,
            "fresh_idle_catalog_connection_required")
    require(raw.info.host == HOST and raw.info.dbname == "postgres" and raw.pgconn.ssl_in_use is True,
            "registered_encrypted_catalog_connection_required")
    identity = raw.execute("SELECT pg_catalog.current_database()::text AS database,"
        "current_user::text AS role,session_user::text AS login,"
        "pg_catalog.current_setting('server_version_num') AS version,"
        "pg_catalog.current_setting('transaction_read_only') AS readonly,"
        "pg_catalog.current_setting('search_path') AS search_path,"
        "pg_catalog.pg_my_temp_schema() AS temporary_schema").fetchone()
    keys = {"database", "role", "login", "version", "readonly", "search_path", "temporary_schema"}
    require(type(identity) is dict and set(identity) == keys and identity["database"] == "postgres"
        and type(identity["role"]) is str and identity["role"] == identity["login"] == raw.info.user
        and identity["role"] not in ROLES and type(identity["version"]) is str
        and re.fullmatch(r"16[0-9]{4}", identity["version"]) and identity["readonly"] == "on"
        and identity["search_path"] == "pg_catalog" and type(identity["temporary_schema"]) is int
        and identity["temporary_schema"] == 0, "catalog_identity_or_readonly_mismatch")
    databases = raw.execute("SELECT DISTINCT d.datname FROM pg_catalog.pg_database d,LATERAL "
        "pg_catalog.aclexplode(coalesce(d.datacl,pg_catalog.acldefault('d',d.datdba))) a "
        "WHERE d.datallowconn AND d.datname<>%s AND a.grantee=0 "
        "AND a.privilege_type='CONNECT' ORDER BY d.datname", (DATABASE,)).fetchall()
    require(type(databases) is list and all(type(r) is dict and set(r) == {"datname"} for r in databases),
            "public_connect_catalog_shape_invalid")
    exists = raw.execute("SELECT datname FROM pg_catalog.pg_database WHERE datname=%s", (DATABASE,)).fetchone()
    require(exists is None or exists == {"datname": DATABASE}, "trial_database_catalog_shape_invalid")
    roles = raw.execute("SELECT rolname FROM pg_catalog.pg_roles WHERE rolname=ANY(%s) ORDER BY rolname",
        (list(ROLES),)).fetchall()
    require(type(roles) is list and all(type(r) is dict and set(r) == {"rolname"}
        and r["rolname"] in ROLES for r in roles) and len({r["rolname"] for r in roles}) == len(roles),
        "trial_role_catalog_shape_invalid")
    names = [r["datname"] for r in databases]
    absent = exists is None and roles == []
    record = dict(status="observed", management_database="postgres", readonly_verified=True,
        tls_verified=True, postgres_major=16, trial_database_absent=exists is None, trial_roles_absent=roles == [],
        non_target_public_connect_databases=names, non_target_public_connect_count=len(names),
        database_boundary_eligible=absent and not names,
        reason="registered_scope_exists" if not absent else (
            "non_target_public_database_connect" if names else "catalog_boundary_eligible"),
        restricted_setup_authorized=False, public_schema_owner_compatibility_verified=False,
        fresh_restricted_login_verified=False, model_credential_isolation_verified=False,
        production_database_touched=False, source_rows_read=0, source_rows_written=0,
        ddl_executed=0, grants_changed=0, model_calls=0, agents_activated=0)
    return validate_record(record)
